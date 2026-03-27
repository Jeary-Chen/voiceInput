"""Programmatic audio cues using Windows native winsound.

Generates short beep/chirp sounds for audio feedback:
  - start: short rising chirp  (recording begins)
  - stop:  short falling chirp (recording stops)
  - done:  gentle confirmation tone (transcription complete)
"""
import math
import struct
import io
import threading
import winsound

from core.log import logger


_SAMPLE_RATE = 22050


def _make_wav(samples: list[float]) -> bytes:
    """Pack float samples [-1,1] into a 16-bit mono WAV."""
    buf = io.BytesIO()
    n = len(samples)
    data_size = n * 2
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, _SAMPLE_RATE, _SAMPLE_RATE * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    for s in samples:
        v = max(-1.0, min(1.0, s))
        buf.write(struct.pack("<h", int(v * 32000)))
    return buf.getvalue()


def _gen_chirp(duration_ms: int, freq_start: int, freq_end: int, volume: float = 0.4) -> bytes:
    n = int(_SAMPLE_RATE * duration_ms / 1000)
    samples = []
    for i in range(n):
        t = i / _SAMPLE_RATE
        progress = i / n
        freq = freq_start + (freq_end - freq_start) * progress
        envelope = math.sin(math.pi * progress)
        val = math.sin(2 * math.pi * freq * t) * envelope * volume
        samples.append(val)
    return _make_wav(samples)


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
    return _make_wav(samples)


def _play_async(wav_data: bytes):
    """Play WAV data in a background thread so it doesn't block the UI."""
    def _play():
        try:
            winsound.PlaySound(wav_data, winsound.SND_MEMORY)
        except Exception as e:
            logger.debug(f"Sound playback error: {e}")
    threading.Thread(target=_play, daemon=True).start()


class AudioCues:
    """Manages playback of UI sound effects via Windows native API."""

    def __init__(self):
        self._enabled = True
        self._sounds = {
            "start": _gen_chirp(80, 800, 1200, 0.35),
            "stop": _gen_chirp(80, 1000, 600, 0.35),
            "done": _gen_confirm(0.3),
        }

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def play_start(self):
        if self._enabled:
            _play_async(self._sounds["start"])

    def play_stop(self):
        if self._enabled:
            _play_async(self._sounds["stop"])

    def play_done(self):
        if self._enabled:
            _play_async(self._sounds["done"])
