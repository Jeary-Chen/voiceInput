"""
Per-fault-kind presentation policy (no Qt dependencies).

Consumed only by :mod:`ui.fault_coordinator`. Edit this table to change UX
without touching tray or engine code. See ``docs/fault-handling.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from core.faults import FaultKind


class TrayIconProfile(Enum):
    NONE = auto()
    CREDENTIAL = auto()
    DEVICE = auto()


class BalloonMode(Enum):
    NONE = auto()
    GENERIC_KEY = auto()
    MESSAGE = auto()
    PREFIX_MESSAGE = auto()


@dataclass(frozen=True)
class FaultPolicy:
    persist_credential_fault: bool
    persist_device_fault: bool
    clear_credential_fault: bool
    tray_icon_profile: TrayIconProfile
    balloon_mode: BalloonMode
    block_hotkey_when_ready: bool
    balloon_cooldown_sec: float
    log_suppressed_as_info: bool = False


FAULT_POLICIES: dict[FaultKind, FaultPolicy] = {
    FaultKind.CREDENTIAL: FaultPolicy(
        persist_credential_fault=True,
        persist_device_fault=False,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.CREDENTIAL,
        balloon_mode=BalloonMode.GENERIC_KEY,
        block_hotkey_when_ready=True,
        balloon_cooldown_sec=12.0,
    ),
    FaultKind.API_REMOTE: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=False,
        clear_credential_fault=True,
        tray_icon_profile=TrayIconProfile.NONE,
        balloon_mode=BalloonMode.MESSAGE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=12.0,
    ),
    FaultKind.CAPTURE: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=False,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.NONE,
        balloon_mode=BalloonMode.PREFIX_MESSAGE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=0.0,
    ),
    FaultKind.SPEECH_EMPTY: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=False,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.NONE,
        balloon_mode=BalloonMode.NONE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=0.0,
        log_suppressed_as_info=True,
    ),
    FaultKind.SPEECH_SILENT: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=False,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.NONE,
        balloon_mode=BalloonMode.NONE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=0.0,
        log_suppressed_as_info=True,
    ),
    FaultKind.DEVICE: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=True,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.DEVICE,
        balloon_mode=BalloonMode.MESSAGE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=0.0,
    ),
    FaultKind.GENERAL: FaultPolicy(
        persist_credential_fault=False,
        persist_device_fault=False,
        clear_credential_fault=False,
        tray_icon_profile=TrayIconProfile.NONE,
        balloon_mode=BalloonMode.PREFIX_MESSAGE,
        block_hotkey_when_ready=False,
        balloon_cooldown_sec=0.0,
    ),
}
