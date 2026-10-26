#!/usr/bin/env python3
"""Collect the source data for a repeatable EDGAR-based equity-research run.

The program does not make an investment recommendation.  It downloads only
public SEC data and records the filing that supplied each reported value.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
from dataclasses import asdict, dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv


SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


@dataclass
class Filing:
    form: str
    accession: str
    filing_date: str
    primary_document: str
    report_date: str
    accepted: str = ""
    document_url: str = ""
    content: bytes | None = None


CONCEPTS: dict[str, tuple[str, list[str], list[str]]] = {
    "total_revenue": ("Total revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"], ["USD"]),
    "operating_income": ("Operating income", ["OperatingIncomeLoss"], ["USD"]),
    "net_income": ("Net income", ["NetIncomeLoss", "ProfitLoss"], ["USD"]),
    "diluted_eps": ("Diluted EPS", ["EarningsPerShareDiluted"], ["USD/shares"]),
    "stockholders_equity": ("Stockholders' equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], ["USD"]),
    "long_term_debt": ("Long-term debt", ["LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtNoncurrent", "LongTermDebt"], ["USD"]),
    "current_debt": ("Current portion of long-term debt", ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"], ["USD"]),
    "cash_from_operations": ("Cash from operations", ["NetCashProvidedByUsedInOperatingActivities"], ["USD"]),
    "capex": ("Additions to property and equipment", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], ["USD"]),
}


def clean_name(value: str) -> str:
    """Make a portable directory component while retaining the company label."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "company"


def plain_text(content: bytes) -> str:
    parser = "xml" if content.lstrip().startswith(b"<?xml") else "lxml"
    soup = BeautifulSoup(content, parser)
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    return re.sub(r"\n{3,}", "\n\n", unescape(soup.get_text("\n", strip=True))) + "\n"


class EdgarClient:
    def __init__(self) -> None:
        load_dotenv(Path(__file__).with_name(".env"))
        agent = os.environ.get("SEC_USER_AGENT", "").strip()
        if not agent:
            raise ValueError("SEC_USER_AGENT is required in this folder's .env file.")
        self.session = requests.Session()
        # This session is the only HTTP client in the program.  Its User-Agent
        # is therefore included on every SEC request.
        self.session.headers.update({"User-Agent": agent, "Accept-Encoding": "gzip, deflate"})

    def get(self, url: str) -> requests.Response:
        response = self.session.get(url, timeout=45)
        response.raise_for_status()
        return response

    def get_json(self, url: str) -> dict[str, Any]:
        return self.get(url).json()


def resolve_ticker(client: EdgarClient, ticker: str) -> tuple[str, str]:
    data = client.get_json(f"{SEC_WWW}/files/company_tickers.json")
    wanted = ticker.upper()
    for item in data.values():
        if item["ticker"].upper() == wanted:
            return str(item["cik_str"]).zfill(10), item["title"]
    raise ValueError(f"Ticker {wanted} was not found in SEC company_tickers.json.")


def submissions(client: EdgarClient, cik: str) -> list[Filing]:
    data = client.get_json(f"{SEC_DATA}/submissions/CIK{cik}.json")
    recent = data["filings"]["recent"]
    return [
        Filing(
            form=recent["form"][i], accession=recent["accessionNumber"][i],
            filing_date=recent["filingDate"][i], primary_document=recent["primaryDocument"][i],
            report_date=recent["reportDate"][i],
        )
        for i in range(len(recent["form"]))
    ]


def filing_url(cik: str, filing: Filing, document: str | None = None) -> str:
    accession = filing.accession.replace("-", "")
    return f"{SEC_ARCHIVES}/{int(cik)}/{accession}/{document or filing.primary_document}"


def index_url(cik: str, filing: Filing) -> str:
    return f"{SEC_ARCHIVES}/{int(cik)}/{filing.accession.replace('-', '')}/{filing.accession}-index.html"


def accepted_time(client: EdgarClient, cik: str, filing: Filing) -> str:
    page = plain_text(client.get(index_url(cik, filing)).content)
    match = re.search(r"Accepted\s*\n?\s*([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})", page)
    if not match:
        raise ValueError(f"Could not read Accepted time from {filing.accession} index page.")
    return match.group(1)


def choose_filings(client: EdgarClient, cik: str, records: list[Filing]) -> dict[str, Filing]:
    selected: dict[str, Filing] = {}
    for target in ("10-K", "10-Q"):
        filing = next((item for item in records if item.form == target), None)
        if not filing:
            raise ValueError(f"No {target} appears in the SEC submissions response.")
        filing.accepted = accepted_time(client, cik, filing)
        selected[target] = filing

    # The submissions feed does not reliably classify an 8-K by item.  Inspect
    # each newest 8-K until its body actually reports Item 2.02.
    for filing in (item for item in records if item.form == "8-K"):
        filing.document_url = filing_url(cik, filing)
        filing.content = client.get(filing.document_url).content
        if re.search(r"item\s*2\.02", plain_text(filing.content), re.IGNORECASE):
            filing.accepted = accepted_time(client, cik, filing)
            selected["8-K"] = filing
            break
    if "8-K" not in selected:
        raise ValueError("No 8-K reporting Item 2.02 appears in the SEC submissions response.")
    return selected


def exhibit_99_1_url(client: EdgarClient, cik: str, filing: Filing) -> str:
    page = client.get(index_url(cik, filing)).text
    soup = BeautifulSoup(page, "lxml")
    for row in soup.select("tr"):
        row_text = row.get_text(" ", strip=True)
        if re.search(r"EX-?99\.1|99\.1", row_text, re.IGNORECASE):
            link = row.find("a", href=True)
            if link:
                return f"{SEC_ARCHIVES}/{int(cik)}/{filing.accession.replace('-', '')}/{link['href'].split('/')[-1]}"
    raise ValueError(f"No Exhibit 99.1 was found on the {filing.accession} filing index page.")


def save_documents(client: EdgarClient, cik: str, picked: dict[str, Filing], run_dir: Path) -> Path:
    files = {"10-K": "10-K.txt", "10-Q": "10-Q.txt", "8-K": "8-K.txt"}
    for form, output in files.items():
        filing = picked[form]
        if filing.content is None:
            filing.document_url = filing_url(cik, filing)
            filing.content = client.get(filing.document_url).content
        (run_dir / output).write_text(plain_text(filing.content), encoding="utf-8")
    release_url = exhibit_99_1_url(client, cik, picked["8-K"])
    release = client.get(release_url).content
    release_html = run_dir / "8-K_exhibit_99_1.html"
    release_html.write_bytes(release)
    (run_dir / "8-K_exhibit_99_1.txt").write_text(plain_text(release), encoding="utf-8")
    return release_html


def annual_units(fact: dict[str, Any], units: list[str]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for unit in units:
        for value in fact.get("units", {}).get(unit, []):
            if value.get("form") == "10-K" and value.get("fy") and value.get("end"):
                # Duration facts must cover a fiscal year.  A 10-K can also
                # contain a fourth-quarter duration ending on the same day.
                # Balance-sheet facts do not have a start date.
                if "start" not in value or 300 <= (datetime.fromisoformat(value["end"]) - datetime.fromisoformat(value["start"])).days <= 380:
                    candidates.append(value)
    return candidates


def newest_tag(facts: dict[str, Any], tags: list[str], units: list[str]) -> tuple[str, list[dict[str, Any]]]:
    options: list[tuple[str, list[dict[str, Any]]]] = []
    for tag in tags:
        fact = facts.get("us-gaap", {}).get(tag)
        if fact:
            values = annual_units(fact, units)
            if values:
                options.append((tag, values))
    if not options:
        raise ValueError(f"No annual US-GAAP fact exists for candidate tags: {', '.join(tags)}")
    return max(options, key=lambda item: max(v["end"] for v in item[1]))


def fact_for_end(values: list[dict[str, Any]], end: str) -> dict[str, Any] | None:
    matches = [v for v in values if v["end"] == end]
    if not matches:
        return None
    # Prefer the most recently filed version of a restated comparison.
    return max(matches, key=lambda value: value.get("filed", ""))


def fact_for_period(values: list[dict[str, Any]], period: dict[str, Any]) -> dict[str, Any] | None:
    """Find the fact for the exact consolidated statement period."""
    matches = [value for value in values if value["end"] == period["end"]]
    # A statement of operations supplies a start date, but a balance-sheet
    # fact is an instant and therefore correctly has none.
    if period.get("start") and any(value.get("start") for value in matches):
        matches = [value for value in matches if value.get("start") == period["start"]]
    return max(matches, key=lambda value: value.get("filed", "")) if matches else None


def prior_annual_fact(values: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    """Return the newest earlier annual fact from the same tag."""
    earlier = [value for value in values if value["end"] < current["end"]]
    if not earlier:
        return None
    prior_end = max(value["end"] for value in earlier)
    return fact_for_end(earlier, prior_end)


def select_total_revenue_row(rows: list[dict[str, Any]], fiscal_end: str) -> tuple[dict[str, Any], str]:
    """Select the consolidated total-revenue line, with net sales as a fallback."""
    revenue, checked = _statement_choice(rows, fiscal_end, r"^total revenues?$", instant=False)
    if revenue is not None:
        return revenue, "Consolidated total revenue as reported on the statement of operations"
    revenue, fallback_checked = _statement_choice(rows, fiscal_end, r"^net sales$", instant=False)
    if revenue is not None:
        return revenue, "Net sales; the consolidated statement does not report a separate total-revenue line"
    raise ValueError(f"Could not map total revenue for fiscal year ended {fiscal_end}; checked {checked}; {fallback_checked}.")


def _fact_for_statement_row(facts: dict[str, Any], row: dict[str, Any], tags: list[str], units: list[str]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Map one reported statement row to its candidate US-GAAP fact and period."""
    namespace, separator, tag = row["tag"].partition(":")
    if not separator or namespace.lower() != "us-gaap" or tag not in tags:
        raise ValueError(f"Statement line '{row['label']}' uses {row['tag']}, not a supported US-GAAP candidate tag.")
    fact = facts.get("us-gaap", {}).get(tag)
    if not fact:
        raise ValueError(f"Company facts has no us-gaap:{tag} fact for statement line '{row['label']}'.")
    values = annual_units(fact, units)
    current = fact_for_period(values, row)
    if current is None:
        raise ValueError(f"us-gaap:{tag} has no annual fact for the statement period {row.get('start', '')} to {row['end']}.")
    return tag, values, current


def collect_facts(client: EdgarClient, cik: str) -> dict[str, Any]:
    """Collect facts for the latest 10-K, anchored to its total-revenue line."""
    facts = client.get_json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik}.json")["facts"]
    filing = next(item for item in submissions(client, cik) if item.form == "10-K")
    revenue_row, revenue_definition = select_total_revenue_row(
        filing_statement_rows(client, cik, filing), filing.report_date)
    revenue_tag, revenue_values, revenue_current = _fact_for_statement_row(
        facts, revenue_row, CONCEPTS["total_revenue"][1], ["USD"])
    fiscal_end = revenue_current["end"]
    revenue_prior = prior_annual_fact(revenue_values, revenue_current)
    if revenue_prior is None:
        raise ValueError("Revenue has no prior annual period in company facts.")
    prior_end = revenue_prior["end"]
    results: dict[str, Any] = {"fiscal_year_end": fiscal_end, "prior_fiscal_year_end": prior_end, "concepts": {}}
    for key, (label, tags, units) in CONCEPTS.items():
        if key == "total_revenue":
            tag, values, now, prior = revenue_tag, revenue_values, revenue_current, revenue_prior
        else:
            options = []
            for tag in tags:
                fact = facts.get("us-gaap", {}).get(tag)
                if not fact:
                    continue
                values = annual_units(fact, units)
                now = fact_for_period(values, revenue_row)
                if now is not None:
                    options.append((tag, values, now))
            if not options:
                tag, values, now = tags[0], [], None
            else:
                tag, values, now = options[0]
            prior = prior_annual_fact(values, now) if now is not None else None
        results["concepts"][key] = {
            "label": label, "tag": f"us-gaap:{tag}", "current": now, "prior": prior,
        }
    results["concepts"]["total_revenue"]["reported_label"] = revenue_row["label"]
    results["concepts"]["total_revenue"]["definition"] = revenue_definition
    results["concepts"]["total_revenue"]["filing"] = filing.accession
    return results


def _local_name(element: Any) -> str:
    """Return an XML/HTML element name without its namespace prefix."""
    return str(getattr(element, "name", "")).split(":")[-1].lower()


def _number(text: str, scale: str | None = None, sign: str | None = None) -> float | None:
    """Read an inline-XBRL number, in the units stated by its scale attribute."""
    compact = re.sub(r"[,$\s]", "", text).replace("(", "-").replace(")", "")
    if compact in {"", "—", "-", "–"}:
        return None
    try:
        number = float(compact) * (10 ** int(scale or "0"))
    except ValueError:
        return None
    return -abs(number) if sign == "-" else number


def filing_statement_rows(client: EdgarClient, cik: str, filing: Filing) -> list[dict[str, Any]]:
    """Extract consolidated, reported inline-XBRL facts with their statement row labels."""
    # A few EDGAR primary documents are XML rather than HTML.  lxml still
    # reads their inline-XBRL tags correctly; suppress Beautiful Soup's noisy
    # parser advisory for that valid case.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(client.get(filing_url(cik, filing)).content, "lxml")
    contexts: dict[str, dict[str, str | bool]] = {}
    for context in soup.find_all(lambda tag: _local_name(tag) == "context"):
        identifier = context.get("id")
        if not identifier:
            continue
        dates = {_local_name(tag): tag.get_text(" ", strip=True)
                 for tag in context.find_all(lambda tag: _local_name(tag) in {"startdate", "enddate", "instant"})}
        # A segment/member makes a fact a product, geography, or other detail,
        # rather than the consolidated statement line needed in Step 7.
        dimensional = bool(context.find(lambda tag: _local_name(tag) in {"segment", "scenario", "explicitmember", "typedmember"}))
        contexts[identifier] = {**dates, "dimensional": dimensional}

    rows: list[dict[str, Any]] = []
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        label = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True)).strip(" :")
        if not label:
            continue
        for fact in row.find_all(lambda tag: _local_name(tag) == "nonfraction"):
            context = contexts.get(fact.get("contextref", ""))
            name = fact.get("name", "")
            if not context or not name or context.get("dimensional"):
                continue
            amount = _number(fact.get_text(" ", strip=True), fact.get("scale"), fact.get("sign"))
            if amount is None:
                continue
            rows.append({
                "label": label, "tag": name, "val": amount,
                "start": context.get("startdate"), "end": context.get("enddate") or context.get("instant"),
                "instant": context.get("instant") is not None,
            })
    return rows


def _statement_choice(rows: list[dict[str, Any]], end: str, pattern: str,
                      *, instant: bool, reject: str = "") -> tuple[dict[str, Any] | None, str]:
    """Choose one consolidated statement line and retain the line checked on failure."""
    checked: list[str] = []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        label = row["label"]
        if not re.search(pattern, label, re.IGNORECASE):
            continue
        checked.append(f"'{label}' ({row['tag']})")
        if row["end"] != end or row["instant"] != instant or (reject and re.search(reject, label, re.IGNORECASE)):
            continue
        candidates.append(row)
    return (candidates[0] if candidates else None, "; ".join(dict.fromkeys(checked)) or "no matching statement line")


def _entry_from_statement(row: dict[str, Any] | None, filing: Filing, label: str,
                          definition: str, unavailable_note: str = "") -> dict[str, Any]:
    tag = row["tag"] if row else "unavailable"
    return {
        "label": label, "reported_label": row["label"] if row else "unavailable",
        "definition": definition, "tag": tag, "current": row if row else None,
        "prior": None, "unavailable_note": unavailable_note,
        "filing": filing.accession,
    }


def _debt_lines_checked(rows: list[dict[str, Any]], end: str) -> str:
    """Name the reported debt rows considered when no debt mapping is safe."""
    lines = [f"'{row['label']}' ({row['tag']})" for row in rows
             if row["end"] == end and row["instant"] and re.search(r"debt|borrow", row["label"], re.IGNORECASE)]
    return "; ".join(dict.fromkeys(lines)) or "no consolidated debt or borrowing line/tag"


def collect_peer_facts(client: EdgarClient, cik: str) -> dict[str, Any]:
    """Use the latest 10-K's own statement rows for peer revenue and debt.

    Company facts can contain several values under one tag for one year.  The
    inline statement supplies the missing meaning: for example, whether a
    Revenue tag is net sales or total revenues, and whether a debt tag is the
    current or noncurrent balance-sheet line.
    """
    filing = next(item for item in submissions(client, cik) if item.form == "10-K")
    rows = filing_statement_rows(client, cik, filing)
    fiscal_end = filing.report_date

    # Use the same statement-line selection rule as Step 4.
    revenue, revenue_definition = select_total_revenue_row(rows, fiscal_end)

    current_portion, _ = _statement_choice(
        rows, fiscal_end,
        r"^(long-term debt due within one year|current portion.*(?:debt|borrowings))$",
        instant=True, reject=r"lease")
    short_term_debt, _ = _statement_choice(
        rows, fiscal_end, r"^short-term debt$", instant=True, reject=r"lease")
    noncurrent_debt, _ = _statement_choice(
        rows, fiscal_end, r"^(noncurrent portion of long-term debt|long-term debt, excluding current portion|long-term debt and other borrowings|long-term debt)$",
        instant=True, reject=r"including current|total notes|debentures|total long-term debt")

    # The preceding-year fact is the fact in the same reported row and tag.
    def prior_for(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        matches = [item for item in rows if item["label"] == row["label"] and item["tag"] == row["tag"]
                   and item["instant"] == row["instant"] and item["end"] != fiscal_end]
        return max(matches, key=lambda item: item["end"]) if matches else None

    result = collect_facts(client, cik)
    result["fiscal_year_end"] = fiscal_end
    result["prior_fiscal_year_end"] = (prior_for(revenue) or {}).get("end", "unavailable")
    result["concepts"]["total_revenue"] = _entry_from_statement(
        revenue, filing, "Total revenue", revenue_definition)
    result["concepts"]["total_revenue"]["prior"] = prior_for(revenue)
    result["concepts"]["current_debt"] = _entry_from_statement(
        current_portion, filing,
        current_portion["label"] if current_portion else "Current portion of long-term debt",
        "Current portion of long-term debt balance-sheet line",
        "not separately reported; checked " + _debt_lines_checked(rows, fiscal_end)
        if current_portion is None else "")
    result["concepts"]["current_debt"]["prior"] = prior_for(current_portion)
    if short_term_debt is not None:
        result["concepts"]["short_term_debt"] = _entry_from_statement(
            short_term_debt, filing, short_term_debt["label"],
            "Short-term debt balance-sheet line; it is not labeled as a current portion of long-term debt")
        result["concepts"]["short_term_debt"]["prior"] = prior_for(short_term_debt)
    result["concepts"]["long_term_debt"] = _entry_from_statement(
        noncurrent_debt, filing,
        noncurrent_debt["label"] if noncurrent_debt else "Long-term debt",
        "Noncurrent debt balance-sheet line",
        "unavailable; checked " + _debt_lines_checked(rows, fiscal_end) if noncurrent_debt is None else "")
    result["concepts"]["long_term_debt"]["prior"] = prior_for(noncurrent_debt)
    return result


def value(facts: dict[str, Any], key: str, period: str = "current") -> float | None:
    entry = facts["concepts"][key][period]
    return float(entry["val"]) if entry and entry.get("val") is not None else None


def ratio(numerator: float | None, denominator: float | None) -> float | None:
    return numerator / denominator if numerator is not None and denominator not in (None, 0) else None


def show_number(number: float | None) -> str:
    if number is None:
        return "not reported for this fiscal-year end"
    return f"{number:,.4f}" if abs(number) < 100 else f"{number:,.0f}"


def print_facts(title: str, facts: dict[str, Any]) -> None:
    print(f"{title}: fiscal year ended {facts['fiscal_year_end']} | prior year ended {facts['prior_fiscal_year_end']}")
    for entry in facts["concepts"].values():
        current = entry["current"]
        prior = entry["prior"]
        source = current.get("accn", "unknown") if current else "unknown"
        current_end = current.get("end", "not reported") if current else "not reported"
        prior_end = prior.get("end", "not reported") if prior else "not reported"
        reported_label = entry.get("reported_label")
        definition = entry.get("definition")
        detail = f" | reported line: {reported_label} | definition: {definition}" if reported_label else ""
        filing = entry.get("filing", source)
        unavailable = f" | {entry['unavailable_note']}" if entry.get("unavailable_note") else ""
        current_value = "unavailable" if entry.get("unavailable_note") else show_number(value_from(current))
        print(f"  {entry['label']}: {current_value} ({current_end}) | prior: {show_number(value_from(prior))} ({prior_end}) | {entry['tag']} | filing {filing}{detail}{unavailable}")


def value_from(item: dict[str, Any] | None) -> float | None:
    return float(item["val"]) if item and item.get("val") is not None else None


def print_calculations(facts: dict[str, Any], price: float | None) -> None:
    revenue, prior_revenue = value(facts, "total_revenue"), value(facts, "total_revenue", "prior")
    op_income = value(facts, "operating_income")
    net_income = value(facts, "net_income")
    equity, prior_equity = value(facts, "stockholders_equity"), value(facts, "stockholders_equity", "prior")
    debt_components = [
        (facts["concepts"][key]["label"], value(facts, key))
        for key in ("long_term_debt", "current_debt", "short_term_debt")
        if key in facts["concepts"]
    ]
    included_debt = [(label, amount) for label, amount in debt_components if amount is not None]
    debt = sum(amount for _, amount in included_debt) if included_debt else None
    debt_formula = (
        f"({' + '.join(label for label, _ in included_debt)}) / stockholders' equity"
        if included_debt else "debt components / stockholders' equity"
    )
    cfo, capex = value(facts, "cash_from_operations"), value(facts, "capex")
    eps = value(facts, "diluted_eps")
    average_equity = (equity + prior_equity) / 2 if equity is not None and prior_equity is not None else None
    calculations = [
        ("Revenue growth", ratio(revenue - prior_revenue if revenue is not None and prior_revenue is not None else None, prior_revenue), "(revenue - prior revenue) / prior revenue"),
        ("Operating margin", ratio(op_income, revenue), "operating income / revenue"),
        ("Return on average equity", ratio(net_income, average_equity), "net income / ((current equity + prior equity) / 2)"),
        ("Debt to equity", ratio(debt, equity), debt_formula),
        ("Free cash flow", cfo - capex if cfo is not None and capex is not None else None, "cash from operations - additions to property and equipment"),
        ("P/E", ratio(price, eps) if price is not None else None, "price / diluted EPS"),
    ]
    for label, result, arithmetic in calculations:
        rendered = f"{result:.2%}" if label in {"Revenue growth", "Operating margin", "Return on average equity", "Debt to equity"} and result is not None else show_number(result)
        print(f"  {label}: {rendered} = {arithmetic}")


def table_rows(release_path: Path) -> list[list[str]]:
    """Return non-empty cells for every row in the saved release HTML."""
    soup = BeautifulSoup(release_path.read_bytes(), "lxml")
    rows: list[list[str]] = []
    for row in soup.select("table tr"):
        cells = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
                 for cell in row.find_all(["th", "td"], recursive=False)]
        cells = [cell for cell in cells if cell]
        if cells:
            rows.append(cells)
    return rows


def row_values(cells: list[str]) -> list[str]:
    """Extract numeric table cells, retaining the release's displayed values."""
    values: list[str] = []
    for cell in cells[1:]:
        compact = re.sub(r"\s+", " ", cell).strip()
        if re.fullmatch(r"\$?\s*\(?\d{1,3}(?:,\d{3})*(?:\.\d+)?\)?", compact):
            values.append(compact)
    return values


def earnings_evidence(release_path: Path) -> list[str]:
    """Read quarterly total revenue and diluted EPS from release table rows."""
    rows = table_rows(release_path)
    evidence: dict[str, tuple[list[str], list[str]]] = {}
    for index, cells in enumerate(rows):
        label = cells[0].strip()
        values = row_values(cells)
        if len(values) < 2:
            continue
        # Releases vary between "Total revenue" and "Total revenues".  Do
        # not accept a currency-adjusted or segment row as the consolidated
        # reported result.
        if re.fullmatch(r"total revenues?", label, re.IGNORECASE):
            evidence.setdefault("Total revenue", (cells, values[:2]))
        elif (re.fullmatch(r"diluted", label, re.IGNORECASE)
              or re.search(r"diluted.*(?:earnings|net income)\s+per\s+(?:common\s+)?share", label, re.IGNORECASE)):
            context = " ".join(" ".join(row) for row in rows[max(0, index - 8):index])
            if (re.search(r"(earnings|income)\s+per\s+(common\s+)?share", context, re.IGNORECASE)
                    or re.search(r"diluted.*(?:earnings|net income)\s+per\s+(?:common\s+)?share", label, re.IGNORECASE)):
                evidence.setdefault("Diluted EPS", (cells, values[:2]))

    missing = [label for label in ("Total revenue", "Diluted EPS") if label not in evidence]
    if missing:
        raise ValueError(f"could not extract quarterly {' and '.join(missing)} from Exhibit 99.1 table rows")

    excerpts = []
    for label in ("Total revenue", "Diluted EPS"):
        cells, quarterly_values = evidence[label]
        excerpts.append(
            f"{label} quarterly values: {' | '.join(quarterly_values)} | source row: {' | '.join(cells)}"
        )
    return excerpts


def peer_tickers(values: list[str]) -> list[str]:
    """Accept comma-separated peer lists, including values split by the shell."""
    tickers: list[str] = []
    for value in values:
        tickers.extend(ticker.strip().upper() for ticker in value.split(",") if ticker.strip())
    return tickers


def run(args: argparse.Namespace) -> None:
    client = EdgarClient()
    ticker = args.ticker.upper()
    cik, company = resolve_ticker(client, ticker)
    picked: dict[str, Filing] | None = None
    run_dir: Path | None = None
    release_path: Path | None = None
    facts: dict[str, Any] | None = None

    def need_filings() -> dict[str, Filing]:
        nonlocal picked
        if picked is None:
            picked = choose_filings(client, cik, submissions(client, cik))
        return picked

    def need_dir() -> Path:
        nonlocal run_dir
        if run_dir is None:
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%Z")
            run_dir = Path("runs") / f"{clean_name(company)}_{stamp}"
            run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    requested = [args.step] if args.step else list(range(1, 8))
    for step in requested:
        print(f"\nStep {step}/7")
        if step == 1:
            print(f"  {ticker} -> CIK {cik} ({company})")
        elif step == 2:
            for form, filing in need_filings().items():
                print(f"  {form}: {filing.accession} | Accepted {filing.accepted}")
        elif step == 3:
            release_path = save_documents(client, cik, need_filings(), need_dir())
            print(f"  Saved 10-K, 10-Q, 8-K, and Exhibit 99.1 HTML and plain text in {need_dir()}")
        elif step == 4:
            facts = collect_facts(client, cik)
            print_facts("  Reported facts", facts)
            destination = need_dir() / "facts.json"
            destination.write_text(json.dumps(facts, indent=2), encoding="utf-8")
            print(f"  Saved facts in {destination}")
        elif step == 5:
            facts = facts or collect_facts(client, cik)
            print_calculations(facts, args.price)
        elif step == 6:
            if release_path is None:
                release_path = save_documents(client, cik, need_filings(), need_dir())
            try:
                for line in earnings_evidence(release_path):
                    print(f"  {line}")
            except ValueError as error:
                print(f"  Step 6 failed: {error}")
        elif step == 7:
            peers = peer_tickers(args.peers)
            if not peers:
                print("  No peers supplied.")
            for peer in peers:
                peer_cik, peer_company = resolve_ticker(client, peer)
                peer_facts = collect_peer_facts(client, peer_cik)
                print_facts(f"  {peer.upper()} ({peer_company})", peer_facts)
                margin = ratio(value(peer_facts, "operating_income"), value(peer_facts, "total_revenue"))
                print(f"  Operating margin: {margin:.2%}" if margin is not None else "  Operating margin: not reported")
        else:
            raise ValueError("--step must be an integer from 1 through 7.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", help="SEC ticker symbol")
    parser.add_argument("--peers", nargs="+", default=[], help="Comma-separated peer ticker symbols")
    parser.add_argument("--price", type=float, help="Share price for P/E")
    parser.add_argument("--step", type=int, choices=range(1, 8), help="Run one numbered step only")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        run(parse_args())
    except (requests.RequestException, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
