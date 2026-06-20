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
import re
import signal
import sys
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from loguru import logger

_LOG_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_LOG_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {extra[component]} | "
    "{name}:{function}:{line} | run={extra[run]} | {message}"
)
_RETENTION_DAYS = 7
_COMPONENT = "APP"
_MAX_FIELD_VALUE_LEN = 8192
_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "dashscope_api_key",
    "password",
    "secret",
    "token",
}

_startup_at = datetime.now()
_startup_ts = _startup_at.strftime("%Y-%m-%d_%H-%M-%S")
_session_log = _LOG_DIR / f"voiceinput_{_startup_ts}.log"
session_log_path = _session_log
run_id = os.environ.get("VOICEINPUT_RUN_ID") or uuid4().hex[:8].upper()

_crash_log_fp = None
_shutdown_logged = False


def _patch_log_record(record):
    record["extra"].setdefault("run", run_id)
    record["extra"].setdefault("component", _COMPONENT)


def _safe_field_key(key: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(key)).strip("_")
    return safe or "field"


def _is_sensitive_field(key: str) -> bool:
    normalized = str(key).casefold().replace("-", "_")
    return normalized in _SENSITIVE_FIELD_NAMES


def _format_field_value(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)

    text = str(value)
    if len(text) > _MAX_FIELD_VALUE_LEN:
        text = text[:_MAX_FIELD_VALUE_LEN] + "...<truncated>"
    text = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if not text:
        return '""'
    if re.search(r"\s|[|=]", text):
        text = text.replace('"', '\\"')
        return f'"{text}"'
    return text


def format_event(event: str, message: str, **fields) -> str:
    """Return the project debug-event message body.

    The file sink already supplies timestamp, level, source, and run context.
    This helper owns stable event names, human text, fields, and redaction.
    """
    event_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(event)).strip("_")
    event_name = event_name or "event.unknown"
    parts = []
    for key, value in fields.items():
        safe_key = _safe_field_key(key)
        safe_value = "[REDACTED]" if _is_sensitive_field(safe_key) else _format_field_value(value)
        parts.append(f"{safe_key}={safe_value}")
    suffix = f" | {' '.join(parts)}" if parts else ""
    return f"{event_name} - {message}{suffix}"


def log_event(level: str, event: str, message: str, **fields) -> None:
    """Log a structured debug event while preserving caller source location."""
    logger.opt(depth=1).log(level.upper(), format_event(event, message, **fields))


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
    line = (
        f"{timestamp} | CRASH   | SYS | core.log:_write_direct:0 | "
        f"run={run_id} | [{marker}] {detail}\n"
    )
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
        format_event(
            "app.exception.unhandled",
            "Unhandled exception",
            error_type=exc_type.__name__,
            error=str(exc_value),
        )
    )
    _record_fatal_event(
        "UNHANDLED_EXCEPTION",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)).strip(),
    )


def _thread_exception_hook(args):
    if args.exc_type is SystemExit:
        return
    logger.opt(exception=(args.exc_type, args.exc_value, args.exc_traceback)).error(
        format_event(
            "thread.exception.unhandled",
            "Unhandled thread exception",
            thread=args.thread.name,
            error_type=args.exc_type.__name__,
            error=str(args.exc_value),
        )
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
        format_event(
            "app.exception.unraisable",
            "Unraisable exception",
            object=obj_repr,
            err_msg=err_msg,
            error_type=exc_type.__name__,
            error=str(exc_value),
        )
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
        duration_ms = int((datetime.now() - _startup_at).total_seconds() * 1000)
        logger.info(format_event(
            "app.lifecycle.end",
            "Process exiting normally",
            exit_code=0,
            duration_ms=duration_ms,
        ))
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
            logger.warning(format_event("qt.message.warning", "Qt warning", qt_message=message))
        elif mode == QtMsgType.QtCriticalMsg:
            logger.error(format_event("qt.message.critical", "Qt critical message", qt_message=message))
        elif mode == QtMsgType.QtFatalMsg:
            logger.critical(format_event("qt.message.fatal", "Qt fatal message", qt_message=message))
            _record_fatal_event("QT_FATAL", str(message))
        else:
            logger.debug(format_event("qt.message.debug", "Qt debug message", qt_message=message))

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
logger.configure(patcher=_patch_log_record)

if sys.stderr is not None:
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<7}</level> | "
            "run={extra[run]} | <level>{message}</level>"
        ),
        colorize=True,
    )

logger.add(
    str(_session_log),
    level="DEBUG",
    format=_LOG_FMT,
    encoding="utf-8",
    enqueue=False,
)

logger.info(format_event(
    "logger.lifecycle.start",
    "Logging initialized",
    session_log=str(_session_log),
))

install_crash_handlers()
