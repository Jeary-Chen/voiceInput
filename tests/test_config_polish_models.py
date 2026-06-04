"""Tests for polish_models in config.json."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import (  # noqa: E402
    Config,
    default_polish_models,
    polish_model_menu_items,
)


class PolishModelCatalogTests(unittest.TestCase):
    def test_menu_items_from_dict_list(self):
        models = [
            {"id": "a", "label": "A"},
            {"name": "B", "id": "b"},
        ]
        self.assertEqual(polish_model_menu_items(models), [("a", "A"), ("b", "B")])

    def test_load_adds_missing_polish_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                cfg = Config.load()
        self.assertEqual(cfg.polish_models, default_polish_models())

    def test_invalid_polish_model_reset_to_first_catalog_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                cfg = Config.load()
                cfg.polish_model = "not-in-catalog"
                cfg.save()
                cfg2 = Config.load()
        self.assertEqual(cfg2.polish_model, "qwen3.6-flash")

    def test_custom_catalog_persisted(self):
        custom = [{"id": "my-model", "label": "My Model"}]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                cfg = Config.load()
                cfg.polish_models = custom
                cfg.polish_model = "my-model"
                cfg.save()
                cfg2 = Config.load()
        self.assertEqual(cfg2.polish_models, custom)
        self.assertEqual(cfg2.polish_model, "my-model")


if __name__ == "__main__":
    unittest.main()
