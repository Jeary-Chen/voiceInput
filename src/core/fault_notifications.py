"""Map fault policy + event text → :class:`NotificationSpec` (data layer only)."""
from __future__ import annotations

from core.fault_policy import BalloonMode, FaultKind, FaultPolicy
from core.faults import single_line_preview
from core.notification_spec import (
    APP_NOTIFICATION_TITLE,
    NotificationSeverity,
    NotificationSpec,
)

_CREDENTIAL_BODY = "API Key 不可用，请右键点击托盘图标重新配置"

_DEFAULT_DURATION_MS = 5000
_ERROR_DURATION_MS = 8000
_INFO_SHORT_MS = 2000


def _severity_for(kind: FaultKind, policy: FaultPolicy) -> NotificationSeverity:
    if policy.notification_severity is not None:
        return policy.notification_severity
    if kind is FaultKind.CONFIG_BUSY:
        return NotificationSeverity.INFO
    if kind in (FaultKind.CREDENTIAL, FaultKind.CONFIG_DISK):
        return NotificationSeverity.ERROR
    if kind in (FaultKind.CAPTURE, FaultKind.GENERAL, FaultKind.DEVICE):
        return NotificationSeverity.WARNING
    return NotificationSeverity.WARNING


def _duration_for(kind: FaultKind, policy: FaultPolicy) -> int:
    if policy.notification_duration_ms is not None:
        return policy.notification_duration_ms
    if kind is FaultKind.CONFIG_BUSY:
        return _INFO_SHORT_MS
    if kind in (FaultKind.CREDENTIAL, FaultKind.CONFIG_DISK, FaultKind.CAPTURE, FaultKind.GENERAL):
        return _ERROR_DURATION_MS
    return _DEFAULT_DURATION_MS


def spec_for_fault(
    kind: FaultKind,
    policy: FaultPolicy,
    *,
    event_message: str | None = None,
) -> NotificationSpec | None:
    """Build a notification spec from fault policy. Returns None when suppressed."""
    mode = policy.balloon_mode
    if mode is BalloonMode.NONE:
        return None

    severity = _severity_for(kind, policy)
    duration_ms = _duration_for(kind, policy)

    if mode is BalloonMode.STATIC:
        return NotificationSpec(
            title=APP_NOTIFICATION_TITLE,
            body=policy.balloon_message or "",
            severity=severity,
            duration_ms=duration_ms,
        )

    if mode is BalloonMode.GENERIC_KEY:
        body = _CREDENTIAL_BODY
        if event_message and event_message.strip():
            body = single_line_preview(event_message)
        return NotificationSpec(
            title=APP_NOTIFICATION_TITLE,
            body=body,
            severity=NotificationSeverity.ERROR,
            duration_ms=_ERROR_DURATION_MS,
        )

    if mode is BalloonMode.MESSAGE:
        text = single_line_preview(event_message or "")
        if not text:
            return None
        return NotificationSpec(
            title=APP_NOTIFICATION_TITLE,
            body=text,
            severity=severity,
            duration_ms=duration_ms,
        )

    if mode is BalloonMode.PREFIX_MESSAGE:
        text = single_line_preview(event_message or "")
        if not text:
            return None
        return NotificationSpec(
            title=APP_NOTIFICATION_TITLE,
            body=f"处理失败：{text}",
            severity=severity,
            duration_ms=duration_ms,
        )

    return None
