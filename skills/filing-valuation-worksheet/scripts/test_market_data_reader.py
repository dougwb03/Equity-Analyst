import importlib.util
import unittest
from pathlib import Path
import pandas as pd


MODULE = Path(__file__).with_name("market_data_reader.py")
SPEC = importlib.util.spec_from_file_location("market_data_reader", MODULE)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class Row:
    def __init__(self, close, volume): self.values = {"Close": close, "Volume": volume}
    def __getitem__(self, key): return self.values[key]


class Stamp:
    def date(self):
        class Date:
            def isoformat(self): return "2026-01-02"
        return Date()


class History:
    empty = False
    columns = {"Close", "Volume"}
    def iterrows(self): return [(Stamp(), Row(12.5, 1000))]


class MarketReaderTests(unittest.TestCase):
    def test_one_year_period_has_a_calendar_start_about_one_year_earlier(self):
        import datetime as dt
        start = dt.date.fromisoformat(reader.period_start("1y"))
        self.assertEqual((dt.date.today() - start).days, 365)

    def test_historical_filing_period_ends_on_the_filing_date(self):
        import datetime as dt
        self.assertEqual(reader.period_start("1y", dt.date(2026, 10, 28)), "2025-10-28")

    def test_lseg_history_uses_daily_price_and_volume_fields(self):
        history = pd.DataFrame(
            {"TRDPRC_1": [12.5], "ACVOL_UNS": [1000]},
            index=pd.to_datetime(["2026-01-02"]),
        )
        self.assertEqual(
            reader.rows_from_history(history),
            [{"date": "2026-01-02", "close": 12.5, "volume": 1000.0}],
        )

    def test_rows_keep_close_and_volume(self):
        self.assertEqual(reader.rows_from_history(History()), [{"date": "2026-01-02", "close": 12.5, "volume": 1000.0}])

    def test_missing_history_is_empty(self):
        self.assertEqual(reader.rows_from_history(None), [])

    def test_after_close_filing_can_use_that_day_regular_close(self):
        rows = [{"date": "2026-11-23", "close": 10.0, "volume": 100},
                {"date": "2026-11-24", "close": 11.0, "volume": 100}]
        self.assertEqual(
            reader.last_close_before_accepted(rows, "2026-11-24 16:09:13"),
            {"date": "2026-11-24", "close": 11.0, "volume": 100},
        )

    def test_before_close_filing_uses_prior_trading_day_close(self):
        rows = [{"date": "2026-11-23", "close": 10.0, "volume": 100},
                {"date": "2026-11-24", "close": 11.0, "volume": 100}]
        self.assertEqual(
            reader.last_close_before_accepted(rows, "2026-11-24 08:00:00"),
            {"date": "2026-11-23", "close": 10.0, "volume": 100},
        )


if __name__ == "__main__":
    unittest.main()
