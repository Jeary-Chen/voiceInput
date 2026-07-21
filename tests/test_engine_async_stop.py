import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _Signal:
    def __init__(self):
        self._slots = []
        self.emitted = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        self.emitted.append(args)
        for slot in list(self._slots):
            slot(*args)


class _FakeStopWorker:
    instances = []

    def __init__(self, recorder, reason, *, cancel=False):
        self.recorder = recorder
        self.reason = reason
        self.cancel = cancel
        self.stopped = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()
        self.started = False
        _FakeStopWorker.instances.append(self)

    def start(self):
        self.started = True


class _FakeStartWorker:
    instances = []

    def __init__(self, recorder, rec_kwargs):
        self.recorder = recorder
        self.rec_kwargs = rec_kwargs
        self.started = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()
        self.thread_started = False
        _FakeStartWorker.instances.append(self)

    def start(self):
        self.thread_started = True


class _FakeTimer:
    def __init__(self):
        self.active = False
        self.starts = 0
        self.stops = 0

    def isActive(self):
        return self.active

    def start(self):
        self.active = True
        self.starts += 1

    def stop(self):
        self.active = False
        self.stops += 1


def _engine_base(state: str):
    from core.engine import VoiceEngine

    engine = SimpleNamespace()
    engine._state = state
    engine._start_worker = None
    engine._stop_worker = None
    engine._countdown_active = False
    engine._stop_tail_timer = _FakeTimer()
    engine._stop_tail_reason = ""
    engine.recorder = SimpleNamespace(
        stop=MagicMock(),
        cancel=MagicMock(),
        no_device=False,
    )
    engine.config = SimpleNamespace(
        mode="polish",
        silence_trim=False,
        smart_chunk_max_duration_sec=300,
    )
    engine.effective_max_duration = 300
    engine._watchdog = SimpleNamespace(start=MagicMock(), stop=MagicMock())
    engine.state_changed = _Signal()
    engine.error_occurred = _Signal()
    engine.mic_unavailable = _Signal()
    engine._on_audio_chunk = lambda data: None
    engine._on_max_reached = lambda: None
    engine._on_mic_error = lambda: None
    engine._start_batch_transcribe = MagicMock()

    def _set_state(state):
        engine._state = state
        if state == "recording":
            engine._watchdog.start()
        else:
            engine._watchdog.stop()
        engine.state_changed.emit(state)

    engine._set_state = _set_state
    engine._stop_recording_async = (
        lambda reason, cancel=False:
        VoiceEngine._stop_recording_async(engine, reason, cancel=cancel)
    )
    engine._stop_recording = (
        lambda reason="manual": VoiceEngine._stop_recording(engine, reason)
    )
    engine._finish_stop_tail = (
        lambda: VoiceEngine._finish_stop_tail(engine)
    )
    engine._cancel_stop_tail = (
        lambda: VoiceEngine._cancel_stop_tail(engine)
    )
    engine._on_recording_start_done = (
        lambda: VoiceEngine._on_recording_start_done(engine)
    )
    engine._on_recording_start_failed = (
        lambda msg: VoiceEngine._on_recording_start_failed(engine, msg)
    )
    engine._on_recording_stop_done = (
        lambda reason, cancelled, pcm:
        VoiceEngine._on_recording_stop_done(engine, reason, cancelled, pcm)
    )
    engine._on_recording_stop_failed = (
        lambda reason, cancelled, msg:
        VoiceEngine._on_recording_stop_failed(engine, reason, cancelled, msg)
    )
    engine._cleanup_start_worker = lambda: None
    engine._cleanup_stop_worker = lambda: None
    return engine


def _engine_recording():
    return _engine_base("recording")


def _engine_ready():
    return _engine_base("ready")


class EngineAsyncStopTests(unittest.TestCase):
    def setUp(self):
        _FakeStopWorker.instances.clear()
        _FakeStartWorker.instances.clear()

    def test_start_request_starts_worker_without_sync_start(self):
        from core.engine import VoiceEngine

        engine = _engine_ready()
        engine.recorder.start = MagicMock()
        with patch("core.engine._RecorderStartWorker", _FakeStartWorker):
            VoiceEngine._start_recording(engine)

        engine.recorder.start.assert_not_called()
        self.assertEqual(engine._state, "processing")
        self.assertEqual(len(_FakeStartWorker.instances), 1)
        self.assertTrue(_FakeStartWorker.instances[0].thread_started)

    def test_stop_request_starts_worker_without_sync_stop(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        with patch("core.engine._RecorderStopWorker", _FakeStopWorker):
            VoiceEngine._stop_recording_async(engine, "stalled")

        engine.recorder.stop.assert_not_called()
        self.assertEqual(engine._state, "processing")
        self.assertEqual(len(_FakeStopWorker.instances), 1)
        self.assertTrue(_FakeStopWorker.instances[0].started)
        self.assertEqual(_FakeStopWorker.instances[0].reason, "stalled")

    def test_stop_request_captures_tail_before_async_stop(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        with patch("core.engine._RecorderStopWorker", _FakeStopWorker):
            VoiceEngine._stop_recording(engine, "max_duration")

        self.assertEqual(engine._state, "recording")
        self.assertEqual(len(_FakeStopWorker.instances), 0)
        self.assertTrue(engine._stop_tail_timer.active)
        self.assertEqual(engine._stop_tail_reason, "max_duration")

        with patch("core.engine._RecorderStopWorker", _FakeStopWorker):
            VoiceEngine._finish_stop_tail(engine)

        self.assertEqual(engine._state, "processing")
        self.assertEqual(len(_FakeStopWorker.instances), 1)
        self.assertTrue(_FakeStopWorker.instances[0].started)
        self.assertEqual(_FakeStopWorker.instances[0].reason, "max_duration")
        self.assertEqual(engine._stop_tail_reason, "")

    def test_stop_tail_ignores_duplicate_stop_request(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        VoiceEngine._stop_recording(engine)
        VoiceEngine._stop_recording(engine)

        self.assertEqual(engine._stop_tail_timer.starts, 1)
        self.assertEqual(len(_FakeStopWorker.instances), 0)

    def test_cancel_request_starts_cancel_worker_without_sync_cancel(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        engine._stop_tail_timer.active = True
        engine._stop_tail_reason = "manual"
        with patch("core.engine._RecorderStopWorker", _FakeStopWorker):
            VoiceEngine.cancel(engine)

        engine.recorder.cancel.assert_not_called()
        self.assertTrue(_FakeStopWorker.instances[0].cancel)
        self.assertFalse(engine._stop_tail_timer.active)
        self.assertEqual(engine._stop_tail_timer.stops, 1)
        self.assertEqual(engine._stop_tail_reason, "")

    def test_stalled_empty_result_reports_device_fault(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        VoiceEngine._on_recording_stop_done(engine, "stalled", False, b"")

        self.assertEqual(engine._state, "ready")
        self.assertTrue(engine.mic_unavailable.emitted)
        self.assertFalse(engine.error_occurred.emitted)

    def test_manual_stop_short_effective_duration_skips_asr(self):
        from core.engine import VoiceEngine
        from core.recorder import VoiceRecorder

        engine = _engine_recording()
        engine.recorder.is_silent = MagicMock(return_value=False)
        engine.recorder.peak_amplitude = 1000
        pcm = b"\0" * int(VoiceRecorder.TARGET_RATE * 2 * 0.45)

        VoiceEngine._on_recording_stop_done(engine, "manual", False, pcm)

        self.assertEqual(engine._state, "ready")
        self.assertEqual(engine.error_occurred.emitted, [("录音过短，请重试",)])
        engine.recorder.is_silent.assert_not_called()
        engine._start_batch_transcribe.assert_not_called()

    def test_non_manual_stop_also_subtracts_tail_for_short_check(self):
        from core.engine import VoiceEngine
        from core.recorder import VoiceRecorder

        engine = _engine_recording()
        engine.recorder.is_silent = MagicMock(return_value=False)
        engine.recorder.peak_amplitude = 1000
        pcm = b"\0" * int(VoiceRecorder.TARGET_RATE * 2 * 0.45)

        VoiceEngine._on_recording_stop_done(engine, "max_duration", False, pcm)

        self.assertEqual(engine._state, "ready")
        self.assertEqual(engine.error_occurred.emitted, [("录音过短，请重试",)])
        engine.recorder.is_silent.assert_not_called()
        engine._start_batch_transcribe.assert_not_called()

    def test_non_manual_stop_keeps_full_pcm_for_asr_when_effective_duration_ok(self):
        from core.engine import VoiceEngine
        from core.recorder import VoiceRecorder

        engine = _engine_recording()
        engine.recorder.is_silent = MagicMock(return_value=False)
        engine.recorder.peak_amplitude = 1000
        pcm = b"\0" * int(VoiceRecorder.TARGET_RATE * 2 * 0.80)

        VoiceEngine._on_recording_stop_done(engine, "max_duration", False, pcm)

        engine._start_batch_transcribe.assert_called_once()
        args = engine._start_batch_transcribe.call_args.args
        self.assertEqual(args[0], pcm)
        self.assertAlmostEqual(args[1], 0.80)
        self.assertFalse(engine.error_occurred.emitted)


if __name__ == "__main__":
    unittest.main()
