"""Stop/cancel must not leak the previous take's tail into the next one.

Root cause of the bleed bug: stop_stream() pauses the stream but leaves
captured-yet-undelivered frames in driver/PortAudio buffers.  Reusing the
paused stream made those frames the first chunk(s) of the next recording.
The fix recycles (close + re-open) the stream on stop/cancel, and stops the
stream *before* clearing _recording so buffers drained during Pa_StopStream
are kept in the current take instead of being dropped.
"""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _FakeStream:
    def __init__(self, on_stop=None):
        self.closed = False
        self.stopped = False
        self._on_stop = on_stop

    def is_active(self):
        return not self.stopped

    def stop_stream(self):
        self.stopped = True
        if self._on_stop:
            self._on_stop()

    def close(self):
        self.closed = True


class _FakePyAudio:
    def __init__(self, fail_open=False):
        self.fail_open = fail_open
        self.open_calls = []
        self.opened_streams = []

    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        if self.fail_open:
            raise OSError("open failed")
        stream = _FakeStream()
        self.opened_streams.append(stream)
        return stream

    def terminate(self):
        pass


def _recorder(pa, stream):
    from core.recorder import VoiceRecorder

    rec = VoiceRecorder()
    rec._pa = pa
    rec._stream = stream
    rec._prepared = True
    rec._recording = True
    rec._stream_rate = VoiceRecorder.TARGET_RATE
    rec._stream_channels = 1
    return rec


class RecorderStopBleedTests(unittest.TestCase):
    def test_stop_recycles_stream_to_discard_stale_buffers(self):
        pa = _FakePyAudio()
        old_stream = _FakeStream()
        rec = _recorder(pa, old_stream)
        rec._audio_chunks = [b"\x01\x00" * 1600]
        rec._chunk_count = 1

        pcm = rec.stop()

        self.assertEqual(pcm, b"\x01\x00" * 1600)
        self.assertTrue(old_stream.stopped)
        self.assertTrue(old_stream.closed)
        self.assertIsNot(rec._stream, old_stream)
        self.assertIs(rec._stream, pa.opened_streams[-1])
        self.assertTrue(rec._prepared)
        self.assertFalse(pa.open_calls[-1]["start"])

    def test_stop_keeps_frames_drained_during_stop_stream(self):
        pa = _FakePyAudio()
        rec = None
        tail = b"\x02\x00" * 800

        def deliver_tail():
            # Simulate PortAudio flushing a final buffer while Pa_StopStream
            # is in progress; it must land in the current take.
            rec._audio_callback(tail, 800, {}, 0)

        old_stream = _FakeStream(on_stop=deliver_tail)
        rec = _recorder(pa, old_stream)
        rec._audio_chunks = [b"\x01\x00" * 1600]
        rec._chunk_count = 1

        pcm = rec.stop()

        self.assertTrue(pcm.endswith(tail))
        self.assertEqual(len(pcm), 2 * 1600 + 2 * 800)

    def test_cancel_recycles_stream(self):
        pa = _FakePyAudio()
        old_stream = _FakeStream()
        rec = _recorder(pa, old_stream)
        rec._audio_chunks = [b"\x01\x00" * 100]

        rec.cancel()

        self.assertEqual(rec._audio_chunks, [])
        self.assertTrue(old_stream.closed)
        self.assertIsNot(rec._stream, old_stream)
        self.assertIs(rec._stream, pa.opened_streams[-1])

    def test_recycle_failure_forces_reprepare_on_next_start(self):
        pa = _FakePyAudio(fail_open=True)
        old_stream = _FakeStream()
        rec = _recorder(pa, old_stream)
        rec._audio_chunks = [b"\x01\x00" * 1600]

        pcm = rec.stop()

        self.assertTrue(pcm)
        self.assertTrue(old_stream.closed)
        self.assertIsNone(rec._stream)
        self.assertFalse(rec._prepared)

    def test_recycle_skipped_without_device(self):
        pa = _FakePyAudio()
        rec = _recorder(pa, None)
        rec._no_device = True
        rec._audio_chunks = []

        pcm = rec.stop()

        self.assertEqual(pcm, b"")
        self.assertEqual(pa.open_calls, [])


if __name__ == "__main__":
    unittest.main()
