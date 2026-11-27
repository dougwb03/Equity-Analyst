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
        (root / "settings.json").write_text(json.dumps({"client_purchase_size": 1000, "valuation": {"method": "owner_cash_flow_multiple", "low_rate": 4, "mid_rate": 6}}))
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

    def test_total_common_share_count_and_printed_value_calculation_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            (run / "10-Q.txt").write_text(
                "As of 2026-01-01, there were 50 shares of the registrant common stock outstanding."
            )
            sheet.write_text(
                "Shares outstanding: 50\n"
                "100 + 100 - 100 = 100\n"
                "Low value per share: 8.0000; mid value per share: 12.0000.\n"
                "Price: 10 on 2026-01-02; source: TRDPRC_1; low-value difference: -2.0000.\n"
            )
            self.assertEqual(checker.verify(sheet, root / "settings.json", root / "market.json"), [])

    def test_final_report_must_keep_the_checked_worksheet_and_filing_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            sheet.write_text("Revenue: 100\n")
            verified = run / "verified_report.md"
            verified.write_text("Filings read: 8-K (0000000000-00-000000)\n\nReported amount: 100\n")
            report = run / "desk_report.md"
            report.write_text("Filings read: 8-K (0000000000-00-000000)\n\nReported amount: 100\n\nRevenue: 100\n")
            self.assertEqual(
                checker.verify_final_report(report, sheet, root / "settings.json", root / "market.json"), []
            )
            report.write_text("Revenue: 100\n")
            self.assertEqual(
                checker.verify_final_report(report, sheet, root / "settings.json", root / "market.json"),
                ["refused: final report has no first-line filing list"],
            )

    def test_final_report_refuses_an_edited_verified_report_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            sheet.write_text("Revenue: 100\n")
            (run / "verified_report.md").write_text("Filings read: 8-K (0000000000-00-000000)\nAmount: 100\n")
            report = run / "desk_report.md"
            report.write_text("Filings read: 8-K (0000000000-00-000000)\nAmount: 101\nRevenue: 100\n")
            self.assertEqual(
                checker.verify_final_report(report, sheet, root / "settings.json", root / "market.json"),
                ["refused: final report does not contain the saved verified report"],
            )

    def test_url_and_cik_identifiers_are_not_numeric_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            sheet.write_text("Parent: Example; https://www.sec.gov/Archives/edgar/data/1234567/0000000000-00-000001.txt, Cover.\n")
            self.assertEqual(checker.verify(sheet, root / "settings.json", root / "market.json"), [])

    def test_accession_in_a_saved_filename_is_not_a_numeric_claim(self):
        self.assertEqual(checker.relevant_numbers("Source: spinoff_0000000000-00-000001.txt"), [])

    def test_numbered_list_markers_are_not_numeric_claims(self):
        self.assertEqual(checker.relevant_numbers("2. A narrative condition."), [])

    def test_sec_form_identifiers_are_not_numeric_claims(self):
        self.assertEqual(checker.relevant_numbers("Latest saved earnings release: 8-K 0000000000-00-000001."), [])

    def test_saved_cash_flow_depreciation_line_supports_extended_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run = self.fixture(root); sheet = run / "worksheet.md"
            (run / "10-K.txt").write_text("CASH FLOWS\nDepreciation, amortization, and other\n$\n38,534\n")
            sheet.write_text("Depreciation: 38534000000\n")
            self.assertEqual(checker.verify(sheet, root / "settings.json", root / "market.json"), [])


if __name__ == "__main__":
    unittest.main()
