"""Install layout paths for portable / installer builds."""

from __future__ import annotations

import sys
from pathlib import Path


def install_root() -> Path:
    """Directory that contains VoiceInput.exe in shipped portable/installer builds."""
    return Path(__file__).resolve().parent.parent.parent


def installed_exe_path() -> Path | None:
    """VoiceInput.exe when running from a shipped tree; None in dev or onefile extract."""
    if getattr(sys, "_MEIPASS", None):
        return None
    exe = install_root() / "VoiceInput.exe"
    return exe if exe.is_file() else None


def autostart_command() -> str | None:
    """Registry Run value for HKCU autostart, or None when exe cannot be resolved."""
    exe = installed_exe_path()
    if exe is None:
        return None
    return f'"{exe}"'
