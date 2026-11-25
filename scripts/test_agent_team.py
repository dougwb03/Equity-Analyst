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


if __name__ == "__main__":
    unittest.main()
