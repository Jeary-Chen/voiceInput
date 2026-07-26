"""Background update checker with silent download and install."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PyQt6.QtCore import QThread, pyqtSignal, QTimer

from _version import VERSION
from core.app_paths import (
    LAUNCHER_NEW_NAME,
    PARTIAL_SUFFIX,
    install_root,
    installed_exe_path,
    launcher_new_path,
    partial_version_dir,
    read_current_version,
    read_version_py,
    version_dir,
    versions_dir,
)
from core.log import logger
from core.network import open_update_url

_REPO = "myuan19/voiceInput"
_API_URL = f"https://api.github.com/repos/{_REPO}/releases?per_page=20"
RELEASES_PAGE_URL = f"https://github.com/{_REPO}/releases"
_CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000  # 4 hours


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _update_install_log_path() -> Path:
    root = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    log_dir = root / ".voiceinput" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "update_install.log"


_WAIT_PROCESS_TIMEOUT_SEC = 15
_START_HEALTH_POLL_MS = 800
_START_HEALTH_POLL_MAX = 3

# Legacy TEMP staging names (pre-1.6.2). Sweep still cleans leftovers.
_LEGACY_STAGING_DIR_NAME = "VoiceInput_update_staging"
_LEGACY_STAGING_APPLIED_MARKER = ".applied-"
# Marker written into versions/{ver}/ when extract-once staging completes.
_STAGE_VERSION_FILE = ".update_version"
_ZIP_PAYLOAD_PREFIX = "VoiceInput/"


def _build_install_script(
    *,
    app_dir: Path,
    exe_path: Path,
    log_path: Path,
    old_pid: int,
    target_version: str,
) -> str:
    """Build the out-of-process apply script (pointer flip only).

    The new version tree is already extracted under ``versions\\{target}``.
    Critical path after the old process exits:
    1. Verify prepared ``versions\\{target}``
    2. Flip ``current.txt`` + refresh stable launcher from ``.new``
    3. Start the stable exe
    4. Delete other version dirs / flat leftovers in the background
    """
    versions_root = app_dir / "versions"
    version_path = versions_root / target_version
    current_file = app_dir / "current.txt"
    version_file = version_path / "src" / "_version.py"
    exe_new = app_dir / LAUNCHER_NEW_NAME
    return (
        f'$ErrorActionPreference = "Continue"\n'
        f'$LogPath = "{log_path}"\n'
        f'$OldPid = {old_pid}\n'
        f'$TargetVersion = "{target_version}"\n'
        f'$ProductRoot = "{app_dir}"\n'
        f'$VersionsRoot = "{versions_root}"\n'
        f'$VersionDir = "{version_path}"\n'
        f'$CurrentFile = "{current_file}"\n'
        f'$ExeNew = "{exe_new}"\n'
        f'$ExePath = "{exe_path}"\n'
        f'function Write-DebugLog([string]$Message) {{\n'
        f'  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"\n'
        f'  Add-Content -Path $LogPath -Encoding UTF8 -Value "$ts | [DEBUG] update_install.ps1 | $Message"\n'
        f'}}\n'
        f'function Abort-Install([string]$Reason) {{\n'
        f'  Write-DebugLog "abort reason=$Reason"\n'
        f'  Write-DebugLog "prepared_preserved path=$VersionDir"\n'
        f'  Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n'
        f'  exit 1\n'
        f'}}\n'
        f'function Wait-ForOldInstance {{\n'
        f'  $OldProcess = Get-Process -Id $OldPid -ErrorAction SilentlyContinue\n'
        f'  if (-not $OldProcess) {{\n'
        f'    Write-DebugLog "wait_process already_exited pid=$OldPid"\n'
        f'    return\n'
        f'  }}\n'
        f'  try {{\n'
        f'    $OldProcess | Wait-Process -Timeout {_WAIT_PROCESS_TIMEOUT_SEC} -ErrorAction Stop\n'
        f'    Write-DebugLog "wait_process exited pid=$OldPid"\n'
        f'  }} catch {{\n'
        f'    Abort-Install "wait_process_timeout pid=$OldPid timeout_sec={_WAIT_PROCESS_TIMEOUT_SEC}"\n'
        f'  }}\n'
        f'}}\n'
        f'function Switch-ToPreparedVersion {{\n'
        f'  if (-not (Test-Path -LiteralPath (Join-Path $VersionDir "python"))) {{\n'
        f'    Abort-Install "prepared_python_missing path=$VersionDir"\n'
        f'  }}\n'
        f'  if (-not (Test-Path -LiteralPath (Join-Path $VersionDir "src"))) {{\n'
        f'    Abort-Install "prepared_src_missing path=$VersionDir"\n'
        f'  }}\n'
        f'  Set-Content -LiteralPath $CurrentFile -Value $TargetVersion -Encoding ASCII -NoNewline\n'
        f'  Write-DebugLog "current_switched version=$TargetVersion path=$CurrentFile"\n'
        f'  if (Test-Path -LiteralPath $ExeNew) {{\n'
        f'    Copy-Item -LiteralPath $ExeNew -Destination $ExePath -Force\n'
        f'    Remove-Item -LiteralPath $ExeNew -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "launcher_refreshed path=$ExePath"\n'
        f'  }} else {{\n'
        f'    Write-DebugLog "launcher_new_missing path=$ExeNew"\n'
        f'  }}\n'
        f'  $marker = Join-Path $VersionDir "{_STAGE_VERSION_FILE}"\n'
        f'  if (Test-Path -LiteralPath $marker) {{\n'
        f'    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "stage_marker_cleared path=$marker"\n'
        f'  }}\n'
        f'}}\n'
        f'function Test-InstalledVersion {{\n'
        f'  $VersionFile = "{version_file}"\n'
        f'  if (-not (Test-Path $VersionFile)) {{\n'
        f'    Abort-Install "version_file_missing path=$VersionFile"\n'
        f'  }}\n'
        f'  $content = Get-Content $VersionFile -Raw -ErrorAction Stop\n'
        f'  if ($content -notmatch \'VERSION\\s*=\\s*"([^"]+)"\') {{\n'
        f'    Abort-Install "version_parse_failed path=$VersionFile"\n'
        f'  }}\n'
        f'  $installedVersion = $Matches[1]\n'
        f'  Write-DebugLog "verify_version installed=$installedVersion target=$TargetVersion"\n'
        f'  if ($installedVersion -ne $TargetVersion) {{\n'
        f'    Abort-Install "version_mismatch installed=$installedVersion target=$TargetVersion"\n'
        f'  }}\n'
        f'  $pointer = (Get-Content -LiteralPath $CurrentFile -Raw -ErrorAction SilentlyContinue)\n'
        f'  if ($null -eq $pointer) {{ Abort-Install "current_missing path=$CurrentFile" }}\n'
        f'  if ($pointer.Trim() -ne $TargetVersion) {{\n'
        f'    Abort-Install "current_mismatch pointer=$($pointer.Trim()) target=$TargetVersion"\n'
        f'  }}\n'
        f'}}\n'
        f'function Update-UninstallRegistration {{\n'
        f'  $AppDirNorm = [System.IO.Path]::GetFullPath($ProductRoot).TrimEnd(\'\\\\\')\n'
        f'  $DisplayName = "VoiceInput version $TargetVersion"\n'
        f'  $roots = @(\n'
        f'    "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",\n'
        f'    "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",\n'
        f'    "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"\n'
        f'  )\n'
        f'  $updated = 0\n'
        f'  foreach ($root in $roots) {{\n'
        f'    if (-not (Test-Path $root)) {{ continue }}\n'
        f'    Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {{\n'
        f'      $keyPath = $_.PSPath\n'
        f'      $props = Get-ItemProperty -LiteralPath $keyPath -ErrorAction SilentlyContinue\n'
        f'      if ($null -eq $props) {{ return }}\n'
        f'      $name = [string]$props.DisplayName\n'
        f'      $loc = [string]$props.InstallLocation\n'
        f'      $keyId = $_.PSChildName\n'
        f'      $locNorm = if ($loc) {{ [System.IO.Path]::GetFullPath($loc).TrimEnd(\'\\\\\') }} else {{ "" }}\n'
        f'      $match = ($keyId -eq "VoiceInput_is1") -or\n'
        f'        ($locNorm -and ($locNorm -eq $AppDirNorm)) -or\n'
        f'        ($name -like "VoiceInput*")\n'
        f'      if (-not $match) {{ return }}\n'
        f'      Set-ItemProperty -LiteralPath $keyPath -Name "DisplayVersion" -Value $TargetVersion -ErrorAction SilentlyContinue\n'
        f'      Set-ItemProperty -LiteralPath $keyPath -Name "DisplayName" -Value $DisplayName -ErrorAction SilentlyContinue\n'
        f'      $script:updated++\n'
        f'      Write-DebugLog "uninstall_reg_updated key=$keyId display_version=$TargetVersion"\n'
        f'    }}\n'
        f'  }}\n'
        f'  if ($updated -eq 0) {{\n'
        f'    Write-DebugLog "uninstall_reg_not_found app_dir=$AppDirNorm"\n'
        f'  }}\n'
        f'}}\n'
        f'function Remove-ObsoleteVersions {{\n'
        f'  if (Test-Path -LiteralPath (Join-Path $ProductRoot "python")) {{\n'
        f'    Remove-Item -LiteralPath (Join-Path $ProductRoot "python") -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "flat_python_removed"\n'
        f'  }}\n'
        f'  if (Test-Path -LiteralPath (Join-Path $ProductRoot "src")) {{\n'
        f'    Remove-Item -LiteralPath (Join-Path $ProductRoot "src") -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "flat_src_removed"\n'
        f'  }}\n'
        f'  if (Test-Path -LiteralPath (Join-Path $ProductRoot "VoiceInput")) {{\n'
        f'    $orphan = Join-Path $ProductRoot "VoiceInput"\n'
        f'    if ((Test-Path -LiteralPath (Join-Path $orphan "python")) -or (Test-Path -LiteralPath (Join-Path $orphan "src"))) {{\n'
        f'      Remove-Item -LiteralPath $orphan -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'      Write-DebugLog "orphan_payload_removed path=$orphan"\n'
        f'    }}\n'
        f'  }}\n'
        f'  if (Test-Path -LiteralPath $ExeNew) {{\n'
        f'    Remove-Item -LiteralPath $ExeNew -Force -ErrorAction SilentlyContinue\n'
        f'  }}\n'
        f'  if (-not (Test-Path -LiteralPath $VersionsRoot)) {{ return }}\n'
        f'  Get-ChildItem -LiteralPath $VersionsRoot -Directory -ErrorAction SilentlyContinue | ForEach-Object {{\n'
        f'    if ($_.Name -ne $TargetVersion) {{\n'
        f'      Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'      Write-DebugLog "old_version_removed name=$($_.Name)"\n'
        f'    }}\n'
        f'  }}\n'
        f'  $legacyStaging = Join-Path $env:TEMP "{_LEGACY_STAGING_DIR_NAME}"\n'
        f'  if (Test-Path -LiteralPath $legacyStaging) {{\n'
        f'    Remove-Item -LiteralPath $legacyStaging -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "legacy_temp_staging_removed"\n'
        f'  }}\n'
        f'  Get-ChildItem -LiteralPath $env:TEMP -Directory -ErrorAction SilentlyContinue | Where-Object {{\n'
        f'    $_.Name.StartsWith("{_LEGACY_STAGING_DIR_NAME}{_LEGACY_STAGING_APPLIED_MARKER}")\n'
        f'  }} | ForEach-Object {{\n'
        f'    Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue\n'
        f'    Write-DebugLog "legacy_applied_staging_removed name=$($_.Name)"\n'
        f'  }}\n'
        f'}}\n'
        f'$TotalStart = Get-Date\n'
        f'Write-DebugLog "start mode=flip_pointer app_dir=$ProductRoot exe=$ExePath old_pid=$OldPid target=$TargetVersion version_dir=$VersionDir"\n'
        f'$StepStart = Get-Date\n'
        f'Wait-ForOldInstance\n'
        f'Write-DebugLog "wait_old_instance elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'Switch-ToPreparedVersion\n'
        f'Write-DebugLog "switch_prepared elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'Test-InstalledVersion\n'
        f'Write-DebugLog "verify_version elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'Update-UninstallRegistration\n'
        f'Write-DebugLog "update_uninstall_reg elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'$StepStart = Get-Date\n'
        f'try {{\n'
        f'  $NewProc = Start-Process $ExePath -PassThru -ErrorAction Stop\n'
        f'}} catch {{\n'
        f'  Abort-Install "start_process_failed error=$($_.Exception.Message)"\n'
        f'}}\n'
        f'$alive = $false\n'
        f'for ($poll = 1; $poll -le {_START_HEALTH_POLL_MAX}; $poll++) {{\n'
        f'  Start-Sleep -Milliseconds {_START_HEALTH_POLL_MS}\n'
        f'  if (Get-Process -Id $NewProc.Id -ErrorAction SilentlyContinue) {{\n'
        f'    $alive = $true\n'
        f'  }} else {{\n'
        f'    $alive = $false\n'
        f'    break\n'
        f'  }}\n'
        f'}}\n'
        f'Write-DebugLog "start_process pid=$($NewProc.Id) alive=$alive polls=$poll elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'if (-not $alive) {{\n'
        f'  Abort-Install "new_process_not_running pid=$($NewProc.Id) polls=$poll"\n'
        f'}}\n'
        f'$StepStart = Get-Date\n'
        f'Remove-ObsoleteVersions\n'
        f'Write-DebugLog "cleanup_old_versions elapsed_ms=$([int]((Get-Date) - $StepStart).TotalMilliseconds)"\n'
        f'Write-DebugLog "install_success version=$TargetVersion new_pid=$($NewProc.Id)"\n'
        f'Write-DebugLog "total elapsed_ms=$([int]((Get-Date) - $TotalStart).TotalMilliseconds)"\n'
        f'Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue\n'
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

    Shipped builds expose a stable ``VoiceInput.exe`` at the product root.
    PyInstaller onefile and bare dev checkouts cannot self-update.
    """
    try:
        return installed_exe_path() is not None
    except Exception:
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


@dataclass(frozen=True)
class StagedUpdate:
    version: str
    staging_dir: Path
    source_dir: Path


def _rmtree_onexc(func, path, exc):
    """Tolerate missing/locked entries during best-effort cleanup."""
    if isinstance(exc, (FileNotFoundError, NotADirectoryError, PermissionError)):
        return
    raise exc


def _rmtree_best_effort(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path, onexc=_rmtree_onexc)
    except OSError as exc:
        logger.warning(f"[Updater] Failed to remove {path}: {exc}")


class StagedUpdateStore:
    """Owns prepared version payloads under ``{product}/versions/``.

    Ready invariant: ``versions/{ver}/`` contains ``.update_version``, matching
    ``src/_version.py``, and ``python/`` + ``src/``. Extract writes to
    ``versions/{ver}.partial`` then renames into place.
    """

    def __init__(self, *, product_root: Path | None = None, temp_dir: Path | None = None):
        # ``temp_dir`` kept as a test alias for a fake product root.
        if product_root is not None:
            self.product_root = Path(product_root)
        elif temp_dir is not None:
            self.product_root = Path(temp_dir)
        else:
            self.product_root = install_root()
        self.versions_root = versions_dir(self.product_root)
        self.temp_dir = Path(tempfile.gettempdir())

    @property
    def staging_dir(self) -> Path:
        """Directory of the loaded staged version, or a non-existent sentinel."""
        staged = self.load()
        if staged is not None:
            return staged.staging_dir
        return self.versions_root / "__none__"

    def clear(self) -> None:
        """Remove non-active staged / partial payloads and legacy TEMP leftovers."""
        current = read_current_version(self.product_root)
        if self.versions_root.is_dir():
            for child in list(self.versions_root.iterdir()):
                if not child.is_dir():
                    continue
                name = child.name
                if name.endswith(PARTIAL_SUFFIX):
                    _rmtree_best_effort(child)
                    continue
                if name == current:
                    # Never delete the active version; drop a leftover stage marker.
                    marker = child / _STAGE_VERSION_FILE
                    if marker.is_file():
                        try:
                            marker.unlink()
                        except OSError:
                            pass
                    continue
                if (child / _STAGE_VERSION_FILE).is_file():
                    _rmtree_best_effort(child)
        exe_new = launcher_new_path(self.product_root)
        if exe_new.is_file():
            try:
                exe_new.unlink()
            except OSError as exc:
                logger.warning(f"[Updater] Failed to remove {exe_new}: {exc}")
        self._clear_legacy_temp_staging()

    def write_version(self, version: str, *, version_path: Path | None = None) -> None:
        target = version_path if version_path is not None else version_dir(version, self.product_root)
        (target / _STAGE_VERSION_FILE).write_text(version, encoding="utf-8")

    def load(self) -> StagedUpdate | None:
        """Load the newest structurally valid staged version payload."""
        candidates: list[StagedUpdate] = []
        if not self.versions_root.is_dir():
            return None
        for child in self.versions_root.iterdir():
            if not child.is_dir() or child.name.endswith(PARTIAL_SUFFIX):
                continue
            staged = self._load_version_dir(child)
            if staged is not None:
                candidates.append(staged)
        if not candidates:
            return None
        candidates.sort(key=lambda item: _parse_version(item.version))
        return candidates[-1]

    def load_applicable(self, *, newer_than: str) -> StagedUpdate | None:
        """Return staging only when valid and strictly newer than ``newer_than``."""
        staged = self.load()
        if staged is None:
            return None
        if not _is_newer(staged.version, newer_than):
            return None
        return staged

    def validate(self, expected_version: str) -> StagedUpdate | None:
        staged = self._load_version_dir(version_dir(expected_version, self.product_root))
        if staged is None:
            return None
        if staged.version != expected_version:
            return None
        return staged

    def sweep(self, *, newer_than: str) -> None:
        """Best-effort disk cleanup of non-Ready leftovers. Never raises."""
        try:
            if self.load_applicable(newer_than=newer_than) is None:
                logger.info(
                    f"[Updater] Sweeping non-applicable staged versions under "
                    f"{self.versions_root}"
                )
                self.clear()
            else:
                # Still remove abandoned .partial dirs and legacy TEMP trash.
                if self.versions_root.is_dir():
                    for child in list(self.versions_root.iterdir()):
                        if child.is_dir() and child.name.endswith(PARTIAL_SUFFIX):
                            logger.info(f"[Updater] Sweeping partial staging {child}")
                            _rmtree_best_effort(child)
                self._clear_legacy_temp_staging()
        except Exception:
            logger.opt(exception=True).warning("[Updater] Staging sweep failed")

    def _load_version_dir(self, path: Path) -> StagedUpdate | None:
        if not path.is_dir():
            return None
        version = self._read_staged_version(path)
        if not version:
            return None
        if not (path / "python").is_dir() or not (path / "src").is_dir():
            return None
        source_version = read_version_py(path / "src" / "_version.py")
        if source_version != version:
            logger.warning(
                f"[Updater] Staged update version mismatch; marker={version}, "
                f"source={path}"
            )
            return None
        return StagedUpdate(version=version, staging_dir=path, source_dir=path)

    def _read_staged_version(self, version_path: Path) -> str:
        ver_file = version_path / _STAGE_VERSION_FILE
        if not ver_file.is_file():
            return ""
        try:
            return ver_file.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _clear_legacy_temp_staging(self) -> None:
        legacy = self.temp_dir / _LEGACY_STAGING_DIR_NAME
        if legacy.exists():
            logger.info(f"[Updater] Sweeping legacy TEMP staging at {legacy}")
            _rmtree_best_effort(legacy)
        prefix = f"{_LEGACY_STAGING_DIR_NAME}{_LEGACY_STAGING_APPLIED_MARKER}"
        try:
            for child in self.temp_dir.iterdir():
                if child.is_dir() and child.name.startswith(prefix):
                    logger.info(f"[Updater] Sweeping legacy applied staging trash {child}")
                    _rmtree_best_effort(child)
        except OSError:
            pass


def _zip_member_relpath(member: str) -> str | None:
    """Map a zip member path to a relative path under the version payload."""
    name = member.replace("\\", "/")
    if not name or name.endswith("/"):
        return None
    if name.startswith(_ZIP_PAYLOAD_PREFIX):
        rel = name[len(_ZIP_PAYLOAD_PREFIX):]
    elif name == "VoiceInput":
        return None
    else:
        rel = name
    return rel or None


class _StageWorker(QThread):
    """Extract a downloaded zip once into ``versions/{ver}`` under the product root."""
    progress = pyqtSignal(int)   # percent 0-100
    finished_ok = pyqtSignal(str)  # ready version directory path
    failed = pyqtSignal(str)

    def __init__(self, zip_path: str, version: str, *, product_root: Path | None = None):
        super().__init__()
        self._zip_path = zip_path
        self._version = version
        self._product_root = product_root

    def run(self):
        started = time.perf_counter()
        store = StagedUpdateStore(product_root=self._product_root)
        version = self._version
        partial = partial_version_dir(version, store.product_root)
        ready = version_dir(version, store.product_root)
        logger.debug(
            f"[DEBUG] _StageWorker.run | zip={self._zip_path}, partial={partial}, ready={ready}"
        )
        try:
            if not version:
                raise ValueError("missing target version for staging")
            store.versions_root.mkdir(parents=True, exist_ok=True)
            if partial.exists():
                clean_started = time.perf_counter()
                _rmtree_best_effort(partial)
                logger.debug(
                    f"[DEBUG] _StageWorker.run | clean_existing_partial "
                    f"elapsed_ms={_elapsed_ms(clean_started)}"
                )
            mkdir_started = time.perf_counter()
            partial.mkdir(parents=True, exist_ok=True)
            logger.debug(
                f"[DEBUG] _StageWorker.run | mkdir_partial elapsed_ms={_elapsed_ms(mkdir_started)}"
            )
            extract_started = time.perf_counter()
            staged_exe: Path | None = None
            with zipfile.ZipFile(self._zip_path, "r") as zf:
                members = [m for m in zf.namelist() if _zip_member_relpath(m) is not None]
                total = len(members)
                total_uncompressed = sum(
                    info.file_size for info in zf.infolist()
                    if _zip_member_relpath(info.filename) is not None
                )
                logger.debug(
                    f"[DEBUG] _StageWorker.run | zip_opened members={total}, "
                    f"uncompressed_bytes={total_uncompressed}"
                )
                last_progress_log = 0
                for i, member in enumerate(members, 1):
                    rel = _zip_member_relpath(member)
                    assert rel is not None
                    if rel == "VoiceInput.exe":
                        staged_exe = launcher_new_path(store.product_root)
                        staged_exe.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(staged_exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    else:
                        dest = partial / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(dest, "wb") as dst:
                            shutil.copyfileobj(src, dst)
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
            if not (partial / "python").is_dir() or not (partial / "src").is_dir():
                raise RuntimeError(f"staging tree incomplete under {partial}")
            version_started = time.perf_counter()
            store.write_version(version, version_path=partial)
            logger.debug(
                f"[DEBUG] _StageWorker.run | write_version elapsed_ms={_elapsed_ms(version_started)}"
            )
            current = read_current_version(store.product_root)
            if ready.exists():
                if ready.name == current:
                    raise RuntimeError(
                        f"refusing to replace active version directory {ready}"
                    )
                _rmtree_best_effort(ready)
            rename_started = time.perf_counter()
            partial.rename(ready)
            logger.debug(
                f"[DEBUG] _StageWorker.run | rename_ready elapsed_ms={_elapsed_ms(rename_started)}, "
                f"exe_new={staged_exe}"
            )
            logger.info(f"[Updater] Staged {total} files to {ready} (v{version})")
            logger.debug(
                f"[DEBUG] _StageWorker.run | total_elapsed_ms={_elapsed_ms(started)}"
            )
            self.finished_ok.emit(str(ready))
        except Exception as e:
            logger.error(f"[Updater] Staging failed: {e}")
            logger.debug(
                f"[DEBUG] _StageWorker.run | exception={type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            _rmtree_best_effort(partial)
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
        self._staged: StagedUpdate | None = None
        self._staged_store = StagedUpdateStore()
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
        self._last_install_error: str | None = None
        self._idle_maintenance_scheduled = False

    @property
    def last_install_error(self) -> str | None:
        return self._last_install_error

    @property
    def install_log_path(self) -> Path:
        return _update_install_log_path()

    def _fail_install(self, message: str) -> bool:
        self._last_install_error = message
        logger.error(f"[Updater] Install failed: {message}")
        return False

    @property
    def latest(self) -> UpdateInfo | None:
        return self._latest

    @property
    def is_downloading(self) -> bool:
        return self._dl_worker is not None and self._dl_worker.isRunning()

    @property
    def is_staging(self) -> bool:
        return self._stage_worker is not None and self._stage_worker.isRunning()

    def background_workers(self) -> list[tuple[str, QThread]]:
        """Live updater threads that quit must wait for (or declare stuck)."""
        pairs = (
            ("update check", self._check_worker),
            ("update download", self._dl_worker),
            ("update staging", self._stage_worker),
        )
        return [(label, worker) for label, worker in pairs if worker is not None]

    @property
    def is_ready_to_install(self) -> bool:
        """True only when an applicable (newer-than-running) staging payload is held."""
        return (
            self._staged is not None
            and _is_newer(self._staged.version, VERSION)
            and self._staged.staging_dir.exists()
        )

    @property
    def staged_version(self) -> str:
        return self._staged.version if self._staged is not None else ""

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
        self._reconcile_staging()
        # Staging trash cleanup runs after recorder prepare finishes (see
        # ``schedule_idle_maintenance``), not on a blind timer.
        self._timer.start()
        self.check_now()

    def schedule_idle_maintenance(self):
        """Best-effort staging sweep once startup-critical work has finished."""
        if getattr(self, "_idle_maintenance_scheduled", False):
            return
        self._idle_maintenance_scheduled = True
        QTimer.singleShot(0, self._sweep_staging)

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

    def install_ready(self, version: str, *, quit_fn=None) -> bool:
        self._last_install_error = None
        applicable = self._staged_store.load_applicable(newer_than=VERSION)
        if applicable is not None and applicable.version == version:
            self._staged = applicable
            return self.install(quit_fn=quit_fn)

        loaded = self._staged_store.load()
        if loaded is not None and loaded.version != version:
            self._staged = applicable
            return self._fail_install(
                f"安装请求已过期：请求 v{version}，"
                f"当前 staging 为 v{loaded.version}"
            )

        self._staged = None
        self._staged_store.clear()
        return self._fail_install(f"已下载的 v{version} 更新包无效或已损坏，请重新下载")

    def install(self, *, quit_fn=None) -> bool:
        """Flip current.txt to the prepared version and restart."""
        started = time.perf_counter()
        if self._staged is None:
            logger.debug("[DEBUG] UpdateChecker.install | no staged update, returning")
            return self._fail_install("没有可用的 staging 更新")
        staged_update = self._staged
        staged = staged_update.staging_dir
        if not staged.exists():
            self._staged = None
            return self._fail_install(f"更新暂存目录不存在: {staged}")
        app_dir = install_root()
        exe_path = installed_exe_path()
        if exe_path is None:
            return self._fail_install("当前运行方式无法确定安装目录")
        old_pid = os.getpid()
        target_version = staged_update.version
        logger.info(
            f"[Updater] Switching to prepared v{target_version} at "
            f"{app_dir / 'versions' / target_version} (old_pid={old_pid})"
        )
        script = Path(tempfile.gettempdir()) / "voiceinput_update.ps1"
        install_log = _update_install_log_path()
        logger.debug(
            f"[DEBUG] UpdateChecker.install | app_dir={app_dir}, "
            f"exe_path={exe_path}, version_dir={staged}, script={script}, "
            f"install_log={install_log}, old_pid={old_pid}, target_version={target_version}"
        )
        build_started = time.perf_counter()
        ps_content = _build_install_script(
            app_dir=app_dir,
            exe_path=exe_path,
            log_path=install_log,
            old_pid=old_pid,
            target_version=target_version,
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
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
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
            logger.debug(
                f"[DEBUG] UpdateChecker.install | launch_exception={type(e).__name__}: {e}, "
                f"total_elapsed_ms={_elapsed_ms(started)}"
            )
            return self._fail_install(str(e))
        self._last_install_error = None
        if quit_fn is not None:
            logger.debug("[DEBUG] UpdateChecker.install | calling quit_fn()")
            quit_fn()
        else:
            logger.debug("[DEBUG] UpdateChecker.install | calling QApplication.quit()")
            from PyQt6.QtWidgets import QApplication
            QApplication.quit()
        return True

    # ── internal callbacks ──

    def _on_check_result(self, result):
        logger.debug(f"[DEBUG] UpdateChecker._on_check_result | result type={type(result).__name__}, value={result!r}")
        if isinstance(result, UpdateInfo):
            self._latest = result
            self._downloaded_path = None
            if self._sync_staging_for_latest(result.version):
                return
            if self._cb_available:
                self._cb_available(result)
        elif result == _NO_UPDATE:
            self._clear_staging_when_no_update()
            if self._cb_no_update:
                self._cb_no_update()
        elif result == _CHECK_ERROR:
            if self._restore_staging_after_check_failure():
                return
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
        if self._cb_dl_done:
            self._cb_dl_done()
        self._start_staging(path)

    def _on_dl_failed(self, msg: str):
        logger.debug(f"[DEBUG] UpdateChecker._on_dl_failed | msg={msg}")
        if self._cb_dl_failed:
            self._cb_dl_failed(msg)

    # ── staging ──

    def _reconcile_staging(self) -> None:
        """Sync in-memory Ready state from disk. Never deletes on this path."""
        self._staged = self._staged_store.load_applicable(newer_than=VERSION)
        if self._staged is not None:
            logger.info(
                f"[Updater] Reconciled applicable staged v{self._staged.version}"
            )

    def _sweep_staging(self) -> None:
        """Deferred best-effort cleanup of obsolete/applied staging trash."""
        self._staged_store.sweep(newer_than=VERSION)
        # Re-read in case sweep removed something we thought we held.
        if self._staged is not None and not self.is_ready_to_install:
            self._staged = self._staged_store.load_applicable(newer_than=VERSION)

    def _sync_staging_for_latest(self, version: str) -> bool:
        """Keep only the staging payload that matches the latest release."""
        staged = self._staged_store.load_applicable(newer_than=VERSION)
        if staged is None:
            self._staged = None
            return False

        if staged.version != version:
            logger.info(
                f"[Updater] Discarding staged v{staged.version}; "
                f"newer v{version} is available"
            )
            self._staged_store.clear()
            self._staged = None
            return False

        logger.info(f"[Updater] Reusing existing staging directory for v{version}")
        self._staged = staged
        # Reuse must not auto-prompt — user already saw (or dismissed) the dialog.
        self._emit_stage_done(prompt=False)
        return True

    def _restore_staging_after_check_failure(self) -> bool:
        staged = self._staged_store.load_applicable(newer_than=VERSION)
        if staged is None:
            self._staged = None
            return False
        logger.info(
            f"[Updater] Update check did not find a newer release; "
            f"keeping staged v{staged.version}"
        )
        self._staged = staged
        self._emit_stage_done(prompt=False)
        return True

    def _emit_stage_done(self, *, prompt: bool) -> None:
        """Notify UI that a staged payload is ready.

        ``prompt=True`` only after a fresh download/extract. Restoring an
        already-staged package on later checks must use ``prompt=False`` so the
        ready dialog does not keep popping open.
        """
        if not self.is_ready_to_install:
            return
        if self._cb_stage_done:
            self._cb_stage_done(prompt)

    def _clear_staging_when_no_update(self) -> None:
        self._staged = None
        if self._staged_store.load() is not None:
            logger.info("[Updater] Discarding staged update because no newer release is available")
            self._staged_store.clear()

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
        expected_version = self._latest.version if self._latest else ""
        staged = self._staged_store.validate(expected_version)
        if staged is None or not _is_newer(staged.version, VERSION):
            self._staged = None
            self._staged_store.clear()
            msg = "更新包版本校验失败，请重新下载"
            logger.error(f"[Updater] {msg} (expected={expected_version})")
            if self._cb_stage_failed:
                self._cb_stage_failed(msg)
            return
        self._staged = staged
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
        self._emit_stage_done(prompt=True)

    def _on_stage_failed(self, msg: str):
        logger.error(f"[Updater] Staging failed: {msg}")
        if self._cb_stage_failed:
            self._cb_stage_failed(msg)

    def _cleanup_check(self):
        if self._check_worker:
            self._check_worker.deleteLater()
            self._check_worker = None
