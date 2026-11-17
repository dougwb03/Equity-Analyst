#!/usr/bin/env python3
"""Run the equity desk pipeline on one ticker, or run the spinoff screen alone."""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSHEET = ROOT / "skills" / "filing-valuation-worksheet" / "scripts" / "worksheet.py"
MARKET = ROOT / "skills" / "filing-valuation-worksheet" / "scripts" / "market_data_reader.py"
SEEKER = ROOT / "skills" / "sec-spinoff-seeker" / "scripts" / "seeker.py"
HOOK = ROOT / ".codex" / "hooks" / "verify_worksheet.py"


def run(command: list[str], label: str) -> tuple[str, float]:
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    seconds = time.monotonic() - started
    print(result.stdout, end="")
    if result.returncode:
        raise RuntimeError(f"{label} failed after {seconds:.1f} seconds")
    print(f"{label}: {seconds:.1f} seconds")
    return result.stdout, seconds


def run_folder(output: str) -> Path:
    match = re.search(r"Run folder:\s*(.+)$", output, re.M)
    if not match:
        raise RuntimeError("agent team completed without printing its run folder")
    return Path(match.group(1).strip())


def final_report(run_dir: Path, ticker: str, timings: dict[str, float]) -> Path:
    verified = run_dir / "verified_report.md"
    sheets = sorted(run_dir.glob("worksheet_*.md"))
    if not verified.exists() or not sheets:
        raise RuntimeError("saved run lacks verified report or worksheet")
    worksheet = sheets[-1]
    text = worksheet.read_text(encoding="utf-8")
    view = re.search(r"## View\n(.*?)(?=\n## |\Z)", text, re.S)
    report = [f"# Desk report: {ticker}", "", "## Filings and verified team report", verified.read_text(encoding="utf-8"),
              "", "## Worksheet", text, "", "## Pipeline timing"]
    report += [f"- {name}: {seconds:.1f} seconds" for name, seconds in timings.items()]
    report += ["", "## View", view.group(1).strip() if view else "not computed"]
    path = run_dir / "desk_report.md"
    path.write_text("\n".join(report) + "\n", encoding="utf-8")
    return path


def ticker_pipeline(args: argparse.Namespace) -> None:
    timings: dict[str, float] = {}
    team_command = [sys.executable, str(ROOT / "agent_team.py"), args.ticker.upper()]
    if args.live:
        team_command.append("--live")
    output, timings["agent team"] = run(team_command, "agent team")
    saved = run_folder(output)
    market_path = saved / "market_data.json"
    _, timings["market data"] = run([sys.executable, str(MARKET), "--symbol", args.ticker.upper(), "--output", str(market_path), "--period", "1y"], "market data")
    worksheet_command = [sys.executable, str(WORKSHEET), "--run-dir", str(saved), "--settings", str(ROOT / "worksheet_settings.json"),
                         "--persona", str(ROOT / "worksheet_persona.md"), "--market-data", str(market_path), "--env-file", str(ROOT / ".env")]
    registry = ROOT / "spinoff_runs" / "registry.json"
    if registry.exists():
        worksheet_command += ["--registry", str(registry)]
    _, timings["worksheet"] = run(worksheet_command, "worksheet")
    worksheet = sorted(saved.glob("worksheet_*.md"))[-1]
    _, timings["hook"] = run([sys.executable, str(HOOK), "--worksheet", str(worksheet), "--settings", str(ROOT / "worksheet_settings.json"), "--market-data", str(market_path)], "hook")
    report = final_report(saved, args.ticker.upper(), timings)
    print(f"Desk report: {report}")


def screen(args: argparse.Namespace) -> None:
    output_dir = ROOT / "spinoff_runs" / f"desk_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}"
    reader = f"{sys.executable} {ROOT / 'skills' / 'sec-spinoff-seeker' / 'scripts' / 'closes_reader.py'} --symbol {{symbol}} --distribution-date {{date}} --regular-way-date {{regular_way_date}} --mode {{mode}}"
    run([sys.executable, str(SEEKER), "--start-date", args.start_date, "--env-file", str(ROOT / ".env"), "--output-dir", str(output_dir), "--factors", str(ROOT / "spinoff_factors.json"), "--closes-reader", reader], "seeker")
    reports = sorted(output_dir.glob("report_*.md"))
    if reports:
        print(f"Screen report: {reports[-1]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ticker", help="Run the complete desk pipeline for one ticker")
    mode.add_argument("--screen", action="store_true", help="Run only the cited spinoff screen")
    parser.add_argument("--live", action="store_true", help="Save the ticker run in runs/live")
    parser.add_argument("--start-date", help="Inclusive screen distribution-date start, YYYY-MM-DD")
    args = parser.parse_args()
    if args.screen:
        if not args.start_date:
            parser.error("--screen requires --start-date YYYY-MM-DD")
        screen(args)
    else:
        ticker_pipeline(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Desk failed: {error}", file=sys.stderr)
        raise SystemExit(1)
