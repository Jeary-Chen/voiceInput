"""Win32/Qt helpers for whether a widget's window is the system foreground."""
from __future__ import annotations

import ctypes
import sys

from PyQt6.QtWidgets import QWidget


def widget_is_foreground(widget: QWidget) -> bool:
    """Return True if *widget*'s top-level window is the Win32 foreground window."""
    if sys.platform != "win32":
        return widget.isActiveWindow()
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    if fg == hwnd:
        return True
    ga_root = 2
    return bool(user32.GetAncestor(fg, ga_root) == hwnd)
