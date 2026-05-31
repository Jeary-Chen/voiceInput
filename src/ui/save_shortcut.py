"""Application-level Ctrl+S handling for dialog save buttons."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget


_FILTER_ATTR = "_voiceinput_ctrl_s_save_filter"


def _button_text(button: QPushButton) -> str:
    return "".join(str(button.text()).replace("&", "").split())


def _is_save_button(button: QPushButton) -> bool:
    text = _button_text(button)
    return "保存" in text and "不保存" not in text


def _is_clickable(button: QPushButton, window: QWidget) -> bool:
    return button.isVisibleTo(window) and button.isEnabledTo(window)


def _is_ctrl_s(event: QKeyEvent) -> bool:
    if event.key() != Qt.Key.Key_S:
        return False
    modifiers = event.modifiers()
    blocked = (
        Qt.KeyboardModifier.ShiftModifier
        | Qt.KeyboardModifier.AltModifier
        | Qt.KeyboardModifier.MetaModifier
    )
    return bool(modifiers & Qt.KeyboardModifier.ControlModifier) and not bool(
        modifiers & blocked
    )


def _is_hotkey_capture_window(window: QWidget | None) -> bool:
    return window is not None and window.__class__.__name__ == "_HotkeyDialog"


def _hotkey_is_ctrl_s(hotkey: str) -> bool:
    parts = {part.strip().lower() for part in (hotkey or "").split("+") if part.strip()}
    ctrl_parts = {"ctrl", "lctrl", "rctrl"}
    return "s" in parts and len(parts - ctrl_parts - {"s"}) == 0 and bool(
        parts & ctrl_parts
    )


class CtrlSSaveFilter(QObject):
    """Clicks the current window's clickable save button on Ctrl+S."""

    def __init__(self, hotkey_provider: Callable[[], str] | None = None, parent=None):
        super().__init__(parent)
        self._hotkey_provider = hotkey_provider or (lambda: "")

    def set_hotkey_provider(self, hotkey_provider: Callable[[], str] | None) -> None:
        self._hotkey_provider = hotkey_provider or (lambda: "")

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API name
        if not isinstance(event, QKeyEvent):
            return super().eventFilter(obj, event)
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return super().eventFilter(obj, event)
        if not _is_ctrl_s(event):
            return super().eventFilter(obj, event)
        if _hotkey_is_ctrl_s(self._hotkey_provider()):
            return super().eventFilter(obj, event)

        window = self._current_window()
        if _is_hotkey_capture_window(window):
            return super().eventFilter(obj, event)

        button = self.find_save_button(window)
        if button is None:
            return super().eventFilter(obj, event)

        event.accept()
        if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat():
            button.click()
        return True

    @staticmethod
    def _current_window() -> QWidget | None:
        active = QApplication.activeModalWidget() or QApplication.activeWindow()
        if active is not None and active.isActiveWindow():
            return active
        return None

    @staticmethod
    def find_save_button(window: QWidget | None) -> QPushButton | None:
        if window is None:
            return None
        buttons = [
            button
            for button in window.findChildren(QPushButton)
            if _is_save_button(button) and _is_clickable(button, window)
        ]
        if not buttons:
            return None

        exact = [button for button in buttons if _button_text(button) == "保存"]
        return exact[0] if exact else buttons[0]


def install_ctrl_s_save_shortcut(
    app: QApplication,
    hotkey_provider: Callable[[], str] | None = None,
) -> CtrlSSaveFilter:
    existing = getattr(app, _FILTER_ATTR, None)
    if isinstance(existing, CtrlSSaveFilter):
        existing.set_hotkey_provider(hotkey_provider)
        return existing
    event_filter = CtrlSSaveFilter(hotkey_provider, app)
    app.installEventFilter(event_filter)
    setattr(app, _FILTER_ATTR, event_filter)
    return event_filter
