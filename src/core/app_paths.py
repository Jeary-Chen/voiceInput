"""Install layout paths for portable / installer builds.

Shipped layout (side-by-side versions)::

    {product}/
      VoiceInput.exe      # stable launcher
      current.txt         # active version id, e.g. 1.6.0
      versions/
        1.6.0/
          python/
          src/

Dev / legacy flat layout keeps ``python/`` + ``src/`` next to the project root
(or next to the exe before the first migration).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CURRENT_FILE = "current.txt"
VERSIONS_DIR = "versions"
LAUNCHER_NEW_NAME = "VoiceInput.exe.new"
PARTIAL_SUFFIX = ".partial"
_VERSION_RE = re.compile(r'VERSION\s*=\s*"([^"]+)"')


def payload_root() -> Path:
    """Directory that contains this process's ``python/`` and ``src/`` trees."""
    return Path(__file__).resolve().parent.parent.parent


def product_root() -> Path:
    """Directory that contains the stable ``VoiceInput.exe`` launcher."""
    payload = payload_root()
    if payload.parent.name == VERSIONS_DIR:
        return payload.parent.parent
    return payload


def install_root() -> Path:
    """Product root used by updater / autostart (stable launcher directory)."""
    return product_root()


def versions_dir(root: Path | None = None) -> Path:
    return (root or product_root()) / VERSIONS_DIR


def version_dir(version: str, root: Path | None = None) -> Path:
    return versions_dir(root) / version


def partial_version_dir(version: str, root: Path | None = None) -> Path:
    return versions_dir(root) / f"{version}{PARTIAL_SUFFIX}"


def launcher_new_path(root: Path | None = None) -> Path:
    return (root or product_root()) / LAUNCHER_NEW_NAME


def current_version_file(root: Path | None = None) -> Path:
    return (root or product_root()) / CURRENT_FILE


def read_current_version(root: Path | None = None) -> str | None:
    path = current_version_file(root)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_version_py(version_py: Path) -> str | None:
    if not version_py.is_file():
        return None
    match = _VERSION_RE.search(version_py.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def is_versioned_layout(root: Path | None = None) -> bool:
    root = root or product_root()
    return current_version_file(root).is_file() and versions_dir(root).is_dir()


def is_flat_layout(root: Path | None = None) -> bool:
    root = root or product_root()
    return (root / "python").is_dir() and (root / "src").is_dir()


def installed_exe_path() -> Path | None:
    """VoiceInput.exe when running from a shipped tree; None in dev or onefile extract."""
    if getattr(sys, "_MEIPASS", None):
        return None
    exe = product_root() / "VoiceInput.exe"
    return exe if exe.is_file() else None


def autostart_command() -> str | None:
    """Registry Run value for HKCU autostart, or None when exe cannot be resolved."""
    exe = installed_exe_path()
    if exe is None:
        return None
    return f'"{exe}"'
