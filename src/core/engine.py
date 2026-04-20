"""Core voice engine — coordinates recording, ASR, and text injection."""
import time

from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

from config import Config
from core.log import logger
from core.recorder import VoiceRecorder
from core.asr import DashScopeASR
from core.injector import TextInjector
from core.history import HistoryManager
from core.polisher import TextPolisher
from core.silence_trim import trim_silence
from core.chunked_asr import transcribe_chunked, MAX_CHUNK_SEC

_TAG = "[Engine]"

COUNTDOWN_SEC = 10


class _TranscribeWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, asr: DashScopeASR, pcm_data: bytes, duration: float):
        super().__init__()
        self._asr = asr
        self._pcm = pcm_data
        self._duration = duration
        self.processing_info: dict | None = None

    def run(self):
        try:
            logger.info(f"[ASR] Transcribing {self._duration:.1f}s audio (model: {self._asr.model})")
            t0 = time.perf_counter()
            text = self._asr.transcribe(self._pcm)
            elapsed = time.perf_counter() - t0
            logger.info(f"[ASR] Transcription done in {elapsed:.1f}s → {len(text)} chars")
            self.result_ready.emit(text)
        except Exception as e:
            logger.error(f"[ASR] Transcription failed: {e}")
            self.error_occurred.emit(str(e))


class _ChunkedTranscribeWorker(QThread):
    result_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, asr: DashScopeASR, pcm_data: bytes, duration: float):
        super().__init__()
        self._asr = asr
        self._pcm = pcm_data
        self._duration = duration
        self.chunk_info: dict | None = None

    def run(self):
        try:
            logger.info(f"[ASR] Chunked transcription of {self._duration:.1f}s audio "
                        f"(model: {self._asr.model})")
            text, self.chunk_info = transcribe_chunked(self._asr, self._pcm)
            self.result_ready.emit(text)
        except Exception as e:
            logger.error(f"[ASR] Chunked transcription failed: {e}")
            self.error_occurred.emit(str(e))


class _PolishWorker(QThread):
    result_ready = pyqtSignal(str)
    polish_failed = pyqtSignal(str)

    def __init__(self, polisher: TextPolisher, raw_text: str, config: Config):
        super().__init__()
        self._polisher = polisher
        self._raw = raw_text
        self._config = config

    def run(self):
        extra = (self._config.active_prompt_text or "").strip()
        logger.info(f"[Polisher] Polishing {len(self._raw)} chars (model: {self._polisher._model})")
        t0 = time.perf_counter()
        ok, result = self._polisher.polish(self._raw, extra)
        elapsed = time.perf_counter() - t0
        logger.info(f"[Polisher] Done in {elapsed:.1f}s → {len(result)} chars")
        if not ok:
            self.polish_failed.emit("润色失败，已使用原文")
        self.result_ready.emit(result)


class VoiceEngine(QObject):
    state_changed = pyqtSignal(str)       # "ready" | "recording" | "processing"
    audio_data = pyqtSignal(bytes)         # raw PCM chunks for waveform
    live_text = pyqtSignal(str)            # status text for expanded panel
    transcription_done = pyqtSignal(str)   # final text
    countdown_tick = pyqtSignal(int)       # seconds remaining before auto-stop
    error_occurred = pyqtSignal(str)
    mic_unavailable = pyqtSignal(str)
    _max_reached = pyqtSignal()
    _mic_error = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config

        resolved = None
        if config.mic_name:
            resolved = VoiceRecorder.resolve_device(config.mic_name, config.mic_index)
            if resolved != config.mic_index:
                config.mic_index = resolved
                config.save()

        self.recorder = VoiceRecorder(device_index=resolved, preferred_name=config.mic_name)
        self.recorder.prepare()
        self.asr = DashScopeASR(
            api_key=config.api_key,
            model=config.asr_model,
            base_url=config.api_base_url,
        )
        self.injector = TextInjector()
        self.history = HistoryManager(config)
        self.polisher = TextPolisher(
            api_key=config.api_key,
            model=config.polish_model,
            base_url=config.api_base_url,
        )
        self._state = "ready"
        self._worker: _TranscribeWorker | _ChunkedTranscribeWorker | None = None
        self._polish_worker: _PolishWorker | None = None
        self._processing_info: dict | None = None
        self._original_pcm: bytes = b""
        self._raw_text: str = ""
        self._countdown_active = False
        self._max_reached.connect(self._stop_recording)
        self._mic_error.connect(self._on_recording_mic_error)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_recording_health)
        logger.info(f"{_TAG} Initialized (mode={config.mode}, "
                    f"asr={config.asr_model}, polish={config.polish_model})")

    @property
    def state(self) -> str:
        return self._state

    @property
    def effective_max_duration(self) -> int:
        return self.config.smart_chunk_max_duration_sec

    def _set_state(self, s: str):
        self._state = s
        if s == "recording":
            self._countdown_active = False
            self._watchdog.start()
        else:
            self._countdown_active = False
            self._watchdog.stop()
        logger.debug(f"{_TAG} State → {s}")
        self.state_changed.emit(s)

    def toggle_record(self):
        if self._state == "ready":
            self._start_recording()
        elif self._state == "recording":
            self._stop_recording()

    def cancel(self):
        if self._state == "recording":
            logger.info(f"{_TAG} Recording cancelled by user")
            self.recorder.cancel()
            self._set_state("ready")

    def get_duration(self) -> float:
        return self.recorder.get_duration()

    # ── recording ──

    def _start_recording(self):
        max_dur = self.effective_max_duration
        logger.info(f"{_TAG} Start recording (mode={self.config.mode}, "
                    f"max_duration={max_dur}s, silence_trim={self.config.silence_trim})")
        if self.recorder.no_device:
            logger.warning(f"{_TAG} No input device available")
            self.mic_unavailable.emit("未找到输入设备")
            return
        rec_kwargs = dict(
            on_audio_data=self._on_audio_chunk,
            on_max_reached=self._on_max_reached,
            on_mic_error=self._on_mic_error,
        )
        try:
            self.recorder.start(**rec_kwargs)
        except Exception as e:
            logger.warning(f"{_TAG} Start failed ({e}), re-preparing...")
            try:
                self.recorder.prepare()
                self.recorder.start(**rec_kwargs)
            except Exception as e2:
                logger.error(f"{_TAG} Failed to open microphone: {e2}")
                self.mic_unavailable.emit(f"无法打开麦克风: {e2}")
                self._set_state("ready")
                return

        self._set_state("recording")

    def _on_max_reached(self):
        self._max_reached.emit()

    def _on_mic_error(self):
        self._mic_error.emit()

    def _on_audio_chunk(self, data: bytes):
        self.audio_data.emit(data)

    def _on_recording_mic_error(self):
        if self._state != "recording":
            return
        logger.error(f"{_TAG} Mic error during recording, auto-stopping")
        self.recorder.stop()
        self.mic_unavailable.emit("录音过程中发现麦克风异常，已自动停止")
        self._set_state("ready")

    def _check_recording_health(self):
        """Watchdog: detect stalled stream + countdown auto-stop."""
        if self._state != "recording":
            return

        if self.recorder.is_stalled():
            logger.error(f"{_TAG} Audio stream stalled "
                         f"(no callback for >{self.recorder.STALL_TIMEOUT}s), "
                         f"device likely disconnected")
            self.recorder.stop()
            self.mic_unavailable.emit("麦克风似乎已断开连接，录音已自动停止")
            self._set_state("ready")
            return

        elapsed = self.get_duration()
        max_dur = self.effective_max_duration
        remaining = max_dur - elapsed

        if remaining <= 0:
            # Limit already exceeded (e.g. user lowered the limit mid-recording)
            logger.info(f"{_TAG} Auto-stop: limit ({max_dur}s) already exceeded "
                        f"(elapsed={elapsed:.0f}s)")
            if self._countdown_active:
                self._countdown_active = False
                self.countdown_tick.emit(-1)
            self._stop_recording()
        elif remaining < COUNTDOWN_SEC + 1:
            if not self._countdown_active:
                self._countdown_active = True
                self._countdown_secs = COUNTDOWN_SEC
                logger.info(f"{_TAG} Countdown started: {self._countdown_secs}s remaining")
                self.countdown_tick.emit(self._countdown_secs)
            else:
                self._countdown_secs -= 1
                secs = max(0, self._countdown_secs)
                self.countdown_tick.emit(secs)
                if secs <= 0:
                    logger.info(f"{_TAG} Auto-stop: max duration ({max_dur}s) reached")
                    self._stop_recording()
        elif self._countdown_active:
            self._countdown_active = False
            self.countdown_tick.emit(-1)
            logger.info(f"{_TAG} Countdown cancelled: limit changed, "
                        f"{int(remaining)}s remaining")

    def _stop_recording(self):
        if self._state != "recording":
            return
        pcm = self.recorder.stop()
        if not pcm:
            logger.warning(f"{_TAG} No audio captured")
            self.error_occurred.emit("未录到音频，请重试")
            self._set_state("ready")
            return

        duration = len(pcm) / (VoiceRecorder.TARGET_RATE * 2)
        logger.info(f"{_TAG} Recording stopped — {duration:.1f}s, "
                    f"PCM {len(pcm)} bytes")

        if self.recorder.is_silent():
            logger.info(f"{_TAG} Audio silent (peak={self.recorder.peak_amplitude}), "
                        f"skipping ASR")
            self.error_occurred.emit("未检测到语音")
            self._set_state("ready")
            return

        self._start_batch_transcribe(pcm, duration)

    def _start_batch_transcribe(self, pcm: bytes, duration: float):
        self._set_state("processing")
        self._original_pcm = pcm
        self._processing_info = {}

        asr_pcm = pcm
        if self.config.silence_trim:
            asr_pcm, trim_info = trim_silence(pcm)
            self._processing_info["silence_trim"] = trim_info
            duration = len(asr_pcm) / (VoiceRecorder.TARGET_RATE * 2)

        needs_chunking = duration > MAX_CHUNK_SEC
        logger.info(f"{_TAG} Pipeline: silence_trim={'on' if self.config.silence_trim else 'off'}, "
                    f"chunking={'yes' if needs_chunking else 'no'} "
                    f"(duration={duration:.1f}s)")

        if needs_chunking:
            self._worker = _ChunkedTranscribeWorker(self.asr, asr_pcm, duration)
        else:
            self._worker = _TranscribeWorker(self.asr, asr_pcm, duration)

        self._worker.result_ready.connect(self._on_transcribe_done)
        self._worker.error_occurred.connect(self._on_transcribe_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_transcribe_done(self, text: str):
        if isinstance(self._worker, _ChunkedTranscribeWorker) and self._worker.chunk_info:
            self._processing_info["chunk"] = self._worker.chunk_info
        self._finalize(text)

    def _cleanup_worker(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _cleanup_polish_worker(self):
        if self._polish_worker:
            self._polish_worker.deleteLater()
            self._polish_worker = None

    def _finalize(self, text: str):
        if not text:
            logger.warning(f"{_TAG} Empty transcription result")
            self.error_occurred.emit("识别结果为空")
            self._set_state("ready")
            return

        if self.config.mode == "polish":
            logger.info(f"{_TAG} Entering polish pipeline")
            self._raw_text = text
            self._set_state("processing")
            self.live_text.emit(f"[原文] {text}")
            self._polish_worker = _PolishWorker(self.polisher, text, self.config)
            self._polish_worker.result_ready.connect(self._inject_and_save)
            self._polish_worker.polish_failed.connect(self.error_occurred)
            self._polish_worker.finished.connect(self._cleanup_polish_worker)
            self._polish_worker.start()
        else:
            self._raw_text = ""
            self._inject_and_save(text)

    def _inject_and_save(self, text: str):
        if self.config.paste_result:
            self.injector.inject(text, restore_clipboard=self.config.restore_clipboard)
            logger.info(f"{_TAG} Pasted {len(text)} chars")
        else:
            self.injector.copy_only(text)
            logger.info(f"{_TAG} Copied {len(text)} chars to clipboard")

        original_pcm = self._original_pcm
        duration = len(original_pcm) / (VoiceRecorder.TARGET_RATE * 2)
        proc_info = self._processing_info if self._processing_info else None
        self.history.save_entry(
            text=text,
            duration=duration,
            mode=self.config.mode,
            audio_data=original_pcm if self.config.save_audio else None,
            raw_text=self._raw_text,
            processing_info=proc_info,
        )
        self._original_pcm = b""
        self._processing_info = None
        self._raw_text = ""
        self.transcription_done.emit(text)
        self._set_state("ready")

    def _on_transcribe_error(self, msg: str):
        logger.error(f"{_TAG} Transcription error: {msg}")
        self._original_pcm = b""
        self._processing_info = None
        self.error_occurred.emit(msg)
        self._set_state("ready")
