"""User notification data — no Qt dependencies.

UI layer (:mod:`ui.notifier`) reads these specs and renders via system tray
balloon or styled modal dialog.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

APP_NOTIFICATION_TITLE = "VoiceInput"


class NotificationSeverity(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class NotificationPresentation(str, Enum):
    """Hint for the UI layer."""

    TRAY_BALLOON = "tray_balloon"
    MODAL = "modal"


@dataclass(frozen=True)
class NotificationSpec:
    body: str
    severity: NotificationSeverity = NotificationSeverity.INFO
    title: str = APP_NOTIFICATION_TITLE
    detail: str | None = None
    duration_ms: int = 5000
    presentation: NotificationPresentation = NotificationPresentation.TRAY_BALLOON
