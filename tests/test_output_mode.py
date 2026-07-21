"""output_mode helpers and legacy boolean migration."""

from __future__ import annotations

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


class OutputModeHelperTests(unittest.TestCase):
    def test_normalize_and_legacy_mapping(self):
        from core.output_mode import (
            DEFAULT_OUTPUT_MODE,
            OUTPUT_MODE_COPY,
            OUTPUT_MODE_PASTE,
            OUTPUT_MODE_PASTE_COPY,
            normalize_output_mode,
            output_mode_from_legacy,
            resolve_output_mode_from_raw,
        )

        self.assertEqual(normalize_output_mode("paste"), OUTPUT_MODE_PASTE)
        self.assertEqual(normalize_output_mode("nope"), DEFAULT_OUTPUT_MODE)
        self.assertEqual(output_mode_from_legacy(False, True), OUTPUT_MODE_COPY)
        self.assertEqual(output_mode_from_legacy(True, True), OUTPUT_MODE_PASTE)
        self.assertEqual(output_mode_from_legacy(True, False), OUTPUT_MODE_PASTE_COPY)
        self.assertEqual(
            resolve_output_mode_from_raw({"paste_result": False}),
            OUTPUT_MODE_COPY,
        )
        self.assertEqual(
            resolve_output_mode_from_raw({"output_mode": "paste"}),
            OUTPUT_MODE_PASTE,
        )


class OutputModeConfigMigrationTests(unittest.TestCase):
    def test_load_migrates_legacy_bools_and_strips_them(self):
        from config import Config, _config_path

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "paste_result": False,
                        "restore_clipboard": True,
                        "simulate_keypresses": True,
                        "mode": "polish",
                    }),
                    encoding="utf-8",
                )
                cfg = Config.load()
                disk = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(cfg.output_mode, "copy")
        self.assertEqual(disk["output_mode"], "copy")
        self.assertNotIn("paste_result", disk)
        self.assertNotIn("restore_clipboard", disk)
        self.assertNotIn("simulate_keypresses", disk)

    def test_load_prefers_explicit_output_mode_over_legacy(self):
        from config import Config, _config_path

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"USERPROFILE": tmp}):
                path = _config_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps({
                        "output_mode": "paste",
                        "paste_result": False,
                        "restore_clipboard": False,
                    }),
                    encoding="utf-8",
                )
                cfg = Config.load()
                disk = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(cfg.output_mode, "paste")
        self.assertEqual(disk["output_mode"], "paste")
        self.assertNotIn("paste_result", disk)


class InjectorDeliverTests(unittest.TestCase):
    def test_deliver_dispatches_by_mode(self):
        from core.injector import TextInjector
        from unittest.mock import patch

        inj = TextInjector()
        with patch.object(inj, "copy_only", return_value=True) as c, \
             patch.object(inj, "paste_only", return_value=True) as p, \
             patch.object(inj, "paste_and_copy", return_value=True) as pc:
            self.assertEqual(inj.deliver("t", "copy"), "copied")
            self.assertEqual(inj.deliver("t", "paste"), "pasted")
            self.assertEqual(inj.deliver("t", "paste_copy"), "pasted_copied")
            c.assert_called_once_with("t")
            p.assert_called_once_with("t")
            pc.assert_called_once_with("t")


if __name__ == "__main__":
    unittest.main()
