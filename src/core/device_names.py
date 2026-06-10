"""Shared Windows/PyAudio audio endpoint name normalization and matching."""
from __future__ import annotations

import re

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


def device_identity_key(name: str) -> str:
    """Stable key for matching the same endpoint across naming variants."""
    fixed = _WIN_DUP_PREFIX_RE.sub("(", fix_device_name(name))
    return "".join(ch for ch in fixed.casefold() if ch.isalnum())


def same_device_name(left: str, right: str) -> bool:
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
