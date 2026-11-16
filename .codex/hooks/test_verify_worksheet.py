import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).with_name("verify_worksheet.py")
SPEC = importlib.util.spec_from_file_location("verify_worksheet", MODULE)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class WorksheetHookTests(unittest.TestCase):
    def fixture(self, root):
        run = root / "run"; run.mkdir()
        (run / "facts.json").write_text(json.dumps({"concepts": {"income": {"current": {"val": 100}}}}))
        (root / "settings.json").write_text(json.dumps({"client_purchase_size": 1000, "valuation": {"low_rate": 4, "mid_rate": 6}}))
        (root / "market.json").write_text(json.dumps({"rows": [{"date": "2026-01-02", "close": 10, "volume": 200}]}))
        return run

    def test_clean_supported_numbers_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            sheet.write_text("Revenue: 100\nRate: 4x\nPrice: 10 on 2026-01-02\n100 + 100 - 100 = 100\n")
            self.assertEqual(checker.verify(sheet, root / "settings.json", root / "market.json"), [])

    def test_seeded_amount_fails_with_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            sheet.write_text("Revenue: 101\n")
            self.assertEqual(checker.verify(sheet, root / "settings.json", root / "market.json"), ["refused: line 1, 101 has no filing, facts, settings, calculation, or reader support"])


if __name__ == "__main__":
    unittest.main()
