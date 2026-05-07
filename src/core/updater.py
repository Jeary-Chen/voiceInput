"""Background update checker with silent download and install."""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from _version import VERSION
from core.log import logger

_REPO = "myuan19/voiceInput"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours


class UpdateInfo(NamedTuple):
    version: str
    download_url: str
    filename: str
    size: int


def _parse_version(tag: str) -> tuple[int, ...]:
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


def _is_installed_version() -> bool:
    """Detect if running from an Inno Setup installed location."""
    if not getattr(sys, "frozen", False):
        return False
    exe = Path(sys.executable).resolve()
    programs = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs"
    program_files = Path(os.environ.get("PROGRAMFILES", ""))
    program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)", ""))
    for base in (programs, program_files, program_files_x86):
        try:
            if base.exists() and exe.is_relative_to(base):
                return True
        except (ValueError, OSError):
            pass
    return False


def _pick_asset(assets: list[dict], version: str) -> tuple[str, str, int] | None:
    """Pick the matching asset based on install type."""
    installed = _is_installed_version()
    logger.debug(f"[DEBUG] _pick_asset | is_installed={installed}")
    if installed:
        preferred = [f"VoiceInput-{version}-setup.exe", f"VoiceInput-{version}-portable.zip"]
    else:
        preferred = [f"VoiceInput-{version}-portable.zip", f"VoiceInput-{version}-setup.exe"]
    for name in preferred:
        for a in assets:
            if a.get("name") == name:
                return a["browser_download_url"], a["name"], a.get("size", 0)
    return None


_NO_UPDATE = "NO_UPDATE"
_CHECK_ERROR = "CHECK_ERROR"


class _CheckWorker(QThread):
    result = pyqtSignal(object)  # UpdateInfo | _NO_UPDATE | _CHECK_ERROR

    def run(self):
        logger.debug(f"[DEBUG] _CheckWorker.run | started, local VERSION={VERSION}")
        try:
            req = urllib.request.Request(_API_URL, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "VoiceInput-Updater",
            })
            logger.debug(f"[DEBUG] _CheckWorker.run | requesting {_API_URL}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
                logger.debug(f"[DEBUG] _CheckWorker.run | response length={len(raw)}")
                data = json.loads(raw)
            tag = data.get("tag_name", "")
            logger.debug(f"[DEBUG] _CheckWorker.run | remote tag={tag!r}, local={VERSION!r}, is_newer={_is_newer(tag, VERSION)}")
            if not _is_newer(tag, VERSION):
                logger.debug("[DEBUG] _CheckWorker.run | no update needed, emitting _NO_UPDATE")
                self.result.emit(_NO_UPDATE)
                return
            version = tag.lstrip("vV")
            assets = data.get("assets", [])
            asset_names = [a.get("name") for a in assets]
            logger.debug(f"[DEBUG] _CheckWorker.run | version={version}, assets={asset_names}")
            picked = _pick_asset(assets, version)
            if not picked:
                logger.warning(f"[Updater] No suitable asset found for v{version}")
                logger.debug(f"[DEBUG] _CheckWorker.run | no matching asset, emitting _NO_UPDATE")
                self.result.emit(_NO_UPDATE)
                return
            url, filename, size = picked
            info = UpdateInfo(version=version, download_url=url, filename=filename, size=size)
            logger.info(f"[Updater] New version available: v{info.version} ({info.filename})")
            logger.debug(f"[DEBUG] _CheckWorker.run | emitting UpdateInfo: {info}")
            self.result.emit(info)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            logger.debug(f"[DEBUG] _CheckWorker.run | exception: {type(e).__name__}: {e}")
            self.result.emit(_CHECK_ERROR)


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)  # percent 0-100
    finished_ok = pyqtSignal(str)  # local file path
    failed = pyqtSignal(str)  # error message

    def __init__(self, url: str, filename: str):
        super().__init__()
        self._url = url
        self._filename = filename

    def run(self):
        logger.debug(f"[DEBUG] _DownloadWorker.run | url={self._url}, filename={self._filename}")
        try:
            dest = Path(tempfile.gettempdir()) / self._filename
            logger.debug(f"[DEBUG] _DownloadWorker.run | dest={dest}")
            req = urllib.request.Request(self._url, headers={
                "User-Agent": "VoiceInput-Updater",
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                logger.debug(f"[DEBUG] _DownloadWorker.run | Content-Length={total}")
                downloaded = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self.progress.emit(int(downloaded * 100 / total))
            logger.info(f"[Updater] Downloaded: {dest}")
            logger.debug(f"[DEBUG] _DownloadWorker.run | download complete, size={downloaded}, emitting finished_ok")
            self.finished_ok.emit(str(dest))
        except Exception as e:
            logger.error(f"[Updater] Download failed: {e}")
            logger.debug(f"[DEBUG] _DownloadWorker.run | exception: {type(e).__name__}: {e}")
            self.failed.emit(str(e))


class UpdateChecker:
    """Checks for updates, downloads, and installs silently."""

    def __init__(self):
        self._timer = QTimer()
        self._timer.setInterval(_CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)
        self._check_worker: _CheckWorker | None = None
        self._dl_worker: _DownloadWorker | None = None
        self._latest: UpdateInfo | None = None
        self._downloaded_path: str | None = None
        self._cb_available = None
        self._cb_no_update = None
        self._cb_check_failed = None
        self._cb_progress = None
        self._cb_done = None
        self._cb_dl_failed = None

    @property
    def latest(self) -> UpdateInfo | None:
        return self._latest

    @property
    def is_downloading(self) -> bool:
        return self._dl_worker is not None and self._dl_worker.isRunning()

    @property
    def is_ready_to_install(self) -> bool:
        return self._downloaded_path is not None and Path(self._downloaded_path).exists()

    def start(self, *, on_available=None, on_no_update=None, on_check_failed=None,
              on_progress=None, on_done=None, on_dl_failed=None):
        self._cb_available = on_available
        self._cb_no_update = on_no_update
        self._cb_check_failed = on_check_failed
        self._cb_progress = on_progress
        self._cb_done = on_done
        self._cb_dl_failed = on_dl_failed
        self._timer.start()
        self.check_now()

    def check_now(self):
        if self._check_worker is not None and self._check_worker.isRunning():
            logger.debug("[DEBUG] UpdateChecker.check_now | skipped, worker already running")
            return
        logger.debug("[DEBUG] UpdateChecker.check_now | spawning _CheckWorker")
        self._check_worker = _CheckWorker()
        self._check_worker.result.connect(self._on_check_result)
        self._check_worker.finished.connect(self._cleanup_check)
        self._check_worker.start()

    def download_and_install(self):
        """Start downloading the update. After download, call install()."""
        logger.debug(f"[DEBUG] UpdateChecker.download_and_install | latest={self._latest}, is_downloading={self.is_downloading}, is_ready={self.is_ready_to_install}")
        if not self._latest:
            logger.debug("[DEBUG] UpdateChecker.download_and_install | no latest, returning")
            return
        if self.is_downloading:
            logger.debug("[DEBUG] UpdateChecker.download_and_install | already downloading, returning")
            return
        if self.is_ready_to_install:
            logger.debug("[DEBUG] UpdateChecker.download_and_install | already downloaded, calling install()")
            self.install()
            return
        logger.debug(f"[DEBUG] UpdateChecker.download_and_install | starting download: {self._latest.download_url}")
        self._dl_worker = _DownloadWorker(self._latest.download_url, self._latest.filename)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finished_ok.connect(self._on_dl_done)
        self._dl_worker.failed.connect(self._on_dl_failed)
        self._dl_worker.start()

    def install(self):
        """Launch the installer/updater and quit the app."""
        if not self._downloaded_path:
            logger.debug("[DEBUG] UpdateChecker.install | no downloaded_path, returning")
            return
        path = self._downloaded_path
        logger.info(f"[Updater] Installing: {path}")
        if path.endswith("-setup.exe"):
            logger.debug(f"[DEBUG] UpdateChecker.install | launching setup: {path} /VERYSILENT")
            subprocess.Popen(
                [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                creationflags=subprocess.DETACHED_PROCESS,
            )
        elif path.endswith(".zip"):
            logger.debug(f"[DEBUG] UpdateChecker.install | launching zip install: {path}")
            self._install_zip(path)
        else:
            logger.debug(f"[DEBUG] UpdateChecker.install | unknown file type: {path}")
        logger.debug("[DEBUG] UpdateChecker.install | calling QApplication.quit()")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def _install_zip(self, zip_path: str):
        """Extract zip over current app directory via a hidden PowerShell script."""
        if getattr(sys, "frozen", False):
            app_dir = Path(sys.executable).parent
        else:
            app_dir = Path(__file__).resolve().parent.parent.parent
        script = Path(tempfile.gettempdir()) / "voiceinput_update.ps1"
        exe_path = app_dir / "VoiceInput.exe"
        script.write_text(
            f'Start-Sleep -Seconds 2\n'
            f'Expand-Archive -Path "{zip_path}" -DestinationPath "{app_dir}" -Force\n'
            f'Start-Process "{exe_path}"\n'
            f'Remove-Item $MyInvocation.MyCommand.Path -Force\n',
            encoding="utf-8",
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _on_check_result(self, result):
        logger.debug(f"[DEBUG] UpdateChecker._on_check_result | result type={type(result).__name__}, value={result!r}")
        if isinstance(result, UpdateInfo):
            self._latest = result
            self._downloaded_path = None
            if self._cb_available:
                logger.debug("[DEBUG] UpdateChecker._on_check_result | calling cb_available")
                self._cb_available(result)
            else:
                logger.debug("[DEBUG] UpdateChecker._on_check_result | cb_available is None")
        elif result == _NO_UPDATE:
            if self._cb_no_update:
                logger.debug("[DEBUG] UpdateChecker._on_check_result | calling cb_no_update")
                self._cb_no_update()
        elif result == _CHECK_ERROR:
            if self._cb_check_failed:
                logger.debug("[DEBUG] UpdateChecker._on_check_result | calling cb_check_failed")
                self._cb_check_failed()

    def _on_dl_progress(self, percent: int):
        if self._cb_progress:
            self._cb_progress(percent)

    def _on_dl_done(self, path: str):
        logger.debug(f"[DEBUG] UpdateChecker._on_dl_done | path={path}")
        self._downloaded_path = path
        if self._cb_done:
            logger.debug("[DEBUG] UpdateChecker._on_dl_done | calling cb_done")
            self._cb_done()

    def _on_dl_failed(self, msg: str):
        logger.debug(f"[DEBUG] UpdateChecker._on_dl_failed | msg={msg}")
        if self._cb_dl_failed:
            self._cb_dl_failed(msg)

    def _cleanup_check(self):
        if self._check_worker:
            self._check_worker.deleteLater()
            self._check_worker = None
