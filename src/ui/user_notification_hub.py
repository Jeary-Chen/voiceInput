"""
Deprecated thin wrapper — use :class:`ui.fault_coordinator.FaultCoordinator`.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject

from core.engine import VoiceEngine
from ui.fault_coordinator import FaultCoordinator
from ui.tray import VoiceTray


class UserNotificationHub(FaultCoordinator):
    """Backward-compatible name; behavior is :class:`FaultCoordinator`."""

    def __init__(
        self,
        engine: VoiceEngine,
        tray: VoiceTray,
        parent: QObject | None = None,
    ):
        super().__init__(engine, tray, parent=parent)
