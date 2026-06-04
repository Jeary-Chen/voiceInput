"""Simulate: recording in progress while opening settings/management dialogs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from PyQt6.QtWidgets import QDialog

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.tray import VoiceTray


def _tray_recording() -> VoiceTray:
    tray = VoiceTray.__new__(VoiceTray)
    tray._hotkey_pause_depth = 0
    tray._foreground_hotkey_pause_active = False
    tray._hotkey_listener_suppressed = False
    tray._hotkey_hold_active = False
    tray._fg_gated_dialogs = []
    tray._fg_hotkey_filter = MagicMock()
    tray._fg_hotkey_sync_timer = MagicMock()
    tray._fg_hotkey_poll = MagicMock()
    tray._fg_hotkey_poll.isActive.return_value = False
    tray._hotkey = MagicMock()
    tray._config = MagicMock()
    tray._config.hotkey = "f1"
    tray._engine = MagicMock()
    type(tray._engine).state = PropertyMock(return_value="recording")
    tray._mini = MagicMock()

    def _stop_listener():
        VoiceTray._clear_hotkey_hold_state(tray)
        tray._hotkey.stop_hotkey()
        tray._hotkey.wait(2000)

    tray._stop_hotkey_listener = _stop_listener
    tray._spawn_hotkey_thread = MagicMock()
    return tray


class RecordingContinuesTests(unittest.TestCase):
    def test_foreground_prompt_dialog_does_not_touch_engine(self):
        tray = _tray_recording()
        dlg = MagicMock(spec=QDialog)
        dlg.isVisible.return_value = True
        with patch("ui.tray.widget_is_foreground", return_value=True):
            tray._register_foreground_hotkey_gate(dlg)
            tray._sync_foreground_hotkey_gate()
        tray._engine.toggle_record.assert_not_called()
        tray._engine.cancel.assert_not_called()
        self.assertEqual(tray._engine.state, "recording")


class HotkeyWhileRecordingAndDialogTests(unittest.TestCase):
    def test_prompt_dialog_foreground_pauses_hotkey(self):
        tray = _tray_recording()
        dlg = MagicMock(spec=QDialog)
        dlg.isVisible.return_value = True
        with patch("ui.tray.widget_is_foreground", return_value=True):
            tray._register_foreground_hotkey_gate(dlg)
            tray._sync_foreground_hotkey_gate()
        self.assertTrue(tray._hotkey_listener_suppressed)
        self.assertTrue(tray._should_suppress_hotkey_listener())

    def test_prompt_dialog_background_resumes_hotkey(self):
        tray = _tray_recording()
        dlg = MagicMock(spec=QDialog)
        dlg.isVisible.return_value = True
        tray._fg_gated_dialogs = [dlg]
        tray._hotkey_listener_suppressed = True
        tray._foreground_hotkey_pause_active = True
        with patch("ui.tray.widget_is_foreground", return_value=False):
            tray._sync_foreground_hotkey_gate()
        tray._spawn_hotkey_thread.assert_called_once()
        self.assertFalse(tray._foreground_hotkey_pause_active)

    def test_modal_hotkey_config_pauses_entire_exec(self):
        tray = _tray_recording()
        tray._begin_hotkey_pause()
        self.assertTrue(tray._should_suppress_hotkey_listener())
        tray._end_hotkey_pause()
        tray._spawn_hotkey_thread.assert_called_once()

    def test_stop_via_tray_still_allowed_when_hotkey_paused(self):
        tray = _tray_recording()
        tray._audio = MagicMock()
        tray._faults = None
        tray._begin_hotkey_pause()
        VoiceTray._on_tray_click(tray)
        tray._engine.toggle_record.assert_called_once()


class HotkeyHoldCleanupTests(unittest.TestCase):
    def test_stop_listener_clears_hold_state(self):
        tray = _tray_recording()
        tray._hotkey_hold_active = True
        tray._clear_hotkey_hold_state()
        self.assertFalse(tray._hotkey_hold_active)
        tray._mini.stop_hotkey_hold.assert_called_once()

    def test_foreground_pause_clears_hold(self):
        tray = _tray_recording()
        tray._hotkey_hold_active = True
        tray._set_foreground_hotkey_pause(True)
        self.assertFalse(tray._hotkey_hold_active)
        tray._mini.stop_hotkey_hold.assert_called()


if __name__ == "__main__":
    unittest.main()
