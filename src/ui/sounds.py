"""Programmatic audio cues via pyaudio callback stream.

Generates short beep/chirp sounds for audio feedback:
  - start: short rising chirp  (recording begins)
  - stop:  short falling chirp (recording stops)
  - done:  gentle confirmation tone (transcription complete)

Uses a pyaudio callback-mode output stream bound to Windows' current
default playback device. Sound data is queued into a buffer and
consumed by the audio callback without blocking the caller.
When the audio path has been idle long enough for the DAC / amplifier
to enter low-power standby, a short silence prefix is injected before
the actual sound to let the hardware wake up first.
"""
import math
import struct
import collections
import threading
import time

import pyaudio

from core.device_watcher import get_default_render_device_name
from core.log import logger

_SAMPLE_RATE = 22050
_TAG = "[Sound]"

_FRAMES_PER_BUFFER = 1024
_START_DEBOUNCE_SEC = 0.25
_COLD_THRESHOLD_SEC = 2.0
_WARMUP_SILENCE_SEC = 0.1
_OUTPUT_REOPEN_DEBOUNCE_SEC = 0.35


def _fix_name(name: str) -> str:
    try:
        return name.encode("gbk").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def _normalized_device_name(name: str) -> str:
    return " ".join(_fix_name(name).casefold().split())


def _gen_pcm(samples: list[float]) -> bytes:
    """Pack float samples [-1,1] into raw 16-bit mono PCM."""
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32000)) for s in samples)


def _gen_chirp(duration_ms: int, freq_start: int, freq_end: int, volume: float = 0.4) -> bytes:
    n = int(_SAMPLE_RATE * duration_ms / 1000)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        progress = i / n
        freq = freq_start + (freq_end - freq_start) * progress
        envelope = math.sin(math.pi * progress)
        samples.append(math.sin(2 * math.pi * freq * t) * envelope * volume)
    return _gen_pcm(samples)


def _gen_confirm(volume: float = 0.3) -> bytes:
    """Two-tone confirmation: C5 then E5."""
    n1 = int(_SAMPLE_RATE * 0.08)
    n2 = int(_SAMPLE_RATE * 0.12)
    gap = int(_SAMPLE_RATE * 0.03)
    samples = []
    for i in range(n1):
        t = i / _SAMPLE_RATE
        env = math.sin(math.pi * i / n1)
        samples.append(math.sin(2 * math.pi * 523 * t) * env * volume)
    samples.extend([0.0] * gap)
    for i in range(n2):
        t = i / _SAMPLE_RATE
        env = math.sin(math.pi * i / n2)
        samples.append(math.sin(2 * math.pi * 659 * t) * env * volume)
    return _gen_pcm(samples)


def _gen_tick(volume: float = 0.25) -> bytes:
    """Short 40ms tick at 880 Hz (A5) with fast decay."""
    n = int(_SAMPLE_RATE * 0.04)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        progress = i / n
        envelope = (1.0 - progress) ** 3
        samples.append(math.sin(2 * math.pi * 880 * t) * envelope * volume)
    return _gen_pcm(samples)


def _resolve_default_output_device(pa: pyaudio.PyAudio) -> tuple[int | None, str | None]:
    """Resolve Windows' current default render endpoint to a PyAudio device index."""
    default_name = get_default_render_device_name()
    if not default_name:
        return None, None

    default_norm = _normalized_device_name(default_name)
    fallback: tuple[int, str] | None = None
    try:
        wasapi_hosts = {
            idx
            for idx in range(pa.get_host_api_count())
            if "wasapi" in str(pa.get_host_api_info_by_index(idx).get("name", "")).casefold()
        }
        for idx in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(idx)
            if int(info.get("maxOutputChannels", 0) or 0) <= 0:
                continue
            name = _fix_name(str(info.get("name", "")))
            name_norm = _normalized_device_name(name)
            if not name_norm:
                continue
            matches = name_norm == default_norm or name_norm in default_norm or default_norm in name_norm
            if not matches:
                continue
            if info.get("hostApi") in wasapi_hosts:
                return idx, name
            if fallback is None:
                fallback = (idx, name)
    except Exception:
        logger.opt(exception=True).warning(f"{_TAG} Failed to resolve default output device")

    if fallback is not None:
        return fallback
    return None, default_name


class AudioCues:
    """Manages playback of UI sound effects.

    Opens a callback-mode pyaudio output stream at init and keeps it
    alive. Enqueuing sound data returns immediately; the audio callback
    drains the buffer in the background.
    """

    def __init__(self):
        self._enabled = True
        self._sounds = {
            "start": _gen_chirp(80, 800, 1200, 0.35),
            "stop": _gen_chirp(80, 1000, 600, 0.35),
            "done": _gen_confirm(0.3),
            "tick": _gen_tick(),
        }
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._buf: collections.deque[bytes] = collections.deque()
        self._stream_ready = False
        self._init_started = False
        self._released = False
        self._output_device_name: str | None = None
        self._reopen_timer: threading.Timer | None = None
        self._last_start_monotonic = 0.0
        self._last_enqueue_monotonic = 0.0
        self._warmup_silence = b"\x00" * int(_SAMPLE_RATE * _WARMUP_SILENCE_SEC) * 2
        self._init_stream_async()

    def _init_stream_async(self):
        with self._init_lock:
            if self._init_started or self._stream_ready or self._released:
                return
            self._init_started = True
        threading.Thread(target=self._init_stream, name="AudioCueInit", daemon=True).start()

    def _init_stream(self):
        pa = None
        stream = None
        try:
            t0 = time.perf_counter()
            pa = pyaudio.PyAudio()
            device_index, device_name = _resolve_default_output_device(pa)
            open_kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=_SAMPLE_RATE,
                output=True,
                frames_per_buffer=_FRAMES_PER_BUFFER,
                stream_callback=self._audio_callback,
            )
            if device_index is not None:
                open_kwargs["output_device_index"] = device_index
            stream = pa.open(**open_kwargs)
            ms = (time.perf_counter() - t0) * 1000
            route = device_name or "system default"
            logger.info(f"{_TAG} Callback stream ready ({ms:.0f}ms, output={route})")
            with self._lock:
                if self._released:
                    try:
                        stream.stop_stream()
                        stream.close()
                    finally:
                        pa.terminate()
                    return
                self._pa = pa
                self._stream = stream
                self._stream_ready = True
                self._output_device_name = device_name
        except Exception as e:
            logger.warning(f"{_TAG} Stream init failed: {e}")
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
            try:
                if pa is not None:
                    pa.terminate()
            except Exception:
                pass
            with self._lock:
                self._stream_ready = False
                self._output_device_name = None
        finally:
            with self._init_lock:
                self._init_started = False

    def refresh_output_device_async(self):
        with self._init_lock:
            if self._released:
                return
            if self._reopen_timer is not None:
                self._reopen_timer.cancel()
            timer = threading.Timer(_OUTPUT_REOPEN_DEBOUNCE_SEC, self._reopen_stream)
            timer.daemon = True
            self._reopen_timer = timer
            timer.start()

    def _reopen_stream(self):
        with self._init_lock:
            self._reopen_timer = None
            self._init_started = False
        old_stream = None
        old_pa = None
        with self._lock:
            old_stream = self._stream
            old_pa = self._pa
            self._stream = None
            self._pa = None
            self._stream_ready = False
            self._output_device_name = None
            self._buf.clear()
        if old_stream is not None:
            try:
                old_stream.stop_stream()
                old_stream.close()
            except Exception:
                pass
        if old_pa is not None:
            try:
                old_pa.terminate()
            except Exception:
                pass
        logger.info(f"{_TAG} Reopening callback stream for current default output")
        self._init_stream()

    def _audio_callback(self, in_data, frame_count, time_info, status):
        needed = frame_count * 2  # 16-bit mono
        data = b""
        while len(data) < needed and self._buf:
            chunk = self._buf[0]
            take = needed - len(data)
            if len(chunk) <= take:
                data += self._buf.popleft()
            else:
                data += chunk[:take]
                self._buf[0] = chunk[take:]
        if len(data) < needed:
            data += b"\x00" * (needed - len(data))
        return (data, pyaudio.paContinue)

    def release(self):
        """Terminate PyAudio. Call on app exit."""
        with self._init_lock:
            self._released = True
            if self._reopen_timer is not None:
                self._reopen_timer.cancel()
                self._reopen_timer = None
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._pa:
                try:
                    self._pa.terminate()
                except Exception:
                    pass
                self._pa = None
            self._stream_ready = False
            self._output_device_name = None
            self._buf.clear()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def _enqueue(self, name: str, pcm: bytes):
        t0 = time.perf_counter()
        now = time.monotonic()
        cold = (now - self._last_enqueue_monotonic) > _COLD_THRESHOLD_SEC
        needs_init = False
        with self._lock:
            if cold:
                self._buf.append(self._warmup_silence)
            self._buf.append(pcm)
            if not self._stream_ready:
                needs_init = True
        self._last_enqueue_monotonic = now
        if needs_init:
            self._init_stream_async()
        ms = (time.perf_counter() - t0) * 1000
        if needs_init:
            logger.info(f"{_TAG} '{name}' queued while stream initializes ({ms:.0f}ms)")
        elif cold:
            logger.info(f"{_TAG} '{name}' enqueued with warmup prefix ({ms:.0f}ms)")
        else:
            logger.info(f"{_TAG} '{name}' enqueued ({ms:.0f}ms)")

    def _play(self, name: str, pcm: bytes, *, source: str = "unknown"):
        queue_chunks = len(self._buf)
        logger.info(
            f"{_TAG} Playing '{name}' ({len(pcm)} B, source={source}, "
            f"queue_chunks={queue_chunks})"
        )
        self._enqueue(name, pcm)

    def play_start(self, *, source: str = "unknown"):
        if self._enabled:
            now = time.monotonic()
            elapsed = now - self._last_start_monotonic
            if elapsed < _START_DEBOUNCE_SEC:
                logger.warning(
                    f"{_TAG} Suppressed duplicate 'start' "
                    f"(source={source}, elapsed_ms={elapsed * 1000:.0f})"
                )
                return
            self._last_start_monotonic = now
            self._play("start", self._sounds["start"], source=source)

    def play_stop(self):
        if self._enabled:
            self._play("stop", self._sounds["stop"])

    def play_done(self):
        if self._enabled:
            self._play("done", self._sounds["done"])

    def play_tick(self):
        if self._enabled:
            self._play("tick", self._sounds["tick"])
