#!/usr/bin/env python3
"""Return an LSEG daily close after filing-cited regular-way trading starts."""
from __future__ import annotations

import argparse, datetime as dt, json, os

from dotenv import load_dotenv

def history_with_close_field(history):
    """Translate LSEG's daily price field to the reader's guarded schema."""
    if history is None or history.empty or "TRDPRC_1" not in history:
        raise ValueError("LSEG returned no TRDPRC_1 daily-price field")
    return history.rename(columns={"TRDPRC_1": "Close"})

def lseg_ric(ld, symbol: str) -> str:
    converted=ld.discovery.convert_symbols(symbols=symbol.upper(),from_symbol_type="TickerSymbol",to_symbol_types="RIC")
    values=[] if converted is None or "RIC" not in converted else [str(value).strip() for value in converted["RIC"].tolist() if str(value).strip()]
    if len(values)!=1: raise ValueError(f"LSEG could not establish one RIC for ticker {symbol!r}")
    return values[0]

def lseg_history(symbol: str, start_date: str):
    load_dotenv(); app_key=os.environ.get("LSEG_APP_KEY", "").strip()
    if not app_key: raise ValueError("LSEG_APP_KEY is required in .env")
    import lseg.data as ld
    ld.open_session(app_key=app_key)
    try:
        ric=lseg_ric(ld,symbol)
        history=ld.get_history(universe=ric,fields=["TRDPRC_1"],interval="1D",start=start_date)
        return ric,history_with_close_field(history)
    finally:
        ld.close_session()

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
    distribution=dt.date.fromisoformat(args.distribution_date)
    regular_way=dt.date.fromisoformat(args.regular_way_date)
    start=min(distribution,regular_way)
    ric,history=lseg_history(args.symbol,start.isoformat())
    function=first_regular_close if args.mode=="first" else latest_regular_close
    price_date, close=function(history,args.distribution_date,args.regular_way_date)
    print(json.dumps({"date":price_date,"close":close,"ric":ric,"source":"LSEG Data Library TRDPRC_1 daily price"},separators=(",",":")))

if __name__=="__main__": main()
