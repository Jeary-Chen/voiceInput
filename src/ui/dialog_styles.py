"""Dialog styling facade — tokens, components, and apply helpers.

Import from this module in feature code. Internal split:

  dialog_tokens     raw hex / spacing / typography
  dialog_components QSS built from tokens
  dialog_styles     re-exports + apply_dialog_chrome()
"""

from __future__ import annotations

import ctypes
import sys

from PyQt6.QtWidgets import QWidget

from ui import dialog_components as _components
from ui import dialog_tokens as _tokens

# Star-import skips underscore names; re-export the dialog API explicitly.
for _mod in (_tokens, _components):
    for _name in dir(_mod):
        if _name.startswith("_DIALOG") or _name == "COLOR_TEXT_PRIMARY":
            globals()[_name] = getattr(_mod, _name)

del _mod, _name, _tokens, _components

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY = 19
_DWMWA_BORDER_COLOR = 34
_DIALOG_DARK_BORDER_COLORREF = 0x001E1E1E


def _dwm_set_window_attribute(hwnd: int, attribute: int, value: int) -> int:
    c_value = ctypes.c_int(value)
    return ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd),
        ctypes.c_uint(attribute),
        ctypes.byref(c_value),
        ctypes.sizeof(c_value),
    )


def _apply_windows_dark_frame(widget: QWidget) -> None:
    if sys.platform != "win32":
        return

    try:
        hwnd = int(widget.winId())
    except Exception:
        return

    if not hwnd:
        return

    try:
        result = _dwm_set_window_attribute(
            hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1,
        )
        if result != 0:
            _dwm_set_window_attribute(
                hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_LEGACY, 1,
            )
        _dwm_set_window_attribute(
            hwnd, _DWMWA_BORDER_COLOR, _DIALOG_DARK_BORDER_COLORREF,
        )
    except Exception:
        return


def apply_dialog_chrome(widget: QWidget) -> None:
    """Apply shared dialog shell (background, labels, tooltips)."""
    widget.setStyleSheet(_DIALOG_CHROME_QSS)
    _apply_windows_dark_frame(widget)
