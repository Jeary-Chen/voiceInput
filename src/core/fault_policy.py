"""
Per-fault-kind presentation policy (no Qt dependencies).

Consumed only by :mod:`ui.fault_coordinator`. Edit this table to change UX
without touching tray or engine code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from core.faults import FaultKind
from core.notification_spec import NotificationSeverity


class TrayIconProfile(Enum):
    NONE = auto()
    CREDENTIAL = auto()
    CONFIG = auto()
    DEVICE = auto()


class BalloonMode(Enum):
    NONE = auto()
    GENERIC_KEY = auto()
    MESSAGE = auto()
    PREFIX_MESSAGE = auto()
    STATIC = auto()


@dataclass(frozen=True)
class FaultPolicy:
    """How to react when a fault event of the matching kind is dispatched."""

    activate_self: bool = False
    clears: frozenset[FaultKind] = field(default_factory=frozenset)
    tray_icon_profile: TrayIconProfile = TrayIconProfile.NONE
    balloon_mode: BalloonMode = BalloonMode.NONE
    block_recording_start: bool = False
    balloon_cooldown_sec: float = 0.0
    balloon_message: str | None = None
    icon_tooltip: str | None = None
    log_suppressed_as_info: bool = False
    notification_severity: NotificationSeverity | None = None
    notification_duration_ms: int | None = None


FAULT_POLICIES: dict[FaultKind, FaultPolicy] = {
    FaultKind.CREDENTIAL: FaultPolicy(
        activate_self=True,
        tray_icon_profile=TrayIconProfile.CREDENTIAL,
        balloon_mode=BalloonMode.GENERIC_KEY,
        block_recording_start=True,
        balloon_cooldown_sec=12.0,
    ),
    FaultKind.CONFIG_DISK: FaultPolicy(
        activate_self=True,
        tray_icon_profile=TrayIconProfile.CONFIG,
        balloon_mode=BalloonMode.NONE,
        block_recording_start=True,
        icon_tooltip="配置文件异常，请修复后使用",
    ),
    FaultKind.CONFIG_BUSY: FaultPolicy(
        balloon_mode=BalloonMode.STATIC,
        block_recording_start=True,
        balloon_cooldown_sec=1.5,
        balloon_message="正在更新配置，请稍后再试",
        notification_severity=NotificationSeverity.INFO,
        notification_duration_ms=1500,
    ),
    FaultKind.API_REMOTE: FaultPolicy(
        clears=frozenset({FaultKind.CREDENTIAL}),
        balloon_mode=BalloonMode.MESSAGE,
        balloon_cooldown_sec=12.0,
    ),
    FaultKind.CAPTURE: FaultPolicy(
        balloon_mode=BalloonMode.PREFIX_MESSAGE,
    ),
    FaultKind.SPEECH_EMPTY: FaultPolicy(
        log_suppressed_as_info=True,
    ),
    FaultKind.SPEECH_SILENT: FaultPolicy(
        log_suppressed_as_info=True,
    ),
    FaultKind.DEVICE: FaultPolicy(
        activate_self=True,
        tray_icon_profile=TrayIconProfile.DEVICE,
        balloon_mode=BalloonMode.MESSAGE,
        icon_tooltip="麦克风不可用，右键切换输入设备",
    ),
    FaultKind.GENERAL: FaultPolicy(
        balloon_mode=BalloonMode.PREFIX_MESSAGE,
    ),
}

# Highest priority first when choosing idle tray icon / tooltip.
FAULT_ICON_PRIORITY: tuple[FaultKind, ...] = (
    FaultKind.CONFIG_DISK,
    FaultKind.CREDENTIAL,
    FaultKind.DEVICE,
)
