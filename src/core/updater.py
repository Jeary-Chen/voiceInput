"""Background update checker with silent download and install."""

import json
import os
import shutil
import sys
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from _version import VERSION
from core.log import logger
from core.network import open_update_url

_REPO = "myuan19/voiceInput"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases?per_page=20"
_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _update_install_log_path() -> Path:
    root = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    log_dir = root / ".voiceinput" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "update_install.log"


def _build_install_script(
    *,
    source: Path,
    app_dir: Path,
    exe_path: Path,
    staged: Path,
    log_path: Path,
) -> str:
    return (
        f'$ErrorActionPreference = "Continue"\n'
        f'$LogPath = "{log_path}"\n'
        f'function Write-DebugLog([string]$Message) {{\n'
        f'  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"\n'
        f'  Add-Content -Path $LogPath -Encoding UTF8 -Value "$ts | [DEBUG] update_install.ps1 | $Message"\n'
        f'}}\n'
        f'$TotalStart = Get-Date\n'
        f'Write-DebugLog "start source={source} app_dir={app_dir} exe={exe_path} staged={staged}"\n'
        f'$StepStart = Get-Date\n'
        f'Start-Sleep -Seconds 1\n'
        f'Write-DebugLog "sleep_before_copy elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'foreach ($ManagedPath in @("{app_dir}\\python", "{app_dir}\\src")) {{\n'
        f'  Remove-Item $ManagedPath -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'}}\n'
        f'Write-DebugLog "cleanup_managed_paths elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'robocopy "{source}" "{app_dir}" /E /IS /IT /NFL /NDL /NJH /NJS /R:3 /W:1\n'
        f'$CopyExitCode = $LASTEXITCODE\n'
        f'Write-DebugLog "robocopy_copy exit_code=$CopyExitCode elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'Start-Process "{exe_path}"\n'
        f'Write-DebugLog "start_process elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'Remove-Item "{staged}" -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'Write-DebugLog "cleanup_staging elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'Write-DebugLog "total elapsed_ms=$([int]((Get-Date) - $TotalStart).TotalMilliseconds)"\n'
        f'Remove-Item $MyInvocation.MyCommand.Path -Force\n'
    )


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


def can_self_update() -> bool:
    """Return True if the current launch mode supports in-app updates.

    Portable and installer builds have VoiceInput.exe alongside python/ and
    src/.  PyInstaller onefile extracts to a temp dir (_MEIPASS), and dev-mode
    (run.ps1 / .venv) has no VoiceInput.exe — neither can self-update.
    """
    if getattr(sys, "_MEIPASS", None):
        return False
    try:
        app_dir = Path(__file__).resolve().parent.parent.parent
        return (app_dir / "VoiceInput.exe").is_file()
    except Exception:
        return False


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
    """Always prefer the portable zip for faster pre-extract updates."""
    preferred = [f"VoiceInput-{version}-portable.zip"]
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
        started = time.perf_counter()
        logger.debug(f"[DEBUG] _CheckWorker.run | started, local VERSION={VERSION}")
        try:
            req = urllib.request.Request(_API_URL, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "VoiceInput-Updater",
            })
            request_started = time.perf_counter()
            logger.debug(f"[DEBUG] _CheckWorker.run | requesting {_API_URL}")
            with open_update_url(req, timeout=10) as resp:
                raw = resp.read()
                logger.debug(
                    f"[DEBUG] _CheckWorker.run | response length={len(raw)}, "
                    f"request_elapsed_ms={_elapsed_ms(request_started)}"
                )
            parse_started = time.perf_counter()
            data = json.loads(raw)
            releases = data if isinstance(data, list) else [data]
            release = _select_latest_release(releases, VERSION)
            logger.debug(
                f"[DEBUG] _CheckWorker.run | parse_select_elapsed_ms={_elapsed_ms(parse_started)}, "
                f"release_count={len(releases)}"
            )
            if release is None:
                logger.debug(
                    f"[DEBUG] _CheckWorker.run | no newer release with matching asset, "
                    f"total_elapsed_ms={_elapsed_ms(started)}"
                )
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
            logger.debug(
                f"[DEBUG] _CheckWorker.run | emitting UpdateInfo: {info}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.result.emit(info)
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            logger.debug(
                f"[DEBUG] _CheckWorker.run | exception: {type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.result.emit(_CHECK_ERROR)


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)  # percent 0-100
    finished_ok = pyqtSignal(str, int)  # (local file path, expected size)
    failed = pyqtSignal(str)  # error message

    def __init__(self, url: str, filename: str):
        super().__init__()
        self._url = url
        self._filename = filename

    def run(self):
        started = time.perf_counter()
        logger.debug(f"[DEBUG] _DownloadWorker.run | url={self._url}, filename={self._filename}")
        try:
            dest = Path(tempfile.gettempdir()) / self._filename
            logger.debug(f"[DEBUG] _DownloadWorker.run | dest={dest}")
            req = urllib.request.Request(self._url, headers={
                "User-Agent": "VoiceInput-Updater",
            })
            open_started = time.perf_counter()
            with open_update_url(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                logger.debug(
                    f"[DEBUG] _DownloadWorker.run | Content-Length={total}, "
                    f"open_elapsed_ms={_elapsed_ms(open_started)}"
                )
                downloaded = 0
                write_started = time.perf_counter()
                last_progress_log = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            percent = int(downloaded * 100 / total)
                            self.progress.emit(percent)
                            if percent >= last_progress_log + 25:
                                logger.debug(
                                    f"[DEBUG] _DownloadWorker.run | progress={percent}%, "
                                    f"downloaded={downloaded}, total={total}, "
                                    f"write_elapsed_ms={_elapsed_ms(write_started)}"
                                )
                                last_progress_log = percent
            if total > 0 and downloaded != total:
                logger.error(f"[Updater] Incomplete download: {downloaded}/{total} bytes ({downloaded*100//total}%)")
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                self.failed.emit(f"下载不完整 ({downloaded}/{total} 字节)")
                return
            logger.info(f"[Updater] Downloaded: {dest} ({downloaded} bytes)")
            logger.debug(
                f"[DEBUG] _DownloadWorker.run | download complete, size={downloaded}, "
                f"total_elapsed_ms={_elapsed_ms(started)}, emitting finished_ok"
            )
            self.finished_ok.emit(str(dest), total)
        except Exception as e:
            logger.error(f"[Updater] Download failed: {e}")
            logger.debug(
                f"[DEBUG] _DownloadWorker.run | exception: {type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.failed.emit(str(e))


_STAGING_DIR_NAME = "VoiceInput_update_staging"


_STAGE_VERSION_FILE = ".update_version"


class _StageWorker(QThread):
    """Extract a downloaded zip to a staging directory."""
    progress = pyqtSignal(int)   # percent 0-100
    finished_ok = pyqtSignal(str)  # staging directory path
    failed = pyqtSignal(str)

    def __init__(self, zip_path: str, version: str):
        super().__init__()
        self._zip_path = zip_path
        self._version = version

    def run(self):
        started = time.perf_counter()
        staging_dir = Path(tempfile.gettempdir()) / _STAGING_DIR_NAME
        logger.debug(f"[DEBUG] _StageWorker.run | zip={self._zip_path}, staging={staging_dir}")
        try:
            if staging_dir.exists():
                clean_started = time.perf_counter()
                shutil.rmtree(staging_dir)
                logger.debug(
                    f"[DEBUG] _StageWorker.run | clean_existing_staging elapsed_ms={_elapsed_ms(clean_started)}"
                )
            mkdir_started = time.perf_counter()
            staging_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(
                f"[DEBUG] _StageWorker.run | mkdir_staging elapsed_ms={_elapsed_ms(mkdir_started)}"
            )
            extract_started = time.perf_counter()
            with zipfile.ZipFile(self._zip_path, "r") as zf:
                members = zf.namelist()
                total = len(members)
                total_uncompressed = sum(info.file_size for info in zf.infolist())
                logger.debug(
                    f"[DEBUG] _StageWorker.run | zip_opened members={total}, "
                    f"uncompressed_bytes={total_uncompressed}"
                )
                last_progress_log = 0
                for i, member in enumerate(members, 1):
                    zf.extract(member, staging_dir)
                    percent = int(i * 100 / total) if total else 100
                    self.progress.emit(percent)
                    if percent >= last_progress_log + 25:
                        logger.debug(
                            f"[DEBUG] _StageWorker.run | extract_progress={percent}%, "
                            f"files={i}/{total}, elapsed_ms={_elapsed_ms(extract_started)}"
                        )
                        last_progress_log = percent
            logger.debug(
                f"[DEBUG] _StageWorker.run | extract_complete elapsed_ms={_elapsed_ms(extract_started)}"
            )
            version_started = time.perf_counter()
            (staging_dir / _STAGE_VERSION_FILE).write_text(self._version, encoding="utf-8")
            logger.debug(
                f"[DEBUG] _StageWorker.run | write_version elapsed_ms={_elapsed_ms(version_started)}"
            )
            logger.info(f"[Updater] Staged {total} files to {staging_dir} (v{self._version})")
            logger.debug(
                f"[DEBUG] _StageWorker.run | total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.finished_ok.emit(str(staging_dir))
        except Exception as e:
            logger.error(f"[Updater] Staging failed: {e}")
            logger.debug(
                f"[DEBUG] _StageWorker.run | exception={type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.failed.emit(str(e))


class UpdateChecker:
    """Checks for updates, downloads, stages, and installs."""

    def __init__(self):
        self._timer = QTimer()
        self._timer.setInterval(_CHECK_INTERVAL_MS)
        self._timer.timeout.connect(self.check_now)
        self._check_worker: _CheckWorker | None = None
        self._dl_worker: _DownloadWorker | None = None
        self._stage_worker: _StageWorker | None = None
        self._latest: UpdateInfo | None = None
        self._downloaded_path: str | None = None
        self._downloaded_expected_size: int = 0
        self._staged_dir: str | None = None
        # callbacks
        self._cb_available = None
        self._cb_no_update = None
        self._cb_check_failed = None
        self._cb_dl_progress = None
        self._cb_dl_done = None
        self._cb_dl_failed = None
        self._cb_stage_progress = None
        self._cb_stage_done = None
        self._cb_stage_failed = None

    @property
    def latest(self) -> UpdateInfo | None:
        return self._latest

    @property
    def is_downloading(self) -> bool:
        return self._dl_worker is not None and self._dl_worker.isRunning()

    @property
    def is_staging(self) -> bool:
        return self._stage_worker is not None and self._stage_worker.isRunning()

    @property
    def is_ready_to_install(self) -> bool:
        return self._staged_dir is not None and Path(self._staged_dir).exists()

    def start(self, *, on_available=None, on_no_update=None, on_check_failed=None,
              on_dl_progress=None, on_dl_done=None, on_dl_failed=None,
              on_stage_progress=None, on_stage_done=None, on_stage_failed=None):
        self._cb_available = on_available
        self._cb_no_update = on_no_update
        self._cb_check_failed = on_check_failed
        self._cb_dl_progress = on_dl_progress
        self._cb_dl_done = on_dl_done
        self._cb_dl_failed = on_dl_failed
        self._cb_stage_progress = on_stage_progress
        self._cb_stage_done = on_stage_done
        self._cb_stage_failed = on_stage_failed
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

    def download_update(self):
        """Start downloading the update zip."""
        logger.debug(f"[DEBUG] UpdateChecker.download_update | latest={self._latest}, "
                     f"downloading={self.is_downloading}, staging={self.is_staging}, ready={self.is_ready_to_install}")
        if not self._latest:
            return
        if self.is_downloading or self.is_staging:
            return
        if self.is_ready_to_install:
            return
        logger.debug(
            f"[DEBUG] UpdateChecker.download_update | starting download: "
            f"url={self._latest.download_url}, filename={self._latest.filename}, "
            f"expected_size={self._latest.size}"
        )
        self._dl_worker = _DownloadWorker(self._latest.download_url, self._latest.filename)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.finished_ok.connect(self._on_dl_done)
        self._dl_worker.failed.connect(self._on_dl_failed)
        self._dl_worker.start()

    def install(self):
        """Copy staged files over the app directory and restart."""
        started = time.perf_counter()
        if not self._staged_dir:
            logger.debug("[DEBUG] UpdateChecker.install | no staged_dir, returning")
            return
        staged = Path(self._staged_dir)
        if not staged.exists():
            logger.error("[Updater] Install aborted: staging directory missing")
            self._staged_dir = None
            return
        app_dir = Path(__file__).resolve().parent.parent.parent
        exe_path = app_dir / "VoiceInput.exe"
        # The zip contains a top-level "VoiceInput/" directory
        inner = staged / "VoiceInput"
        source = inner if inner.is_dir() else staged
        logger.info(f"[Updater] Installing from staged: {source} → {app_dir}")
        script = Path(tempfile.gettempdir()) / "voiceinput_update.ps1"
        install_log = _update_install_log_path()
        logger.debug(
            f"[DEBUG] UpdateChecker.install | source={source}, app_dir={app_dir}, "
            f"exe_path={exe_path}, staged={staged}, script={script}, "
            f"install_log={install_log}"
        )
        build_started = time.perf_counter()
        ps_content = _build_install_script(
            source=source,
            app_dir=app_dir,
            exe_path=exe_path,
            staged=staged,
            log_path=install_log,
        )
        logger.debug(
            f"[DEBUG] UpdateChecker.install | build_script elapsed_ms={_elapsed_ms(build_started)}"
        )
        write_started = time.perf_counter()
        script.write_text(ps_content, encoding="utf-8")
        logger.debug(
            f"[DEBUG] UpdateChecker.install | write_script elapsed_ms={_elapsed_ms(write_started)}, "
            f"bytes={len(ps_content.encode('utf-8'))}"
        )
        logger.debug(f"[DEBUG] UpdateChecker.install | ps_content:\n{ps_content}")
        cmd = ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
               "-File", str(script)]
        try:
            launch_started = time.perf_counter()
            proc = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)
            logger.debug(
                f"[DEBUG] UpdateChecker.install | swap script pid={proc.pid}, "
                f"launch_elapsed_ms={_elapsed_ms(launch_started)}, "
                f"pre_quit_total_elapsed_ms={_elapsed_ms(started)}"
            )
        except Exception as e:
            logger.error(f"[Updater] Install script launch failed: {e}")
            logger.debug(
                f"[DEBUG] UpdateChecker.install | launch_exception={type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            return
        logger.debug("[DEBUG] UpdateChecker.install | calling QApplication.quit()")
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    # ── internal callbacks ──

    def _on_check_result(self, result):
        logger.debug(f"[DEBUG] UpdateChecker._on_check_result | result type={type(result).__name__}, value={result!r}")
        if isinstance(result, UpdateInfo):
            self._latest = result
            self._downloaded_path = None
            self._staged_dir = None
            if self._try_reuse_staging(result.version):
                return
            if self._cb_available:
                self._cb_available(result)
        elif result == _NO_UPDATE:
            if self._cb_no_update:
                self._cb_no_update()
        elif result == _CHECK_ERROR:
            if self._cb_check_failed:
                self._cb_check_failed()

    def _on_dl_progress(self, percent: int):
        if self._cb_dl_progress:
            self._cb_dl_progress(percent)

    def _on_dl_done(self, path: str, expected_size: int):
        actual_size = Path(path).stat().st_size if Path(path).exists() else -1
        logger.debug(
            f"[DEBUG] UpdateChecker._on_dl_done | path={path}, expected_size={expected_size}, "
            f"actual_size={actual_size}"
        )
        self._downloaded_path = path
        self._downloaded_expected_size = expected_size
        if self._cb_dl_done:
            self._cb_dl_done()
        self._start_staging(path)

    def _on_dl_failed(self, msg: str):
        logger.debug(f"[DEBUG] UpdateChecker._on_dl_failed | msg={msg}")
        if self._cb_dl_failed:
            self._cb_dl_failed(msg)

    # ── staging ──

    def _try_reuse_staging(self, version: str) -> bool:
        """Return True if a previously staged directory for *version* exists."""
        staging_dir = Path(tempfile.gettempdir()) / _STAGING_DIR_NAME
        ver_file = staging_dir / _STAGE_VERSION_FILE
        if not ver_file.is_file():
            return False
        try:
            staged_ver = ver_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if staged_ver != version:
            logger.debug(f"[DEBUG] _try_reuse_staging | version mismatch: staged={staged_ver}, wanted={version}")
            return False
        logger.info(f"[Updater] Reusing existing staging directory for v{version}")
        self._staged_dir = str(staging_dir)
        if self._cb_stage_done:
            self._cb_stage_done()
        return True

    def _start_staging(self, zip_path: str):
        zip_size = Path(zip_path).stat().st_size if Path(zip_path).exists() else -1
        logger.debug(
            f"[DEBUG] UpdateChecker._start_staging | zip_path={zip_path}, zip_size={zip_size}"
        )
        self._stage_worker = _StageWorker(zip_path, self._latest.version if self._latest else "")
        self._stage_worker.progress.connect(self._on_stage_progress)
        self._stage_worker.finished_ok.connect(self._on_stage_done)
        self._stage_worker.failed.connect(self._on_stage_failed)
        self._stage_worker.start()

    def _on_stage_progress(self, percent: int):
        if self._cb_stage_progress:
            self._cb_stage_progress(percent)

    def _on_stage_done(self, staged_dir: str):
        logger.info(f"[Updater] Staging complete: {staged_dir}")
        self._staged_dir = staged_dir
        # Clean up the downloaded zip
        if self._downloaded_path:
            try:
                cleanup_started = time.perf_counter()
                Path(self._downloaded_path).unlink(missing_ok=True)
                logger.debug(
                    f"[DEBUG] UpdateChecker._on_stage_done | cleanup_zip elapsed_ms={_elapsed_ms(cleanup_started)}, "
                    f"path={self._downloaded_path}"
                )
            except OSError:
                logger.debug(
                    f"[DEBUG] UpdateChecker._on_stage_done | cleanup_zip failed, path={self._downloaded_path}"
                )
                pass
        if self._cb_stage_done:
            self._cb_stage_done()

    def _on_stage_failed(self, msg: str):
        logger.error(f"[Updater] Staging failed: {msg}")
        if self._cb_stage_failed:
            self._cb_stage_failed(msg)

    def _cleanup_check(self):
        if self._check_worker:
            self._check_worker.deleteLater()
            self._check_worker = None
