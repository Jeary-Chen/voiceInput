"""Windows HKCU Run + StartupApproved autostart (source of truth for boot behavior)."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, QThread, pyqtSignal, Qt

if sys.platform == "win32":
    import ctypes
    import winreg
    from ctypes import wintypes

    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32

    advapi32.RegNotifyChangeKeyValue.argtypes = [
        wintypes.HKEY, wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE, wintypes.BOOL,
    ]
    advapi32.RegNotifyChangeKeyValue.restype = wintypes.LONG

    _REG_NOTIFY_FILTER = 0x00000001 | 0x00000004  # NAME | LAST_SET
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 0x00000102

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "VoiceInput"
_ENABLED_APPROVED = b"\x02" + b"\x00" * 11
_DISABLED_APPROVED_PREFIXES = (0x03, 0x06)


def read_enabled() -> bool:
    """True only when Run entry exists and Explorer has not disabled startup."""
    if sys.platform != "win32":
        return False
    if not _query_run_command():
        return False
    return _is_startup_approved()


def write_enabled(enabled: bool, command: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("当前平台不支持开机自启")
    if enabled:
        if not command:
            raise ValueError("autostart command is required when enabling")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_BINARY, _ENABLED_APPROVED)
        return
    _delete_value(RUN_KEY)
    _delete_value(APPROVED_KEY)


def _query_run_command() -> str | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE,
        ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def _is_startup_approved() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, APPROVED_KEY, 0, winreg.KEY_QUERY_VALUE,
        ) as key:
            data, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return True
    except OSError:
        return True
    if not isinstance(data, (bytes, bytearray)) or not data:
        return True
    return data[0] not in _DISABLED_APPROVED_PREFIXES


def _delete_value(key_path: str) -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        return


def _open_notify_key(path: str):
    try:
        return winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            path,
            0,
            winreg.KEY_NOTIFY | winreg.KEY_READ,
        )
    except OSError:
        return None


class _RegistryNotifyWorker(QThread):
    """Background thread blocked on RegNotifyChangeKeyValue for Run + StartupApproved."""

    registry_changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._stop = False
        self._stop_handle: int | None = None

    def stop(self) -> None:
        self._stop = True
        handle = self._stop_handle
        if handle:
            kernel32.SetEvent(handle)

    def run(self) -> None:
        if sys.platform != "win32":
            return

        keys = []
        for path in (RUN_KEY, APPROVED_KEY):
            key = _open_notify_key(path)
            if key is not None:
                keys.append(key)
        if not keys:
            return

        events: list[int] = []
        stop_handle = kernel32.CreateEventW(None, True, False, None)
        self._stop_handle = stop_handle
        events.append(stop_handle)
        for _ in keys:
            events.append(kernel32.CreateEventW(None, True, False, None))

        notify_handles = (wintypes.HANDLE * len(events))(*events)

        def _arm() -> bool:
            for key, event in zip(keys, events[1:], strict=True):
                rc = advapi32.RegNotifyChangeKeyValue(
                    wintypes.HKEY(key.handle),
                    True,
                    _REG_NOTIFY_FILTER,
                    wintypes.HANDLE(event),
                    True,
                )
                if rc != 0:
                    return False
            return True

        try:
            if not _arm():
                return
            while not self._stop:
                wait = kernel32.WaitForMultipleObjects(
                    len(events), notify_handles, False, 1000,
                )
                if self._stop or wait == _WAIT_OBJECT_0:
                    break
                if wait == _WAIT_TIMEOUT:
                    continue
                self.registry_changed.emit()
                for event in events[1:]:
                    kernel32.ResetEvent(event)
                if not _arm():
                    break
        finally:
            for event in events:
                kernel32.CloseHandle(event)
            self._stop_handle = None
            for key in keys:
                key.Close()


class AutostartWatcher(QObject):
    """Emit when effective autostart state changes (registry notification)."""

    changed = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._last = read_enabled()
        self._worker = _RegistryNotifyWorker()
        self._worker.registry_changed.connect(
            self._on_registry_changed,
            Qt.ConnectionType.QueuedConnection,
        )

    def start(self) -> None:
        if sys.platform != "win32":
            return
        self._last = read_enabled()
        if not self._worker.isRunning():
            self._worker.start()

    def stop(self) -> None:
        if self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)

    def mark_current(self) -> None:
        """Refresh cached state after this app writes the registry."""
        self._last = read_enabled()

    def _on_registry_changed(self) -> None:
        actual = read_enabled()
        if actual == self._last:
            return
        self._last = actual
        self.changed.emit()
