import sys
import os
import signal
import ctypes
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from core.platform_guard import apply_wmi_hang_guard

# Must run before importing any library that calls platform.system()/uname() at
# import time (e.g. aiohttp via dashscope): on Python 3.12 those go through WMI,
# which hangs forever if the WMI service is wedged. See core/platform_guard.py.
apply_wmi_hang_guard()

from core.network import configure_direct_business_traffic

# Ctrl+C 直接终止进程，不抛 KeyboardInterrupt 到随机线程
signal.signal(signal.SIGINT, signal.SIG_DFL)

# Business APIs use direct connections; GitHub update traffic opts into proxy separately.
configure_direct_business_traffic()

from PyQt6.QtCore import Qt, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from core.config_sync import ConfigSync
from core.log import flush_log, logger, install_qt_handler, log_event
from core.engine import VoiceEngine
from ui import icons
from ui.config_dialog import ConfigFaultHandler, load_config_at_startup
from ui.mini_window import MiniRecordingWindow
from ui.save_shortcut import install_ctrl_s_save_shortcut
from ui.tray import VoiceTray
from ui.fault_coordinator import FaultCoordinator
from ui.notifier import Notifier

_APP_KEY = "VoiceInput_SingleInstance_Lock"
_APP_MUTEX_NAME = "VoiceInput_InstallAware_Mutex"
_SHUTDOWN_EVENT_NAME = "VoiceInput_Shutdown_Event"
_app_mutex_handle = None
_shutdown_event_handle = None
_shutdown_bridge = None


def _runtime_fields() -> dict[str, str]:
    from _version import VERSION

    return {
        "version": VERSION,
        "main": str(Path(__file__).resolve()),
        "exe": str(Path(sys.executable).resolve()),
        "cwd": str(Path.cwd()),
    }


def _is_already_running() -> bool:
    """Try connecting to an existing instance. Returns True if one is found."""
    sock = QLocalSocket()
    sock.connectToServer(_APP_KEY)
    connected = sock.waitForConnected(200)
    sock.close()
    return connected


def _create_app_mutex():
    """Create a named Win32 mutex so installers can detect a running app."""
    global _app_mutex_handle
    if sys.platform != "win32":
        return
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, _APP_MUTEX_NAME)
    if not handle:
        logger.warning("Failed to create install-aware app mutex")
        return
    _app_mutex_handle = handle
    logger.debug(f"[DEBUG] _create_app_mutex | handle={handle}, name={_APP_MUTEX_NAME}")


def _release_app_mutex():
    global _app_mutex_handle
    if not _app_mutex_handle:
        return
    logger.debug(f"[DEBUG] _release_app_mutex | closing handle={_app_mutex_handle}")
    ctypes.windll.kernel32.CloseHandle(_app_mutex_handle)
    _app_mutex_handle = None


def _create_shutdown_event():
    global _shutdown_event_handle
    if sys.platform != "win32":
        return
    handle = ctypes.windll.kernel32.CreateEventW(None, True, False, _SHUTDOWN_EVENT_NAME)
    if not handle:
        logger.debug(f"[DEBUG] _create_shutdown_event | FAILED to create event, name={_SHUTDOWN_EVENT_NAME}")
        return
    _shutdown_event_handle = handle
    logger.debug(f"[DEBUG] _create_shutdown_event | handle={handle}, name={_SHUTDOWN_EVENT_NAME}")


def _release_shutdown_event():
    global _shutdown_event_handle
    if not _shutdown_event_handle:
        return
    ctypes.windll.kernel32.CloseHandle(_shutdown_event_handle)
    _shutdown_event_handle = None


class _ShutdownBridge(QObject):
    """Bridges the shutdown event from a background thread to the main thread via Qt signal."""
    shutdown_requested = pyqtSignal()


def _preload_business_sdks():
    """Import the ASR/polish SDKs off the critical path.

    dashscope/openai/httpx together cost ~0.7 s to import, so asr.py and
    polisher.py defer them to first use.  Warming them here (after the tray is
    visible) means the first recording doesn't pay the import cost either —
    Python's import lock makes a concurrent first use simply wait, never fail.
    """
    def _load():
        try:
            import dashscope  # noqa: F401
            import httpx  # noqa: F401
            import openai  # noqa: F401
            logger.debug("[DEBUG] _preload_business_sdks | SDK imports warmed")
        except Exception:
            logger.opt(exception=True).warning("Business SDK preload failed")

    threading.Thread(target=_load, name="SdkPreload", daemon=True).start()


def _start_shutdown_watcher(quit_fn):
    """Background thread that waits for the shutdown event, then invokes quit on the main thread."""
    global _shutdown_bridge

    _shutdown_bridge = _ShutdownBridge()
    _shutdown_bridge.shutdown_requested.connect(quit_fn, Qt.ConnectionType.QueuedConnection)

    def _watch():
        if not _shutdown_event_handle:
            logger.debug("[DEBUG] _watch | no shutdown_event_handle, watcher not started")
            return
        logger.debug(f"[DEBUG] _watch | waiting for shutdown event, handle={_shutdown_event_handle}")
        INFINITE = 0xFFFFFFFF
        ctypes.windll.kernel32.WaitForSingleObject(_shutdown_event_handle, INFINITE)
        logger.info("[Main] Shutdown event received from installer")
        logger.debug("[DEBUG] _watch | emitting shutdown_requested signal")
        _shutdown_bridge.shutdown_requested.emit()

    t = threading.Thread(target=_watch, name="ShutdownWatcher", daemon=True)
    t.start()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    install_qt_handler()

    if _is_already_running():
        log_event("WARNING", "app.single_instance.blocked", "VoiceInput is already running")
        sys.exit(0)

    # Config must be validated before any application icon or tray UI appears.
    config = load_config_at_startup()
    install_ctrl_s_save_shortcut(app, lambda: config.hotkey)

    app.setApplicationName("VoiceInput")
    app.setWindowIcon(icons.app_icon())
    log_event("INFO", "app.lifecycle.start", "Application runtime initialized", **_runtime_fields())

    server = QLocalServer()
    server.removeServer(_APP_KEY)
    server.listen(_APP_KEY)
    _create_app_mutex()
    _create_shutdown_event()
    app.aboutToQuit.connect(_release_app_mutex)
    app.aboutToQuit.connect(_release_shutdown_event)
    app.aboutToQuit.connect(lambda: log_event("INFO", "app.qt.about_to_quit", "QApplication aboutToQuit"))
    app.aboutToQuit.connect(flush_log)

    config_sync = ConfigSync(config)
    app.aboutToQuit.connect(config_sync.stop)

    if not config.api_key:
        from config import _config_path
        log_event("WARNING", "config.api_key.missing", "API key not configured")
        log_event("INFO", "config.api_key.hint", "Configure API key via environment or config file", config_path=_config_path())

    engine = VoiceEngine(config)
    mini = MiniRecordingWindow(engine)
    tray = VoiceTray(engine, mini, config, config_sync)
    notifier = Notifier(tray, parent=app)
    tray.set_notifier(notifier)
    config_sync.config_reloaded.connect(tray.on_config_reloaded)
    config_sync.bind_idle_checker(tray.is_idle_for_config_reload)
    faults = FaultCoordinator(engine, tray, config_sync, notifier, parent=app)
    config_fault = ConfigFaultHandler(config, config_sync, faults, parent=app)
    faults.set_config_fault_handler(config_fault)
    config_sync.set_disk_fault_handler(faults.on_config_disk_save_blocked)
    faults.bind_tray()
    faults.initialize(config)
    config_fault.mark_app_ready()
    _start_shutdown_watcher(tray._quit)

    hotkey_display = config.hotkey.replace("+", " + ").title()
    log_event("SUCCESS", "app.ready", "VoiceInput started", hotkey=hotkey_display)

    tray.reveal()
    mini.refresh_visibility()
    config_sync.start()
    _preload_business_sdks()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
