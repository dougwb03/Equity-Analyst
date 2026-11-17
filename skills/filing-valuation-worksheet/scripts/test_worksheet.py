import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).with_name("worksheet.py")
SPEC = importlib.util.spec_from_file_location("worksheet", MODULE)
worksheet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worksheet)


SETTINGS = {
    "client_purchase_size": 1000,
    "valuation": {"method": "owner_cash_flow_multiple", "low_rate": 4, "mid_rate": 6},
    "conviction": {"high_if_all_of": ["owner_cash_flow", "low_value_per_share", "price", "liquidity"]},
}


class WorksheetTests(unittest.TestCase):
    def make_run(self, root: Path, close_available=True):
        run = root / "saved_run"
        run.mkdir()
        (run / "run_manifest.json").write_text(json.dumps({"ticker": "TEST", "company": "Sample issuer", "cik": "0000000001"}))
        concepts = {
            "net_income": {"label": "Net income", "current": {"val": 100, "accn": "0000000000-00-000001"}},
            "depreciation": {"label": "Depreciation", "current": {"val": 20, "accn": "0000000000-00-000001"}},
            "capex": {"label": "Capital spending", "current": {"val": 30, "accn": "0000000000-00-000001"}},
        }
        (run / "facts.json").write_text(json.dumps({"concepts": concepts}))
        (run / "10-K.txt").write_text("CONSOLIDATED STATEMENTS\nNet income 100\nDepreciation 20\nCapital spending 30\n1,000 shares of common stock outstanding")
        (run / "4-sample.txt").write_text("<nonDerivativeTransaction><transactionCode>P</transactionCode><transactionShares><value>25</value></transactionShares></nonDerivativeTransaction>")
        market = root / "market.json"
        market.write_text(json.dumps({"close": "not available"} if not close_available else {"source": "test reader", "rows": [
            {"date": "2026-01-02", "close": 0.20, "volume": 10000}, {"date": "2026-01-03", "close": 0.25, "volume": 20000}
        ]}))
        persona = root / "persona.md"
        persona.write_text("Use evidence.")
        return run, market, persona

    def test_owner_cash_flow_value_liquidity_and_code_purchases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, market, persona = self.make_run(root)
            output = worksheet.build_worksheet(run, SETTINGS, None, market, {}, persona, "unused", "Judgment only.")
            text = output.read_text()
            self.assertIn("100 + 20 - 30 = 90", text)
            self.assertIn("Low value per share: 0.3600", text)
            self.assertIn("Open-market insider purchases (Form 4, code P only): 25", text)
            self.assertIn("Revenue: not known", text)
            self.assertIn("Median daily dollar volume: 3500.00", text)
            self.assertIn("View: more than the price", text)
            self.assertIn("Conviction: high", text)
            self.assertIn("Parent: not applicable", text)

    def test_no_close_makes_price_and_view_not_computed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, market, persona = self.make_run(root, close_available=False)
            output = worksheet.build_worksheet(run, SETTINGS, None, market, {}, persona, "unused", "Judgment only.")
            text = output.read_text()
            self.assertIn("Price and price against low value: not computed; close is not available.", text)
            self.assertIn("- not computed; close or low value is not available.", text)
            self.assertIn("Conviction: low", text)

    def test_form4_does_not_count_other_transaction_codes(self):
        texts = {"4-sample.txt": "<nonDerivativeTransaction><transactionCode>S</transactionCode><transactionShares><value>99</value></transactionShares></nonDerivativeTransaction>"}
        self.assertEqual(worksheet.form4_purchases(texts), (None, None))

    def test_registry_spinoff_facts_are_cited_and_missing_details_stay_unknown(self):
        row = {
            "registration": {"accession": "0000000000-00-000001"},
            "parent_company": {"value": "Parent", "source": {"filing": "form10", "section": "Cover"}},
            "distribution_ratio": {"value": "one share", "source": {"filing": "form10", "section": "Cover"}},
            "record_date": {"value": "2026-01-01", "source": {"filing": "form10", "section": "Cover"}},
            "distribution_date": {"value": "2026-01-02", "source": {"filing": "form10", "section": "Cover"}},
        }
        lines, missing = worksheet.spinoff_facts(row, {"spinoff_sample.txt": "INFORMATION STATEMENT\nNo debt amount is stated."})
        self.assertIn("Parent: Parent; form10, Cover", "\n".join(lines))
        self.assertIn("debt raised", missing)
        self.assertIn("restrictions left by parent", missing)

    def test_depreciation_can_come_from_a_saved_statement_table(self):
        value, source = worksheet.text_fact({"10-K.txt": "CASH FLOWS\nDepreciation and amortization\n2,454\n863"}, ("depreciation and amortization",))
        self.assertEqual(value, 2454000000)
        self.assertEqual(source["filing"], "10-K.txt")

    def test_share_count_adds_disclosed_common_share_classes(self):
        text = "As of January 1, 2026, Sample Issuer had outstanding 100 shares of Class A common stock, 40 shares of Class B common stock, and 0 shares of Class C common stock."
        shares, source = worksheet.extract_shares({"10-Q.txt": text})
        self.assertEqual(shares, 140)
        self.assertEqual(source["filing"], "10-Q.txt")

    def test_share_count_prefers_the_saved_quarterly_filing(self):
        older = "As of January 1, 2025, Sample Issuer had outstanding 10 shares of Class A common stock, 10 shares of Class B common stock."
        newer = "As of January 1, 2026, Sample Issuer had outstanding 100 shares of Class A common stock, 40 shares of Class B common stock."
        shares, source = worksheet.extract_shares({"10-K.txt": older, "10-Q.txt": newer})
        self.assertEqual(shares, 140)
        self.assertEqual(source["filing"], "10-Q.txt")

    def test_negative_owner_cash_flow_has_no_multiple_based_value(self):
        result = worksheet.valuation(-10, 100, SETTINGS)
        self.assertIsNone(result["low"])
        self.assertIn("not positive", result["reason"])


if __name__ == "__main__":
    unittest.main()
