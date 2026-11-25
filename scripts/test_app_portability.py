import unittest
from pathlib import Path


class AppPortabilityTests(unittest.TestCase):
    def test_seeker_uses_repository_paths_not_a_personal_home_directory(self):
        source = (Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", source)
        self.assertIn('ROOT / "skills"', source)


if __name__ == "__main__":
    unittest.main()
