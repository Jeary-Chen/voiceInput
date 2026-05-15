import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from config import Config  # noqa: E402


class ConfigDefaultsTests(unittest.TestCase):
    def test_missing_config_defaults_to_polish_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                cfg = Config.load()

        self.assertEqual(cfg.mode, "polish")


if __name__ == "__main__":
    unittest.main()
