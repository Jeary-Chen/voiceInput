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


def _engine_base(state: str):
    from core.engine import VoiceEngine

    engine = SimpleNamespace()
    engine._state = state
    engine._start_worker = None
    engine._stop_worker = None
    engine._countdown_active = False
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
    engine.live_text = _Signal()
    engine.error_occurred = _Signal()
    engine.mic_unavailable = _Signal()
    engine._on_audio_chunk = lambda data: None
    engine._on_max_reached = lambda: None
    engine._on_mic_error = lambda: None

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

    def test_cancel_request_starts_cancel_worker_without_sync_cancel(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        with patch("core.engine._RecorderStopWorker", _FakeStopWorker):
            VoiceEngine.cancel(engine)

        engine.recorder.cancel.assert_not_called()
        self.assertTrue(_FakeStopWorker.instances[0].cancel)

    def test_stalled_empty_result_reports_device_fault(self):
        from core.engine import VoiceEngine

        engine = _engine_recording()
        VoiceEngine._on_recording_stop_done(engine, "stalled", False, b"")

        self.assertEqual(engine._state, "ready")
        self.assertTrue(engine.mic_unavailable.emitted)
        self.assertFalse(engine.error_occurred.emitted)


if __name__ == "__main__":
    unittest.main()
