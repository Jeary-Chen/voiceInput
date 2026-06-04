"""Tests for declarative config upgrade operations."""
import json
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
    _CONFIG_UPGRADE_RULES,
    _config_path,
    _default,
    default_polish_models,
)
from config_upgrade_ops import apply_config_upgrade_rules  # noqa: E402


def _apply(
    cfg: Config,
    *,
    from_version: str = "1.4.0",
    to_version: str = "1.5.0",
    rules: list[tuple[str, list[dict]]],
) -> frozenset[str]:
    return apply_config_upgrade_rules(
        cfg,
        from_version=from_version,
        to_version=to_version,
        rules=rules,
        is_known_field=lambda name: name in Config.__dataclass_fields__,
        get_default=_default,
        entry_sources={"default_polish_models": default_polish_models},
    )


class ConfigUpgradeOpsTests(unittest.TestCase):
    def test_builtin_upgrade_rules_are_empty_after_manual_test_cleanup(self):
        self.assertEqual(_CONFIG_UPGRADE_RULES, [])

    def test_catalog_remove_supports_startswith_match(self):
        cfg = Config(
            config_version="1.4.0",
            polish_models=[
                {"id": "qwen-a", "label": "A"},
                {"id": "deepseek-v4-flash", "label": "DeepSeek"},
                {"id": "qwen-b", "label": "B"},
            ],
        )

        _apply(
            cfg,
            rules=[
                ("1.5.0", [
                    {"op": "catalog_remove", "field": "polish_models",
                     "match": {"id__startswith": "qwen"}},
                ]),
            ],
        )

        self.assertEqual(
            cfg.polish_models,
            [{"id": "deepseek-v4-flash", "label": "DeepSeek"}],
        )

    def test_dev_app_version_skips_upgrade_rules(self):
        cfg = Config(
            config_version="dev",
            polish_models=[{"id": "qwen3.6-plus", "label": "Qwen3.6 Plus"}],
        )

        changed = _apply(
            cfg,
            from_version="dev",
            to_version="dev",
            rules=[
                ("1.5.0", [
                    {"op": "catalog_remove", "field": "polish_models",
                     "match": {"id": "qwen3.6-plus"}},
                    {"op": "catalog_add", "field": "polish_models",
                     "entry": {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus"},
                     "if_exists": "skip"},
                ]),
            ],
        )

        self.assertEqual(cfg.polish_models, [{"id": "qwen3.6-plus", "label": "Qwen3.6 Plus"}])
        self.assertEqual(cfg.config_version, "dev")
        self.assertEqual(cfg.upgraded_backup, {})
        self.assertEqual(changed, frozenset())

    def test_legacy_rules_are_converted_to_set_ops(self):
        cfg = Config(config_version="1.4.0", asr_model="old")

        changed = apply_config_upgrade_rules(
            cfg,
            from_version="1.4.0",
            to_version="1.5.0",
            rules=[],
            legacy_rules=[("1.5.0", ("asr_model",))],
            is_known_field=lambda name: name in Config.__dataclass_fields__,
            get_default=_default,
        )

        self.assertEqual(cfg.asr_model, _default("asr_model"))
        self.assertEqual(cfg.upgraded_backup["1.4.0"]["asr_model"], "old")
        self.assertEqual(
            changed,
            frozenset({"asr_model", "config_version", "upgraded_backup"}),
        )

    def test_catalog_ops_preserve_custom_items_and_backup_original_field(self):
        cfg = Config(
            config_version="1.4.0",
            asr_model="old-asr",
            polish_models=[
                {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash"},
                {"id": "my-private", "label": "My Private"},
                {"id": "qwen-legacy", "label": "Legacy"},
                {"id": "qwen3-max", "label": "Old Max", "note": "keep"},
            ],
        )
        original_models = [
            {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash"},
            {"id": "my-private", "label": "My Private"},
            {"id": "qwen-legacy", "label": "Legacy"},
            {"id": "qwen3-max", "label": "Old Max", "note": "keep"},
        ]

        changed = _apply(
            cfg,
            rules=[
                ("1.5.0", [
                    {"op": "set", "field": "asr_model", "value": "new-asr"},
                    {"op": "catalog_remove", "field": "polish_models",
                     "match": {"id": "qwen-legacy"}},
                    {"op": "catalog_update", "field": "polish_models",
                     "match": {"id": "qwen3-max"},
                     "patch": {"label": "Qwen3 Max"}},
                    {"op": "catalog_add", "field": "polish_models",
                     "entry": {"id": "qwen3-new", "label": "Qwen3 New"},
                     "if_exists": "skip"},
                ]),
            ],
        )

        self.assertEqual(
            cfg.polish_models,
            [
                {"id": "qwen3.6-flash", "label": "Qwen3.6 Flash"},
                {"id": "my-private", "label": "My Private"},
                {"id": "qwen3-max", "label": "Qwen3 Max", "note": "keep"},
                {"id": "qwen3-new", "label": "Qwen3 New"},
            ],
        )
        self.assertEqual(cfg.asr_model, "new-asr")
        self.assertEqual(cfg.config_version, "1.5.0")
        self.assertEqual(cfg.upgraded_backup["1.4.0"]["polish_models"], original_models)
        self.assertEqual(cfg.upgraded_backup["1.4.0"]["asr_model"], "old-asr")
        self.assertEqual(
            changed,
            frozenset({"asr_model", "polish_models", "config_version", "upgraded_backup"}),
        )

    def test_catalog_update_uses_all_match_fields_and_updates_all_matches(self):
        cfg = Config(
            config_version="1.4.0",
            polish_models=[
                {"id": "qwen3-max", "label": "Old"},
                {"id": "qwen3-max", "label": "Other"},
                {"id": "qwen3-max", "label": "Old", "region": "cn"},
            ],
        )

        _apply(
            cfg,
            rules=[
                ("1.5.0", [
                    {"op": "catalog_update", "field": "polish_models",
                     "match": {"id": "qwen3-max", "label": "Old"},
                     "patch": {"label": "New"}},
                ]),
            ],
        )

        self.assertEqual(
            cfg.polish_models,
            [
                {"id": "qwen3-max", "label": "New"},
                {"id": "qwen3-max", "label": "Other"},
                {"id": "qwen3-max", "label": "New", "region": "cn"},
            ],
        )

    def test_catalog_add_skip_does_not_overwrite_existing_entry(self):
        cfg = Config(
            config_version="1.4.0",
            polish_models=[{"id": "qwen3-max", "label": "User Label"}],
        )

        changed = _apply(
            cfg,
            rules=[
                ("1.5.0", [
                    {"op": "catalog_add", "field": "polish_models",
                     "entry": {"id": "qwen3-max", "label": "Official Label"},
                     "if_exists": "skip"},
                ]),
            ],
        )

        self.assertEqual(cfg.polish_models, [{"id": "qwen3-max", "label": "User Label"}])
        self.assertNotIn("upgraded_backup", changed)
        self.assertEqual(changed, frozenset({"config_version"}))

    def test_cross_version_jump_applies_rules_in_order_and_backs_up_once(self):
        cfg = Config(config_version="1.3.0", asr_model="old")

        _apply(
            cfg,
            from_version="1.3.0",
            to_version="1.6.0",
            rules=[
                ("1.6.0", [{"op": "set", "field": "asr_model", "value": "v16"}]),
                ("1.5.0", [{"op": "set", "field": "asr_model", "value": "v15"}]),
            ],
        )

        self.assertEqual(cfg.asr_model, "v16")
        self.assertEqual(cfg.upgraded_backup["1.3.0"]["asr_model"], "old")
        self.assertEqual(cfg.config_version, "1.6.0")

    def test_config_load_persists_upgrade_and_normalized_selected_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                data = Config(
                    config_version="1.4.0",
                    polish_model="qwen-legacy",
                    polish_models=[
                        {"id": "qwen-legacy", "label": "Legacy"},
                        {"id": "qwen3-max", "label": "Qwen3 Max"},
                    ],
                )._as_dict()
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

                with patch("config._CONFIG_UPGRADE_RULES", [
                    ("1.5.0", [
                        {"op": "catalog_remove", "field": "polish_models",
                         "match": {"id": "qwen-legacy"}},
                    ]),
                ]), patch("_version.VERSION", "1.5.0"):
                    cfg = Config.load()

                disk = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(cfg.polish_models, [{"id": "qwen3-max", "label": "Qwen3 Max"}])
        self.assertEqual(cfg.polish_model, "qwen3-max")
        self.assertEqual(disk["polish_models"], cfg.polish_models)
        self.assertEqual(disk["polish_model"], "qwen3-max")
        self.assertEqual(disk["config_version"], "1.5.0")
        self.assertIn("upgraded_backup", disk)

    def test_config_load_clears_dev_version_without_applying_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                data = Config(
                    config_version="dev",
                    polish_model="qwen3.6-plus",
                    polish_models=[
                        {"id": "qwen3.6-plus", "label": "Qwen3.6 Plus"},
                    ],
                )._as_dict()
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

                with patch("config._CONFIG_UPGRADE_RULES", [
                    ("1.5.0", [
                        {"op": "catalog_remove", "field": "polish_models",
                         "match": {"id": "qwen3.6-plus"}},
                        {"op": "catalog_add", "field": "polish_models",
                         "entry": {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus"},
                         "if_exists": "skip"},
                    ]),
                ]), patch("_version.VERSION", "dev"):
                    cfg = Config.load()

                disk = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(cfg.polish_models, [{"id": "qwen3.6-plus", "label": "Qwen3.6 Plus"}])
        self.assertEqual(cfg.polish_model, "qwen3.6-plus")
        self.assertEqual(disk["config_version"], "")
        self.assertEqual(disk["polish_models"], cfg.polish_models)
        self.assertEqual(disk.get("upgraded_backup"), {})


if __name__ == "__main__":
    unittest.main()
