"""Notification renderer — consumes :class:`core.notification_spec.NotificationSpec`.

Transient notices use the OS tray balloon; modals use :mod:`ui.styled_message_box`.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QSystemTrayIcon

from core.notification_spec import NotificationPresentation, NotificationSpec
from ui.styled_message_box import show_styled_message_box


class Notifier(QObject):
    """Renders notification specs: system tray balloon or styled modal."""

    def __init__(
        self,
        tray: QSystemTrayIcon | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._tray = tray

    def show(self, spec: NotificationSpec) -> None:
        if spec.presentation is NotificationPresentation.MODAL:
            show_styled_message_box(
                parent=None,
                title=spec.title,
                text=spec.body,
                informative_text=spec.detail,
                severity=spec.severity,
            )
            return
        self._show_tray_balloon(spec)

    def _show_tray_balloon(self, spec: NotificationSpec) -> None:
        if self._tray is None:
            return
        icon = QSystemTrayIcon.MessageIcon.Information
        if spec.severity.value == "error":
            icon = QSystemTrayIcon.MessageIcon.Critical
        elif spec.severity.value == "warning":
            icon = QSystemTrayIcon.MessageIcon.Warning
        self._tray.showMessage(
            spec.title,
            spec.body,
            icon,
            spec.duration_ms,
        )
