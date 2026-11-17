import importlib.util, sys, unittest
from pathlib import Path
import pandas as pd

SPEC=importlib.util.spec_from_file_location("closes_reader",Path(__file__).with_name("closes_reader.py"))
reader=importlib.util.module_from_spec(SPEC); sys.modules["closes_reader"]=reader; SPEC.loader.exec_module(reader)

class ClosesReaderRules(unittest.TestCase):
    def test_skips_distribution_day_and_pre_regular_way_records(self):
        history=pd.DataFrame({"Close":[10.0,11.0,12.0]},index=pd.to_datetime(["2025-01-03","2025-01-04","2025-01-06"]))
        self.assertEqual(reader.first_regular_close(history,"2025-01-03","2025-01-06"),("2025-01-06",12.0))

    def test_requires_a_close_after_both_guards(self):
        history=pd.DataFrame({"Close":[10.0]},index=pd.to_datetime(["2025-01-03"]))
        with self.assertRaises(ValueError): reader.first_regular_close(history,"2025-01-03","2025-01-03")

    def test_latest_mode_uses_the_last_close_after_the_guards(self):
        history=pd.DataFrame({"Close":[10.0,11.0,12.0]},index=pd.to_datetime(["2025-01-03","2025-01-06","2025-01-07"]))
        self.assertEqual(reader.latest_regular_close(history,"2025-01-03","2025-01-06"),("2025-01-07",12.0))

if __name__=="__main__": unittest.main()
