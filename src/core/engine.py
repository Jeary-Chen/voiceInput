"""Core voice engine — coordinates recording, ASR, and text injection."""
import time
import wave

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

from config import Config
from core.log import logger
from core.recorder import VoiceRecorder, _resample, _mix_to_mono
from core.asr import DashScopeASR
from core.injector import TextInjector
from core.history import HistoryManager
from core.polisher import TextPolisher
from core.silence_trim import trim_silence
from core.chunked_asr import transcribe_chunked, MAX_CHUNK_SEC

_TAG = "[Engine]"

COUNTDOWN_SEC = 10
_MAX_UPLOAD_DURATION_SEC = 20 * 60  # 20 minutes
_STOP_TAIL_MS = 350
_MIN_EFFECTIVE_RECORDING_SEC = 0.25


def _validation_duration(total_duration: float, reason: str) -> float:
    """Duration used for UX validity checks, not for ASR input.

    Stop requests include a short tail capture to avoid clipping final words.
    That tail should not make an accidental tap look like real speech.
    """
    return max(0.0, total_duration - _STOP_TAIL_MS / 1000)


def _recording_too_short(total_duration: float, reason: str) -> bool:
    return _validation_duration(total_duration, reason) < _MIN_EFFECTIVE_RECORDING_SEC


def _load_audio_file(path: str) -> tuple[bytes, float]:
    """Read an audio file and return (16-bit mono PCM at 16 kHz, duration_sec).

    WAV is handled natively; other formats use pydub (requires ffmpeg).
    """
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""

    if ext == "wav":
        with wave.open(path, "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            raw = wf.readframes(wf.getnframes())

        if sampwidth == 1:
            samples_u8 = np.frombuffer(raw, dtype=np.uint8)
            raw = ((samples_u8.astype(np.int16) - 128) * 256).tobytes()
        elif sampwidth == 3:
            raise ValueError("24-bit WAV 暂不支持，请转换为 16-bit")
        elif sampwidth == 4:
            samples_i32 = np.frombuffer(raw, dtype=np.int32)
            raw = (samples_i32 >> 16).astype(np.int16).tobytes()
        elif sampwidth != 2:
            raise ValueError(f"不支持的 WAV 位深: {sampwidth * 8}-bit")

        if channels > 1:
            raw = _mix_to_mono(raw, channels)
        if rate != VoiceRecorder.TARGET_RATE:
            raw = _resample(raw, rate, VoiceRecorder.TARGET_RATE)
    else:
        try:
            from pydub import AudioSegment
        except ImportError:
            raise ValueError(
                f"不支持 .{ext} 格式（缺少 pydub/ffmpeg）。\n"
                f"请使用 WAV 格式，或安装 pydub + ffmpeg。"
            )
        seg = AudioSegment.from_file(path)
        seg = seg.set_channels(1).set_frame_rate(VoiceRecorder.TARGET_RATE).set_sample_width(2)
        raw = seg.raw_data

    duration = len(raw) / (VoiceRecorder.TARGET_RATE * 2)
    return raw, duration


class _AudioPipelineWorker(QThread):
    result_ready = pyqtSignal(str, object)  # text, processing_info
    error_occurred = pyqtSignal(str)

    def __init__(
        self,
        asr: DashScopeASR,
        pcm_data: bytes,
        duration: float,
        *,
        silence_trim: bool = False,
    ):
        super().__init__()
        self._asr = asr
        self._pcm = pcm_data
        self._duration = duration
        self._silence_trim = silence_trim

    def run(self):
        try:
            processing_info: dict = {}
            asr_pcm = self._pcm
            duration = self._duration
            if self._silence_trim:
                asr_pcm, trim_info = trim_silence(self._pcm)
                processing_info["silence_trim"] = trim_info
                duration = len(asr_pcm) / (VoiceRecorder.TARGET_RATE * 2)

            needs_chunking = duration > MAX_CHUNK_SEC
            logger.info(
                f"{_TAG} Pipeline: silence_trim={'on' if self._silence_trim else 'off'}, "
                f"chunking={'yes' if needs_chunking else 'no'} "
                f"(duration={duration:.1f}s)"
            )

            if needs_chunking:
                logger.info(
                    f"[ASR] Chunked transcription of {duration:.1f}s audio "
                    f"(model: {self._asr.model})"
                )
                text, chunk_info = transcribe_chunked(self._asr, asr_pcm)
                processing_info["chunk"] = chunk_info
            else:
                logger.info(f"[ASR] Transcribing {duration:.1f}s audio (model: {self._asr.model})")
                t0 = time.perf_counter()
                text = self._asr.transcribe(asr_pcm)
                elapsed = time.perf_counter() - t0
                logger.info(f"[ASR] Transcription done in {elapsed:.1f}s → {len(text)} chars")

            self.result_ready.emit(text, processing_info)
        except Exception as e:
            logger.error(f"[ASR] Transcription failed: {e}")
            self.error_occurred.emit(str(e))


class _AudioFileLoadWorker(QThread):
    loaded = pyqtSignal(bytes, float)
    failed = pyqtSignal(str)

    def __init__(self, file_path: str):
        super().__init__()
        self._file_path = file_path

    def run(self):
        try:
            pcm, duration = _load_audio_file(self._file_path)
            self.loaded.emit(pcm, duration)
        except Exception as e:
            logger.error(f"{_TAG} Failed to load audio file: {e}")
            self.failed.emit(str(e))


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


class _RecorderStopWorker(QThread):
    stopped = pyqtSignal(str, bool, bytes)  # reason, cancelled, pcm
    failed = pyqtSignal(str, bool, str)     # reason, cancelled, error

    def __init__(self, recorder: VoiceRecorder, reason: str, *, cancel: bool = False):
        super().__init__()
        self._recorder = recorder
        self._reason = reason
        self._cancel = cancel

    def run(self):
        try:
            if self._cancel:
                self._recorder.cancel()
                self.stopped.emit(self._reason, True, b"")
                return
            pcm = self._recorder.stop()
            self.stopped.emit(self._reason, False, pcm)
        except Exception as e:
            logger.error(f"{_TAG} Recorder stop failed ({self._reason}): {e}")
            self.failed.emit(self._reason, self._cancel, str(e))


class _RecorderStartWorker(QThread):
    started = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, recorder: VoiceRecorder, rec_kwargs: dict):
        super().__init__()
        self._recorder = recorder
        self._rec_kwargs = rec_kwargs

    def run(self):
        try:
            self._recorder.start(**self._rec_kwargs)
        except Exception as e:
            logger.warning(f"{_TAG} Start failed ({e}), re-preparing...")
            try:
                self._recorder.prepare()
                self._recorder.start(**self._rec_kwargs)
            except Exception as e2:
                logger.error(f"{_TAG} Failed to open microphone: {e2}")
                self.failed.emit(str(e2))
                return
        self.started.emit()


class _FinalizeWorker(QThread):
    finished_ok = pyqtSignal(str, bool, str)  # text, failed_entry, action
    failed = pyqtSignal(str, bool)            # error, failed_entry

    def __init__(
        self,
        injector: TextInjector,
        history: HistoryManager,
        *,
        text: str,
        original_pcm: bytes,
        mode: str,
        save_audio: bool,
        paste_result: bool,
        restore_clipboard: bool,
        raw_text: str = "",
        processing_info: dict | None = None,
        failed_entry: bool = False,
        error_msg: str = "",
    ):
        super().__init__()
        self._injector = injector
        self._history = history
        self._text = text
        self._original_pcm = original_pcm
        self._mode = mode
        self._save_audio = save_audio
        self._paste_result = paste_result
        self._restore_clipboard = restore_clipboard
        self._raw_text = raw_text
        self._processing_info = processing_info
        self._failed_entry = failed_entry
        self._error_msg = error_msg

    def run(self):
        try:
            action = "failed"
            if not self._failed_entry:
                if self._paste_result:
                    self._injector.inject(
                        self._text,
                        restore_clipboard=self._restore_clipboard,
                    )
                    action = "pasted"
                else:
                    self._injector.copy_only(self._text)
                    action = "copied"

            duration = len(self._original_pcm) / (VoiceRecorder.TARGET_RATE * 2)
            self._history.save_entry(
                text="" if self._failed_entry else self._text,
                duration=duration,
                mode=self._mode,
                audio_data=self._original_pcm if self._save_audio else None,
                raw_text="" if self._failed_entry else self._raw_text,
                processing_info=None if self._failed_entry else self._processing_info,
                failed=self._failed_entry,
                error_msg=self._error_msg if self._failed_entry else "",
            )
            self.finished_ok.emit(self._text, self._failed_entry, action)
        except Exception as e:
            logger.error(f"{_TAG} Finalize failed: {e}")
            self.failed.emit(str(e), self._failed_entry)


class VoiceEngine(QObject):
    state_changed = pyqtSignal(str)       # "ready" | "recording" | "processing" | "cancelling"
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

        # No native PortAudio calls here: device resolution and mic warm-up can
        # block for tens of seconds when the audio subsystem is wedged, so they
        # run in the tray's background prepare worker (and on demand in
        # recorder.start()).  prepare() re-resolves the saved name to a current
        # index, and the first device refresh persists any index drift back to
        # config, so passing the possibly stale saved index is safe.
        resolved = config.mic_index if config.mic_name else None
        self.recorder = VoiceRecorder(device_index=resolved, preferred_name=config.mic_name)
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
        self._worker: _AudioPipelineWorker | None = None
        self._file_worker: _AudioFileLoadWorker | None = None
        self._polish_worker: _PolishWorker | None = None
        self._start_worker: _RecorderStartWorker | None = None
        self._stop_worker: _RecorderStopWorker | None = None
        self._finalize_worker: _FinalizeWorker | None = None
        self._processing_info: dict | None = None
        self._original_pcm: bytes = b""
        self._raw_text: str = ""
        self._countdown_active = False
        self._max_reached.connect(self._stop_recording)
        self._mic_error.connect(self._on_recording_mic_error)
        self._stop_tail_reason = ""
        self._stop_tail_timer = QTimer(self)
        self._stop_tail_timer.setSingleShot(True)
        self._stop_tail_timer.setInterval(_STOP_TAIL_MS)
        self._stop_tail_timer.timeout.connect(self._finish_stop_tail)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1000)
        self._watchdog.timeout.connect(self._check_recording_health)
        logger.info(f"{_TAG} Initialized (mode={config.mode}, "
                    f"asr={config.asr_model}, polish={config.polish_model})")

    def apply_config(self, changed: set[str]) -> None:
        """Apply hot-reloaded config fields to runtime services."""
        cfg = self.config
        if changed & {"api_key", "asr_model", "api_base_url"}:
            self.asr.update_settings(
                api_key=cfg.api_key,
                model=cfg.asr_model,
                base_url=cfg.api_base_url,
            )
        if changed & {"api_key", "polish_model", "api_base_url"}:
            self.polisher.update_settings(
                api_key=cfg.api_key,
                model=cfg.polish_model,
                base_url=cfg.api_base_url,
            )
        # Mic fields are intentionally not handled here: the tray routes them
        # through its background prepare worker (_schedule_recorder_device_apply)
        # because applying a device enters native PortAudio and must stay off
        # the GUI thread.

    def background_workers(self) -> list[tuple[str, QThread]]:
        """Live pipeline threads that quit must wait for (or declare stuck)."""
        pairs = (
            ("audio pipeline", self._worker),
            ("file load", self._file_worker),
            ("polish", self._polish_worker),
            ("recorder start", self._start_worker),
            ("recorder stop", self._stop_worker),
            ("finalize", self._finalize_worker),
        )
        return [(label, worker) for label, worker in pairs if worker is not None]

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
            self._cancel_stop_tail()
            self._stop_recording_async("cancel", cancel=True)

    def transcribe_file(self, file_path: str):
        """Load a local audio file and run the ASR → polish → paste pipeline."""
        if self._state != "ready":
            return
        logger.info(f"{_TAG} transcribe_file: {file_path}")
        self._set_state("processing")
        self.live_text.emit("正在读取音频文件…")
        worker = _AudioFileLoadWorker(file_path)
        worker.loaded.connect(self._on_audio_file_loaded)
        worker.failed.connect(self._on_audio_file_load_failed)
        worker.finished.connect(self._cleanup_file_worker)
        self._file_worker = worker
        worker.start()

    def _on_audio_file_loaded(self, pcm: bytes, duration: float):
        if not pcm:
            self.error_occurred.emit("音频文件为空")
            self._set_state("ready")
            return
        if duration > _MAX_UPLOAD_DURATION_SEC:
            self.error_occurred.emit(
                f"音频时长 {duration / 60:.1f} 分钟，超过上限 "
                f"{_MAX_UPLOAD_DURATION_SEC // 60} 分钟"
            )
            self._set_state("ready")
            return
        logger.info(f"{_TAG} Audio file loaded: {duration:.1f}s, "
                    f"PCM {len(pcm)} bytes")
        self._start_batch_transcribe(pcm, duration)

    def _on_audio_file_load_failed(self, msg: str):
        self.error_occurred.emit(f"音频文件加载失败：{msg}")
        self._set_state("ready")

    def _cleanup_file_worker(self):
        if self._file_worker:
            self._file_worker.deleteLater()
            self._file_worker = None

    def get_duration(self) -> float:
        return self.recorder.get_duration()

    # ── recording ──

    def _start_recording(self):
        if self._start_worker is not None:
            return
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
        self._set_state("processing")
        self.live_text.emit("正在准备录音…")
        worker = _RecorderStartWorker(self.recorder, rec_kwargs)
        worker.started.connect(self._on_recording_start_done)
        worker.failed.connect(self._on_recording_start_failed)
        worker.finished.connect(self._cleanup_start_worker)
        self._start_worker = worker
        worker.start()

    def _on_recording_start_done(self):
        self._set_state("recording")

    def _on_recording_start_failed(self, msg: str):
        self.mic_unavailable.emit(f"无法打开麦克风: {msg}")
        self._set_state("ready")

    def _cleanup_start_worker(self):
        if self._start_worker:
            self._start_worker.deleteLater()
            self._start_worker = None

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
        self._stop_recording("mic_error")

    def _check_recording_health(self):
        """Watchdog: detect stalled stream + countdown auto-stop."""
        if self._state != "recording":
            return

        if self.recorder.is_stalled():
            logger.error(f"{_TAG} Audio stream stalled "
                         f"(no callback for >{self.recorder.STALL_TIMEOUT}s), "
                         f"device likely disconnected")
            self._stop_recording("stalled")
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
            self._stop_recording("max_duration")
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
                    self._stop_recording("max_duration")
        elif self._countdown_active:
            self._countdown_active = False
            self.countdown_tick.emit(-1)
            logger.info(f"{_TAG} Countdown cancelled: limit changed, "
                        f"{int(remaining)}s remaining")

    def _stop_recording(self, reason: str = "manual"):
        if self._state != "recording":
            return
        if self._stop_tail_timer.isActive():
            logger.debug(f"{_TAG} Stop tail already pending ({self._stop_tail_reason})")
            return
        self._stop_tail_reason = reason
        logger.info(
            f"{_TAG} Stop requested ({reason}); capturing "
            f"{_STOP_TAIL_MS}ms tail audio"
        )
        self._stop_tail_timer.start()

    def _finish_stop_tail(self):
        if self._state != "recording":
            return
        reason = self._stop_tail_reason or "manual"
        self._stop_tail_reason = ""
        self._stop_recording_async(reason)

    def _cancel_stop_tail(self):
        if self._stop_tail_timer.isActive():
            self._stop_tail_timer.stop()
        self._stop_tail_reason = ""

    def _stop_recording_async(self, reason: str, *, cancel: bool = False):
        if self._state != "recording":
            return
        if self._stop_worker is not None:
            logger.debug(f"{_TAG} Stop already in progress; ignored ({reason})")
            return
        # 作废与正常停止区分上报：cancelling 不属于「处理中」，UI 不应显示处理指示。
        self._set_state("cancelling" if cancel else "processing")
        self.live_text.emit("正在取消…" if cancel else "正在停止录音…")
        worker = _RecorderStopWorker(self.recorder, reason, cancel=cancel)
        worker.stopped.connect(self._on_recording_stop_done)
        worker.failed.connect(self._on_recording_stop_failed)
        worker.finished.connect(self._cleanup_stop_worker)
        self._stop_worker = worker
        worker.start()

    def _on_recording_stop_done(self, reason: str, cancelled: bool, pcm: bytes):
        if cancelled:
            self._set_state("ready")
            return
        if not pcm:
            logger.warning(f"{_TAG} No audio captured")
            if reason == "stalled":
                self.mic_unavailable.emit("麦克风似乎已断开连接，录音已自动停止")
            elif reason == "mic_error":
                self.mic_unavailable.emit("录音过程中发现麦克风异常，已自动停止")
            else:
                self.error_occurred.emit("未录到音频，请重试")
            self._set_state("ready")
            return

        duration = len(pcm) / (VoiceRecorder.TARGET_RATE * 2)
        logger.info(f"{_TAG} Recording stopped — {duration:.1f}s, "
                    f"PCM {len(pcm)} bytes")

        if _recording_too_short(duration, reason):
            effective_duration = _validation_duration(duration, reason)
            logger.info(
                f"{_TAG} Recording too short: effective={effective_duration:.2f}s, "
                f"total={duration:.2f}s, reason={reason}"
            )
            self.error_occurred.emit("录音过短，请重试")
            self._set_state("ready")
            return

        if self.recorder.is_silent():
            logger.info(f"{_TAG} Audio silent (peak={self.recorder.peak_amplitude}), "
                        f"skipping ASR")
            self.error_occurred.emit("未检测到语音")
            self._set_state("ready")
            return

        self._start_batch_transcribe(pcm, duration)

    def _on_recording_stop_failed(self, reason: str, cancelled: bool, msg: str):
        if reason in ("stalled", "mic_error"):
            self.mic_unavailable.emit("麦克风停止失败，可能已断开连接")
        elif not cancelled:
            self.error_occurred.emit(f"停止录音失败：{msg}")
        self._set_state("ready")

    def _cleanup_stop_worker(self):
        if self._stop_worker:
            self._stop_worker.deleteLater()
            self._stop_worker = None

    def _start_batch_transcribe(self, pcm: bytes, duration: float):
        self._set_state("processing")
        self._original_pcm = pcm
        self._processing_info = None
        self._worker = _AudioPipelineWorker(
            self.asr,
            pcm,
            duration,
            silence_trim=self.config.silence_trim,
        )
        self._worker.result_ready.connect(self._on_transcribe_done)
        self._worker.error_occurred.connect(self._on_transcribe_error)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.start()

    def _on_transcribe_done(self, text: str, processing_info: object):
        self._processing_info = processing_info if isinstance(processing_info, dict) else None
        self._finalize(text)

    def _cleanup_worker(self):
        if self._worker:
            self._worker.deleteLater()
            self._worker = None

    def _cleanup_polish_worker(self):
        if self._polish_worker:
            self._polish_worker.deleteLater()
            self._polish_worker = None

    def _cleanup_finalize_worker(self):
        if self._finalize_worker:
            self._finalize_worker.deleteLater()
            self._finalize_worker = None

    def _save_failed_audio(self, error_msg: str = ""):
        """Save a failed entry to history (JSON always; WAV only when save_audio is on)."""
        if not self._original_pcm:
            return
        self._start_finalize_worker(
            text="",
            failed_entry=True,
            error_msg=error_msg,
        )

    def _finalize(self, text: str):
        if not text:
            logger.warning(f"{_TAG} Empty transcription result")
            self._save_failed_audio(error_msg="识别结果为空")
            self.error_occurred.emit("识别结果为空")
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
        self._start_finalize_worker(text=text)

    def _start_finalize_worker(
        self,
        *,
        text: str,
        failed_entry: bool = False,
        error_msg: str = "",
    ):
        if self._finalize_worker is not None:
            logger.warning(f"{_TAG} Finalize already in progress")
            return
        worker = _FinalizeWorker(
            self.injector,
            self.history,
            text=text,
            original_pcm=self._original_pcm,
            mode=self.config.mode,
            save_audio=self.config.save_audio,
            paste_result=self.config.paste_result,
            restore_clipboard=self.config.restore_clipboard,
            raw_text=self._raw_text,
            processing_info=self._processing_info if self._processing_info else None,
            failed_entry=failed_entry,
            error_msg=error_msg,
        )
        worker.finished_ok.connect(self._on_finalize_done)
        worker.failed.connect(self._on_finalize_failed)
        worker.finished.connect(self._cleanup_finalize_worker)
        self._finalize_worker = worker
        worker.start()

    def _on_finalize_done(self, text: str, failed_entry: bool, action: str):
        self._original_pcm = b""
        self._processing_info = None
        self._raw_text = ""
        if not failed_entry:
            if action == "pasted":
                logger.info(f"{_TAG} Pasted {len(text)} chars")
            elif action == "copied":
                logger.info(f"{_TAG} Copied {len(text)} chars to clipboard")
            self.transcription_done.emit(text)
        self._set_state("ready")

    def _on_finalize_failed(self, msg: str, failed_entry: bool):
        self._original_pcm = b""
        self._processing_info = None
        self._raw_text = ""
        if not failed_entry:
            self.error_occurred.emit(f"结果保存失败：{msg}")
        self._set_state("ready")

    def _on_transcribe_error(self, msg: str):
        logger.error(f"{_TAG} Transcription error: {msg}")
        self._save_failed_audio(error_msg=msg)
        self.error_occurred.emit(msg)
