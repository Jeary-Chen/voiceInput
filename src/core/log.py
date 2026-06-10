"""Centralized logging via loguru.

Logs to:
  - Console (colorized, INFO level)
  - Session file (~/.voiceinput/logs/voiceinput_YYYY-MM-DD_HH-MM-SS.log, DEBUG level)
    One file per process run, from startup through exit; all levels in the same file.

Crash capture:
  - sys.excepthook / threading.excepthook / sys.unraisablehook
  - faulthandler (segfault, abort, fatal native errors)
  - Direct fsync backup writes when hooks fire
  - Normal shutdown marker via atexit (absence implies abrupt exit)

Usage:
  from core.log import logger, session_log_path
"""
from __future__ import annotations

import atexit
import faulthandler
import os
import signal
import sys
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

_LOG_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FMT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} | {message}"
_RETENTION_DAYS = 7

_startup_ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
_session_log = _LOG_DIR / f"voiceinput_{_startup_ts}.log"
session_log_path = _session_log

_crash_log_fp = None
_shutdown_logged = False


def _cleanup_old_logs():
    cutoff = datetime.now() - timedelta(days=_RETENTION_DAYS)
    for f in _LOG_DIR.glob("*.log"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except Exception:
            pass


def _write_direct(marker: str, detail: str = "") -> None:
    """Last-resort write that bypasses loguru and fsyncs immediately."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"{timestamp} | CRASH   | core.log:_write_direct:0 | [{marker}] {detail}\n"
    try:
        with open(_session_log, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        pass


def flush_log() -> None:
    """Flush loguru sinks and fsync the session log file."""
    try:
        logger.complete()
    except Exception:
        pass
    try:
        with open(_session_log, "a", encoding="utf-8") as handle:
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        pass


def _record_fatal_event(kind: str, detail: str) -> None:
    _write_direct(kind, detail)
    flush_log()


def _exception_hook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    logger.opt(exception=(exc_type, exc_value, exc_tb)).critical(
        f"Unhandled exception: {exc_type.__name__}: {exc_value}"
    )
    _record_fatal_event(
        "UNHANDLED_EXCEPTION",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).strip(),
    )


def _thread_exception_hook(args):
    if args.exc_type is SystemExit:
        return
    logger.opt(exception=(args.exc_type, args.exc_value, args.exc_traceback)).error(
        f"Thread '{args.thread.name}' exception: {args.exc_type.__name__}: {args.exc_value}"
    )
    _record_fatal_event(
        f"THREAD_EXCEPTION:{args.thread.name}",
        "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback
        )).strip(),
    )


def _unraisable_hook(unraisable):
    exc_type = unraisable.exc_type
    exc_value = unraisable.exc_value
    exc_tb = unraisable.exc_traceback
    err_msg = unraisable.err_msg or ""
    obj = unraisable.object
    obj_repr = repr(obj) if obj is not None else "<unknown>"
    logger.opt(exception=(exc_type, exc_value, exc_tb)).error(
        f"Unraisable exception on {obj_repr}: {err_msg}"
    )
    _record_fatal_event(
        "UNRAISABLE_EXCEPTION",
        f"object={obj_repr}; err_msg={err_msg}; "
        + "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).strip(),
    )


def _log_normal_shutdown() -> None:
    global _shutdown_logged
    if _shutdown_logged:
        return
    _shutdown_logged = True
    try:
        logger.info("[Runtime] Process exiting normally")
        flush_log()
    except Exception:
        _write_direct("SHUTDOWN", "Process exiting normally")


def _enable_faulthandler() -> None:
    global _crash_log_fp
    try:
        _crash_log_fp = open(_session_log, "a", encoding="utf-8")
    except OSError as exc:
        logger.warning(f"[Log] Failed to open crash log file: {exc}")
        return

    faulthandler.enable(file=_crash_log_fp, all_threads=True)
    _write_direct("FAULTHANDLER", "enabled")

    for sig in (getattr(signal, "SIGABRT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            faulthandler.register(sig, file=_crash_log_fp, all_threads=True, chain=True)
        except Exception:
            pass


def install_qt_handler():
    """Install Qt message handler to capture C++ level warnings/errors.
    Must be called after QApplication is created."""
    from PyQt6.QtCore import qInstallMessageHandler, QtMsgType

    def _qt_msg_handler(mode, context, message):
        if mode == QtMsgType.QtWarningMsg:
            logger.warning(f"[Qt] {message}")
        elif mode == QtMsgType.QtCriticalMsg:
            logger.error(f"[Qt] {message}")
        elif mode == QtMsgType.QtFatalMsg:
            logger.critical(f"[Qt] Qt fatal: {message}")
            _record_fatal_event("QT_FATAL", str(message))
        else:
            logger.debug(f"[Qt] {message}")

    qInstallMessageHandler(_qt_msg_handler)


def install_crash_handlers() -> None:
    sys.excepthook = _exception_hook
    threading.excepthook = _thread_exception_hook
    sys.unraisablehook = _unraisable_hook
    atexit.register(_log_normal_shutdown)
    _enable_faulthandler()


# ── setup ──

_cleanup_old_logs()

logger.remove()

if sys.stderr is not None:
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<7}</level> | <level>{message}</level>",
        colorize=True,
    )

logger.add(
    str(_session_log),
    level="DEBUG",
    format=_LOG_FMT,
    encoding="utf-8",
    enqueue=False,
)

install_crash_handlers()
