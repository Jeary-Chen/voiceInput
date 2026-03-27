"""Centralized logging via loguru.

Logs to:
  - Console (colorized, INFO level)
  - File    (~/.voiceinput/logs/voiceinput_{date}.log, DEBUG level, 7-day rotation)

Usage:
  from core.log import logger
"""
import os
import sys
from pathlib import Path

from loguru import logger

_LOG_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".voiceinput" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()

logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <level>{message}</level>",
    colorize=True,
)

logger.add(
    str(_LOG_DIR / "voiceinput_{time:YYYY-MM-DD}.log"),
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {name}:{function}:{line} | {message}",
    rotation="00:00",
    retention="7 days",
    encoding="utf-8",
)
