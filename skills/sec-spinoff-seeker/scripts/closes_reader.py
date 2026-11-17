#!/usr/bin/env python3
"""Return the first Yahoo unadjusted close after a distribution, after regular-way starts."""
from __future__ import annotations

import argparse, datetime as dt, json

def first_regular_close(history, distribution_date: str, regular_way_date: str) -> tuple[str,float]:
    """Use a filing-cited regular-way start as the guard because Yahoo does not label it."""
    distribution=dt.date.fromisoformat(distribution_date)
    regular_way=dt.date.fromisoformat(regular_way_date)
    cutoff=max(distribution + dt.timedelta(days=1), regular_way)
    if history is None or history.empty or "Close" not in history:
        raise ValueError("price source returned no close data")
    for stamp, value in history["Close"].items():
        traded=stamp.date() if hasattr(stamp,"date") else stamp
        if traded >= cutoff and value is not None:
            return traded.isoformat(), float(value)
    raise ValueError("price source has no close on or after the regular-way cutoff")

def latest_regular_close(history, distribution_date: str, regular_way_date: str) -> tuple[str,float]:
    distribution=dt.date.fromisoformat(distribution_date)
    regular_way=dt.date.fromisoformat(regular_way_date)
    cutoff=max(distribution + dt.timedelta(days=1), regular_way)
    eligible=[]
    if history is None or history.empty or "Close" not in history: raise ValueError("price source returned no close data")
    for stamp, value in history["Close"].items():
        traded=stamp.date() if hasattr(stamp,"date") else stamp
        if traded >= cutoff and value is not None: eligible.append((traded.isoformat(),float(value)))
    if not eligible: raise ValueError("price source has no close on or after the regular-way cutoff")
    return eligible[-1]

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol",required=True)
    parser.add_argument("--distribution-date",required=True)
    parser.add_argument("--regular-way-date",required=True)
    parser.add_argument("--mode",choices=("first","latest"),default="first")
    args=parser.parse_args()
    try:
        import yfinance as yf
    except ImportError as error:
        raise SystemExit("yfinance is required for closes_reader.py") from error
    distribution=dt.date.fromisoformat(args.distribution_date)
    regular_way=dt.date.fromisoformat(args.regular_way_date)
    start=min(distribution,regular_way)
    # auto_adjust=False preserves the reported closing price rather than an adjusted series.
    history=yf.Ticker(args.symbol).history(start=start.isoformat(),auto_adjust=False,actions=False)
    function=first_regular_close if args.mode=="first" else latest_regular_close
    price_date, close=function(history,args.distribution_date,args.regular_way_date)
    print(json.dumps({"date":price_date,"close":close,"source":"Yahoo Finance unadjusted Close"},separators=(",",":")))

if __name__=="__main__": main()
