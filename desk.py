#!/usr/bin/env python3
"""Deterministic pipeline desk: one ticker or one cited spinoff screen."""
from __future__ import annotations
import argparse, datetime as dt, json, re, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "runs" / "live"
WORKSHEET = ROOT / "skills" / "filing-valuation-worksheet" / "scripts" / "worksheet.py"
MARKET = ROOT / "skills" / "filing-valuation-worksheet" / "scripts" / "market_data_reader.py"
SEEKER = ROOT / "skills" / "sec-spinoff-seeker" / "scripts" / "seeker.py"
HOOK = ROOT / ".codex" / "hooks" / "verify_worksheet.py"

def clean_name(value: str) -> str: return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
def make_run(name: str, started: dt.datetime, live: bool) -> Path:
    path = (LIVE if live else ROOT / "runs") / f"{clean_name(name)}_{started:%Y-%m-%d_%H-%M-%S_%Z}"
    path.mkdir(parents=True, exist_ok=False); return path
def execute(command: list[str], label: str) -> tuple[str, float]:
    started = time.monotonic(); result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT); elapsed = time.monotonic()-started
    print(result.stdout, end=""); print(f"{label}: {elapsed:.1f} seconds")
    if result.returncode: raise RuntimeError(f"{label} failed: {result.stdout.strip()}")
    return result.stdout, elapsed
def top_screen_ticker(run_dir: Path) -> str:
    rows=json.loads((run_dir/"registry.json").read_text())
    eligible=[r for r in rows if r.get("stage")=="in its window" and not r.get("excluded") and r.get("symbol",{}).get("value")]
    ranked=[r for r in eligible if not r.get("unranked_reason")]
    rows=ranked or eligible
    rows.sort(key=lambda r:float(r.get("total_points","-inf")),reverse=True)
    if not rows or not rows[0].get("symbol",{}).get("value"): raise RuntimeError("seeker found no ranked name with a filing-sourced ticker")
    return rows[0]["symbol"]["value"].upper()
def matching_registry_rows(ticker: str, cik: str, paths: list[Path]) -> list[dict]:
    """Return only a registry row that establishes this saved team run's spinoff facts."""
    for path in paths:
        try: rows=json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): continue
        matches=[]
        for row in rows:
            symbol=(row.get("symbol") or {}).get("value", "")
            if str(row.get("new_company_cik", "")).zfill(10) == str(cik).zfill(10) or str(symbol).upper() == ticker.upper():
                matches.append(row)
        if matches: return matches
    return []
def attach_matching_registry(run_dir: Path, ticker: str) -> bool:
    manifest=json.loads((run_dir/"run_manifest.json").read_text())
    candidates=sorted((ROOT/"spinoff_runs").glob("**/registry.json"), key=lambda path:path.stat().st_mtime, reverse=True)
    rows=matching_registry_rows(ticker, str(manifest.get("cik", "")), candidates)
    if not rows: return False
    (run_dir/"registry.json").write_text(json.dumps(rows, indent=2)+"\n")
    return True
def earnings_accepted_time(run_dir: Path) -> str:
    """Return the saved Item 2.02 8-K Accepted time for price selection."""
    step = run_dir / "02_step_2_output.json"
    if not step.exists():
        raise RuntimeError("saved run lacks the filing-selection record needed for filing-time price selection")
    accepted = json.loads(step.read_text()).get("8-K", {}).get("accepted", "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", accepted):
        raise RuntimeError("saved earnings 8-K has no valid EDGAR Accepted time")
    return accepted
def save_conference_call(run_dir: Path, source_file: Path, source_url: str) -> None:
    """Save only a user-supplied, HTTPS-sourced company call transcript."""
    if not source_file.is_file():
        raise ValueError("conference-call file does not exist")
    if not re.fullmatch(r"https://\S+", source_url):
        raise ValueError("conference-call source must be an HTTPS URL")
    shutil.copyfile(source_file, run_dir / "conference_call.txt")
    (run_dir / "conference_call_source.json").write_text(json.dumps({
        "source_url": source_url, "saved_at": dt.datetime.now().astimezone().isoformat(),
        "scope": "user-supplied company call transcript; source URL retained for audit",
    }, indent=2) + "\n")
def run_seeker(run_dir: Path, start_date: str) -> tuple[str,float]:
    reader=f"{sys.executable} {ROOT/'skills'/'sec-spinoff-seeker'/'scripts'/'closes_reader.py'} --symbol {{symbol}} --distribution-date {{date}} --regular-way-date {{regular_way_date}} --mode {{mode}}"
    return execute([sys.executable,str(SEEKER),"--start-date",start_date,"--env-file",str(ROOT/".env"),"--output-dir",str(run_dir),"--factors",str(ROOT/"spinoff_factors.json"),"--closes-reader",reader],"seeker")
def filings_read(run_dir: Path) -> str:
    """List each accession saved or cited in this shared pipeline folder."""
    accessions: set[tuple[str, str]] = set()
    step = run_dir / "02_step_2_output.json"
    if step.exists():
        for item in json.loads(step.read_text()).values():
            if item.get("accession"):
                accessions.add((item.get("form", "filing"), item["accession"]))
    registry = run_dir / "registry.json"
    if registry.exists():
        for row in json.loads(registry.read_text()):
            registration = row.get("registration", {})
            if registration.get("accession"):
                accessions.add((registration.get("form", "Form 10"), registration["accession"]))
    records = [f"{form} ({accession})" for form, accession in sorted(accessions)]
    call = run_dir / "conference_call_source.json"
    if call.exists():
        records.append(f"company call ({json.loads(call.read_text()).get('source_url', 'source URL unavailable')})")
    return "; ".join(records) or "none saved"

def final_report(run_dir: Path,ticker:str,timings:dict[str,float]) -> Path:
    verified=run_dir/"verified_report.md"; worksheet=sorted(run_dir.glob("worksheet_*.md"))[-1]
    body=worksheet.read_text()
    matches=re.findall(r"(?m)^- \*\*Recommendation: (?:Buy|Hold|Sell)\.\*\* .+$", body)
    if not matches:
        raise RuntimeError("worksheet did not produce a supported Buy, Hold, or Sell recommendation")
    decision=matches[-1]
    text=[f"Filings read: {filings_read(run_dir)}","",f"# Desk report: {ticker}","","## Verified team report",verified.read_text(),"","## Worksheet — sole authoritative client view",body,"","## Pipeline timing"]
    text += [f"- {part}: {seconds:.1f} seconds" for part,seconds in timings.items()]
    text += ["", "## Final client decision", "- Reproduced unchanged from the worksheet's supported valuation decision.", decision]
    path=run_dir/"desk_report.md"; path.write_text("\n".join(text)+"\n"); return path
def pipeline(ticker:str,run_dir:Path,timings:dict[str,float]) -> Path:
    _,timings["agent team"]=execute([sys.executable,str(ROOT/"agent_team.py"),ticker,"--run-dir",str(run_dir)],"agent team")
    registry_started=time.monotonic(); attached=attach_matching_registry(run_dir,ticker); timings["registry match"]=time.monotonic()-registry_started
    print(f"registry match: {timings['registry match']:.1f} seconds ({'matched' if attached else 'not applicable'})")
    market=run_dir/"market_data.json"
    accepted = earnings_accepted_time(run_dir)
    _,timings["market data"]=execute([sys.executable,str(MARKET),"--symbol",ticker,"--output",str(market),"--period","1y","--accepted",accepted],"market data")
    command=[sys.executable,str(WORKSHEET),"--run-dir",str(run_dir),"--settings",str(ROOT/"worksheet_settings.json"),"--persona",str(ROOT/"worksheet_persona.md"),"--market-data",str(market),"--env-file",str(ROOT/".env")]
    if (run_dir/"registry.json").exists(): command += ["--registry",str(run_dir/"registry.json")]
    _,timings["worksheet"]=execute(command,"worksheet")
    sheet=sorted(run_dir.glob("worksheet_*.md"))[-1]
    _,timings["worksheet hook"]=execute([sys.executable,str(HOOK),"--worksheet",str(sheet),"--settings",str(ROOT/"worksheet_settings.json"),"--market-data",str(market)],"worksheet hook")
    started=time.monotonic(); report=final_report(run_dir,ticker,timings); timings["final report"]=time.monotonic()-started
    print(f"final report: {timings['final report']:.1f} seconds")
    _,timings["final report hook"]=execute([sys.executable,str(HOOK),"--worksheet",str(sheet),"--final-report",str(report),"--settings",str(ROOT/"worksheet_settings.json"),"--market-data",str(market)],"final report hook")
    return report
def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("input",nargs="?",help="Ticker symbol or the word screen"); parser.add_argument("--start-date",default=(dt.date.today()-dt.timedelta(days=365)).isoformat()); parser.add_argument("--live",action="store_true"); parser.add_argument("--replay",type=Path); parser.add_argument("--conference-call-file",type=Path); parser.add_argument("--conference-call-source")
    args=parser.parse_args()
    if args.replay:
        report=args.replay/"desk_report.md"
        if not report.exists(): raise RuntimeError("saved folder lacks desk_report.md")
        sheets=sorted(args.replay.glob("worksheet_*.md")); market=args.replay/"market_data.json"
        if not sheets or not market.exists(): raise RuntimeError("saved folder lacks the worksheet or market-data artifact")
        execute([sys.executable,str(HOOK),"--worksheet",str(sheets[-1]),"--final-report",str(report),"--settings",str(ROOT/"worksheet_settings.json"),"--market-data",str(market)],"replay hook")
        print(f"Desk report: {report}"); return
    if not args.input:
        parser.error("input is required unless --replay is used")
    if bool(args.conference_call_file) != bool(args.conference_call_source):
        parser.error("--conference-call-file and --conference-call-source must be supplied together")
    started=dt.datetime.now().astimezone(); timings={}; run_dir: Path | None = None
    try:
        selection_started=time.monotonic()
        if args.input.lower()=="screen":
            staged=make_run("SCREEN",started,args.live)
            _,timings["seeker"]=run_seeker(staged,args.start_date)
            ticker=top_screen_ticker(staged)
            run_dir=staged.with_name(f"{clean_name(ticker)}_{started:%Y-%m-%d_%H-%M-%S_%Z}")
            staged.rename(run_dir)
        else:
            ticker=args.input.upper()
            run_dir=make_run(ticker,started,args.live)
        if args.conference_call_file:
            save_conference_call(run_dir, args.conference_call_file, args.conference_call_source)
        timings["ticker selection"]=time.monotonic()-selection_started
        print(f"ticker selection: {timings['ticker selection']:.1f} seconds")
        report=pipeline(ticker,run_dir,timings)
        print(f"Desk report: {report}")
    except Exception as error: print(f"Desk failed in {run_dir.name if run_dir else 'new run'}: {error}",file=sys.stderr); raise SystemExit(1)
if __name__=="__main__": main()
