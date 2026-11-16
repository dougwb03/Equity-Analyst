#!/usr/bin/env python3
"""A fixed LangGraph equity-research desk using the OpenAI Agents SDK.

The graph, not a model, controls node order.  SEC collection is deterministic;
only the four role nodes and the verifier make model calls.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypedDict

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, set_default_openai_client, set_tracing_disabled
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

# The seven deterministic SEC steps are the checked checkpoint-1 implementation.
import checkpoint_1.equity_desk as sec


ROOT = Path(__file__).resolve().parent
ROLES = ROOT / "roles"
REQUIRED_PARTS = (
    "Overview", "Market Positioning and Growth Outlook", "Valuation Insights",
    "Future Developments and Strategic Recommendations", "Investment Recommendation",
)
ROLE_FILES = {
    "Research Analyst": "research_analyst.md", "Financial Analyst": "financial_analyst.md",
    "Investment Advisor": "investment_advisor.md", "Strategy Manager": "strategy_manager.md",
}


class DeskState(TypedDict, total=False):
    ticker: str
    peers: list[str]
    price: float | None
    model: str
    run_dir: str
    company: str
    cik: str
    filings: dict[str, sec.Filing]
    facts: dict[str, Any]
    quarter: list[str]
    peer_data: list[dict[str, Any]]
    role_outputs: dict[str, str]
    manager_review: str
    report: str
    report_path: str
    rework: dict[str, str] | None
    verify_only: bool


def json_default(value: Any) -> Any:
    if isinstance(value, Path): return str(value)
    if isinstance(value, sec.Filing): return value.__dict__
    return str(value)


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=json_default), encoding="utf-8")


def record(state: DeskState, node: str, payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(state["run_dir"])
    save_json(run_dir / f"{node}_input.json", payload)
    save_json(run_dir / f"{node}_output.json", result)
    return result


def role_instructions(role: str) -> str:
    instructions = (ROLES / ROLE_FILES[role]).read_text(encoding="utf-8")
    citation_contract = """

Citation contract

Use these exact hidden source records after the sentence or paragraph they
support. Do not invent another citation format. Do not use `file=`, `fact=`,
`evidence=`, a comma-separated source list, or a source-line range.

* Filing text: `<!-- [source: line|file=8-K_exhibit_99_1.txt|line=44] -->`
* XBRL fact: `<!-- [source: xbrl|tag=us-gaap:NetIncomeLoss|period=2025-09-01:2026-08-30|value=9226000000] -->`
* Calculation: `<!-- [source: ratio|operation=divide|inputs=11685000000;303154000000|result=0.0385|definition=operating income / total revenue] -->`

Use one source record per claim when possible. A score such as `4/5` is your
judgment, not a filing number. State its filing support in words, but do not
put a score inside the same sentence as a sourced financial number. Keep
`48.4x` distinct from `5x`; do not round a P/E multiple to a different order
of magnitude. Count only locations that the filing names in the category used.
"""
    return instructions + citation_contract


def evidence_excerpt(path: Path, maximum_characters: int) -> str:
    """Keep line references while keeping a model packet below its context limit."""
    return sec.bounded_evidence(sec.numbered_text(path), maximum_characters)


def first_release_paragraph(path: Path) -> str:
    """Return the announcement paragraph that follows an earnings-release title."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    start = next((index for index, line in enumerate(lines)
                  if re.search(r"\b(today )?announced\b.*\bresults\b", line, re.I)), None)
    if start is None:
        start = next((index for index, line in enumerate(lines) if line), 0)
    return " ".join(line for line in lines[start:start + 2] if line)


def comparable_sales_table(path: Path) -> list[dict[str, str]]:
    """Label a four-column comparable-sales table from its filing headings."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((index for index, value in enumerate(lines)
                  if re.search(r"comparable sales.*fourth quarter.*fiscal year", value, re.I)), None)
    if start is None:
        return []
    percent = re.compile(r"^\s*\d+(?:\.\d+)?%\s*$")
    table: list[dict[str, str]] = []
    for index in range(start + 1, min(len(lines), start + 50)):
        label, values = lines[index].strip(), lines[index + 1:index + 5]
        if not label or percent.match(label) or len(values) != 4 or not all(percent.match(value) for value in values):
            continue
        table.append({"label": label, "fourth_quarter": values[0].strip(),
                      "fourth_quarter_adjusted": values[1].strip(),
                      "fiscal_year": values[2].strip(),
                      "fiscal_year_adjusted": values[3].strip(),
                      "source_lines": f"{index + 2}-{index + 5}"})
    return table


def agents_model(api_key: str, model_name: str) -> OpenAIChatCompletionsModel:
    # This course uses Purdue GenAI Studio.  An environment override still
    # permits another OpenAI-compatible endpoint without changing the code.
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or "https://genai.rcac.purdue.edu/api"
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)
    return OpenAIChatCompletionsModel(model=model_name, openai_client=client)


def call_role(state: DeskState, role: str, packet: dict[str, Any], attempt: int = 1) -> str:
    """Make exactly one SDK model call. The JSON packet is the whole role input."""
    print(f"{role} starting...")
    input_path = Path(state["run_dir"]) / f"{role.lower().replace(' ', '_')}_{attempt}_input.json"
    output_path = Path(state["run_dir"]) / f"{role.lower().replace(' ', '_')}_{attempt}_output.json"
    save_json(input_path, packet)
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key: raise ValueError("API_KEY is required in .env.")
    agent = Agent(name=role, instructions=role_instructions(role), model=agents_model(api_key, state["model"]),
                  model_settings=ModelSettings(max_tokens=4_000,
                                               extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
    result = Runner.run_sync(agent, json.dumps(packet, ensure_ascii=False), max_turns=1)
    output = str(result.final_output or "").strip()
    if not output: raise ValueError(f"{role} returned an empty response.")
    save_json(output_path, {"role": role, "attempt": attempt, "output": output})
    return output


def step_1(state: DeskState) -> dict[str, Any]:
    print("Step 1 starting...")
    client = sec.EdgarClient()
    cik, company = sec.resolve_ticker(client, state["ticker"])
    return record(state, "01_step_1", {"ticker": state["ticker"]}, {"cik": cik, "company": company}) | {"cik": cik, "company": company}


def step_2(state: DeskState) -> dict[str, Any]:
    print("Step 2 starting...")
    client = sec.EdgarClient(); filings = sec.choose_filings(client, state["cik"], sec.submissions(client, state["cik"]))
    output = {form: sec.filing_source(filing) for form, filing in filings.items()}
    return record(state, "02_step_2", {"cik": state["cik"]}, output) | {"filings": filings}


def step_3(state: DeskState) -> dict[str, Any]:
    print("Step 3 starting...")
    path = sec.save_documents(sec.EdgarClient(), state["cik"], state["filings"], Path(state["run_dir"]))
    return record(state, "03_step_3", {"filings": state["filings"]}, {"earnings_release_html": str(path)})


def step_4(state: DeskState) -> dict[str, Any]:
    print("Step 4 starting...")
    facts = sec.collect_facts(sec.EdgarClient(), state["cik"])
    save_json(Path(state["run_dir"]) / "facts.json", facts)
    return record(state, "04_step_4", {"cik": state["cik"]}, facts) | {"facts": facts}


def step_5(state: DeskState) -> dict[str, Any]:
    print("Step 5 starting...")
    ratios = sec.calculation_records(state["facts"], state.get("price"))
    return record(state, "05_step_5", {"price": state.get("price"), "facts": state["facts"]}, {"ratios": ratios})


def step_6(state: DeskState) -> dict[str, Any]:
    print("Step 6 starting...")
    quarter = sec.earnings_evidence(Path(state["run_dir"]) / "8-K_exhibit_99_1.html")
    return record(state, "06_step_6", {"release": "8-K_exhibit_99_1.html"}, {"quarter": quarter}) | {"quarter": quarter}


def step_7(state: DeskState) -> dict[str, Any]:
    print("Step 7 starting...")
    client = sec.EdgarClient(); peers: list[dict[str, Any]] = []
    for ticker in state.get("peers", []):
        cik, _ = sec.resolve_ticker(client, ticker); facts = sec.collect_peer_facts(client, cik)
        peers.append({"ticker": ticker, "facts": sec.source_facts(facts),
                      "operating_margin": {"value": sec.ratio(sec.value(facts, "operating_income"), sec.value(facts, "total_revenue")),
                                           "arithmetic": "operating income / total revenue"}})
    return record(state, "07_step_7", {"peers": state.get("peers", [])}, {"peer_data": peers}) | {"peer_data": peers}


def research_node(state: DeskState) -> dict[str, Any]:
    run_dir = Path(state["run_dir"]); sources = {form: sec.filing_source(item) for form, item in state["filings"].items()}
    packet = {"task": "Prepare cited research analysis.", "context": {"filings": sources,
        "business_10_k": sec.bounded_evidence(sec.section_with_lines(run_dir / "10-K.txt", r"item\\s+1\\.?\\s+business", r"item\\s+1a\\.?"), 12_000),
        "mda_10_k": sec.bounded_evidence(sec.section_with_lines(run_dir / "10-K.txt", r"item\\s*7\\.?", r"item\\s*7a\\.?"), 12_000),
        "earnings_release": evidence_excerpt(run_dir / "8-K_exhibit_99_1.txt", 8_000),
        "comparable_sales_table": comparable_sales_table(run_dir / "8-K_exhibit_99_1.txt")}, "expected_output": "Markdown material for the assigned report parts."}
    output = call_role(state, "Research Analyst", packet)
    roles = dict(state.get("role_outputs", {})); roles["Research Analyst"] = output
    return record(state, "08_research_analyst", packet, {"output": output}) | {"role_outputs": roles}


def financial_node(state: DeskState) -> dict[str, Any]:
    run_dir = Path(state["run_dir"]); sources = {form: sec.filing_source(item) for form, item in state["filings"].items()}
    packet = {"task": "Prepare cited financial analysis.", "context": {"filings": sources, "price": state.get("price"),
        "step_4_facts": sec.source_facts(state["facts"]), "step_5_ratios": sec.calculation_records(state["facts"], state.get("price")),
        "step_6_quarter": state.get("quarter", []), "earnings_release": evidence_excerpt(run_dir / "8-K_exhibit_99_1.txt", 8_000),
        "comparable_sales_table": comparable_sales_table(run_dir / "8-K_exhibit_99_1.txt"), "step_7_peers": state.get("peer_data", [])},
        "expected_output": "Markdown material for Valuation Insights."}
    output = call_role(state, "Financial Analyst", packet)
    roles = dict(state.get("role_outputs", {})); roles["Financial Analyst"] = output
    return record(state, "09_financial_analyst", packet, {"output": output}) | {"role_outputs": roles}


def advisor_node(state: DeskState) -> dict[str, Any]:
    packet = {"task": "Create the thesis and rating.", "context": {"research_analyst": state["role_outputs"]["Research Analyst"], "financial_analyst": state["role_outputs"]["Financial Analyst"]},
              "expected_output": "Markdown Investment Recommendation material."}
    output = call_role(state, "Investment Advisor", packet)
    roles = dict(state["role_outputs"]); roles["Investment Advisor"] = output
    return record(state, "10_investment_advisor", packet, {"output": output}) | {"role_outputs": roles}


def parse_rework(text: str) -> dict[str, str] | None:
    match = re.search(r"^REWORK ROLE:\s*(Research Analyst|Financial Analyst|Investment Advisor)\s*;\s*PART:\s*(.+?)\s*;\s*FEEDBACK:\s*(.+)$", text, re.I | re.M)
    if not match: return None
    return {"role": match.group(1), "part": match.group(2), "feedback": match.group(3)}


def insight_line(text: str) -> str | None:
    """Return the required final Insight line, if the role supplied one."""
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    if not nonempty or not nonempty[-1].startswith("Insight:"):
        return None
    return nonempty[-1]


def insight_failure(role_outputs: dict[str, str], release_first_paragraph: str) -> dict[str, str] | None:
    """Apply the two required Insight checks without adding a graph branch."""
    release_numbers = {token.rstrip("%") for token in NUMBER.findall(release_first_paragraph)}
    for role in ("Research Analyst", "Financial Analyst", "Investment Advisor"):
        line = insight_line(role_outputs.get(role, ""))
        part = next(part for part, mapped_role in PART_ROLE.items() if mapped_role == role)
        if line is None:
            return {"role": role, "part": part, "feedback": "Add one final Insight: line with a filing and line source."}
        sources = [source_fields(source) for source in SOURCE.findall(line)]
        if not any(source.get("kind") == "line" and source.get("file") and source.get("line", "").isdigit() for source in sources):
            return {"role": role, "part": part, "feedback": "The final Insight line must name a saved filing and one exact line."}
        claim = SOURCE.sub("", line)
        claim_numbers = {token.rstrip("%") for token in NUMBER.findall(claim)}
        if claim_numbers and claim_numbers.issubset(release_numbers):
            return {"role": role, "part": part,
                    "feedback": "Use an Insight number that the earnings release first paragraph does not state."}
    return None


PART_ROLE = {
    "Overview": "Research Analyst",
    "Market Positioning and Growth Outlook": "Research Analyst",
    "Valuation Insights": "Financial Analyst",
    "Future Developments and Strategic Recommendations": "Research Analyst",
    "Investment Recommendation": "Investment Advisor",
}


def section_text(text: str, part: str) -> str | None:
    """Return the Markdown section with the named required heading."""
    match = re.search(rf"(?m)^#{{1,2}}\s+{re.escape(part)}\s*$", text)
    if not match:
        return None
    following = re.search(r"(?m)^#{1,2}\s+", text[match.end():])
    end = match.end() + following.start() if following else len(text)
    return text[match.end():end].strip()


def guaranteed_report(manager_text: str, role_outputs: dict[str, str]) -> tuple[str, bool]:
    """Keep complete, machine-verifiable sections when a provider cuts off citations."""
    sections: list[str] = []
    repaired = False
    for part in REQUIRED_PARTS:
        content = section_text(manager_text, part)
        specialist = section_text(role_outputs[PART_ROLE[part]], part)
        # The Manager compiles the report, but must not discard source records
        # while it makes the prose shorter.
        if (not content or source_count(content) < source_count(specialist or "")
                or (insight_line(specialist or "") and not insight_line(content))):
            repaired = True
            content = specialist
        if not content:
            repaired = True
            content = role_outputs[PART_ROLE[part]].strip()
        sections.append(f"## {part}\n\n{content}")
    return "\n\n".join(sections), repaired


def manager_node(state: DeskState) -> dict[str, Any]:
    release_first_paragraph = first_release_paragraph(Path(state["run_dir"]) / "8-K_exhibit_99_1.txt")
    packet = {"task": "Review the five required report parts for presence, consistency, and required Insight lines.", "context": {"role_outputs": state["role_outputs"], "required_parts": REQUIRED_PARTS,
              "earnings_release_first_paragraph": release_first_paragraph},
              "expected_output": "Exactly one REWORK ROLE line when one part fails; otherwise exactly APPROVED."}
    output = call_role(state, "Strategy Manager", packet)
    rework = insight_failure(state["role_outputs"], release_first_paragraph) or parse_rework(output)
    # Keep the graph moving if a provider adds prose around APPROVED. The raw
    # review is saved for inspection; the fixed compile node still runs next.
    result: dict[str, Any] = {"manager_review": output, "rework": rework,
                              "review_format_valid": bool(rework) or output.strip().upper() == "APPROVED"}
    return record(state, "11_strategy_manager", packet, result) | result


def rework_node(state: DeskState) -> dict[str, Any]:
    print("Permitted rework starting...")
    rework = state.get("rework")
    if not rework: return record(state, "12_rework", {"rework": None}, {"skipped": True})
    role = rework["role"]
    # One retry is the only code path that can call a specialist twice.
    packet = {"task": "Revise only the failing part once.", "context": {"prior_output": state["role_outputs"][role], "manager_feedback": rework},
              "expected_output": f"One replacement Markdown section named '{rework['part']}', in 180 words or fewer, retaining valid citations. End the output with one valid Insight: line. Do not revise any other part."}
    output = call_role(state, role, packet, attempt=2)
    # A retry replaces only one report part.  Preserve all other material from
    # the first role call so the compiling Manager still has every required
    # section available.
    roles = dict(state["role_outputs"])
    roles[role] = (state["role_outputs"][role].rstrip() + "\n\n"
                   + f"## Revised {rework['part']}\n\n" + output.strip())
    return record(state, "12_rework", packet, {"role": role, "output": output}) | {"role_outputs": roles}


def compile_node(state: DeskState) -> dict[str, Any]:
    print("Strategy Manager compile starting...")
    packet = {"task": "Compile the final report after the one permitted rework.", "context": {"role_outputs": state["role_outputs"], "required_parts": REQUIRED_PARTS},
              "expected_output": "One final Markdown report with exactly the five required H2 headings and all three analyst Insight lines."}
    manager_body = call_role(state, "Strategy Manager", packet, attempt=2)
    body, repaired = guaranteed_report(manager_body, state["role_outputs"])
    result = {"report_body": body, "manager_output_repaired": repaired}
    header = "Filings read: " + "; ".join(f"{form} ({item.accession})" for form, item in state["filings"].items()) + "\n"
    report = canonical_source_markup(header + body.strip() + "\n", state["facts"])
    report_path = Path(state["run_dir"]) / "investment_report.md"
    report_path.write_text(report, encoding="utf-8")
    return record(state, "13_compile", packet, result | {"report_path": str(report_path)}) | {"report": report, "report_path": str(report_path)}


NUMBER = re.compile(r"(?<![A-Za-z0-9])\(?\$?\d(?:[\d,]*\d)?(?:\.\d+)?%?\)?")
SOURCE = re.compile(r"\[source:\s*(.*?)\]", re.I)


def normal(text: str) -> str: return re.sub(r"\s+", " ", text).strip().lower()


def source_count(text: str) -> int:
    return len(SOURCE.findall(text))

def decimal(text: str) -> Decimal:
    value = text.strip().replace("$", "").replace(",", "").replace("(", "-").replace(")", "")
    percent = value.endswith("%"); value = value[:-1] if percent else value
    return Decimal(value) / (Decimal(100) if percent else Decimal(1))


def display_match(quoted: Decimal, actual: Decimal, token: str) -> bool:
    """Accept normal report rounding, but not a material change to a number."""
    raw = token.strip().replace("$", "").replace("%", "").replace("(", "").replace(")", "")
    digits = len(raw.rsplit(".", 1)[1]) if "." in raw else 0
    tolerance = Decimal("0.5") * (Decimal(10) ** -digits)
    if token.strip().endswith("%"):
        tolerance /= Decimal(100)
    return abs(quoted - actual) <= tolerance


def source_fields(source: str) -> dict[str, str]:
    pieces = [piece.strip() for piece in source.split("|")]; fields = {"kind": pieces[0].lower()}
    for piece in pieces[1:]:
        if "=" in piece:
            key, value = piece.split("=", 1); fields[key.strip().lower()] = value.strip()
    period = fields.get("period", "")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:\s+to\s+|/)(\d{4}-\d{2}-\d{2})", period, re.I)
    if match:
        fields["period"] = f"{match.group(1)}:{match.group(2)}"
    # Some models spell the required percent-change operation as prose. It is
    # the same deterministic calculation, so normalize it before verification.
    if fields.get("operation", "").lower() in {"subtract_then_divide", "subtract then divide"}:
        fields["operation"] = "percent_change"
    return fields


def xbrl_record(entry: dict[str, Any], item: dict[str, Any]) -> str | None:
    """Create the only XBRL source form accepted by the deterministic verifier."""
    tag, value, end = entry.get("tag"), item.get("val"), item.get("end")
    if not tag or value is None or not end:
        return None
    period = f"{item['start']}:{end}" if item.get("start") else end
    return f"[source: xbrl|tag={tag}|period={period}|value={value}]"


def canonical_source_markup(report: str, facts: dict[str, Any] | None = None) -> str:
    """Repair common model shorthand into the required source syntax.

    The repair does not create facts. It only expands a legacy shorthand when
    the saved facts file contains the exact referenced XBRL records.
    """
    def replace(match: re.Match[str]) -> str:
        source = match.group(1)
        # Models sometimes write ``file=...|line=...`` one or more times.
        # Expand every real pair into its own deterministic line citation.
        line_pairs = re.findall(r"file\s*=\s*([^|,\]]+)\s*\|\s*line\s*=\s*(\d+)", source, re.I)
        if line_pairs:
            return " ".join(f"[source: line|file={name.strip()}|line={number}]"
                            for name, number in line_pairs)

        # Older role drafts used ``fact=...|key=...|period=current,prior``.
        # The saved facts map supplies the exact tag, period, and value needed
        # for a valid XBRL citation.
        key_match = re.search(r"(?:^|\|)key\s*=\s*([^|]+)", source, re.I)
        period_match = re.search(r"(?:^|\|)period\s*=\s*([^|]+)", source, re.I)
        if facts and key_match:
            keys = [key.strip() for key in key_match.group(1).split(",")]
            periods = [period.strip() for period in (period_match.group(1) if period_match else "current").split(",")]
            records: list[str] = []
            for key in keys:
                entry = facts.get("concepts", {}).get(key, {})
                for period_name in periods:
                    record = xbrl_record(entry, entry.get(period_name, {}))
                    if record and record not in records:
                        records.append(record)
            if records:
                return " ".join(records)
        fields = source_fields(source)
        if "period" not in fields or (" to " not in source.lower() and "/" not in source):
            return match.group(0)
        ordered = [fields.pop("kind")]
        ordered.extend(f"{key}={value}" for key, value in fields.items())
        return "[source: " + "|".join(ordered) + "]"
    return SOURCE.sub(replace, report)


def fact_values(facts: dict[str, Any]) -> dict[tuple[str, str], Decimal]:
    values: dict[tuple[str, str], Decimal] = {}
    for entry in facts.get("concepts", {}).values():
        for period in ("current", "prior"):
            item = entry.get(period) or {}
            if item.get("val") is not None and entry.get("tag") and item.get("end"):
                values[(entry["tag"], item["end"])] = Decimal(str(item["val"]))
                if item.get("start"):
                    values[(entry["tag"], f"{item['start']}:{item['end']}")] = Decimal(str(item["val"]))
    return values


def machine_mark(line: str, number: str, fields: dict[str, str], run_dir: Path, facts: dict[str, Any]) -> tuple[str, str]:
    try:
        quoted = decimal(number)
    except InvalidOperation:
        return "unsupported", "number cannot be read"
    if fields.get("kind") == "xbrl":
        expected = fact_values(facts).get((fields.get("tag", ""), fields.get("period", "")))
        if expected is None or "value" not in fields: return "unsupported", "missing XBRL tag, period, or value"
        cited = decimal(fields["value"])
        # A narrative can state a dollar fact in billions while the XBRL fact
        # and citation keep its exact dollar amount. Only scale large monetary
        # facts; EPS and ratios must remain unscaled.
        if abs(cited) >= Decimal("1000000"):
            lower_line = line.lower()
            if "billion" in lower_line:
                cited /= Decimal("1000000000"); expected /= Decimal("1000000000")
            elif "million" in lower_line:
                cited /= Decimal("1000000"); expected /= Decimal("1000000")
        if not display_match(quoted, cited, number):
            return "unsupported", "XBRL citation names a different number"
        return ("correct", "exact XBRL fact match") if display_match(quoted, expected, number) else ("wrong", f"facts value is {expected}")
    if fields.get("kind") == "line":
        file = fields.get("file", ""); line_number = fields.get("line", "")
        if file not in {"10-K.txt", "10-Q.txt", "8-K.txt", "8-K_exhibit_99_1.txt"}:
            return "unsupported", "source is not a saved filing text"
        target = run_dir / file
        if not target.exists() or not line_number.isdigit(): return "unsupported", "missing filing file or line"
        lines = target.read_text(encoding="utf-8").splitlines(); index = int(line_number) - 1
        if index < 0 or index >= len(lines): return "wrong", "line does not exist"
        source_tokens = NUMBER.findall(lines[index])
        source_numbers = []
        for candidate in source_tokens:
            try: source_numbers.append(decimal(candidate))
            except InvalidOperation: pass
        percent_word = bool(re.search(r"\bpercent\b|%", lines[index], re.IGNORECASE))
        # Tables often report dollars in millions (for example, 15,825), while
        # the narrative reports the same value in billions ($15.825). Treat
        # that unit conversion as formatting, not as an error.
        percent_format_match = any(
            (number.endswith("%") and not token.endswith("%") and quoted * Decimal(100) == decimal(token))
            or (not number.endswith("%") and token.endswith("%") and quoted / Decimal(100) == decimal(token))
            for token in source_tokens
        ) if percent_word else False
        scaled_match = any(display_match(quoted, candidate / Decimal(1000), number)
                           for candidate in source_numbers)
        direct_match = any(display_match(quoted, candidate, number) for candidate in source_numbers)
        if direct_match or scaled_match or percent_format_match:
            return "correct", f"{file}:L{line_number} contains the formatted number"
        return "wrong", f"{file}:L{line_number} does not contain the cited number"
    if fields.get("kind") == "ratio":
        if not fields.get("definition") or not fields.get("operation") or not fields.get("inputs") or not fields.get("result"):
            return "unsupported", "ratio lacks definition, operation, inputs, or result"
        cited_result = decimal(fields["result"])
        display_divisor = Decimal(1)
        # A prose claim may express a cash-flow result in billions while its
        # ratio record keeps exact dollars. Preserve the exact arithmetic but
        # compare in the report's stated unit.
        if abs(cited_result) >= Decimal("1000000"):
            lower_line = line.lower()
            if "billion" in lower_line:
                display_divisor = Decimal("1000000000")
            elif "million" in lower_line:
                display_divisor = Decimal("1000000")
        cited_result /= display_divisor
        # A ratio citation supports its result, not each input shown in it.
        if not display_match(quoted, cited_result, number):
            return "unsupported", "ratio citation names a different number"
        operands = [decimal(value) for value in fields["inputs"].split(";")]
        op = fields["operation"]
        if op == "divide" and len(operands) == 2 and operands[1] != 0: actual = operands[0] / operands[1]
        elif op == "percent_change" and len(operands) == 2 and operands[1] != 0: actual = (operands[0] - operands[1]) / operands[1]
        elif op == "subtract" and len(operands) == 2: actual = operands[0] - operands[1]
        elif op == "add": actual = sum(operands)
        elif op == "multiply":
            actual = Decimal(1)
            for operand in operands: actual *= operand
        elif op == "average" and operands: actual = sum(operands) / len(operands)
        else: return "unsupported", "ratio operation cannot be recomputed"
        actual /= display_divisor
        return ("correct", "ratio recomputed") if display_match(cited_result, actual, fields["result"]) else ("wrong", f"recomputed value is {actual}")
    return "unsupported", "no recognized source"


def block_sources(lines: list[str]) -> dict[int, list[dict[str, str]]]:
    """Assign source-only citation lines to the preceding claim paragraph."""
    assigned: dict[int, list[dict[str, str]]] = {}
    claim_lines: list[int] = []
    for index, line in enumerate(lines):
        source_matches = SOURCE.findall(line)
        # Source records can be in an HTML comment. Markdown renderers hide
        # that metadata, while the saved report remains verifiable.
        without_sources = re.sub(r"<!--|-->", "", SOURCE.sub("", line)).strip()
        if source_matches and not without_sources:
            fields = [source_fields(source) for source in source_matches]
            for claim in claim_lines:
                assigned.setdefault(claim, []).extend(fields)
            continue
        if line.strip() and not line.startswith("## ") and not line.startswith("Filings read:"):
            claim_lines = [index]
        elif not line.strip():
            # Keep the last paragraph active; models often put citation-only
            # lines after a blank line.
            continue
        if source_matches:
            assigned.setdefault(index, []).extend(source_fields(source) for source in source_matches)
    return assigned


def best_mark(line: str, number: str, sources: list[dict[str, str]], run_dir: Path,
              facts: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    if not sources:
        mark, detail = machine_mark(line, number, {}, run_dir, facts)
        return mark, detail, {}
    attempts = [(machine_mark(line, number, fields, run_dir, facts), fields) for fields in sources]
    for (mark, detail), fields in attempts:
        if mark == "correct":
            return mark, detail, fields
    wrong_attempts = [((mark, detail), fields) for (mark, detail), fields in attempts if mark == "wrong"]
    if wrong_attempts:
        # A paragraph can have several citations. Select the cited line whose
        # numeric value is closest to the bad claim, so a changed EPS points
        # to the per-share line rather than an unrelated sales source.
        def distance(attempt: tuple[tuple[str, str], dict[str, str]]) -> Decimal:
            fields = attempt[1]
            target = run_dir / fields.get("file", "")
            line_number = fields.get("line", "")
            if fields.get("kind") != "line" or not target.exists() or not line_number.isdigit():
                return Decimal("Infinity")
            lines = target.read_text(encoding="utf-8").splitlines(); index = int(line_number) - 1
            if not 0 <= index < len(lines):
                return Decimal("Infinity")
            try:
                candidates = [item for item in NUMBER.findall(lines[index])
                              if item.startswith("$") == number.startswith("$")
                              and item.endswith("%") == number.endswith("%")]
                return min((abs(decimal(item) - decimal(number)) for item in candidates), default=Decimal("Infinity"))
            except InvalidOperation:
                return Decimal("Infinity")
        (mark, detail), fields = min(wrong_attempts, key=distance)
        return mark, detail, fields
    return "unsupported", "no listed source supports this number", attempts[0][1]


def correction_for(number: str, mark: str, fields: dict[str, str], run_dir: Path,
                   facts: dict[str, Any]) -> str:
    """Give a reproducible correction from the saved evidence, when possible.

    This is deliberately code, rather than an LLM guess.  It is also useful for
    the seeded one-dollar EPS test: the nearest number on the cited 10-K
    per-share line is the filing's actual EPS.
    """
    if mark == "correct":
        return ""
    kind = fields.get("kind")
    if kind == "xbrl":
        actual = fact_values(facts).get((fields.get("tag", ""), fields.get("period", "")))
        if actual is not None:
            return f"{actual} [XBRL {fields.get('tag')} | {fields.get('period')}]"
    if kind == "line":
        name, line_number = fields.get("file", ""), fields.get("line", "")
        target = run_dir / name
        if target.exists() and line_number.isdigit():
            lines = target.read_text(encoding="utf-8").splitlines()
            index = int(line_number) - 1
            if 0 <= index < len(lines):
                candidates = NUMBER.findall(lines[index])
                try:
                    if not (number.startswith("$") or number.endswith("%")):
                        return "No correction supported by the cited filing line."
                    quoted = decimal(number)
                    # Do not turn an unsupported date, count, or dollar claim
                    # into an unrelated correction from the same filing line.
                    compatible = [value for value in candidates
                                  if value.endswith("%") == number.endswith("%")
                                  and value.startswith("$") == number.startswith("$")]
                    if not compatible:
                        return "No correction supported by the cited filing line."
                    candidates = compatible
                    if candidates:
                        value = min(candidates, key=lambda item: abs(decimal(item) - quoted))
                        return f"{value} [{name}:L{line_number}]"
                except InvalidOperation:
                    pass
    if kind == "ratio" and fields.get("inputs") and fields.get("operation"):
        try:
            operands = [decimal(value) for value in fields["inputs"].split(";")]
            if fields["operation"] == "divide" and len(operands) == 2 and operands[1] != 0:
                actual = operands[0] / operands[1]
            elif fields["operation"] == "percent_change" and len(operands) == 2 and operands[1] != 0:
                actual = (operands[0] - operands[1]) / operands[1]
            elif fields["operation"] == "subtract" and len(operands) == 2:
                actual = operands[0] - operands[1]
            else:
                return "No correction supported by this ratio citation."
            value = f"{actual * 100:.2f}%" if number.endswith("%") else str(actual)
            return f"{value} [{fields.get('definition', 'ratio')} from quoted inputs]"
        except (InvalidOperation, KeyError):
            pass
    return "No correction supported by the saved filings or facts."


def verification_rows(report: str, run_dir: Path, facts: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    in_advisor = False
    lines = report.splitlines()
    sources_by_line = block_sources(lines)
    for line_number, line in enumerate(lines, 1):
        if line.startswith("Filings read:"):
            continue
        if line.startswith("## "): in_advisor = line.strip() == "## Investment Recommendation"
        if in_advisor and not line.strip().startswith("Insight:"):
            continue  # Skip the Advisor thesis but always verify its Insight.
        sources = sources_by_line.get(line_number - 1, [source_fields(item) for item in SOURCE.findall(line)])
        score_numbers = {value for match in re.finditer(r"(?:score\s*:\s*|\()\s*(\d+)\s*/\s*(\d+)\b", line, re.I)
                         for value in match.groups()}
        score_numbers.update(value for match in re.finditer(r"\bscore\s*:\s*(\d+)\s+out\s+of\s+(\d+)\b", line, re.I)
                             for value in match.groups())
        for number in NUMBER.findall(SOURCE.sub("", line)):
            # Persona scorecards use values such as 4/5. They are explicit
            # analyst judgments, not claims about the filing, so they are not
            # within the course's sourced-number verification requirement.
            if number in score_numbers:
                continue
            mark, detail, fields = best_mark(line, number, sources, run_dir, facts)
            rows.append({"line": str(line_number), "number": number, "mark": mark, "detail": detail,
                         "source": " | ".join(f"{k}={v}" for k, v in fields.items()) or "none",
                         "correction": correction_for(number, mark, fields, run_dir, facts)})
    return rows


def cited_filing_texts(report: str, run_dir: Path) -> dict[str, str]:
    """Give the Verifier only saved filing lines that the report cites.

    The code still searches the complete saved filing for its deterministic
    checks. The model receives short, exact excerpts so its request is well
    inside the hosted model context window.
    """
    requested: dict[str, set[int]] = {}
    for source in SOURCE.findall(report):
        fields = source_fields(source)
        if fields.get("kind") == "line" and fields.get("file") and fields.get("line", "").isdigit():
            requested.setdefault(fields["file"], set()).add(int(fields["line"]))
    texts: dict[str, str] = {}
    allowed = {"10-K.txt", "10-Q.txt", "8-K.txt", "8-K_exhibit_99_1.txt"}
    for name, numbers in requested.items():
        if name not in allowed or not (run_dir / name).exists():
            continue
        lines = (run_dir / name).read_text(encoding="utf-8").splitlines()
        excerpts: list[str] = []
        for number in sorted(numbers):
            for current in range(max(1, number - 2), min(len(lines), number + 2) + 1):
                entry = f"{name}:L{current}: {lines[current - 1]}"
                if entry not in excerpts:
                    excerpts.append(entry)
        texts[name] = "\n".join(excerpts)
    return texts


def verifier_model(state: DeskState, report: str, texts: dict[str, str], facts: dict[str, Any]) -> str:
    print("Verifier model check starting...")
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key: raise ValueError("API_KEY is required in .env.")
    instructions = """You are the Verifier. Use only the JSON packet. Skip the Investment Recommendation thesis, but do verify every Insight line, including an Investment Advisor Insight. Check whether each sourced number has the correct period and definition. Focus on semantic errors that arithmetic and exact text matching cannot detect, such as adjusted versus unadjusted values. For each semantic error, give a correction only when the supplied filing text or facts supports it, with the actual filing line or XBRL tag and period. If no correction is supported, say so. Do not repeat correct claims, do not use outside knowledge, and write 350 words or fewer."""
    packet = {"task": "Semantically verify sourced numbers and provide corrections.", "context": {"compiled_report": report, "filing_texts": texts, "facts": facts}, "expected_output": "Markdown semantic verification and corrections."}
    agent = Agent(name="Verifier", instructions=instructions, model=agents_model(api_key, state["model"]),
                  model_settings=ModelSettings(max_tokens=4_000,
                                               extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
    result = Runner.run_sync(agent, json.dumps(packet, ensure_ascii=False), max_turns=1)
    return str(result.final_output or "").strip()


def verifier_node(state: DeskState) -> dict[str, Any]:
    print("Verifier starting...")
    run_dir = Path(state["run_dir"]); report = state.get("report") or Path(state["report_path"]).read_text(encoding="utf-8")
    facts = state.get("facts") or json.loads((run_dir / "facts.json").read_text(encoding="utf-8"))
    texts = cited_filing_texts(report, run_dir)
    marks = verification_rows(report, run_dir, facts)
    packet = {"task": "Verify the compiled report.", "context": {"compiled_report": report, "filing_texts": texts, "facts": facts}, "expected_output": "Semantic filing verification and corrections."}
    semantic = verifier_model(state, report, texts, facts)
    table_rows = []
    for row in marks:
        source = row["source"].replace("|", "\\|")
        correction = row["correction"].replace("|", "\\|")
        table_rows.append(f"| {row['line']} | {row['number']} | {row['mark']} | {row['detail']} | {source} | {correction} |")
    table = "\n".join(["| Report line | Number | Mark | Code check | Source | Correction |", "|---:|---:|---|---|---|---|"] + table_rows)
    lines = report.splitlines(); first = lines[0] if lines else "Filings read: unavailable"
    verified = first + "\n\n## Verification Marks\n\n" + table + "\n\n## Verifier Semantic Check\n\n" + semantic + "\n\n" + "\n".join(lines[1:]) + "\n"
    path = run_dir / "verified_report.md"; path.write_text(verified, encoding="utf-8")
    print(f"Verified report: {path}")
    return record(state, "14_verifier", packet, {"marks": marks, "semantic_check": semantic, "verified_report": str(path)})


def full_graph() -> Any:
    graph = StateGraph(DeskState)
    for name, fn in [("step_1", step_1), ("step_2", step_2), ("step_3", step_3), ("step_4", step_4), ("step_5", step_5), ("step_6", step_6), ("step_7", step_7), ("research", research_node), ("financial", financial_node), ("advisor", advisor_node), ("manager", manager_node), ("rework", rework_node), ("compile", compile_node), ("verifier", verifier_node)]: graph.add_node(name, fn)
    names = ["step_1", "step_2", "step_3", "step_4", "step_5", "step_6", "step_7", "research", "financial", "advisor", "manager", "rework", "compile", "verifier"]
    graph.add_edge(START, names[0])
    for left, right in zip(names, names[1:]): graph.add_edge(left, right)
    graph.add_edge("verifier", END)
    return graph.compile()


def verifier_graph() -> Any:
    graph = StateGraph(DeskState); graph.add_node("verifier", verifier_node); graph.add_edge(START, "verifier"); graph.add_edge("verifier", END); return graph.compile()


def new_run_dir(company: str, suffix: str = "", base: Path | None = None) -> Path:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f_%Z")
    path = (base or ROOT / "runs") / f"{sec.clean_name(company)}_{stamp}{suffix}"; path.mkdir(parents=True, exist_ok=False); return path


def run_full(args: argparse.Namespace) -> None:
    bootstrap = sec.EdgarClient(); cik, company = sec.resolve_ticker(bootstrap, args.ticker.upper())
    run_dir = new_run_dir(company, base=ROOT / "runs" / "live" if args.live else None)
    state: DeskState = {"ticker": args.ticker.upper(), "peers": sec.peer_tickers(args.peers), "price": args.price, "model": args.model, "run_dir": str(run_dir), "cik": cik, "company": company, "role_outputs": {}}
    save_json(run_dir / "run_manifest.json", {"ticker": state["ticker"], "company": company, "cik": cik, "created_at": datetime.now().astimezone().isoformat(), "live": args.live})
    full_graph().invoke(state)
    print(f"Run folder: {run_dir}")


def run_verify_only(source: Path, model: str) -> None:
    source = source.resolve()
    if not source.is_dir(): raise ValueError("--verify-only must name a saved run folder.")
    manifest = json.loads((source / "run_manifest.json").read_text(encoding="utf-8")) if (source / "run_manifest.json").exists() else {"company": source.name}
    run_dir = new_run_dir(manifest["company"], "_verify")
    for name in ("10-K.txt", "10-Q.txt", "8-K.txt", "8-K_exhibit_99_1.txt", "facts.json", "investment_report.md"):
        candidate = source / name
        if not candidate.exists(): raise ValueError(f"Saved run lacks {name}.")
        shutil.copy2(candidate, run_dir / name)
    report_path = run_dir / "investment_report.md"; facts = json.loads((run_dir / "facts.json").read_text(encoding="utf-8"))
    # Verification-only runs also repair legacy citation shorthand. This lets
    # saved reports remain reproducible after the citation contract improves.
    report_path.write_text(canonical_source_markup(report_path.read_text(encoding="utf-8"), facts), encoding="utf-8")
    state: DeskState = {"run_dir": str(run_dir), "model": model, "report_path": str(report_path), "facts": facts, "verify_only": True}
    save_json(run_dir / "run_manifest.json", {"verify_only_source": str(source), "created_at": datetime.now().astimezone().isoformat()})
    verifier_graph().invoke(state); print(f"Verification run folder: {run_dir}")


def parse_args() -> argparse.Namespace:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", nargs="?", help="SEC ticker symbol")
    parser.add_argument("--peers", nargs="+", default=[], help="Peer ticker symbols, comma-separated or space-separated")
    parser.add_argument("--price", type=float, help="Optional share price for P/E")
    parser.add_argument("--live", action="store_true", help="Save the run in runs/live for the weekly live record")
    parser.add_argument("--verify-only", type=Path, help="Path to a saved run folder")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "qwen3.8:27b"), help="OpenAI-compatible model name")
    args = parser.parse_args()
    if bool(args.ticker) == bool(args.verify_only): parser.error("provide a ticker or --verify-only PATH, but not both")
    return args


def main() -> None:
    try:
        args = parse_args()
        if args.verify_only: run_verify_only(args.verify_only, args.model)
        else: run_full(args)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr); sys.exit(1)


if __name__ == "__main__": main()
