import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("desk", Path(__file__).parents[1] / "desk.py")
desk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desk)


class DeskTests(unittest.TestCase):
    def test_top_screen_ticker_uses_highest_ranked_in_window_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "registry.json").write_text(json.dumps([
                {"stage": "registered", "symbol": {"value": "WAIT"}, "total_points": "99"},
                {"stage": "in its window", "symbol": {"value": "LOW"}, "total_points": "2"},
                {"stage": "in its window", "symbol": {"value": "TOP"}, "total_points": "3"},
            ]))
            self.assertEqual(desk.top_screen_ticker(run), "TOP")

    def test_top_screen_ticker_refuses_no_ranked_ticker(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "registry.json").write_text("[]")
            with self.assertRaisesRegex(RuntimeError, "no ranked name"):
                desk.top_screen_ticker(run)

    def test_top_screen_ticker_uses_the_only_eligible_unranked_name_when_ranking_is_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "registry.json").write_text(json.dumps([
                {"stage": "in its window", "symbol": {"value": "ONLY"}, "unranked_reason": "first price is blank"},
            ]))
            self.assertEqual(desk.top_screen_ticker(run), "ONLY")

    def test_matching_registry_copies_only_the_matching_ticker_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry.json"
            registry.write_text(json.dumps([
                {"new_company_cik": "0000000001", "symbol": {"value": "OTHER"}},
                {"new_company_cik": "0000000002", "symbol": {"value": "MATCH"}, "registration": {"accession": "0000000000-00-000001"}},
            ]))
            rows = desk.matching_registry_rows("MATCH", "0000000002", [registry])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"]["value"], "MATCH")

    def test_uses_saved_earnings_8_k_accepted_time_for_the_market_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "02_step_2_output.json").write_text(json.dumps({
                "8-K": {"accepted": "2026-11-24 16:09:13"}
            }))
            self.assertEqual(desk.earnings_accepted_time(run), "2026-11-24 16:09:13")

    def test_final_report_keeps_one_authoritative_worksheet_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "verified_report.md").write_text("Filings read: 8-K (0000000000-00-000001)\nTeam context\n")
            (run / "worksheet_2026-01-01.md").write_text(
                "## View\n- View: less than the price.\n\n"
                "## Recommendation\n- **Recommendation: Sell.** Supported low value is below the price.\n"
            )
            (run / "02_step_2_output.json").write_text(json.dumps({"8-K": {"form": "8-K", "accession": "0000000000-00-000001"}}))
            report = desk.final_report(run, "TEST", {"agent team": 1.0})
            text = report.read_text()
            self.assertEqual(text.count("View: less than the price."), 1)
            self.assertIn("sole authoritative client view", text)
            self.assertTrue(text.rstrip().endswith("- **Recommendation: Sell.** Supported low value is below the price."))
            self.assertIn("## Final client decision", text)

    def test_saved_conference_call_requires_an_https_source_and_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "call.txt"; source.write_text("Official call text")
            run = root / "run"; run.mkdir()
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                desk.save_conference_call(run, source, "http://example.test/call")
            desk.save_conference_call(run, source, "https://investor.example.test/call")
            self.assertEqual((run / "conference_call.txt").read_text(), "Official call text")
            self.assertEqual(json.loads((run / "conference_call_source.json").read_text())["source_url"], "https://investor.example.test/call")


if __name__ == "__main__":
    unittest.main()
