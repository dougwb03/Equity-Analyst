import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("desk", Path(__file__).parents[1] / "desk.py")
desk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desk)


class DeskTests(unittest.TestCase):
    def test_run_folder_reads_agent_team_output(self):
        path = desk.run_folder("Run folder: runs/live/EXAMPLE\n")
        self.assertEqual(path, Path("runs/live/EXAMPLE"))

    def test_run_folder_refuses_missing_contract(self):
        with self.assertRaises(RuntimeError):
            desk.run_folder("agent team complete")


if __name__ == "__main__":
    unittest.main()
