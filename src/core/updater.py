"""Background update checker with silent download and install."""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from _version import VERSION
from core.log import logger

_REPO = "myuan19/voiceInput"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases?per_page=20"
_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours
_PROXY_SCHEMES = ("http", "https")


def _filter_update_proxies(proxies: dict[str, str]) -> dict[str, str]:
    return {
        scheme: proxy
        for scheme, proxy in (proxies or {}).items()
        if scheme.lower() in _PROXY_SCHEMES and proxy
    }


def _windows_system_proxies() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    getter = getattr(urllib.request, "getproxies_registry", None)
    if getter is None:
        return {}
    try:
        return _filter_update_proxies(getter())
    except OSError as e:
        logger.debug(f"[Updater] Failed to read Windows system proxy: {e}")
        return {}


@contextmanager
def _without_no_proxy_env():
    saved = {name: os.environ.pop(name, None) for name in ("NO_PROXY", "no_proxy")}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def _environment_proxies_without_no_proxy() -> dict[str, str]:
    with _without_no_proxy_env():
        return _filter_update_proxies(urllib.request.getproxies())


def _resolve_update_proxies() -> dict[str, str]:
    """Resolve proxies for GitHub update traffic without changing app-wide env."""
    proxies = _windows_system_proxies()
    if proxies:
        return proxies
    return _environment_proxies_without_no_proxy()


def _open_update_url(req: urllib.request.Request, *, timeout: int):
    proxies = _resolve_update_proxies()
    if not proxies:
        logger.debug("[Updater] Opening update URL without proxy")
        return urllib.request.urlopen(req, timeout=timeout)

    logger.debug(f"[Updater] Opening update URL with proxy schemes: {sorted(proxies)}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    with _without_no_proxy_env():
        return opener.open(req, timeout=timeout)


class UpdateInfo(NamedTuple):
    version: str
    download_url: str
    filename: str
    size: int
    title: str
    body: str
    html_url: str
    published_at: str


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
    """Detect if running from an Inno Setup installed location.

    Checks the directory of the running code (not sys.frozen, which may be
    False for embedded-Python builds) against known install paths.
    """
    try:
        code_dir = Path(__file__).resolve().parent.parent.parent
    except Exception:
        return False
    install_bases = []
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        install_bases.append(Path(local_app) / "Programs")
    pf = os.environ.get("PROGRAMFILES", "")
    if pf:
        install_bases.append(Path(pf))
    pf86 = os.environ.get("PROGRAMFILES(X86)", "")
    if pf86:
        install_bases.append(Path(pf86))
    for base in install_bases:
        try:
            if base.exists() and code_dir.is_relative_to(base):
                logger.debug(f"[DEBUG] _is_installed_version | code_dir={code_dir} is under {base}")
                return True
        except (ValueError, OSError):
            pass
    logger.debug(f"[DEBUG] _is_installed_version | code_dir={code_dir} not under any install base")
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


def _select_latest_release(releases: list[dict], local_version: str) -> dict | None:
    candidates = []
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "")
        if not _is_newer(tag, local_version):
            continue
        version = tag.lstrip("vV")
        if _pick_asset(release.get("assets", []), version):
            candidates.append(release)
    if not candidates:
        return None
    return max(candidates, key=lambda release: _parse_version(release.get("tag_name", "")))


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
            with _open_update_url(req, timeout=10) as resp:
                raw = resp.read()
                logger.debug(f"[DEBUG] _CheckWorker.run | response length={len(raw)}")
            data = json.loads(raw)
            releases = data if isinstance(data, list) else [data]
            release = _select_latest_release(releases, VERSION)
            if release is None:
                logger.debug("[DEBUG] _CheckWorker.run | no newer release with matching asset")
                self.result.emit(_NO_UPDATE)
                return
            tag = release.get("tag_name", "")
            logger.debug(f"[DEBUG] _CheckWorker.run | remote tag={tag!r}, local={VERSION!r}, is_newer={_is_newer(tag, VERSION)}")
            version = tag.lstrip("vV")
            assets = release.get("assets", [])
            asset_names = [a.get("name") for a in assets]
            logger.debug(f"[DEBUG] _CheckWorker.run | version={version}, assets={asset_names}")
            picked = _pick_asset(assets, version)
            if not picked:
                logger.warning(f"[Updater] No suitable asset found for v{version}")
                logger.debug(f"[DEBUG] _CheckWorker.run | no matching asset, emitting _NO_UPDATE")
                self.result.emit(_NO_UPDATE)
                return
            url, filename, size = picked
            info = UpdateInfo(
                version=version,
                download_url=url,
                filename=filename,
                size=size,
                title=release.get("name", "") or f"VoiceInput v{version}",
                body=release.get("body", "") or "",
                html_url=release.get("html_url", "") or "",
                published_at=release.get("published_at", "") or "",
            )
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
            with _open_update_url(req, timeout=60) as resp:
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
        file_exists = Path(path).exists()
        file_size = Path(path).stat().st_size if file_exists else 0
        logger.info(f"[Updater] Installing: {path}")
        logger.debug(f"[DEBUG] UpdateChecker.install | file_exists={file_exists}, file_size={file_size}")
        if path.endswith("-setup.exe"):
            cmd = [path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"]
            logger.debug(f"[DEBUG] UpdateChecker.install | launching setup cmd={cmd}")
            try:
                proc = subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS)
                logger.debug(f"[DEBUG] UpdateChecker.install | setup Popen ok, pid={proc.pid}")
            except Exception as e:
                logger.error(f"[DEBUG] UpdateChecker.install | setup Popen FAILED: {type(e).__name__}: {e}")
                return
        elif path.endswith(".zip"):
            logger.debug(f"[DEBUG] UpdateChecker.install | launching zip install: {path}")
            try:
                self._install_zip(path)
            except Exception as e:
                logger.error(f"[DEBUG] UpdateChecker.install | zip install FAILED: {type(e).__name__}: {e}")
                return
        else:
            logger.debug(f"[DEBUG] UpdateChecker.install | unknown file type: {path}")
            return
        logger.debug("[DEBUG] UpdateChecker.install | calling QApplication.quit()")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def _install_zip(self, zip_path: str):
        """Extract zip over current app directory via a hidden PowerShell script."""
        app_dir = Path(__file__).resolve().parent.parent.parent
        script = Path(tempfile.gettempdir()) / "voiceinput_update.ps1"
        exe_path = app_dir / "VoiceInput.exe"
        logger.debug(f"[DEBUG] _install_zip | app_dir={app_dir}, exe_path={exe_path}, exe_exists={exe_path.exists()}")
        ps_content = (
            f'Start-Sleep -Seconds 2\n'
            f'Expand-Archive -Path "{zip_path}" -DestinationPath "{app_dir}" -Force\n'
            f'Start-Process "{exe_path}"\n'
            f'Remove-Item $MyInvocation.MyCommand.Path -Force\n'
        )
        script.write_text(ps_content, encoding="utf-8")
        logger.debug(f"[DEBUG] _install_zip | script={script}, script_exists={script.exists()}")
        logger.debug(f"[DEBUG] _install_zip | ps_content:\n{ps_content}")
        cmd = ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
               "-File", str(script)]
        logger.debug(f"[DEBUG] _install_zip | launching cmd={cmd}")
        proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
        logger.debug(f"[DEBUG] _install_zip | Popen ok, pid={proc.pid}")

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
