"""
Unified fault dispatch — the only subscriber for engine fault signals.

Pipeline: classify_fault → FAULT_POLICIES → tray side-effects.

See docs/fault-handling.md.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QSystemTrayIcon

from core.engine import VoiceEngine
from core.fault_policy import BalloonMode, FAULT_POLICIES, FaultPolicy
from core.faults import FaultEvent, FaultKind, FaultSource, classify_fault, single_line_preview
from core.log import logger
from ui.tray import VoiceTray

_TAG = "[Fault]"


class FaultCoordinator(QObject):
    def __init__(
        self,
        engine: VoiceEngine,
        tray: VoiceTray,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._tray = tray
        self._last_api_balloon_at = 0.0
        engine.error_occurred.connect(self._on_engine_error)
        engine.mic_unavailable.connect(self._on_mic_unavailable)
        engine.transcription_done.connect(self._on_transcription_ok)

    def _on_engine_error(self, message: str) -> None:
        if not (message or "").strip():
            return
        self._dispatch(classify_fault(FaultSource.ENGINE, message))

    def _on_mic_unavailable(self, message: str) -> None:
        self._dispatch(classify_fault(FaultSource.MIC, message))

    def _on_transcription_ok(self, _text: str) -> None:
        if self._tray.credential_fault:
            self._tray.set_credential_fault(False)
            self._tray.refresh_idle_icon()
            logger.debug(f"{_TAG} Cleared credential fault after successful transcription")

    def _dispatch(self, event: FaultEvent) -> None:
        policy = FAULT_POLICIES[event.kind]
        logger.debug(
            f"{_TAG} kind={event.kind.name} source={event.source.name} "
            f"status={event.http_status} msg={event.message[:80]!r}"
        )
        self._apply_policy(event, policy)

    def _apply_policy(self, event: FaultEvent, policy: FaultPolicy) -> None:
        self._apply_tray_flags(event, policy)
        self._tray.refresh_idle_icon()
        self._apply_balloon(event, policy)

    def _apply_tray_flags(self, event: FaultEvent, policy: FaultPolicy) -> None:
        if policy.clear_credential_fault:
            self._tray.set_credential_fault(False)
        if policy.persist_credential_fault:
            self._tray.set_credential_fault(True)

        if event.kind == FaultKind.DEVICE:
            no_device = self._tray.engine.recorder.no_device
            self._tray.set_device_fault(not no_device)
            logger.warning(f"{_TAG} Device: {event.message}")
            self._tray.request_device_refresh()

    def _apply_balloon(self, event: FaultEvent, policy: FaultPolicy) -> None:
        if policy.balloon_mode == BalloonMode.NONE:
            if policy.log_suppressed_as_info:
                logger.info(f"{_TAG} Suppressed balloon (kind={event.kind.name})")
            return

        if not self._cooldown_allows(policy):
            return

        if policy.balloon_mode == BalloonMode.GENERIC_KEY:
            self._tray.show_api_error_notice(None)
        elif policy.balloon_mode == BalloonMode.MESSAGE:
            self._show_message_balloon(event)
        elif policy.balloon_mode == BalloonMode.PREFIX_MESSAGE:
            body = f"处理失败：{single_line_preview(event.message)}"
            self._tray.show_tray_message(
                "VoiceInput",
                body,
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )

    def _cooldown_allows(self, policy: FaultPolicy) -> bool:
        if policy.balloon_cooldown_sec <= 0:
            return True
        now = time.monotonic()
        if now - self._last_api_balloon_at < policy.balloon_cooldown_sec:
            return False
        self._last_api_balloon_at = now
        return True

    def _show_message_balloon(self, event: FaultEvent) -> None:
        text = single_line_preview(event.message)
        if event.kind == FaultKind.DEVICE:
            self._tray.show_tray_message(
                "VoiceInput",
                text,
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
        else:
            self._tray.show_api_error_notice(text)
