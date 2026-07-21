"""Text delivery to the focused app / clipboard.

Modes (config keys from core.output_mode):
  copy        → copy_only()
  paste       → paste_only()       — Unicode SendInput, no clipboard
  paste_copy  → paste_and_copy()   — clipboard then type
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import pyperclip

from core.log import logger
from core.output_mode import (
    DELIVER_COPIED,
    DELIVER_FAILED,
    DELIVER_PASTED,
    DELIVER_PASTED_COPIED,
    OUTPUT_MODE_COPY,
    OUTPUT_MODE_PASTE,
    normalize_output_mode,
)


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


# MOUSEINPUT / HARDWAREINPUT：撑起 Win32 INPUT 联合体布局（SendInput 要求正确尺寸）。
class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUNION(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", _INPUTUNION))


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT


def _ki_event(*, vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(
        wVk=vk,
        wScan=scan,
        dwFlags=flags,
        time=0,
        dwExtraInfo=ULONG_PTR(0),
    )
    return inp


def _unicode_char_events(ch: str) -> list[INPUT]:
    """Emit one character as Unicode text (never as a virtual-key chord)."""
    events: list[INPUT] = []
    encoded = ch.encode("utf-16-le")
    for off in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[off : off + 2], "little")
        events.append(_ki_event(scan=unit, flags=KEYEVENTF_UNICODE))
        events.append(
            _ki_event(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        )
    return events


def _events_for_text(text: str) -> list[INPUT]:
    """Build SendInput events for *text*.

    Newlines and tabs are injected as Unicode U+000A / U+0009 so host apps
    treat them as characters (e.g. line break in an editor) instead of the
    physical Enter/Tab keys that often mean "send" in chat UIs.
    Lone ``\\r`` from Windows CRLF is dropped; ``\\n`` carries the break.
    """
    events: list[INPUT] = []
    for ch in text:
        if ch == "\r":
            continue
        events.extend(_unicode_char_events(ch))
    return events


def type_unicode(text: str, *, chunk_chars: int = 64) -> bool:
    """向当前键盘焦点键入文本。成功送出全部事件时返回 True。"""
    events = _events_for_text(text)
    if not events:
        return True
    size = ctypes.sizeof(INPUT)
    step = max(2, chunk_chars * 2)
    sent = 0
    for start in range(0, len(events), step):
        batch = events[start : start + step]
        arr = (INPUT * len(batch))(*batch)
        n = int(_SendInput(len(batch), arr, size))
        sent += n
        if n != len(batch):
            logger.warning(
                f"[Injector] SendInput partial failure: "
                f"sent {sent}/{len(events)} events"
            )
            return False
    return True


class TextInjector:
    def deliver(self, text: str, mode: str) -> str:
        """Dispatch by output_mode. Returns a DELIVER_* action token."""
        if not text:
            return DELIVER_FAILED
        mode = normalize_output_mode(mode)
        if mode == OUTPUT_MODE_COPY:
            return DELIVER_COPIED if self.copy_only(text) else DELIVER_FAILED
        if mode == OUTPUT_MODE_PASTE:
            return DELIVER_PASTED if self.paste_only(text) else DELIVER_FAILED
        return DELIVER_PASTED_COPIED if self.paste_and_copy(text) else DELIVER_FAILED

    def copy_only(self, text: str) -> bool:
        if not text:
            return False
        try:
            pyperclip.copy(text)
            return True
        except Exception as exc:
            logger.warning(f"[Injector] copy_only failed: {exc}")
            return False

    def paste_only(self, text: str) -> bool:
        """键入到输入焦点，不读写剪贴板。"""
        if not text:
            return False
        try:
            return type_unicode(text)
        except Exception as exc:
            logger.warning(f"[Injector] paste_only failed: {exc}")
            return False

    def paste_and_copy(self, text: str) -> bool:
        """写入剪贴板并键入到输入焦点（剪贴板保留文本）。"""
        if not text:
            return False
        try:
            pyperclip.copy(text)
        except Exception as exc:
            logger.warning(f"[Injector] paste_and_copy clipboard failed: {exc}")
            return False
        try:
            return type_unicode(text)
        except Exception as exc:
            logger.warning(f"[Injector] paste_and_copy type failed: {exc}")
            return True
