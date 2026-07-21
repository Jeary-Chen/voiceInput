"""Shared Windows/PyAudio audio endpoint name normalization and matching."""
from __future__ import annotations

import re

# PortAudio/PyAudio on Windows truncates friendly names to this many characters.
PYAUDIO_DEVICE_NAME_MAX_LEN = 31

_WIN_DUP_PREFIX_RE = re.compile(r"\(\s*\d+\s*-\s*")

_SYSTEM_CAPTURE_ALIASES = frozenset({
    "microsoft声音映射器input",
    "microsoftsoundmapperinput",
    "主声音捕获驱动程序",
    "primarysoundcapturedriver",
})


def fix_device_name(name: str) -> str:
    """Fix PyAudio device name encoding on Chinese Windows."""
    try:
        return name.encode("gbk").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def pyaudio_truncated_name(name: str) -> str:
    """Return the PyAudio-visible form of a Windows friendly name."""
    return (name or "")[:PYAUDIO_DEVICE_NAME_MAX_LEN]


def is_pyaudio_name_truncation_pair(left: str, right: str) -> bool:
    """True iff one name is exactly the 31-char PyAudio prefix of the other.

    This is intentionally narrow: it only collapses trunc↔full spellings of the
    same endpoint, never two distinct devices that merely look similar.
    """
    if not left or not right or left == right:
        return False
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    return (
        len(shorter) == PYAUDIO_DEVICE_NAME_MAX_LEN
        and longer.startswith(shorter)
    )


def device_identity_key(name: str) -> str:
    """Stable key for matching the same endpoint across naming variants."""
    fixed = _WIN_DUP_PREFIX_RE.sub("(", fix_device_name(name))
    return "".join(ch for ch in fixed.casefold() if ch.isalnum())


def same_device_name(left: str, right: str) -> bool:
    if is_pyaudio_name_truncation_pair(left, right):
        return True
    left_key = device_identity_key(left)
    right_key = device_identity_key(right)
    return bool(left_key) and left_key == right_key


def device_labels_overlap(left: str, right: str) -> bool:
    """Loose label match for default output endpoint resolution."""
    left_label = " ".join(fix_device_name(left).casefold().split())
    right_label = " ".join(fix_device_name(right).casefold().split())
    if not left_label or not right_label:
        return False
    return (
        left_label == right_label
        or left_label in right_label
        or right_label in left_label
        or same_device_name(left, right)
    )


def is_system_capture_alias(name: str) -> bool:
    return device_identity_key(name) in _SYSTEM_CAPTURE_ALIASES
