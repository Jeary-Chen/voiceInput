"""Core voice engine — coordinates recording, ASR, and text injection."""
import time

from PyQt6.QtCore import QObject, pyqtSignal, QThread

from config import Config
from core.log import logger
from core.recorder import VoiceRecorder
from core.asr import DashScopeASR
from core.injector import TextInjector
from core.history import HistoryManager
from core.polisher import TextPolisher


class _TranscribeWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, asr: DashScopeASR, pcm_data: bytes, duration: float):
        super().__init__()
        self._asr = asr
        self._pcm = pcm_data
        self._duration = duration

    def run(self):
        try:
            logger.info(f"Transcribing {self._duration:.1f}s audio (model: {self._asr.model})")
            t0 = time.perf_counter()
            text = self._asr.transcribe(self._pcm)
            elapsed = time.perf_counter() - t0
            logger.info(f"Transcription done in {elapsed:.1f}s")
            self.result_ready.emit(text)
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            self.error_occurred.emit(str(e))


class _PolishWorker(QThread):
    result_ready = pyqtSignal(str)

    def __init__(self, polisher: TextPolisher, raw_text: str):
        super().__init__()
        self._polisher = polisher
        self._raw = raw_text

    def run(self):
        logger.info(f"Polishing text ({len(self._raw)} chars)")
        t0 = time.perf_counter()
        result = self._polisher.polish(self._raw)
        elapsed = time.perf_counter() - t0
        logger.info(f"Polish done in {elapsed:.1f}s → {len(result)} chars")
        self.result_ready.emit(result)


class VoiceEngine(QObject):
    state_changed = pyqtSignal(str)       # "ready" | "recording" | "processing"
    audio_data = pyqtSignal(bytes)         # raw PCM chunks for waveform
    live_text = pyqtSignal(str)            # status text for expanded panel
    transcription_done = pyqtSignal(str)   # final text
    error_occurred = pyqtSignal(str)
    api_key_invalid = pyqtSignal()         # 401 or missing key
    mic_unavailable = pyqtSignal()         # no microphone or open failed
    _max_reached = pyqtSignal()            # thread-safe auto-stop trigger

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.recorder = VoiceRecorder(device_index=config.mic_index)
        self.asr = DashScopeASR(
            api_key=config.api_key,
            model=config.asr_model,
            base_url=config.api_base_url,
        )
        self.injector = TextInjector()
        self.history = HistoryManager(config)
        self.polisher = TextPolisher(
            api_key=config.api_key,
            model="qwen-plus",
        )
        self._state = "ready"
        self._worker: _TranscribeWorker | None = None
        self._polish_worker: _PolishWorker | None = None
        self._max_reached.connect(self._stop_recording)

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, s: str):
        self._state = s
        logger.debug(f"State → {s}")
        self.state_changed.emit(s)

    def toggle_record(self):
        if self._state == "ready":
            self._start_recording()
        elif self._state == "recording":
            self._stop_recording()

    def cancel(self):
        if self._state == "recording":
            logger.info("Recording cancelled")
            self.recorder.cancel()
            self._set_state("ready")

    def get_duration(self) -> float:
        return self.recorder.get_duration()

    # ── recording ──

    def _start_recording(self):
        logger.info("Recording started")
        self._record_t0 = time.monotonic()
        try:
            self.recorder.start(
                on_audio_data=self._on_audio_chunk,
                on_max_reached=self._on_max_reached,
            )
        except Exception as e:
            logger.error(f"Failed to open microphone: {e}")
            self.error_occurred.emit(f"无法打开麦克风: {e}")
            self.mic_unavailable.emit()
            self._set_state("ready")
            return
        self._set_state("recording")

    def _on_max_reached(self):
        self._max_reached.emit()

    def _on_audio_chunk(self, data: bytes):
        self.audio_data.emit(data)

    def _stop_recording(self):
        if self._state != "recording":
            return
        wall_duration = time.monotonic() - self._record_t0
        pcm = self.recorder.stop()
        if not pcm:
            logger.warning("No audio captured")
            self._set_state("ready")
            return

        logger.info(f"Recording stopped — {wall_duration:.1f}s captured "
                    f"(PCM {len(pcm)} bytes)")
        self._start_batch_transcribe(pcm, wall_duration)

    def _start_batch_transcribe(self, pcm: bytes, duration: float):
        self._set_state("processing")
        self._worker = _TranscribeWorker(self.asr, pcm, duration)
        self._worker.result_ready.connect(lambda t: self._finalize(t, pcm))
        self._worker.error_occurred.connect(self._on_transcribe_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _cleanup_worker(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _cleanup_polish_worker(self):
        if self._polish_worker:
            self._polish_worker.deleteLater()
            self._polish_worker = None

    def _finalize(self, text: str, pcm: bytes):
        if not text:
            logger.warning("Empty transcription result")
            self.error_occurred.emit("识别结果为空")
            self._set_state("ready")
            return

        if self.config.mode == "polish":
            self._set_state("processing")
            self.live_text.emit(f"[原文] {text}")
            self._polish_worker = _PolishWorker(self.polisher, text)
            self._polish_worker.result_ready.connect(lambda polished: self._inject_and_save(polished, pcm))
            self._polish_worker.finished.connect(self._cleanup_polish_worker)
            self._polish_worker.start()
        else:
            self._inject_and_save(text, pcm)

    def _inject_and_save(self, text: str, pcm: bytes):
        if self.config.paste_result:
            self.injector.inject(text, restore_clipboard=self.config.restore_clipboard)
            logger.info(f"Text pasted at cursor ({len(text)} chars)")
        else:
            self.injector.copy_only(text)
            logger.info(f"Text copied to clipboard ({len(text)} chars)")

        duration = len(pcm) / (16000 * 2)
        self.history.save_entry(
            text=text,
            duration=duration,
            mode=self.config.mode,
            audio_data=pcm if self.config.save_audio else None,
        )
        self.transcription_done.emit(text)
        self._set_state("ready")

    def _on_transcribe_error(self, msg: str):
        logger.error(msg)
        self.error_occurred.emit(msg)
        if "API 401:" in msg:
            self.api_key_invalid.emit()
        self._set_state("ready")
