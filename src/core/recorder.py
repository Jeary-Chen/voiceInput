import time
from typing import Callable

import pyaudio

from core.log import logger


class VoiceRecorder:
    SAMPLE_RATE = 16000
    CHANNELS = 1
    BLOCK_SIZE = 3200        # frames per callback (~0.2s at 16kHz)
    MAX_DURATION = 120       # 2 min hard cap

    def __init__(self, device_index: int | None = None):
        self._device_index = device_index
        self._mic: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None
        self._audio_chunks: list[bytes] = []
        self._recording = False
        self._on_audio_data: Callable[[bytes], None] | None = None
        self._on_max_reached: Callable[[], None] | None = None
        self._start_time: float = 0.0
        self._chunk_count = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    def set_device(self, index: int | None):
        self._device_index = index

    def start(self, on_audio_data: Callable[[bytes], None] | None = None,
              on_max_reached: Callable[[], None] | None = None):
        self._close_stream()

        self._audio_chunks = []
        self._on_audio_data = on_audio_data
        self._on_max_reached = on_max_reached
        self._start_time = time.monotonic()
        self._chunk_count = 0

        self._mic = pyaudio.PyAudio()
        kwargs = dict(
            format=pyaudio.paInt16,
            channels=self.CHANNELS,
            rate=self.SAMPLE_RATE,
            input=True,
            frames_per_buffer=self.BLOCK_SIZE,
            stream_callback=self._audio_callback,
        )
        if self._device_index is not None:
            kwargs["input_device_index"] = self._device_index

        self._stream = self._mic.open(**kwargs)
        self._stream.start_stream()
        self._recording = True
        logger.debug("Recorder started (callback mode)")

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Called by PortAudio from its own thread at hardware rate."""
        if not self._recording:
            return (None, pyaudio.paComplete)

        self._audio_chunks.append(in_data)
        self._chunk_count += 1

        if self._on_audio_data:
            self._on_audio_data(in_data)

        elapsed = time.monotonic() - self._start_time
        if elapsed >= self.MAX_DURATION:
            logger.warning(f"Max duration ({self.MAX_DURATION}s) reached, "
                           f"{self._chunk_count} chunks")
            self._recording = False
            if self._on_max_reached:
                self._on_max_reached()
            return (None, pyaudio.paComplete)

        return (None, pyaudio.paContinue)

    def stop(self) -> bytes:
        self._recording = False
        wall_duration = time.monotonic() - self._start_time

        self._close_stream()

        if not self._audio_chunks:
            return b""

        pcm = b"".join(self._audio_chunks)
        pcm_duration = len(pcm) / (self.SAMPLE_RATE * 2)
        logger.debug(f"Stop: {self._chunk_count} chunks, "
                     f"PCM {len(pcm)} bytes ({pcm_duration:.1f}s), "
                     f"wall {wall_duration:.1f}s")
        return pcm

    def cancel(self):
        self._recording = False
        self._close_stream()
        self._audio_chunks = []

    def get_duration(self) -> float:
        total_bytes = sum(len(c) for c in self._audio_chunks)
        return total_bytes / (self.SAMPLE_RATE * 2)

    def _close_stream(self):
        if self._stream:
            try:
                if self._stream.is_active():
                    self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._mic:
            try:
                self._mic.terminate()
            except Exception:
                pass
            self._mic = None

    @staticmethod
    def list_devices() -> list[dict]:
        pa = pyaudio.PyAudio()
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                devices.append({"index": i, "name": info["name"]})
        pa.terminate()
        return devices
