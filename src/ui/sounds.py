"""Programmatic audio cues via background winsound playback.

Generates short beep/chirp sounds for audio feedback:
  - start: short rising chirp  (recording begins)
  - stop:  short falling chirp (recording stops)
  - done:  gentle confirmation tone (transcription complete)

Keeps cue playback fully outside the PyAudio/PortAudio graph so output
device churn cannot interfere with microphone startup.
"""
import math
import struct
import io
import threading
import time
import winsound

from core.log import logger

_SAMPLE_RATE = 22050
_TAG = "[Sound]"

_START_DEBOUNCE_SEC = 0.25


def _make_wav(pcm: bytes) -> bytes:
    """Wrap raw int16 mono PCM into a WAV container (for winsound fallback)."""
    buf = io.BytesIO()
    data_size = len(pcm)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, _SAMPLE_RATE, _SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm)
    return buf.getvalue()


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
    """Short 40ms tick at 880 Hz (A5) with fast decay — countdown beep."""
    n = int(_SAMPLE_RATE * 0.04)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        progress = i / n
        envelope = (1.0 - progress) ** 3
        samples.append(math.sin(2 * math.pi * 880 * t) * envelope * volume)
    return _gen_pcm(samples)


class AudioCues:
    """Manages playback of UI sound effects.

    Pre-generates in-memory WAV payloads and plays them on daemon
    threads.  Callers never wait for audio hardware.
    """

    def __init__(self):
        self._enabled = True
        self._sounds = {
            "start": _make_wav(_gen_chirp(80, 800, 1200, 0.35)),
            "stop": _make_wav(_gen_chirp(80, 1000, 600, 0.35)),
            "done": _make_wav(_gen_confirm(0.3)),
            "tick": _make_wav(_gen_tick()),
        }
        self._last_start_monotonic = 0.0

    def release(self):
        """Kept for lifecycle symmetry; winsound has no persistent stream."""
        return

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def _play_via_winsound(self, name: str, wav: bytes):
        winsound.PlaySound(wav, winsound.SND_MEMORY)

    def _play(self, name: str, wav: bytes, *, source: str = "unknown"):
        logger.info(
            f"{_TAG} Playing '{name}' ({len(wav)} B, source={source}, backend=winsound)"
        )
        try:
            threading.Thread(
                target=self._play_via_winsound, args=(name, wav), daemon=True
            ).start()
        except Exception as e:
            logger.warning(f"{_TAG} '{name}' winsound failed: {e}")

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
