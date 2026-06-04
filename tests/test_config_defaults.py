import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from config import Config, _config_path  # noqa: E402


class ConfigDefaultsTests(unittest.TestCase):
    def test_missing_config_defaults_to_polish_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                cfg = Config.load()

        self.assertEqual(cfg.mode, "polish")

    def test_persist_all_orders_root_fields_with_backup_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "upgraded_backup": {"1.0.0": {"mode": "old"}},
                        "z_unknown": 1,
                        "a_unknown": 2,
                    }),
                    encoding="utf-8",
                )
                Config()._persist_all()
                data = json.loads(path.read_text(encoding="utf-8"))

        keys = list(data)
        self.assertEqual(keys[:3], ["hotkey", "trigger_mode", "mode"])
        self.assertEqual(keys[-3:], ["a_unknown", "z_unknown", "upgraded_backup"])

    def test_persist_fields_orders_root_fields_with_backup_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "upgraded_backup": {"1.0.0": {"mode": "old"}},
                        "z_unknown": 1,
                        "mode": "transcribe",
                    }),
                    encoding="utf-8",
                )
                cfg = Config(mode="polish")
                cfg._persist_fields(frozenset({"mode"}))
                data = json.loads(path.read_text(encoding="utf-8"))

        keys = list(data)
        self.assertEqual(keys[:1], ["mode"])
        self.assertEqual(keys[-2:], ["z_unknown", "upgraded_backup"])


if __name__ == "__main__":
    unittest.main()
