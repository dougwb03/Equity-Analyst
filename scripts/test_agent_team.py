import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("agent_team", Path(__file__).parents[1] / "agent_team.py")
agent_team = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_team)


class AgentTeamTests(unittest.TestCase):
    def test_model_client_has_a_bounded_timeout(self):
        captured = {}

        class Client:
            pass

        def fake_client(**kwargs):
            captured.update(kwargs)
            return Client()

        with patch.object(agent_team, "AsyncOpenAI", side_effect=fake_client), \
             patch.object(agent_team, "set_default_openai_client"), \
             patch.object(agent_team, "set_tracing_disabled"):
            agent_team.agents_model("test-key", "test-model")

        self.assertEqual(captured["timeout"], 120.0)
        self.assertEqual(captured["max_retries"], 2)

    def test_verifier_marks_a_null_ratio_as_unsupported_instead_of_crashing(self):
        mark, detail = agent_team.machine_mark(
            "Price-to-earnings: not computed.", "1",
            {"kind": "ratio", "operation": "divide", "inputs": "null;8.68", "result": "null", "definition": "price / EPS"},
            Path("."), {},
        )
        self.assertEqual(mark, "unsupported")
        self.assertIn("cannot be read", detail)

    def test_pre_price_advisor_defers_the_final_rating_to_the_worksheet(self):
        instructions = (Path(__file__).parents[1] / "roles" / "investment_advisor.md").read_text()
        self.assertIn("must not issue the final Buy, Hold, or Sell rating", instructions)
        self.assertIn("sole authoritative client view", instructions)

    def test_roles_require_distinct_bold_insights_for_each_section(self):
        research = (Path(__file__).parents[1] / "roles" / "research_analyst.md").read_text()
        financial = (Path(__file__).parents[1] / "roles" / "financial_analyst.md").read_text()
        advisor = (Path(__file__).parents[1] / "roles" / "investment_advisor.md").read_text()
        self.assertIn("three distinct standalone bold insight lines", research)
        self.assertIn("**Insight:", financial)
        self.assertIn("**Insight:", advisor)

    def test_n_a_score_placeholder_requires_rework(self):
        outputs = {
            "Research Analyst": "## Overview\nEvidence.\n\n**Insight: Distinct overview.** <!-- [source: line|file=8-K.txt|line=2] -->\n\n## Market Positioning and Growth Outlook\nEvidence.\n\n**Insight: Distinct market.** <!-- [source: line|file=8-K.txt|line=3] -->\n\n## Future Developments and Strategic Recommendations\nEvidence.\n\n**Insight: Distinct future.** <!-- [source: line|file=8-K.txt|line=4] -->",
            "Financial Analyst": "## Valuation Insights\n**Valuation: N/A**\n\n**Insight: Distinct financial.** <!-- [source: line|file=8-K.txt|line=5] -->",
            "Investment Advisor": "## Investment Recommendation\nEvidence.\n\n**Insight: Distinct advisor.** <!-- [source: line|file=8-K.txt|line=6] -->",
        }
        failure = agent_team.placeholder_failure(outputs)
        self.assertEqual(failure["role"], "Financial Analyst")
        self.assertIn("N/A", failure["feedback"])

    def test_replace_section_replaces_instead_of_appending_a_failed_section(self):
        prior = "## Valuation Insights\nValuation: N/A\n\n**Insight: Old.** <!-- [source: line|file=8-K.txt|line=5] -->"
        revised = "## Valuation Insights\nValuation evidence is limited.\n\n**Insight: New.** <!-- [source: line|file=8-K.txt|line=6] -->"
        replacement = agent_team.replace_section(prior, "Valuation Insights", revised)
        self.assertNotIn("N/A", replacement)
        self.assertIn("Insight: New", replacement)


if __name__ == "__main__":
    unittest.main()
