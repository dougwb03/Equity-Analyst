#!/usr/bin/env python3
"""Refuse completion when a saved worksheet has an unsupported numeric claim."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path


NUMBER = re.compile(r"(?<![A-Za-z0-9.-])-?\$?[0-9][0-9,]*(?:\.\d+)?(?:e[+-]?\d+)?%?|(?<![A-Za-z0-9])\d+(?:\.\d+)?x\b", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?\b")
ACCESSION = re.compile(r"(?<!\d)\d{10}-\d{2}-\d{6}(?!\d)")


def numeric(token: str) -> float:
    return float(token.lower().replace("$", "").replace(",", "").rstrip("%x"))


def facts_values(facts: dict) -> set[float]:
    values = set()
    for item in facts.get("concepts", {}).values():
        for period in ("current", "prior"):
            fact = item.get(period, {})
            if isinstance(fact, dict) and isinstance(fact.get("val"), (int, float)):
                values.add(float(fact["val"]))
    return values


def market_values(data: dict) -> tuple[set[float], float | None]:
    rows = data.get("rows", [])
    values = {float(row["close"]) for row in rows if isinstance(row.get("close"), (int, float))}
    dollars = [float(row["close"]) * float(row["volume"]) for row in rows if isinstance(row.get("close"), (int, float)) and isinstance(row.get("volume"), (int, float))]
    return values, statistics.median(dollars) if dollars else None


def filing_share_values(run_dir: Path) -> set[float]:
    """Read an explicit common-share total from saved filing text."""
    values: set[float] = set()
    for text_file in run_dir.glob("*.txt"):
        text = text_file.read_text(errors="replace")
        for amount in re.findall(r"([0-9][0-9,]*)\s+shares?\s+of[^.\n]{0,120}?common stock\s+outstanding", text, re.I):
            values.add(float(amount.replace(",", "")))
    return values


def supported_values(run_dir: Path, settings: dict, market: dict) -> set[float]:
    facts = json.loads((run_dir / "facts.json").read_text())
    values = facts_values(facts)
    for item in (settings.get("client_purchase_size"), settings.get("valuation", {}).get("low_rate"), settings.get("valuation", {}).get("mid_rate")):
        if isinstance(item, (int, float)):
            values.add(float(item))
    closes, median = market_values(market)
    values |= closes
    if median is not None:
        values.add(median)
    values |= filing_share_values(run_dir)
    # Direct statement values are allowed only when their named row is in saved filing text.
    for text_file in run_dir.glob("*.txt"):
        text = text_file.read_text(errors="replace")
        for amount in re.findall(r"(?:depreciation\s+and\s+amortization|depreciation,\s+amortization,\s+and\s+other)(?:\s|\n|\$|\(|\)){1,80}([0-9][0-9,]*)", text, re.I):
            values.add(float(amount.replace(",", "")) * 1_000_000)
        cover = re.search(r"outstanding\s+([0-9][0-9,]*)\s+shares?\s+of\s+Class\s+A\s+common stock,?\s+([0-9][0-9,]*)\s+shares?\s+of\s+Class\s+B\s+common stock", text, re.I | re.S)
        if cover:
            values.add(float(int(cover.group(1).replace(",", "")) + int(cover.group(2).replace(",", ""))))
    # The judgment may state the same established dollar fact in millions or billions.
    values |= {value / 1_000_000 for value in list(values)}
    values |= {value / 1_000_000_000 for value in list(values)}
    return values


def relevant_numbers(line: str) -> list[str]:
    line = re.sub(r"^\s*(?:[-*]\s+)?\d+\.\s+", "", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"\bCIK\s*:?[ ]*\d+\b", "", line, flags=re.I)
    line = DATE.sub("", line)
    line = ACCESSION.sub("", line)
    # Field identifiers in a vendor source name (for example TRDPRC_1) are
    # identifiers, not numeric claims.
    line = re.sub(r"\b[A-Za-z][A-Za-z0-9_]*_\d+\b", "", line)
    # SEC form names identify a document; their leading number is not a claim.
    line = re.sub(r"\b(?:\d{1,3}-[A-Z](?:/[A-Z])?|S-\d+|20-F|40-F)\b", "", line, flags=re.I)
    return [token for token in NUMBER.findall(line) if not re.fullmatch(r"\d+", token) or not re.search(r"(?:line|section)\s+" + re.escape(token) + r"\b", line, re.I)]


def verify(worksheet: Path, settings_path: Path, market_path: Path) -> list[str]:
    run_dir = worksheet.parent
    settings = json.loads(settings_path.read_text())
    market = json.loads(market_path.read_text())
    values = supported_values(run_dir, settings, market)
    closes, _ = market_values(market)
    shares = filing_share_values(run_dir)
    failures = []
    for line_number, line in enumerate(worksheet.read_text().splitlines(), 1):
        if line.startswith("# ") or line.startswith("## ") or line.lstrip().startswith("- not computed") or re.search(r":\s*(?:not known|not applicable)(?:\.|;|$)", line, re.I):
            continue
        # Owner cash-flow arithmetic is a printed computation and must recompute exactly.
        arithmetic = re.search(r"(-?[0-9,]+) \+ (-?[0-9,]+) - ([0-9,]+) = (-?[0-9,]+)", line)
        if arithmetic:
            left, add, subtract, result = (numeric(part) for part in arithmetic.groups())
            if left + add - subtract != result:
                failures.append(f"refused: line {line_number}, owner cash flow does not recompute")
            else:
                values.add(result)
                values.add(result / 1_000_000)
                values.add(result / 1_000_000_000)
                valuation = settings.get("valuation", {})
                if valuation.get("method") == "owner_cash_flow_multiple":
                    for rate in (valuation.get("low_rate"), valuation.get("mid_rate")):
                        if isinstance(rate, (int, float)):
                            for share_count in shares:
                                per_share_value = result * float(rate) / share_count
                                values.add(per_share_value)
                                values |= {per_share_value - close for close in closes}
            continue
        for token in relevant_numbers(line):
            value = numeric(token)
            tolerance = 0.005 if "." in token else 0.000001
            if not any(abs(value - candidate) <= tolerance for candidate in values):
                failures.append(f"refused: line {line_number}, {token} has no filing, facts, settings, calculation, or reader support")
    return failures


def verify_final_report(report: Path, worksheet: Path, settings_path: Path, market_path: Path) -> list[str]:
    """Check that the final artifact retains the checked worksheet and filing list."""
    lines = report.read_text(errors="replace").splitlines()
    if not lines or not lines[0].startswith("Filings read:") or not lines[0].removeprefix("Filings read:").strip():
        return ["refused: final report has no first-line filing list"]
    worksheet_text = worksheet.read_text(errors="replace").strip()
    report_text = report.read_text(errors="replace")
    verified = report.parent / "verified_report.md"
    if not verified.exists() or verified.read_text(errors="replace").strip() not in report_text:
        return ["refused: final report does not contain the saved verified report"]
    if worksheet_text not in report_text:
        return ["refused: final report does not contain the checked worksheet"]
    return verify(worksheet, settings_path, market_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worksheet", type=Path)
    parser.add_argument("--settings", type=Path, default=Path("worksheet_settings.json"))
    parser.add_argument("--market-data", type=Path, default=Path("worksheet_market_data.json"))
    parser.add_argument("--final-report", type=Path)
    parser.add_argument("--latest", action="store_true")
    args = parser.parse_args()
    worksheet = args.worksheet
    if args.latest:
        choices = sorted(Path("runs").glob("**/worksheet_*.md"))
        worksheet = choices[-1] if choices else None
    if worksheet is None:
        return
    if args.final_report:
        failures = verify_final_report(args.final_report, worksheet, args.settings, args.market_data)
    else:
        failures = verify(worksheet, args.settings, args.market_data)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
