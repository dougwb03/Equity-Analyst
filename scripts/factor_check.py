#!/usr/bin/env python3
"""Run the pre-registered spinoff factor comparison from saved evidence."""
from __future__ import annotations
import argparse, datetime as dt, json, statistics
from pathlib import Path


def factor_value(row, factor):
    value=(row.get("factor_values", {}).get(factor) or {}).get("value")
    return float(value) if value is not None else None

def outcome_return(outcome, holding_days):
    required=("entry_date","entry_close","exit_date","exit_close")
    if not outcome or any(outcome.get(key) is None for key in required): raise ValueError("missing entry or exit evidence")
    entry=dt.date.fromisoformat(str(outcome["entry_date"])); exit=dt.date.fromisoformat(str(outcome["exit_date"]))
    entry_close=float(outcome["entry_close"]); exit_close=float(outcome["exit_close"])
    if entry_close <= 0: raise ValueError("entry close is not positive")
    if exit < entry + dt.timedelta(days=holding_days): raise ValueError("exit is before the fixed holding period")
    return (exit_close-entry_close)/entry_close


def compare(rows, outcomes, plan):
    eligible=[]; excluded=[]
    for row in rows:
        cik=str(row.get("new_company_cik", "")).zfill(10)
        outcome=outcomes.get(cik) or outcomes.get(str(row.get("new_company_cik", "")))
        try: observed_return=outcome_return(outcome, int(plan["holding_period_calendar_days"]))
        except (TypeError, ValueError) as error:
            excluded.append({"cik": cik, "reason": str(error)}); continue
        eligible.append({"cik": cik, "name": row.get("new_company_name", ""), "ticker": (row.get("symbol") or {}).get("value", ""), "return": observed_return, "row": row})
    comparisons=[]
    for spec in plan.get("factors", []):
        if float(spec.get("weight", 0)) == 0: continue
        available=[item | {"value": factor_value(item["row"], spec["name"])} for item in eligible]
        available=[item for item in available if item["value"] is not None]
        if len(available) < 2: continue
        available.sort(key=lambda item:item["value"], reverse=spec["direction"] == "higher")
        size=len(available)//2
        if size == 0: continue
        high,low=available[:size],available[-size:]
        simplify=lambda group:[{"cik":item["cik"],"ticker":item["ticker"],"value":item["value"],"return":item["return"]} for item in group]
        comparisons.append({"factor":spec["name"],"direction":spec["direction"],"weight":spec["weight"],"high_priority":simplify(high),"low_priority":simplify(low),"high_priority_median_return":statistics.median(item["return"] for item in high),"low_priority_median_return":statistics.median(item["return"] for item in low)})
    return {"eligible_count":len(eligible),"excluded":excluded,"comparisons":comparisons}


def report(result, plan):
    lines=[f"# Spinoff factor check — {dt.date.today().isoformat()}","",f"Holding period: {plan['holding_period_calendar_days']} calendar days.",f"Eligible outcomes: {result['eligible_count']}; excluded outcomes: {len(result['excluded'])}; comparisons: {len(result['comparisons'])}.","","## Comparisons"]
    for item in result["comparisons"]:
        lines += [f"- {item['factor']} ({item['direction']}, weight {item['weight']}): high-priority median return {item['high_priority_median_return']:.4f}; low-priority median return {item['low_priority_median_return']:.4f}.", f"  High-priority: {json.dumps(item['high_priority'])}",f"  Low-priority: {json.dumps(item['low_priority'])}"]
    if not result["comparisons"]: lines.append("- No comparison has enough complete outcomes.")
    lines += ["","## Excluded",*([f"- {item['cik']}: {item['reason']}." for item in result["excluded"]] or ["- none."]),"","This is a pre-registered check, not evidence of causation. Missing facts and prices are not estimated."]
    return "\n".join(lines)+"\n"


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--registry",required=True,type=Path); parser.add_argument("--outcomes",required=True,type=Path); parser.add_argument("--plan",default="factor_check_plan.json",type=Path); parser.add_argument("--output",required=True,type=Path); args=parser.parse_args()
    rows=json.loads(args.registry.read_text()); outcomes=json.loads(args.outcomes.read_text()); plan=json.loads(args.plan.read_text())
    result=compare(rows,outcomes,plan); args.output.write_text(report(result,plan)); print(args.output)

if __name__=="__main__": main()
