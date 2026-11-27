#!/usr/bin/env python3
"""Write LSEG daily price and volume rows for a saved worksheet run."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv


def rows_from_history(history) -> list[dict[str, float | str]]:
    if history is None or history.empty:
        return []
    if {"TRDPRC_1", "ACVOL_UNS"}.issubset(history.columns):
        history = history.rename(columns={"TRDPRC_1": "Close", "ACVOL_UNS": "Volume"})
    if not {"Close", "Volume"}.issubset(history.columns): return []
    rows = []
    for stamp, row in history.iterrows():
        close, volume = row["Close"], row["Volume"]
        if close is None or volume is None:
            continue
        day = stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp)[:10]
        rows.append({"date": day, "close": float(close), "volume": float(volume)})
    return rows


def period_start(period: str, as_of: dt.date | None = None) -> str:
    value=period.strip().lower()
    if value.endswith("y") and value[:-1].isdigit(): days=int(value[:-1])*365
    elif value.endswith("mo") and value[:-2].isdigit(): days=int(value[:-2])*31
    else: raise ValueError("period must be an integer followed by y or mo, such as 1y")
    return ((as_of or dt.date.today())-dt.timedelta(days=days)).isoformat()


def last_close_before_accepted(rows: list[dict[str, float | str]], accepted: str) -> dict[str, float | str] | None:
    """Select the last daily regular-session close available before an EDGAR Accepted time.

    EDGAR displays Accepted time in Eastern time.  A daily close is available at
    4:00 PM Eastern.  An after-close filing can use that day's close; a filing
    at or before 4:00 PM must use the preceding available trading-day close.
    """
    moment = dt.datetime.strptime(accepted, "%Y-%m-%d %H:%M:%S")
    cutoff = moment.date() if moment.time() > dt.time(16, 0) else moment.date() - dt.timedelta(days=1)
    eligible = [row for row in rows if str(row.get("date", "")) <= cutoff.isoformat()]
    return max(eligible, key=lambda row: str(row["date"])) if eligible else None


def lseg_history(symbol: str, start: str, end: str | None = None):
    load_dotenv(); app_key=os.environ.get("LSEG_APP_KEY", "").strip()
    if not app_key: raise ValueError("LSEG_APP_KEY is required in .env")
    import lseg.data as ld
    ld.open_session(app_key=app_key)
    try:
        converted=ld.discovery.convert_symbols(symbols=symbol.upper(),from_symbol_type="TickerSymbol",to_symbol_types="RIC")
        rics=[] if converted is None or "RIC" not in converted else [str(value).strip() for value in converted["RIC"].tolist() if str(value).strip()]
        if len(rics)!=1: raise ValueError(f"LSEG could not establish one RIC for ticker {symbol!r}")
        request = {"universe": rics[0], "fields": ["TRDPRC_1", "ACVOL_UNS"], "interval": "1D", "start": start}
        if end:
            request["end"] = end
        return rics[0], ld.get_history(**request)
    finally:
        ld.close_session()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--accepted", help="EDGAR Accepted time in Eastern: YYYY-MM-DD HH:MM:SS")
    args = parser.parse_args()
    try:
        accepted_date = dt.datetime.strptime(args.accepted, "%Y-%m-%d %H:%M:%S").date() if args.accepted else None
        ric,history=lseg_history(args.symbol, period_start(args.period, accepted_date), accepted_date.isoformat() if accepted_date else None); rows=rows_from_history(history)
        data={"close":"not available","source":"LSEG Data Library","reason":"no daily price and volume rows"} if not rows else {
            "source":"LSEG Data Library TRDPRC_1 daily price and ACVOL_UNS daily volume",
            "symbol":args.symbol.upper(),"ric":ric,"rows":rows,
        }
        if rows and args.accepted:
            selected = last_close_before_accepted(rows, args.accepted)
            if selected is None:
                data = {"close": "not available", "source": data["source"],
                        "reason": "no saved daily close precedes the EDGAR Accepted time",
                        "accepted_time_eastern": args.accepted, "rows": rows}
            else:
                data["accepted_time_eastern"] = args.accepted
                data["valuation_price"] = {**selected, "selection": "last daily regular-session close available before EDGAR Accepted time"}
    except Exception as error:
        data={"close":"not available","source":"LSEG Data Library","reason":str(error)}
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
