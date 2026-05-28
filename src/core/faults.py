"""
Fault classification for VoiceInput user-visible errors.

Engine/workers emit plain strings; this module maps them to :class:`FaultEvent`
for unified dispatch via :mod:`ui.fault_coordinator` and :mod:`core.fault_policy`.

Pipeline::

    error_occurred(str)  →  classify_fault()  →  FaultCoordinator  →  VoiceTray

Mic errors use :class:`FaultSource` ``MIC`` (always ``FaultKind.DEVICE``).
API lines use ``API {status}: {detail}``; 401 with key-related detail → CREDENTIAL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto

_API_ERROR_RE = re.compile(r"^API\s+(\d{3}):\s*(.*)$", re.IGNORECASE | re.DOTALL)

_CREDENTIAL_DETAIL_HINTS = (
    "api key",
    "apikey",
    "invalid key",
    "authentication",
    "unauthorized",
    "鉴权",
    "密钥",
)


class FaultKind(Enum):
    CREDENTIAL = auto()
    API_REMOTE = auto()
    CAPTURE = auto()
    SPEECH_EMPTY = auto()
    SPEECH_SILENT = auto()
    DEVICE = auto()
    GENERAL = auto()


class FaultSource(Enum):
    ENGINE = auto()
    MIC = auto()


@dataclass(frozen=True)
class FaultEvent:
    kind: FaultKind
    source: FaultSource
    message: str
    raw_message: str
    http_status: int | None = None


def extract_api_error(message: str) -> tuple[int, str] | None:
    m = (message or "").strip()
    match = _API_ERROR_RE.match(m)
    if not match:
        return None
    status = int(match.group(1))
    detail = (match.group(2) or "").strip()
    return status, detail


def is_local_credential_error(message: str) -> bool:
    m = (message or "").strip()
    if not m:
        return False
    return (
        "Missing credentials" in m
        or "OPENAI_API_KEY" in m
        or "OPENAI_ADMIN_KEY" in m
    )


def api_detail_implies_credential(http_status: int, detail: str) -> bool:
    if http_status != 401:
        return False
    d = detail.lower()
    return any(h in d for h in _CREDENTIAL_DETAIL_HINTS)


def classify_fault(source: FaultSource, message: str) -> FaultEvent:
    raw = (message or "").strip()
    if source == FaultSource.MIC:
        text = raw or "麦克风不可用"
        return FaultEvent(
            kind=FaultKind.DEVICE,
            source=source,
            message=text,
            raw_message=raw,
        )

    if not raw:
        return FaultEvent(
            kind=FaultKind.GENERAL,
            source=source,
            message=raw,
            raw_message=raw,
        )

    if is_local_credential_error(raw):
        return FaultEvent(
            kind=FaultKind.CREDENTIAL,
            source=source,
            message=raw,
            raw_message=raw,
        )

    parsed = extract_api_error(raw)
    if parsed is not None:
        status, detail = parsed
        if detail and api_detail_implies_credential(status, detail):
            return FaultEvent(
                kind=FaultKind.CREDENTIAL,
                source=source,
                message=detail,
                raw_message=raw,
                http_status=status,
            )
        if detail:
            return FaultEvent(
                kind=FaultKind.API_REMOTE,
                source=source,
                message=detail,
                raw_message=raw,
                http_status=status,
            )
        return FaultEvent(
            kind=FaultKind.CREDENTIAL,
            source=source,
            message=raw,
            raw_message=raw,
            http_status=status,
        )

    if raw.upper().startswith("API "):
        return FaultEvent(
            kind=FaultKind.CREDENTIAL,
            source=source,
            message=raw,
            raw_message=raw,
        )

    if raw.startswith("未录到音频"):
        return FaultEvent(FaultKind.CAPTURE, source, raw, raw)
    if raw == "识别结果为空":
        return FaultEvent(FaultKind.SPEECH_EMPTY, source, raw, raw)
    if raw == "未检测到语音":
        return FaultEvent(FaultKind.SPEECH_SILENT, source, raw, raw)

    return FaultEvent(FaultKind.GENERAL, source, raw, raw)


def single_line_preview(text: str, max_len: int = 320) -> str:
    line = text.replace("\n", " ").strip()
    if len(line) > max_len:
        return line[: max_len - 1] + "…"
    return line
