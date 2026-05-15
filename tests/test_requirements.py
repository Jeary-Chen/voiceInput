import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RequirementsTests(unittest.TestCase):
    def test_pyqt_runtime_is_pinned_to_verified_qt_line(self):
        requirements = (ROOT / "src" / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn("PyQt6==6.10.2", requirements)
        self.assertIn("PyQt6-Qt6==6.10.2", requirements)


if __name__ == "__main__":
    unittest.main()
