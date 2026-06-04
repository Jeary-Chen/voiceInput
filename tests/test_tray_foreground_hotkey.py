"""Tests for foreground-gated recorder hotkey pause on modeless dialogs."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QDialog

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.tray import VoiceTray


def _minimal_tray() -> VoiceTray:
    tray = VoiceTray.__new__(VoiceTray)
    tray._hotkey_pause_depth = 0
    tray._foreground_hotkey_pause_active = False
    tray._hotkey_listener_suppressed = False
    tray._fg_gated_dialogs = []
    tray._fg_hotkey_filter = MagicMock()
    tray._fg_hotkey_sync_timer = MagicMock()
    tray._fg_hotkey_poll = MagicMock()
    tray._fg_hotkey_poll.isActive.return_value = False
    tray._stop_hotkey_listener = MagicMock()
    tray._spawn_hotkey_thread = MagicMock()
    return tray


class TrayForegroundHotkeyTests(unittest.TestCase):
    def test_modal_pause_still_suppresses_listener(self):
        tray = _minimal_tray()
        tray._begin_hotkey_pause()
        tray._stop_hotkey_listener.assert_called_once()
        self.assertTrue(tray._hotkey_listener_suppressed)

        tray._end_hotkey_pause()
        tray._spawn_hotkey_thread.assert_called_once()

    def test_foreground_gate_pauses_only_when_dialog_foreground(self):
        tray = _minimal_tray()
        dlg = MagicMock(spec=QDialog)
        dlg.isVisible.return_value = True

        with patch("ui.tray.widget_is_foreground", return_value=True):
            tray._register_foreground_hotkey_gate(dlg)
            tray._sync_foreground_hotkey_gate()

        tray._stop_hotkey_listener.assert_called_once()
        self.assertTrue(tray._foreground_hotkey_pause_active)

        tray._stop_hotkey_listener.reset_mock()
        with patch("ui.tray.widget_is_foreground", return_value=False):
            tray._sync_foreground_hotkey_gate()

        tray._spawn_hotkey_thread.assert_called_once()
        self.assertFalse(tray._foreground_hotkey_pause_active)

    def test_unregister_stops_poll_when_no_dialogs(self):
        tray = _minimal_tray()
        dlg = MagicMock(spec=QDialog)
        tray._fg_gated_dialogs = [dlg]
        tray._foreground_hotkey_pause_active = True
        tray._hotkey_listener_suppressed = True

        tray._unregister_foreground_hotkey_gate(dlg)
        tray._sync_foreground_hotkey_gate()

        tray._fg_hotkey_poll.stop.assert_called()
        self.assertFalse(tray._foreground_hotkey_pause_active)
        tray._spawn_hotkey_thread.assert_called_once()


if __name__ == "__main__":
    unittest.main()
