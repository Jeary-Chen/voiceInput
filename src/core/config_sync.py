"""Runtime config file sync: watch disk, reload in-memory config, safe save with merge."""
from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtCore import QFileSystemWatcher

from config import Config, LoadStatus, ReloadResult, _config_path, _ordered_root_config
from core.log import logger

if TYPE_CHECKING:
    pass

_TAG = "[ConfigSync]"

_DEBOUNCE_MS = 400
_SUPPRESS_AFTER_WRITE_MS = 250
_CORRUPT_RETRY_COUNT = 3
_CORRUPT_RETRY_MS = 250


class ConfigSync(QObject):
    """Keeps a live Config object aligned with config.json on disk.

    External reloads are applied only while the app is idle (ready). During
    recording/processing, changes are queued and flushed when idle.

    Data-flow summary
    -----------------
    Outbound (memory → disk):
        save(touched=...) dispatches to one of three paths:
        • _save_with_merge  — idle + disk conflict  → full merge + write
        • _save_partial     — busy + disk conflict  → patch touched fields, queue merge
        • _guarded_write    — no conflict            → full write

    Inbound (disk → memory):
        QFileSystemWatcher → debounce → _request_external_reload
        • idle  → _apply_external_reload  (immediate)
        • busy  → _pending_reload = True  (deferred until flush)
    """

    config_reloaded = pyqtSignal(frozenset)
    config_disk_fault = pyqtSignal()
    config_disk_recovered = pyqtSignal()
    apply_started = pyqtSignal()
    apply_finished = pyqtSignal()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(self, config: Config, parent: QObject | None = None):
        super().__init__(parent)
        self._config = config
        object.__setattr__(config, "_save_hook", self.save)
        self._sync_token: tuple[int, int] | None = None
        self._writing = False
        self._suppress_until = 0.0
        self._pending_reload = False
        self._applying = False
        self._is_idle: Callable[[], bool] | None = None
        self._corrupt_retries = 0
        self._disk_fault_active = False
        self._disk_fault_handler: Callable[[], None] | None = None

        self._corrupt_retry = QTimer(self)
        self._corrupt_retry.setSingleShot(True)
        self._corrupt_retry.setInterval(_CORRUPT_RETRY_MS)
        self._corrupt_retry.timeout.connect(self._on_corrupt_retry)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._on_debounce_timeout)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        self._capture_sync_token()

    def bind_idle_checker(self, checker: Callable[[], bool]) -> None:
        self._is_idle = checker

    def set_disk_fault_handler(self, handler: Callable[[], None] | None) -> None:
        """Notify when save is attempted while on-disk config is unreadable."""
        self._disk_fault_handler = handler

    def start(self) -> None:
        path = str(_config_path())
        if _config_path().exists():
            self._watcher.addPath(path)
        logger.info(f"{_TAG} Watching {path}")

    def stop(self) -> None:
        self._debounce.stop()
        path = str(_config_path())
        if path in self._watcher.files():
            self._watcher.removePath(path)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> Config:
        return self._config

    @property
    def is_updating(self) -> bool:
        return self._applying

    @property
    def has_pending_reload(self) -> bool:
        return self._pending_reload

    @property
    def disk_fault_active(self) -> bool:
        return self._disk_fault_active

    @property
    def blocks_recording(self) -> bool:
        return self._applying or self._pending_reload or self._debounce.isActive()

    # ------------------------------------------------------------------
    # Outbound: memory → disk  (called by UI / engine)
    # ------------------------------------------------------------------

    def save(self, *, touched: frozenset[str] | None = None) -> None:
        """Persist in-memory config to disk, merging with external edits if needed."""
        if self._disk_fault_active:
            logger.debug(f"{_TAG} Save skipped (disk fault active)")
            if self._disk_fault_handler is not None:
                self._disk_fault_handler()
            return
        fields = (
            touched
            if touched is not None
            else frozenset(self._config.__dataclass_fields__)
        )
        conflict = self._disk_changed_since_sync()

        if conflict and self._can_apply_reload():
            self._save_with_merge(fields)
        elif conflict:
            self._save_partial(fields)
        else:
            self._guarded_write()

    def _save_with_merge(self, touched: frozenset[str]) -> None:
        """Idle + disk conflict: merge external changes, preserve our touched fields."""
        preserved = {
            name: copy.deepcopy(getattr(self._config, name))
            for name in touched
            if name in self._config.__dataclass_fields__
        }
        reloaded = self._reload_memory(source="pre_save")
        for name, value in preserved.items():
            setattr(self._config, name, value)
        self._guarded_write()

        externally_changed = frozenset(reloaded) - touched
        if externally_changed:
            self._applying = True
            self.apply_started.emit()
            try:
                self._emit_reload(externally_changed, source="pre_save")
            finally:
                self._applying = False
                self.apply_finished.emit()

    def _save_partial(self, touched: frozenset[str]) -> None:
        """Busy + disk conflict: patch only our fields to disk, queue full merge."""
        self._guarded_write(only=touched)
        self._pending_reload = True
        logger.info(f"{_TAG} Queued merge after save (busy)")

    # ------------------------------------------------------------------
    # Inbound: disk → memory  (watcher / flush)
    # ------------------------------------------------------------------

    def flush_pending_reload(self) -> None:
        """Apply queued external reload — called when the app becomes idle."""
        if not self._pending_reload:
            return
        if not self._can_apply_reload():
            return
        self._pending_reload = False
        self._apply_external_reload("queued")

    def reload_from_disk(self, *, source: str = "watch", emit: bool = True) -> frozenset[str]:
        """Read config.json into memory. Prefer _request_external_reload for watchers."""
        if self._writing or time.monotonic() < self._suppress_until:
            return frozenset()
        changed = self._reload_memory(source=source)
        if changed and emit:
            self._emit_reload(frozenset(changed), source=source)
        return frozenset(changed)

    def try_recover_from_disk(self) -> bool:
        """When disk becomes readable again, reload memory and clear fault state."""
        self._corrupt_retries = _CORRUPT_RETRY_COUNT
        self._corrupt_retry.stop()
        changed = self._reload_memory(source="fault_recovery")
        if self._disk_fault_active:
            return False
        if changed:
            self._emit_reload(frozenset(changed), source="fault_recovery")
        return True

    def _request_external_reload(self, source: str) -> None:
        """Decide whether to apply or queue an external reload.

        Five cases:
        1. disk unchanged, no pending          → nothing to do
        2. disk unchanged, pending, idle       → apply (resolve stale pending)
        3. disk unchanged, pending, busy       → leave pending for flush
        4. disk changed, idle                  → apply
        5. disk changed, busy                  → queue
        """
        disk_changed = self._disk_changed_since_sync()
        if not disk_changed and not self._pending_reload:
            return
        if self._can_apply_reload():
            self._apply_external_reload(source)
        elif disk_changed:
            self._pending_reload = True
            logger.info(f"{_TAG} Queued external reload ({source}, busy)")

    def _apply_external_reload(self, source: str) -> None:
        if self._applying or not self._can_apply_reload():
            if self._disk_changed_since_sync():
                self._pending_reload = True
            return
        self._applying = True
        self.apply_started.emit()
        try:
            changed = self._reload_memory(source=source)
            if changed:
                self._emit_reload(frozenset(changed), source=source)
            self._pending_reload = False
        finally:
            self._applying = False
            self.apply_finished.emit()

    # ------------------------------------------------------------------
    # Disk I/O (unified write path)
    # ------------------------------------------------------------------

    def _guarded_write(self, *, only: frozenset[str] | None = None) -> None:
        """Write to disk with watcher suppression.

        ``only=None`` (default) writes all memory fields via Config._write_to_disk.
        ``only=frozenset(...)`` patches just those fields on the existing JSON.
        """
        self._writing = True
        try:
            if only is not None:
                self._patch_disk_json(only)
            else:
                self._config._write_to_disk()
            self._capture_sync_token()
        finally:
            self._writing = False
            self._suppress_until = time.monotonic() + (_SUPPRESS_AFTER_WRITE_MS / 1000)
        if only is None:
            QTimer.singleShot(
                _SUPPRESS_AFTER_WRITE_MS + 10,
                self._check_external_change_after_write,
            )

    def _guarded_migration_persist(self, fields: frozenset[str]) -> None:
        """Persist normalized defaults after reload (backup + watcher suppression)."""
        if not fields:
            return
        self._writing = True
        try:
            Config.persist_migration(self._config, fields)
        finally:
            self._writing = False
            self._suppress_until = time.monotonic() + (_SUPPRESS_AFTER_WRITE_MS / 1000)

    def _patch_disk_json(self, fields: frozenset[str]) -> None:
        """Low-level: overwrite *fields* in on-disk JSON, keep everything else."""
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        on_disk: dict = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    on_disk = json.load(f)
                if not isinstance(on_disk, dict):
                    on_disk = {}
            except Exception:
                on_disk = {}
        for name in fields:
            if name in self._config.__dataclass_fields__:
                on_disk[name] = copy.deepcopy(getattr(self._config, name))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_ordered_root_config(on_disk), f, indent=2, ensure_ascii=False)

    def _reload_memory(self, *, source: str) -> set[str]:
        result = self._read_disk_into_memory(source=source)
        return self._handle_reload_result(result, source=source)

    def _read_disk_into_memory(self, *, source: str) -> ReloadResult:
        try:
            return Config.reload_into(self._config, fill_env_api_key=False)
        except Exception as e:
            logger.warning(f"{_TAG} Reload failed ({source}): {e}")
            return ReloadResult(frozenset(), LoadStatus.CORRUPT)

    def _handle_reload_result(self, result: ReloadResult, *, source: str) -> set[str]:
        if result.status in (LoadStatus.CORRUPT, LoadStatus.MISSING):
            if self._corrupt_retries < _CORRUPT_RETRY_COUNT:
                self._corrupt_retries += 1
                logger.warning(
                    f"{_TAG} Config unreadable ({source}), "
                    f"retry {self._corrupt_retries}/{_CORRUPT_RETRY_COUNT}"
                )
                self._corrupt_retry.start()
                return set()

            self._corrupt_retries = 0
            if not self._disk_fault_active:
                self._disk_fault_active = True
                logger.error(
                    f"{_TAG} Config file unreadable ({result.status.value}); "
                    "memory unchanged"
                )
                self.config_disk_fault.emit()
            return set()

        self._corrupt_retries = 0
        self._corrupt_retry.stop()
        if self._disk_fault_active:
            self._disk_fault_active = False
            self.config_disk_recovered.emit()

        if result.migration_fields:
            self._guarded_migration_persist(result.migration_fields)

        self._capture_sync_token()
        changed = set(result.changed)
        if changed:
            logger.debug(
                f"{_TAG} Memory updated ({source}): {', '.join(sorted(changed))}"
            )
        return changed

    def _on_corrupt_retry(self) -> None:
        self._request_external_reload("retry")

    # ------------------------------------------------------------------
    # Watcher plumbing
    # ------------------------------------------------------------------

    def _check_external_change_after_write(self) -> None:
        if self._writing or time.monotonic() < self._suppress_until:
            return
        self._request_external_reload("after_write")

    def _on_file_changed(self, _path: str) -> None:
        path = str(_config_path())
        if path not in self._watcher.files() and _config_path().exists():
            self._watcher.addPath(path)
        if self._writing or time.monotonic() < self._suppress_until:
            return
        self._pending_reload = True
        self._debounce.start()

    def _on_debounce_timeout(self) -> None:
        self._request_external_reload("watch")

    # ------------------------------------------------------------------
    # Sync token
    # ------------------------------------------------------------------

    def _can_apply_reload(self) -> bool:
        return (
            not self._applying
            and self._is_idle is not None
            and self._is_idle()
        )

    def _emit_reload(self, changed: frozenset[str] | set[str], *, source: str) -> None:
        fields = frozenset(changed)
        logger.info(
            f"{_TAG} Reloaded from disk ({source}): "
            f"{', '.join(sorted(fields))}"
        )
        self.config_reloaded.emit(fields)

    def _file_stat(self) -> tuple[int, int] | None:
        path = _config_path()
        if not path.exists():
            return None
        st = path.stat()
        return st.st_mtime_ns, st.st_size

    def _capture_sync_token(self) -> None:
        self._sync_token = self._file_stat()

    def _disk_changed_since_sync(self) -> bool:
        current = self._file_stat()
        if current is None or self._sync_token is None:
            return False
        return current != self._sync_token
