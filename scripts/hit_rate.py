#!/usr/bin/env python3
"""Score saved worksheet views against the filing-time close reaction without changing saved views."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


def filing_time(run: Path) -> dt.datetime | None:
    candidates = []
    for path in run.glob("02_step_2_output.json"):
        data = json.loads(path.read_text())
        records = data.values() if isinstance(data, dict) else data
        for item in records:
            if not isinstance(item, dict): continue
            accepted = item.get("accepted")
            if accepted:
                candidates.append((item.get("form", ""), accepted))
    preferred = next((value for form, value in candidates if form == "8-K"), None)
    value = preferred or (candidates[0][1] if candidates else None)
    return dt.datetime.fromisoformat(value) if value else None


def view_data(run: Path) -> tuple[str, dt.datetime | None]:
    sheets = sorted(run.glob("worksheet_*.md"))
    if not sheets: return "not computed", None
    text = sheets[-1].read_text()
    heading = re.search(r"worksheet, (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", text)
    direction = re.search(r"View:\s*(more|less) than the price", text, re.I)
    return (direction.group(1).lower() if direction else "not computed", dt.datetime.fromisoformat(heading.group(1)) if heading else None)


def closes(symbol: str, accepted: dt.datetime):
    import yfinance as yf
    start, end = accepted.date() - dt.timedelta(days=10), accepted.date() + dt.timedelta(days=10)
    history = yf.Ticker(symbol).history(start=start.isoformat(), end=(end + dt.timedelta(days=1)).isoformat(), auto_adjust=False, actions=False)
    if history.empty or "Close" not in history: return None, None
    cutoff = accepted.time() <= dt.time(16, 0)
    before, after = [], []
    for stamp, row in history.iterrows():
        day = stamp.date() if hasattr(stamp, "date") else stamp
        if day < accepted.date() or (day == accepted.date() and not cutoff): before.append((day, float(row["Close"])))
        if day > accepted.date() or (day == accepted.date() and cutoff): after.append((day, float(row["Close"])))
    return (before[-1] if before else None), (after[0] if after else None)


def is_completed_run(run: Path) -> bool:
    return (run / "run_manifest.json").is_file() and (run / "verified_report.md").is_file()


def rows(runs: Path):
    for run in sorted(path for path in runs.iterdir() if path.is_dir()):
        if not is_completed_run(run): continue
        manifest = run / "run_manifest.json"
        data = json.loads(manifest.read_text()); accepted = filing_time(run); direction, view_time = view_data(run)
        before, after = closes(data["ticker"], accepted) if accepted else (None, None)
        reaction = "not available" if not before or not after else ("more" if after[1] > before[1] else "less" if after[1] < before[1] else "flat")
        after_flag = bool(view_time and after and view_time > dt.datetime.combine(after[0], dt.time(16, 0)))
        agree = "after" if after_flag else ("not computed" if direction == "not computed" or reaction in {"not available", "flat"} else ("agree" if direction == reaction else "disagree"))
        yield data["ticker"], accepted, view_time, direction, reaction, agree


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--runs-dir", required=True, type=Path); args = parser.parse_args()
    counted = agreed = 0
    print("ticker | filing time | view time | direction | reaction | result")
    for ticker, accepted, view, direction, reaction, result in rows(args.runs_dir):
        print(f"{ticker} | {accepted or 'not available'} | {view or 'not available'} | {direction} | {reaction} | {result}")
        if result in {"agree", "disagree"}: counted += 1; agreed += result == "agree"
    print(f"agreements: {agreed}/{counted}" if counted else "agreements: not computed")


if __name__ == "__main__": main()
