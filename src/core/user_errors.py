"""
Backward-compatible re-exports. New code should use :mod:`core.faults`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from core.faults import (
    FaultKind,
    FaultSource,
    classify_fault,
    extract_api_error as extract_api_error_detail,
    is_local_credential_error,
    single_line_preview,
)


class UserErrorDomain(Enum):
    API_CREDENTIALS = auto()
    API_REMOTE = auto()
    CAPTURE = auto()
    SPEECH_EMPTY = auto()
    SPEECH_SILENT = auto()
    GENERAL = auto()


_DOMAIN_BY_KIND = {
    FaultKind.CREDENTIAL: UserErrorDomain.API_CREDENTIALS,
    FaultKind.API_REMOTE: UserErrorDomain.API_REMOTE,
    FaultKind.CAPTURE: UserErrorDomain.CAPTURE,
    FaultKind.SPEECH_EMPTY: UserErrorDomain.SPEECH_EMPTY,
    FaultKind.SPEECH_SILENT: UserErrorDomain.SPEECH_SILENT,
    FaultKind.GENERAL: UserErrorDomain.GENERAL,
}


@dataclass(frozen=True)
class UserErrorContext:
    domain: UserErrorDomain
    message: str


def classify_user_error(message: str) -> UserErrorContext:
    event = classify_fault(FaultSource.ENGINE, message)
    domain = _DOMAIN_BY_KIND.get(event.kind, UserErrorDomain.GENERAL)
    return UserErrorContext(domain, event.message)
