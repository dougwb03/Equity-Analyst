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


if __name__ == "__main__":
    unittest.main()
