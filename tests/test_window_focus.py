"""Tests for ui.window_focus.widget_is_foreground."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.window_focus import widget_is_foreground


@unittest.skipIf(sys.platform != "win32", "Win32 foreground API")
class WindowFocusWin32Tests(unittest.TestCase):
    def test_widget_is_foreground_same_hwnd(self):
        widget = MagicMock()
        widget.winId.return_value = 100
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 100
        with patch("ui.window_focus.ctypes.windll.user32", user32):
            self.assertTrue(widget_is_foreground(widget))
        user32.GetAncestor.assert_not_called()

    def test_widget_is_foreground_via_root_ancestor(self):
        widget = MagicMock()
        widget.winId.return_value = 200
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 50
        user32.GetAncestor.return_value = 200
        with patch("ui.window_focus.ctypes.windll.user32", user32):
            self.assertTrue(widget_is_foreground(widget))
        user32.GetAncestor.assert_called_once_with(50, 2)

    def test_widget_is_foreground_not_active(self):
        widget = MagicMock()
        widget.winId.return_value = 200
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 99
        user32.GetAncestor.return_value = 88
        with patch("ui.window_focus.ctypes.windll.user32", user32):
            self.assertFalse(widget_is_foreground(widget))


class WindowFocusNonWin32Tests(unittest.TestCase):
    def test_non_win32_uses_active_window(self):
        widget = MagicMock()
        widget.isActiveWindow.return_value = True
        with patch("ui.window_focus.sys.platform", "darwin"):
            self.assertTrue(widget_is_foreground(widget))
        widget.isActiveWindow.assert_called_once()


if __name__ == "__main__":
    unittest.main()
