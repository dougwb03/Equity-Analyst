#!/usr/bin/env python3
"""Create an evidence-first worksheet from one saved equity-desk run."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

import requests


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_env(path: Path) -> dict[str, str]:
    values = dict(os.environ)
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class EdgarClient:
    """Use the supplied SEC identity and remain below eight requests per second."""
    def __init__(self, user_agent: str):
        if not user_agent:
            raise ValueError("SEC_USER_AGENT is required in the environment file.")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self.last_request = 0.0

    def get(self, url: str) -> bytes:
        wait = 0.125 - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        return response.content

    def text(self, url: str) -> str:
        return self.get(url).decode("utf-8", "replace")


def first_value(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) else value


def source_text(source: dict[str, Any] | None) -> str:
    if not source:
        return "source not known"
    filing = source.get("filing") or source.get("accession") or "filing not known"
    section = source.get("section") or "section not known"
    return f"{filing}, {section}"


def filing_line(label: str, value: Any, source: dict[str, Any] | None, basis: str) -> str:
    shown = "not known" if value is None or value == "" else str(value)
    return f"- {label}: {shown}; {source_text(source)}; {basis}."


def as_cik(value: str | int) -> str:
    return str(value).zfill(10)


def registry_row(registry_path: Path | None, manifest: dict[str, Any]) -> dict[str, Any] | None:
    if not registry_path or not registry_path.exists():
        return None
    rows = load_json(registry_path)
    cik, ticker = as_cik(manifest.get("cik", "")), manifest.get("ticker", "").upper()
    for row in rows:
        if as_cik(row.get("new_company_cik", "")) == cik:
            return row
        if str(first_value(row.get("symbol", {}))).upper() == ticker:
            return row
    return None


def saved_texts(run_dir: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(run_dir.glob("*.txt"))}


def nearby_section(text: str, index: int) -> str:
    before = text[:index].splitlines()
    for line in reversed(before):
        value = re.sub(r"\s+", " ", line).strip()
        if re.match(r"^(?:ITEM\s+\d+[A-Z]?\.?|[A-Z][A-Z ,&/()-]{4,}|\d+\.\s+.{3,})$", value):
            return value
    return "section not identified in saved text"


def amount_string(value: Any) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value.is_integer():
            return f"{int(value):,}"
        return f"{value:,.6g}"
    return str(value)


def source_for_accession(texts: dict[str, str], accession: str | None, label: str, amount: Any) -> dict[str, str] | None:
    if not accession:
        return None
    likely = [name for name in texts if name.startswith("10-K") or name.startswith("10-Q")]
    needle = re.escape(label.split("(")[0].strip())
    value = re.escape(str(int(amount))) if isinstance(amount, (int, float)) else ""
    for name in likely:
        text = texts[name]
        hit = re.search(needle, text, re.I)
        if not hit and value:
            hit = re.search(value, text)
        if hit:
            return {"filing": accession, "section": nearby_section(text, hit.start())}
    return {"filing": accession, "section": "XBRL fact; section not identified in saved text"}


def concept_fact(facts: dict[str, Any], texts: dict[str, str], aliases: tuple[str, ...]) -> tuple[Any, dict[str, str] | None]:
    concepts = facts.get("concepts", {})
    for name in aliases:
        item = concepts.get(name)
        current = item.get("current") if isinstance(item, dict) else None
        if current and current.get("val") is not None:
            return current["val"], source_for_accession(texts, current.get("accn"), item.get("label", name), current["val"])
    return None, None


def text_fact(texts: dict[str, str], labels: tuple[str, ...]) -> tuple[int | None, dict[str, str] | None]:
    """Read one reported number from the saved statement text, never infer it."""
    for name, text in texts.items():
        for label in labels:
            hit = re.search(re.escape(label) + r"(?:\s|\n|\$|\(|\)){1,80}([0-9][0-9,]*)", text, re.I)
            if hit:
                return int(hit.group(1).replace(",", "")) * 1_000_000, {"filing": name, "section": nearby_section(text, hit.start())}
    return None, None


def extract_shares(texts: dict[str, str]) -> tuple[int | None, dict[str, str] | None]:
    cover = re.compile(r"as of .{0,300}?outstanding\s+([0-9][0-9,]*)\s+shares?\s+of\s+Class\s+A\s+common stock,?\s+([0-9][0-9,]*)\s+shares?\s+of\s+Class\s+B\s+common stock", re.I | re.S)
    ordered = sorted((name for name in texts if name.startswith(("10-Q", "10-K"))), key=lambda name: (not name.startswith("10-Q"), name))
    for name in ordered:
        text = texts[name]
        hit = cover.search(text)
        if hit:
            total = int(hit.group(1).replace(",", "")) + int(hit.group(2).replace(",", ""))
            return total, {"filing": name, "section": nearby_section(text, hit.start())}
    expression = re.compile(r"([0-9][0-9,]{2,})\s+shares?\s+of[^.]{0,120}?common stock[^.]{0,120}?(?:outstanding|issued and outstanding)", re.I | re.S)
    for name in ordered:
        text = texts[name]
        hit = expression.search(text)
        if hit:
            return int(hit.group(1).replace(",", "")), {"filing": name, "section": nearby_section(text, hit.start())}
    return None, None


def fetch_information_statement(run_dir: Path, row: dict[str, Any], client: EdgarClient) -> list[str]:
    registration = row.get("registration", {})
    url = registration.get("filing", "")
    accession = registration.get("accession", "")
    cik = row.get("new_company_cik", "")
    if not url or not accession or not cik:
        return []
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    try:
        index = json.loads(client.text(base + "index.json"))
    except (requests.RequestException, json.JSONDecodeError):
        return []
    saved = []
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        if not re.search(r"\.(?:htm|html|txt)$", name, re.I):
            continue
        try:
            text = client.text(base + name)
        except requests.RequestException:
            continue
        if "information statement" not in text.lower() and name.lower() not in {"primary_doc.xml", "primary-document.html"}:
            continue
        target = run_dir / f"spinoff_{accession}_{name}.txt"
        target.write_text(text, encoding="utf-8")
        saved.append(target.name)
    return saved


def spinoff_detail(texts: dict[str, str], row: dict[str, Any], label: str, patterns: tuple[str, ...]) -> tuple[str | None, dict[str, str] | None]:
    """Return only an express filing statement; an unmatched fact stays unknown."""
    accession = row.get("registration", {}).get("accession", "Form 10")
    amount = r"(?:\$\s*[0-9][0-9,]*(?:\.\d+)?|[0-9][0-9,]*(?:\.\d+)?\s+(?:million|billion|thousand))"
    for name, text in texts.items():
        if not name.startswith("spinoff_"):
            continue
        for pattern in patterns:
            hit = re.search(pattern, text, re.I | re.S)
            if not hit:
                continue
            window = re.sub(r"\s+", " ", text[hit.start():hit.start() + 700]).strip()
            money = re.search(amount, window, re.I)
            if money:
                value = money.group(0).strip()
                return value, {"filing": accession, "section": nearby_section(text, hit.start())}
    return None, None


def spinoff_facts(row: dict[str, Any] | None, texts: dict[str, str]) -> tuple[list[str], list[str]]:
    labels = (("Parent", "parent_company"), ("Distribution ratio", "distribution_ratio"),
              ("Record date", "record_date"), ("Distribution date", "distribution_date"))
    if row is None:
        return ([f"- {label}: not applicable." for label, _ in labels] +
                ["- Debt raised: not applicable.", "- Cash to parent: not applicable.",
                 "- Liabilities left by parent: not applicable.", "- Restrictions left by parent: not applicable."], [])
    lines, missing = [], []
    for label, key in labels:
        item = row.get(key, {})
        value = first_value(item)
        lines.append(filing_line(label, value, item.get("source") if isinstance(item, dict) else None, "spinoff filing fact"))
        if value in (None, ""):
            missing.append(label.lower())
    details = (
        ("Debt raised", (r"(?:will |expect to )?(?:incur|raise|borrow)[^.]{0,300}?(?:debt|indebtedness|borrowings)",)),
        ("Cash to parent", (r"(?:cash|net) proceeds[^.]{0,350}?(?:to|paid to|distributed to)[^.]{0,100}?(?:parent|former parent)",
                              r"(?:parent|former parent)[^.]{0,250}?(?:cash|net) proceeds")),
        ("Liabilities left by parent", (r"(?:assume|responsible for)[^.]{0,350}?liabilit",)),
        ("Restrictions left by parent", (r"(?:tax matters agreement|restriction)[^.]{0,350}?(?:restrict|limit|prohibit)",)),
    )
    for label, patterns in details:
        value, source = spinoff_detail(texts, row, label, patterns)
        lines.append(filing_line(label, value, source, "spinoff filing fact"))
        if value is None:
            missing.append(label.lower())
    return lines, missing


def form4_purchases(texts: dict[str, str]) -> tuple[int | None, dict[str, str] | None]:
    total, source = 0, None
    for name, text in texts.items():
        if not name.startswith("4"):
            continue
        for block in re.finditer(r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", text, re.I | re.S):
            if not re.search(r"<transactionCode>\s*P\s*</transactionCode>", block.group(1), re.I):
                continue
            shares = re.findall(r"<transactionShares>\s*<value>\s*([0-9,.]+)", block.group(1), re.I)
            if shares:
                total += int(float(shares[0].replace(",", "")))
                source = {"filing": name, "section": "Table I, non-derivative securities"}
    return (total, source) if source else (None, None)


def market_inputs(path: Path | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not path or not path.exists():
        return None, None, None
    data = load_json(path)
    if data.get("close") == "not available":
        return None, None, None
    rows = [row for row in data.get("rows", []) if isinstance(row.get("close"), (int, float))]
    if not rows:
        return None, None, None
    rows.sort(key=lambda row: row["date"])
    selected = data.get("valuation_price")
    if isinstance(selected, dict) and isinstance(selected.get("close"), (int, float)) and selected.get("date"):
        price = {"value": selected["close"], "date": selected["date"],
                 "source": data.get("source", str(path)), "selection": selected.get("selection", "filing-time selection")}
    else:
        latest = rows[-1]
        price = {"value": latest["close"], "date": latest["date"], "source": data.get("source", str(path)),
                 "selection": "latest saved daily close; no EDGAR Accepted time was supplied"}
    values = [float(row["close"]) * float(row["volume"]) for row in rows if isinstance(row.get("volume"), (int, float))]
    liquidity = None if not values else {"value": statistics.median(values), "start_date": rows[0]["date"], "end_date": rows[-1]["date"], "source": data.get("source", str(path))}
    return price, liquidity, {"rows": len(rows), "source": data.get("source", str(path))}


def period_context(run_dir: Path, facts: dict[str, Any]) -> list[str]:
    """State the periods used and prevent an implicit annual/quarterly mix."""
    selected = load_json(run_dir / "02_step_2_output.json") if (run_dir / "02_step_2_output.json").exists() else {}
    annual, interim, release = selected.get("10-K", {}), selected.get("10-Q", {}), selected.get("8-K", {})
    lines = [
        f"- Owner-cash-flow valuation period: fiscal year ended {facts.get('fiscal_year_end', annual.get('report_period', 'not known'))}; "
        f"10-K {annual.get('accession', 'not known')}. The worksheet uses annual reported inputs for this calculation.",
        f"- Latest saved interim filing: 10-Q {interim.get('accession', 'not known')}; period ended {interim.get('report_period', 'not known')}.",
        f"- Latest saved earnings release: 8-K {release.get('accession', 'not known')}; report date {release.get('report_period', 'not known')}.",
        "- Period rule: the worksheet does not mix annual owner-cash-flow inputs with a quarterly earnings-release figure. It leaves a current-period adjustment not computed unless saved filings establish every required input.",
    ]
    call_source = run_dir / "conference_call_source.json"
    if call_source.exists() and (run_dir / "conference_call.txt").exists():
        details = load_json(call_source)
        lines.append(f"- Company-call evidence: saved user-supplied transcript; source URL {details.get('source_url', 'not known')}. The report cites this file only when a claim names its exact saved line.")
    else:
        lines.append("- Company-call evidence: not included. EDGAR does not normally supply earnings-call transcripts, and no saved company transcript with a source URL is available for this run.")
    return lines


def valuation(owner_cash_flow: float | None, shares: int | None, config: dict[str, Any]) -> dict[str, Any]:
    spec = config["valuation"]
    if owner_cash_flow is None or shares is None or shares <= 0:
        return {"low": None, "mid": None, "reason": "owner cash flow or filing-sourced share count is not known"}
    if owner_cash_flow <= 0:
        return {"low": None, "mid": None, "reason": "owner cash flow is not positive; a positive owner-cash-flow multiple cannot establish a value"}
    method, low_rate, mid_rate = spec["method"], float(spec["low_rate"]), float(spec["mid_rate"])
    if low_rate <= 0 or mid_rate <= 0:
        raise ValueError("valuation rates must be greater than zero")
    if method == "owner_cash_flow_multiple":
        low_equity, mid_equity = owner_cash_flow * low_rate, owner_cash_flow * mid_rate
    elif method == "capitalization_rate":
        low_equity, mid_equity = owner_cash_flow / low_rate, owner_cash_flow / mid_rate
    else:
        raise ValueError("valuation.method must be owner_cash_flow_multiple or capitalization_rate")
    return {"low": low_equity / shares, "mid": mid_equity / shares, "method": method,
            "low_rate": low_rate, "mid_rate": mid_rate, "shares": shares}


def conviction(config: dict[str, Any], available: dict[str, bool]) -> str:
    required = config["conviction"]["high_if_all_of"]
    return "high" if all(available.get(key, False) for key in required) else "low"


def recommendation(price: float | None, low_value: float | None) -> tuple[str, str]:
    """Return one reproducible client action from the worksheet's stated low case.

    This is deliberately not a model decision: the price and low value are
    already printed, and missing evidence must lead to Hold rather than an
    estimate.  The recommendation does not predict a return.
    """
    if price is None or low_value is None:
        return ("Hold", "The saved filing packet does not establish both a filing-time regular-way price and a low owner-cash-flow value, so the desk does not estimate an action.")
    if low_value > price:
        return ("Buy", "The filing-time regular-way price is below the worksheet's stated low owner-cash-flow value; the decision uses the printed price and valuation inputs only.")
    return ("Sell", "The filing-time regular-way price is above the worksheet's stated low owner-cash-flow value; the decision uses the printed price and valuation inputs only.")


def call_judgment(persona_path: Path, packet: dict[str, Any], env: dict[str, str], model_name: str) -> str:
    """The sole model call. All numbers have already been calculated."""
    from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, set_default_openai_client, set_tracing_disabled
    from openai import AsyncOpenAI
    api_key = env.get("API_KEY", "")
    if not api_key:
        raise ValueError("API_KEY is required for the single judgment call.")
    client = AsyncOpenAI(api_key=api_key, base_url=env.get("OPENAI_BASE_URL") or "https://genai.rcac.purdue.edu/api")
    set_default_openai_client(client, use_for_tracing=False)
    set_tracing_disabled(True)
    instructions = persona_path.read_text(encoding="utf-8") + "\n\nUse only the JSON packet. Do not calculate, alter, or add a number. Do not state that a metric grew, declined, improved, or weakened unless the packet explicitly supplies that comparison. A single revenue amount is not evidence that revenue grew or failed to grow; say that sustained revenue growth is not established from the packet. Explain why this name may be cheap or why it does not fit that story, and state what in the next filing would change the view."
    agent = Agent(name="Worksheet judgment", instructions=instructions,
                  model=OpenAIChatCompletionsModel(model=model_name, openai_client=client),
                  model_settings=ModelSettings(max_tokens=1200, extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
    result = Runner.run_sync(agent, json.dumps(packet, ensure_ascii=False), max_turns=1)
    output = str(result.final_output or "").strip()
    if not output:
        raise ValueError("judgment model returned an empty response")
    return output


def build_worksheet(run_dir: Path, settings: dict[str, Any], registry: Path | None, market_data: Path | None,
                    env: dict[str, str], persona: Path, model_name: str, test_judgment: str | None = None) -> Path:
    manifest, facts, texts = load_json(run_dir / "run_manifest.json"), load_json(run_dir / "facts.json"), saved_texts(run_dir)
    row = registry_row(registry, manifest)
    if row:
        fetch_information_statement(run_dir, row, EdgarClient(env.get("SEC_USER_AGENT", "")))
        texts = saved_texts(run_dir)
    spin_lines, unknown = spinoff_facts(row, texts)
    revenue, revenue_source = concept_fact(facts, texts, ("total_revenue", "revenue"))
    operating_income, operating_income_source = concept_fact(facts, texts, ("operating_income",))
    net_income, net_source = concept_fact(facts, texts, ("net_income", "after_tax_earnings"))
    depreciation, depreciation_source = concept_fact(facts, texts, ("depreciation", "depreciation_and_amortization"))
    if depreciation is None:
        depreciation, depreciation_source = text_fact(texts, ("depreciation and amortization", "depreciation, amortization, and other"))
    capex, capex_source = concept_fact(facts, texts, ("capex", "capital_expenditures"))
    shares, shares_source = extract_shares(texts)
    owner_cash_flow = None if None in (net_income, depreciation, capex) else float(net_income) + float(depreciation) - float(capex)
    if owner_cash_flow is None:
        unknown.append("owner cash flow inputs")
    purchase_count, purchase_source = form4_purchases(texts)
    if purchase_count is None:
        unknown.append("Form 4 open-market purchases (code P)")
    price, liquidity, _ = market_inputs(market_data)
    if price is None:
        unknown.append("close")
    if liquidity is None:
        unknown.append("median daily dollar volume")
    value = valuation(owner_cash_flow, shares, settings)
    if value["low"] is None:
        unknown.append("low and mid value")
    filing_basis = "reported after the spinoff" if row else "reported (spinoff parts not applicable)"
    packet = {"filing_facts": {"spinoff": spin_lines, "revenue": revenue, "operating_income": operating_income,
              "after_tax_earnings": net_income, "depreciation": depreciation,
              "capital_spending": capex, "owner_cash_flow": owner_cash_flow, "value": value,
              "price": price}, "instruction": "Write judgment only; no numeric calculation or new number."}
    judgment = test_judgment if test_judgment is not None else call_judgment(persona, packet, env, model_name)
    distribution_lines, parent_lines = spin_lines[:4], spin_lines[4:]
    period_lines = period_context(run_dir, facts)
    financial_lines = [
        filing_line("Revenue", revenue, revenue_source, filing_basis),
        filing_line("Operating income", operating_income, operating_income_source, filing_basis),
        filing_line("After-tax earnings", net_income, net_source, filing_basis),
        filing_line("Depreciation", depreciation, depreciation_source, filing_basis),
        filing_line("Capital spending", capex, capex_source, filing_basis),
        filing_line("Filing-sourced shares outstanding", shares, shares_source, filing_basis),
    ]
    status = "applicable: a registry row matched the saved run" if row else "not applicable: no registry row matched the saved run"
    lines = [
        f"# {manifest.get('company', manifest.get('ticker', 'Unknown'))}: worksheet, {dt.datetime.now().isoformat(timespec='seconds')}",
        "Screens to look at, not a recommendation.", "", "## Filing scope",
        f"- Ticker: {manifest.get('ticker', 'not known')}; CIK: {manifest.get('cik', 'not known')}.",
        "- Evidence used: saved SEC filing text, the run facts file, the registry when applicable, and the supplied closes-and-volumes file.",
        f"- Spinoff status: {status}.",
        "- Evidence rule: a fact stays not known when the saved filing packet does not establish it; this worksheet does not estimate missing facts.",
        "", "## Distribution mechanics", *distribution_lines,
        "", "## What the parent pushed into it", *parent_lines,
        "", "## Period alignment", *period_lines,
        "", "## Financial facts and accounting basis",
        f"- Statement basis: {filing_basis}.", *financial_lines,
        "", "## Owner cash flow",
        "- Formula: after-tax earnings + depreciation - capital spending.",
    ]
    if owner_cash_flow is None:
        lines.append("- not computed: after-tax earnings, depreciation, or capital spending is not known.")
    else:
        lines.append(f"- {amount_string(net_income)} + {amount_string(depreciation)} - {amount_string(capex)} = {amount_string(owner_cash_flow)}.")
    lines += ["", "## Price, value, and liquidity",
              f"- Valuation method selected in worksheet settings: {settings['valuation']['method']}; low input: {settings['valuation']['low_rate']}; mid input: {settings['valuation']['mid_rate']}."]
    if value["low"] is None:
        lines.append(f"- not computed: {value['reason']}.")
    else:
        lines += [f"- Low value per share: {value['low']:.4f}; mid value per share: {value['mid']:.4f}.",
                  f"- Recomputable arithmetic: owner cash flow × low or mid input ÷ filing-sourced shares outstanding ({value['shares']})."]
    if price is None:
        lines.append("- Price and price against low value: not computed; close is not available.")
    elif value["low"] is None:
        lines.append(f"- Price: {price['value']} on {price['date']}; source: {price['source']}; selection: {price['selection']}; price against low value: not computed.")
    else:
        lines.append(f"- Price: {price['value']} on {price['date']}; source: {price['source']}; selection: {price['selection']}; low-value difference: {value['low'] - price['value']:.4f}.")
    if liquidity is None:
        lines.append("- Median daily dollar volume: not known.")
    else:
        comparison = "meets or exceeds" if liquidity["value"] >= settings["client_purchase_size"] else "is below"
        lines.append(f"- Median daily dollar volume: {liquidity['value']:.2f}; {liquidity['start_date']} through {liquidity['end_date']}; {liquidity['source']}; {comparison} client purchase size of {settings['client_purchase_size']}.")
        lines.append(f"- Can you buy it: {comparison} the stated purchase size using the saved median daily dollar-volume calculation.")
    lines += ["", "## Insiders and catalysts",
              filing_line("Open-market insider purchases (Form 4, code P only)", purchase_count, purchase_source, filing_basis),
              "- Next filing to read: the next SEC report or earnings release, for reported operating results, cash flow, and any stated catalyst.",
              "", "## Judgment", judgment, "", "## View"]
    if price is None or value["low"] is None:
        lines.append("- not computed; close or low value is not available.")
    else:
        direction = "more" if value["low"] > price["value"] else "less"
        lines.append(f"- View: {direction} than the price. Deciding number: low value per share {value['low']:.4f} versus price {price['value']} on {price['date']}.")
    available = {"owner_cash_flow": owner_cash_flow is not None, "low_value_per_share": value["low"] is not None,
                 "price": price is not None, "liquidity": liquidity is not None}
    lines.append(f"- Conviction: {conviction(settings, available)}; rule: all of {', '.join(settings['conviction']['high_if_all_of'])} are available.")
    limits = sorted(set(unknown)) or ["No missing input was identified by the worksheet rules."]
    action, action_reason = recommendation(None if price is None else price["value"], value["low"])
    lines += ["", "## Recommendation",
              f"- **Recommendation: {action}.** {action_reason}",
              "- Key risks and monitors: use the cited operating, cash-flow, leverage, and guidance evidence in the team report. The next filing can change this conclusion only if it establishes a different supported valuation input or operating evidence.",
              "", "## Evidence limits",
              "- This worksheet does not establish that a price move proves forced selling, insider intent, or future performance.",
              "- A model supplied only the judgment text. Code selected facts, performed arithmetic, and applied the conviction rule.",
              "", "## Not known"] + [f"- {item}." for item in limits]
    output = run_dir / f"worksheet_{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--persona", required=True, type=Path)
    parser.add_argument("--market-data", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "qwen3.8:27b"))
    parser.add_argument("--test-judgment", help=argparse.SUPPRESS)
    args = parser.parse_args()
    registry = args.registry or (Path("spinoff_runs/registry.json") if Path("spinoff_runs/registry.json").exists() else None)
    output = build_worksheet(args.run_dir, load_json(args.settings), registry, args.market_data,
                             read_env(args.env_file), args.persona, args.model, args.test_judgment)
    print(output)


if __name__ == "__main__":
    main()
