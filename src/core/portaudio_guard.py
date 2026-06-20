"""Process-wide guard for PortAudio/PyAudio native calls.

PortAudio keeps process-global state on Windows.  Device hot-plug handling can
otherwise interleave PyAudio init/terminate/open/close across recorder input,
sound output, and enumeration probes.

Lock ordering:
  - Component locks protect Python object state.
  - This guard protects native PortAudio calls.
  - Do not emit Qt signals or call user callbacks while holding this guard.
"""
from __future__ import annotations

from contextlib import contextmanager
import threading
from typing import Iterator

_PORTAUDIO_LOCK = threading.RLock()


@contextmanager
def portaudio_session(reason: str = "") -> Iterator[None]:
    """Serialize a block that enters PortAudio/PyAudio native code."""
    del reason  # Kept for searchable call sites and future diagnostics.
    with _PORTAUDIO_LOCK:
        yield
