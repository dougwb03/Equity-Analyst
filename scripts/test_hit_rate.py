import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).with_name("hit_rate.py")
SPEC = importlib.util.spec_from_file_location("hit_rate", MODULE)
hit_rate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hit_rate)


class HitRateTests(unittest.TestCase):
    def test_completed_run_requires_manifest_and_verified_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"; run.mkdir()
            self.assertFalse(hit_rate.is_completed_run(run))
            (run / "run_manifest.json").write_text("{}")
            self.assertFalse(hit_rate.is_completed_run(run))
            (run / "verified_report.md").write_text("report")
            self.assertTrue(hit_rate.is_completed_run(run))


if __name__ == "__main__":
    unittest.main()
