"""Probe whether the foreground UI can accept typed text.

Used by TextInjector delivery policy — not by tray/hotkey (those use
ui.window_focus for *our* windows only).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass


@dataclass(frozen=True)
class FocusTarget:
    """Snapshot of the foreground thread's keyboard focus."""

    hwnd_foreground: int
    hwnd_focus: int
    hwnd_caret: int
    focus_process_id: int

    @property
    def has_keyboard_focus(self) -> bool:
        return self.hwnd_focus != 0


def _guithreadinfo_type():
    class GUITHREADINFO(ctypes.Structure):
        _fields_ = (
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        )

    return GUITHREADINFO


def probe_focus_target() -> FocusTarget | None:
    """Return foreground keyboard-focus info, or None if unavailable."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32

    hwnd_fg = int(user32.GetForegroundWindow() or 0)
    if not hwnd_fg:
        return None

    tid = user32.GetWindowThreadProcessId(hwnd_fg, None)
    if not tid:
        return None

    info_t = _guithreadinfo_type()
    info = info_t()
    info.cbSize = ctypes.sizeof(info_t)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None

    hwnd_focus = int(info.hwndFocus or 0)
    pid = wintypes.DWORD(0)
    if hwnd_focus:
        user32.GetWindowThreadProcessId(hwnd_focus, ctypes.byref(pid))
    else:
        user32.GetWindowThreadProcessId(hwnd_fg, ctypes.byref(pid))

    return FocusTarget(
        hwnd_foreground=hwnd_fg,
        hwnd_focus=hwnd_focus,
        hwnd_caret=int(info.hwndCaret or 0),
        focus_process_id=int(pid.value),
    )


def can_accept_typed_text() -> bool:
    """True when an external window holds keyboard focus we can type into.

    Same-process focus (our dialogs / UI) is rejected so delivery falls back
    to the clipboard instead of typing into VoiceInput itself.
    """
    target = probe_focus_target()
    if target is None or not target.has_keyboard_focus:
        return False
    if sys.platform == "win32":
        try:
            self_pid = int(ctypes.windll.kernel32.GetCurrentProcessId())
        except Exception:
            self_pid = 0
        if self_pid and target.focus_process_id == self_pid:
            return False
    return True
