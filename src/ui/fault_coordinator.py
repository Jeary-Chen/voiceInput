"""
Unified fault dispatch — single entry for all user-visible fault UX.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject

from core.fault_notifications import spec_for_fault
from core.fault_policy import (
    BalloonMode,
    FAULT_ICON_PRIORITY,
    FAULT_POLICIES,
    FaultPolicy,
    TrayIconProfile,
)
from core.faults import FaultEvent, FaultKind, FaultSource, classify_fault
from core.log import logger

if TYPE_CHECKING:
    from config import Config
    from core.config_sync import ConfigSync
    from core.engine import VoiceEngine
    from ui.config_dialog import ConfigFaultHandler
    from ui.notifier import Notifier
    from ui.tray import VoiceTray

_TAG = "[Fault]"


class FaultCoordinator(QObject):
    def __init__(
        self,
        engine: VoiceEngine,
        tray: VoiceTray,
        config_sync: ConfigSync | None = None,
        notifier: Notifier | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._tray = tray
        self._config_sync = config_sync
        self._notifier = notifier
        self._config_fault: ConfigFaultHandler | None = None
        self._active: set[FaultKind] = set()
        self._last_notify_at: dict[FaultKind, float] = {}

        engine.error_occurred.connect(self._on_engine_error)
        engine.mic_unavailable.connect(self._on_mic_unavailable)
        engine.transcription_done.connect(self._on_transcription_ok)
        engine.state_changed.connect(self._on_engine_state)

        if config_sync is not None:
            config_sync.config_disk_fault.connect(self._on_config_disk_fault)
            config_sync.config_disk_recovered.connect(self._on_config_disk_recovered)
            config_sync.apply_started.connect(
                lambda: self.set_active(FaultKind.CONFIG_BUSY, True, notify=False)
            )
            config_sync.apply_finished.connect(
                lambda: self.set_active(FaultKind.CONFIG_BUSY, False, notify=False)
            )

    def set_config_fault_handler(self, handler: ConfigFaultHandler) -> None:
        self._config_fault = handler

    def initialize(self, config: Config) -> None:
        if not config.api_key:
            self.set_active(FaultKind.CREDENTIAL, True, notify=False)

    def bind_tray(self) -> None:
        self._tray.set_fault_coordinator(self)

    def is_active(self, kind: FaultKind) -> bool:
        return kind in self._active

    def set_active(
        self,
        kind: FaultKind,
        active: bool,
        *,
        notify: bool = True,
    ) -> None:
        policy = FAULT_POLICIES[kind]
        if active:
            if kind in self._active:
                return
            self._active.add(kind)
            self._tray.refresh_idle_icon()
            if notify and kind is not FaultKind.CONFIG_DISK:
                self._notify_for_kind(kind)
            return

        if kind not in self._active:
            return
        self._active.discard(kind)
        self._tray.refresh_idle_icon()

    def sync_credential_from_config(self) -> None:
        self.set_active(
            FaultKind.CREDENTIAL,
            not bool(self._tray.config.api_key),
            notify=False,
        )

    def sync_device_from_recorder(self) -> None:
        """Reconcile device fault presentation with the recorder's live state."""
        recorder = self._tray.engine.recorder
        if recorder.no_device:
            self._tray.refresh_idle_icon()
            return

        if FaultKind.DEVICE in self._active:
            self.set_active(FaultKind.DEVICE, False, notify=False)
        else:
            self._tray.refresh_idle_icon()

    def guard_recording_start(self) -> bool:
        if self._present_config_disk_if_active():
            return True

        if self._config_sync is not None and self._config_sync.blocks_recording:
            self.set_active(FaultKind.CONFIG_BUSY, True, notify=False)
            self._notify_for_kind(FaultKind.CONFIG_BUSY)
            return True

        if not self._tray.config.api_key:
            self._tray.open_api_key_dialog()
            return True

        if (
            FaultKind.CREDENTIAL in self._active
            and FAULT_POLICIES[FaultKind.CREDENTIAL].block_recording_start
        ):
            self._notify_for_kind(FaultKind.CREDENTIAL)
            return True

        return False

    def on_config_disk_save_blocked(self) -> None:
        """User tried to persist settings while config.json is unreadable."""
        self._present_config_disk_if_active()

    def _present_config_disk_if_active(self) -> bool:
        if FaultKind.CONFIG_DISK not in self._active:
            return False
        if self._config_fault is not None:
            self._config_fault.present()
        return True

    def idle_icon_profile(self) -> tuple[TrayIconProfile, str] | None:
        for kind in FAULT_ICON_PRIORITY:
            if kind not in self._active:
                continue
            policy = FAULT_POLICIES[kind]
            tooltip = self._tooltip_for(kind, policy)
            if tooltip is not None:
                return policy.tray_icon_profile, tooltip

        if self._tray.engine.recorder.no_device:
            return TrayIconProfile.DEVICE, "未找到输入设备"

        return None

    def _tooltip_for(self, kind: FaultKind, policy: FaultPolicy) -> str | None:
        if kind is FaultKind.CREDENTIAL:
            if not self._tray.config.api_key:
                return "API Key 未配置，右键点击配置"
            return "API Key 无效，右键点击配置"
        return policy.icon_tooltip

    def _on_config_disk_fault(self) -> None:
        self.set_active(FaultKind.CONFIG_DISK, True, notify=False)

    def _on_config_disk_recovered(self) -> None:
        self.set_active(FaultKind.CONFIG_DISK, False, notify=False)
        if self._config_fault is not None:
            self._config_fault.stop_watching()
        logger.info(f"{_TAG} Config disk fault cleared")

    def _on_engine_error(self, message: str) -> None:
        if not (message or "").strip():
            return
        self._dispatch(classify_fault(FaultSource.ENGINE, message))

    def _on_mic_unavailable(self, message: str) -> None:
        self._dispatch(classify_fault(FaultSource.MIC, message))

    def _on_transcription_ok(self, _text: str) -> None:
        if FaultKind.CREDENTIAL in self._active:
            self.set_active(FaultKind.CREDENTIAL, False, notify=False)
            logger.debug(f"{_TAG} Cleared credential fault after successful transcription")

    def _on_engine_state(self, state: str) -> None:
        if state == "recording":
            self.set_active(FaultKind.DEVICE, False, notify=False)

    def _dispatch(self, event: FaultEvent) -> None:
        policy = FAULT_POLICIES[event.kind]
        logger.debug(
            f"{_TAG} kind={event.kind.name} source={event.source.name} "
            f"status={event.http_status} msg={event.message[:80]!r}"
        )
        self._apply_policy(event, policy)

    def _apply_policy(self, event: FaultEvent, policy: FaultPolicy) -> None:
        for cleared in policy.clears:
            self.set_active(cleared, False, notify=False)

        if policy.activate_self:
            if event.kind is FaultKind.DEVICE:
                no_device = self._tray.engine.recorder.no_device
                if no_device:
                    logger.warning(f"{_TAG} Device: {event.message} (no input device)")
                else:
                    self.set_active(FaultKind.DEVICE, True, notify=False)
                    logger.warning(f"{_TAG} Device: {event.message}")
                self._tray.request_device_refresh()
            else:
                self.set_active(event.kind, True, notify=False)

        self._tray.refresh_idle_icon()
        self._notify_for_event(event, policy)

    def _notify_for_event(self, event: FaultEvent, policy: FaultPolicy) -> None:
        if policy.balloon_mode is BalloonMode.NONE:
            if policy.log_suppressed_as_info:
                logger.info(f"{_TAG} Suppressed notification (kind={event.kind.name})")
            return
        if not self._cooldown_allows(event.kind, policy):
            return
        spec = spec_for_fault(event.kind, policy, event_message=event.message)
        if spec is not None:
            self._publish(spec)

    def _notify_for_kind(self, kind: FaultKind) -> None:
        if kind is FaultKind.CONFIG_DISK:
            return
        policy = FAULT_POLICIES[kind]
        if not self._cooldown_allows(kind, policy):
            return
        spec = spec_for_fault(kind, policy)
        if spec is not None:
            self._publish(spec)

    def _publish(self, spec) -> None:
        if self._notifier is not None:
            self._notifier.show(spec)
        else:
            self._tray.show_notification_spec(spec)

    def _cooldown_allows(self, kind: FaultKind, policy: FaultPolicy) -> bool:
        if policy.balloon_cooldown_sec <= 0:
            return True
        now = time.monotonic()
        last = self._last_notify_at.get(kind, 0.0)
        if now - last < policy.balloon_cooldown_sec:
            return False
        self._last_notify_at[kind] = now
        return True

    @property
    def credential_fault(self) -> bool:
        return FaultKind.CREDENTIAL in self._active
