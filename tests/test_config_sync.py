"""ConfigSync concurrency and merge behavior tests."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import Config, _config_path  # noqa: E402
from core.config_sync import ConfigSync, _CORRUPT_RETRY_COUNT, _SUPPRESS_AFTER_WRITE_MS  # noqa: E402


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    time.sleep(0.05)


def _base_config(**overrides) -> dict:
    cfg = Config()
    data = {
        k: v
        for k, v in cfg.__dict__.items()
        if not k.startswith("_")
    }
    data.update(overrides)
    return data


class ConfigSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _setup(self, *, idle: bool = True) -> tuple[Config, ConfigSync, Path]:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict("os.environ", {"USERPROFILE": self._tmpdir.name})
        self._env_patch.start()
        path = _config_path()
        data = _base_config(
            polish_model="qwen3.6-flash",
            output_mode="paste_copy",
            active_prompt_id="__tpl_translate_en",
        )
        _write_json(path, data)
        cfg = Config.load()
        sync = ConfigSync(cfg)
        sync.bind_idle_checker(lambda: idle)
        sync.start()
        return cfg, sync, path

    def tearDown(self):
        if hasattr(self, "_env_patch"):
            self._env_patch.stop()
        if hasattr(self, "_tmpdir"):
            self._tmpdir.cleanup()

    def test_external_reload_updates_memory_when_idle(self):
        cfg, sync, path = self._setup()
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        sync._request_external_reload("test")
        self.assertEqual(cfg.polish_model, "qwen3.7-max")
        self.assertFalse(sync.has_pending_reload)

    def test_runtime_reload_persists_missing_fields(self):
        cfg, sync, path = self._setup()
        _write_json(path, {"mode": "raw"})

        sync._request_external_reload("test")

        self.assertEqual(cfg.mode, "raw")
        self.assertEqual(cfg.hotkey, Config().hotkey)
        disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(disk["mode"], "raw")
        self.assertIn("hotkey", disk)
        self.assertEqual(disk["hotkey"], Config().hotkey)

    def test_external_reload_queued_when_busy(self):
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        sync._request_external_reload("test")
        self.assertEqual(cfg.polish_model, "qwen3.6-flash")
        self.assertTrue(sync.has_pending_reload)

    def test_flush_pending_reload_when_idle(self):
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)
        sync._request_external_reload("test")
        sync.bind_idle_checker(lambda: True)
        sync.flush_pending_reload()
        self.assertEqual(cfg.polish_model, "qwen3.7-max")
        self.assertFalse(sync.has_pending_reload)

    def test_ui_save_merges_external_disk_preserves_touched(self):
        cfg, sync, path = self._setup()

        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["output_mode"] = "copy"
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        cfg.polish_model = "qwen3.6-plus"
        cfg.save(touched=frozenset({"polish_model"}))

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["polish_model"], "qwen3.6-plus")
        self.assertEqual(on_disk["output_mode"], "copy")
        self.assertEqual(cfg.polish_model, "qwen3.6-plus")
        self.assertEqual(cfg.output_mode, "copy")

    def test_external_edit_during_write_guard_recovered_after_write(self):
        cfg, sync, path = self._setup()
        sync.stop()
        sync._capture_sync_token()

        sync._writing = True
        try:
            disk = json.loads(path.read_text(encoding="utf-8"))
            disk["polish_model"] = "qwen3.7-max"
            _write_json(path, disk)
            sync._on_file_changed(str(path))
            self.assertFalse(sync._debounce.isActive())
        finally:
            sync._writing = False
            sync._suppress_until = 0.0

        sync._check_external_change_after_write()
        self.assertEqual(cfg.polish_model, "qwen3.7-max")

    def test_save_while_busy_writes_touched_only_and_queues_reload(self):
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["output_mode"] = "copy"
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        cfg.output_mode = "paste_copy"
        cfg.save(touched=frozenset({"output_mode"}))

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["output_mode"], "paste_copy")
        self.assertEqual(on_disk["polish_model"], "qwen3.7-max")
        self.assertTrue(sync.has_pending_reload)

    def test_apply_external_reload_sets_updating_flag(self):
        cfg, sync, path = self._setup()
        states: list[str] = []
        sync.apply_started.connect(lambda: states.append("start"))
        sync.apply_finished.connect(lambda: states.append("end"))

        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)
        sync._request_external_reload("test")

        self.assertEqual(states, ["start", "end"])
        self.assertFalse(sync.is_updating)

    def test_save_while_busy_without_disk_conflict_writes_immediately(self):
        """App -> disk is NOT deferred when disk has no pending external edits."""
        cfg, sync, path = self._setup(idle=False)
        cfg.output_mode = "copy"
        cfg.save(touched=frozenset({"output_mode"}))

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["output_mode"], "copy")
        self.assertFalse(sync.has_pending_reload)

    def test_busy_conflict_save_leaves_memory_stale_until_flush(self):
        """Touched field syncs to disk immediately; other external edits stay queued in memory."""
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        cfg.output_mode = "copy"
        cfg.save(touched=frozenset({"output_mode"}))

        self.assertEqual(cfg.polish_model, "qwen3.6-flash")
        self.assertTrue(sync.has_pending_reload)

        sync.bind_idle_checker(lambda: True)
        sync.flush_pending_reload()
        self.assertEqual(cfg.polish_model, "qwen3.7-max")
        self.assertEqual(cfg.output_mode, "copy")

    def test_blocks_recording_while_pending_or_debouncing(self):
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)
        sync._request_external_reload("test")
        self.assertTrue(sync.blocks_recording)

        sync.bind_idle_checker(lambda: True)
        sync.flush_pending_reload()
        self.assertFalse(sync.has_pending_reload)
        self.assertFalse(sync.blocks_recording)

    def test_external_only_during_busy_no_app_writeback(self):
        """External edit while busy: app does not write back stale memory to disk."""
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        sync._request_external_reload("test")
        self.assertEqual(cfg.polish_model, "qwen3.6-flash")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["polish_model"], "qwen3.7-max")

    def test_debounce_after_write_touched_only_preserves_pending(self):
        """Watcher debounce after _write_touched_only must not clear _pending_reload.

        Sequence: busy + external edit -> UI save (touched only) -> debounce fires
        -> pending must survive so flush can resolve it later.
        """
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        cfg.output_mode = "copy"
        cfg.save(touched=frozenset({"output_mode"}))
        self.assertTrue(sync.has_pending_reload)

        sync._suppress_until = 0.0
        sync._request_external_reload("watch")
        self.assertTrue(sync.has_pending_reload,
                        "_pending_reload cleared by debounce after _write_touched_only")

        self.assertEqual(cfg.polish_model, "qwen3.6-flash")

    def test_corrupt_disk_emits_fault_and_keeps_memory(self):
        cfg, sync, path = self._setup()
        faults: list[bool] = []
        sync.config_disk_fault.connect(lambda: faults.append(True))
        sync._corrupt_retries = _CORRUPT_RETRY_COUNT

        path.write_text("", encoding="utf-8")
        sync._request_external_reload("test")

        self.assertEqual(faults, [True])
        self.assertTrue(sync.disk_fault_active)
        self.assertEqual(cfg.polish_model, "qwen3.6-flash")

    def test_corrupt_disk_recovery_clears_fault(self):
        cfg, sync, path = self._setup()
        sync._corrupt_retries = _CORRUPT_RETRY_COUNT
        path.write_text("", encoding="utf-8")
        sync._request_external_reload("test")
        self.assertTrue(sync.disk_fault_active)

        recovered: list[bool] = []
        sync.config_disk_recovered.connect(lambda: recovered.append(True))
        disk = _base_config(polish_model="qwen3.7-max")
        _write_json(path, disk)
        sync._request_external_reload("test")

        self.assertEqual(recovered, [True])
        self.assertFalse(sync.disk_fault_active)
        self.assertEqual(cfg.polish_model, "qwen3.7-max")

    def test_debounce_after_idle_merge_clears_pending(self):
        """After an idle merge already synced memory, a stale debounce should clear pending."""
        cfg, sync, path = self._setup()

        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        sync._pending_reload = True
        sync._suppress_until = 0.0
        sync._request_external_reload("watch")

        self.assertEqual(cfg.polish_model, "qwen3.7-max")
        self.assertFalse(sync.has_pending_reload)

    def test_write_touched_only_suppresses_watcher(self):
        """_write_touched_only sets _writing and _suppress_until so watcher ignores own write."""
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk["polish_model"] = "qwen3.7-max"
        _write_json(path, disk)

        cfg.output_mode = "copy"
        cfg.save(touched=frozenset({"output_mode"}))

        self.assertTrue(sync._suppress_until > 0)
        sync._on_file_changed(str(path))
        self.assertFalse(sync._debounce.isActive(),
                         "watcher event during suppress window should be ignored")

    def test_busy_touched_write_orders_root_fields_with_backup_last(self):
        cfg, sync, path = self._setup(idle=False)
        disk = json.loads(path.read_text(encoding="utf-8"))
        disk = {
            "upgraded_backup": {"1.0.0": {"mode": "old"}},
            "z_unknown": 1,
            **disk,
        }
        _write_json(path, disk)

        cfg.output_mode = "copy"
        cfg.save(touched=frozenset({"output_mode"}))

        on_disk = json.loads(path.read_text(encoding="utf-8"))
        keys = list(on_disk)
        self.assertLess(keys.index("output_mode"), keys.index("z_unknown"))
        self.assertEqual(keys[-2:], ["z_unknown", "upgraded_backup"])


if __name__ == "__main__":
    unittest.main()
