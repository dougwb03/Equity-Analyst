#!/usr/bin/env python3
"""Write unadjusted daily close and volume rows from the installed price reader."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def rows_from_history(history) -> list[dict[str, float | str]]:
    if history is None or history.empty or not {"Close", "Volume"}.issubset(history.columns):
        return []
    rows = []
    for stamp, row in history.iterrows():
        close, volume = row["Close"], row["Volume"]
        if close is None or volume is None:
            continue
        day = stamp.date().isoformat() if hasattr(stamp, "date") else str(stamp)[:10]
        rows.append({"date": day, "close": float(close), "volume": float(volume)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--period", default="1y")
    args = parser.parse_args()
    try:
        import yfinance as yf
    except ImportError as error:
        raise SystemExit("yfinance is required for the installed closes reader") from error
    history = yf.Ticker(args.symbol).history(period=args.period, auto_adjust=False, actions=False)
    rows = rows_from_history(history)
    data = {"close": "not available"} if not rows else {
        "source": "Yahoo Finance unadjusted Close and Volume via the existing closes-reader provider",
        "symbol": args.symbol.upper(), "rows": rows,
    }
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
