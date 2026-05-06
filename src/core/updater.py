"""Background update checker — polls GitHub Releases API."""

import json
import urllib.request
import urllib.error
from typing import NamedTuple

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from _version import VERSION
from core.log import logger

_REPO = "myuan19/voiceInput"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours


class UpdateInfo(NamedTuple):
    version: str
    url: str
    body: str


def _parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' -> (1, 2, 3)"""
    tag = tag.lstrip("vV")
    parts = []
    for p in tag.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _is_newer(remote_tag: str, local_version: str) -> bool:
    return _parse_version(remote_tag) > _parse_version(local_version)


class _CheckWorker(QThread):
    update_found = pyqtSignal(object)  # UpdateInfo | None

    def run(self):
        try:
            req = urllib.request.Request(_API_URL, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "VoiceInput-Updater",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            if _is_newer(tag, VERSION):
                info = UpdateInfo(
                    version=tag.lstrip("vV"),
                    url=data.get("html_url", f"https://github.com/{_REPO}/releases/latest"),
                    body=data.get("body", ""),
                )
                logger.info(f"[Updater] New version available: {info.version} (current: {VERSION})")
                self.update_found.emit(info)
            else:
                self.update_found.emit(None)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"[Updater] Check failed: {e}")
            self.update_found.emit(None)


class UpdateChecker:
    """Periodic update checker. Emits ``update_available`` when a newer release is found."""

    def __init__(self):
        self._timer = QTimer()
        self._timer.setInterval(_CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)
        self._worker: _CheckWorker | None = None
        self._latest: UpdateInfo | None = None
        self._callback = None

    @property
    def latest(self) -> UpdateInfo | None:
        return self._latest

    def start(self, callback):
        """Begin periodic checks. callback(UpdateInfo) is called when update found."""
        self._callback = callback
        self._timer.start()
        self.check_now()

    def check_now(self):
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _CheckWorker()
        self._worker.update_found.connect(self._on_result)
        self._worker.finished.connect(self._cleanup)
        self._worker.start()

    def _on_result(self, info: UpdateInfo | None):
        if info is not None:
            self._latest = info
            if self._callback:
                self._callback(info)

    def _cleanup(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
