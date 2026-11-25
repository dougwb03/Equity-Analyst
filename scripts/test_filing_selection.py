import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkpoint_1 import equity_desk as sec


class Client:
    def get(self, _url):
        class Response:
            content = b"<html><body>Item 2.02 Results of Operations</body></html>"
        return Response()


class FilingSelectionTests(unittest.TestCase):
    def test_new_registrant_can_use_a_first_10_q_when_no_10_k_exists(self):
        quarterly = sec.Filing("10-Q", "0000000000-00-000001", "2026-11-01", "quarter.htm", "2026-09-30")
        release = sec.Filing("8-K", "0000000000-00-000002", "2026-11-02", "release.htm", "2026-11-02")
        with patch.object(sec, "accepted_time", return_value="2026-11-02 16:00:00"):
            selected = sec.choose_filings(Client(), "0000000001", [quarterly, release])
        self.assertNotIn("10-K", selected)
        self.assertEqual(selected["10-Q"].accession, quarterly.accession)
        self.assertEqual(selected["8-K"].accession, release.accession)

    def test_unmapped_interim_taxonomy_returns_blank_facts_not_estimates(self):
        facts = sec.unavailable_facts("2026-09-30", "interim statement line could not be mapped")
        self.assertIsNone(facts["concepts"]["total_revenue"]["current"])
        self.assertIn("could not be mapped", facts["concepts"]["total_revenue"]["unavailable_note"])

    def test_total_net_revenue_is_a_consolidated_revenue_line(self):
        row = {
            "label": "Total net revenue",
            "tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "val": 100.0,
            "start": "2025-02-01",
            "end": "2026-01-30",
            "instant": False,
        }
        selected, definition = sec.select_total_revenue_row([row], "2026-01-30")
        self.assertEqual(selected, row)
        self.assertIn("Consolidated total revenue", definition)

    def test_non_earnings_exhibit_has_no_quarterly_evidence_instead_of_a_guess(self):
        with tempfile.NamedTemporaryFile(suffix=".html") as handle:
            handle.write(b"<html><body>Other event only.</body></html>")
            handle.flush()
            self.assertEqual(sec.earnings_evidence(Path(handle.name)), [])


if __name__ == "__main__":
    unittest.main()
