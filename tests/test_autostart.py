"""Windows autostart registry read/write tests (mocked winreg)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core import autostart  # noqa: E402


class AutostartWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    @patch.object(autostart, "read_enabled", return_value=True)
    def test_watcher_emits_on_external_change(self, _read):
        watcher = autostart.AutostartWatcher()
        watcher._last = False
        seen = []
        watcher.changed.connect(lambda: seen.append(True))
        watcher._on_registry_changed()
        self.assertEqual(len(seen), 1)
        watcher.stop()

    @patch.object(autostart, "read_enabled", return_value=True)
    def test_watcher_silent_when_unchanged(self, _read):
        watcher = autostart.AutostartWatcher()
        seen = []
        watcher.changed.connect(seen.append)
        watcher.mark_current()
        watcher._on_registry_changed()
        self.assertEqual(seen, [])
        watcher.stop()


class AutostartReadTests(unittest.TestCase):
    @patch.object(autostart, "_is_startup_approved", return_value=True)
    @patch.object(autostart, "_query_run_command", return_value=None)
    def test_read_disabled_when_run_missing(self, _approved, _run):
        self.assertFalse(autostart.read_enabled())

    @patch.object(autostart, "_is_startup_approved", return_value=True)
    @patch.object(autostart, "_query_run_command", return_value='"C:\\VoiceInput.exe"')
    def test_read_enabled_when_run_present_and_approved(self, _approved, _run):
        self.assertTrue(autostart.read_enabled())

    @patch.object(autostart, "_is_startup_approved", return_value=False)
    @patch.object(autostart, "_query_run_command", return_value='"C:\\VoiceInput.exe"')
    def test_read_disabled_when_startup_approved_blocks(self, _approved, _run):
        self.assertFalse(autostart.read_enabled())

    @patch.object(autostart, "sys")
    def test_read_disabled_on_non_windows(self, mock_sys):
        mock_sys.platform = "linux"
        self.assertFalse(autostart.read_enabled())


class AutostartApprovedTests(unittest.TestCase):
    def test_missing_approved_key_means_enabled(self):
        with patch("core.autostart.winreg.OpenKey", side_effect=FileNotFoundError):
            self.assertTrue(autostart._is_startup_approved())

    def test_disabled_prefix_0x03(self):
        key = MagicMock()
        key.__enter__ = MagicMock(return_value=key)
        key.__exit__ = MagicMock(return_value=False)
        with patch("core.autostart.winreg.OpenKey", return_value=key):
            with patch("core.autostart.winreg.QueryValueEx", return_value=(b"\x03" + b"\x00" * 11, 3)):
                self.assertFalse(autostart._is_startup_approved())

    def test_enabled_prefix_0x02(self):
        key = MagicMock()
        key.__enter__ = MagicMock(return_value=key)
        key.__exit__ = MagicMock(return_value=False)
        with patch("core.autostart.winreg.OpenKey", return_value=key):
            with patch("core.autostart.winreg.QueryValueEx", return_value=(autostart._ENABLED_APPROVED, 3)):
                self.assertTrue(autostart._is_startup_approved())


@unittest.skipUnless(sys.platform == "win32", "write_enabled uses winreg")
class AutostartWriteTests(unittest.TestCase):
    def test_write_enable_sets_run_and_approved(self):
        created = {}

        def create_key(hive, path):
            key = MagicMock()
            key.__enter__ = MagicMock(return_value=key)
            key.__exit__ = MagicMock(return_value=False)
            created[path] = key
            return key

        with patch("core.autostart.winreg.CreateKey", side_effect=create_key):
            with patch("core.autostart.winreg.SetValueEx") as set_value:
                autostart.write_enabled(True, '"C:\\VoiceInput.exe"')

        self.assertIn(autostart.RUN_KEY, created)
        self.assertIn(autostart.APPROVED_KEY, created)
        set_value.assert_any_call(
            created[autostart.RUN_KEY],
            autostart.VALUE_NAME,
            0,
            autostart.winreg.REG_SZ,
            '"C:\\VoiceInput.exe"',
        )
        set_value.assert_any_call(
            created[autostart.APPROVED_KEY],
            autostart.VALUE_NAME,
            0,
            autostart.winreg.REG_BINARY,
            autostart._ENABLED_APPROVED,
        )

    def test_write_disable_deletes_both_keys(self):
        with patch("core.autostart._delete_value") as delete_value:
            autostart.write_enabled(False, "")
        delete_value.assert_any_call(autostart.RUN_KEY)
        delete_value.assert_any_call(autostart.APPROVED_KEY)


if __name__ == "__main__":
    unittest.main()
