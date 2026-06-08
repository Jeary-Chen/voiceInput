import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from ui.sounds import AudioCues, _resolve_default_output_device  # noqa: E402


class _FakePyAudio:
    def __init__(self, devices, hosts=None):
        self._devices = devices
        self._hosts = hosts or [{"name": "Windows WASAPI"}]

    def get_host_api_count(self):
        return len(self._hosts)

    def get_host_api_info_by_index(self, index):
        return self._hosts[index]

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, index):
        return self._devices[index]


class AudioCuesTests(unittest.TestCase):
    def _cues(self) -> AudioCues:
        cues = AudioCues.__new__(AudioCues)
        cues._enabled = True
        cues._sounds = {"start": b"start"}
        cues._last_start_monotonic = 0.0
        cues._last_enqueue_monotonic = 0.0
        cues._warmup_silence = b"warmup"
        cues._buf = []
        cues._stream_ready = True
        cues._lock = MagicMock()
        cues._init_lock = MagicMock()
        cues._released = False
        cues._init_started = False
        cues._reopen_timer = None
        return cues

    def test_start_sound_suppresses_immediate_duplicate(self):
        cues = self._cues()

        with patch("ui.sounds.time.monotonic", side_effect=[10.0, 10.05]):
            with patch.object(cues, "_play") as play:
                cues.play_start(source="hotkey")
                cues.play_start(source="hotkey")

        play.assert_called_once_with("start", b"start", source="hotkey")

    def test_start_sound_allows_later_playback(self):
        cues = self._cues()

        with patch("ui.sounds.time.monotonic", side_effect=[10.0, 10.5]):
            with patch.object(cues, "_play") as play:
                cues.play_start(source="hotkey")
                cues.play_start(source="hotkey")

        self.assertEqual(play.call_count, 2)

    def test_play_enqueues_into_pyaudio_buffer(self):
        cues = self._cues()

        with patch("ui.sounds.time.monotonic", return_value=10.0):
            cues._play("start", b"wav", source="test")

        self.assertEqual(cues._buf, [b"warmup", b"wav"])

    def test_play_queues_while_pyaudio_initializes(self):
        cues = self._cues()
        cues._stream_ready = False

        with patch.object(cues, "_init_stream_async") as init_stream:
            with patch("ui.sounds.time.monotonic", return_value=10.0):
                cues._play("start", b"wav", source="test")

        self.assertEqual(cues._buf, [b"warmup", b"wav"])
        init_stream.assert_called_once_with()

    def test_refresh_output_device_schedules_reopen(self):
        cues = self._cues()
        timers = []

        class FakeTimer:
            daemon = False

            def __init__(self, interval, callback):
                self.interval = interval
                self.callback = callback
                self.started = False
                self.cancelled = False
                timers.append(self)

            def start(self):
                self.started = True

            def cancel(self):
                self.cancelled = True

        with patch("ui.sounds.threading.Timer", FakeTimer):
            cues.refresh_output_device_async()
            cues.refresh_output_device_async()

        self.assertTrue(timers[0].cancelled)
        self.assertTrue(timers[1].started)
        self.assertIs(cues._reopen_timer, timers[1])

    def test_reopen_stream_closes_old_stream_and_reinitializes(self):
        cues = self._cues()
        old_pa = MagicMock()
        old_stream = MagicMock()
        cues._pa = old_pa
        cues._stream = old_stream
        cues._buf = [b"old"]
        cues._stream_ready = True

        with patch.object(cues, "_init_stream") as init_stream:
            cues._reopen_stream()

        old_stream.stop_stream.assert_called_once_with()
        old_stream.close.assert_called_once_with()
        old_pa.terminate.assert_called_once_with()
        self.assertEqual(cues._buf, [])
        init_stream.assert_called_once_with()

    def test_resolve_default_output_prefers_matching_wasapi_device(self):
        pa = _FakePyAudio([
            {"name": "Speakers (Realtek Audio)", "hostApi": 0, "maxOutputChannels": 2},
            {"name": "Headphones (Bluetooth Stereo)", "hostApi": 0, "maxOutputChannels": 2},
        ])

        with patch("ui.sounds.get_default_render_device_name", return_value="Headphones (Bluetooth Stereo)"):
            index, name = _resolve_default_output_device(pa)

        self.assertEqual(index, 1)
        self.assertEqual(name, "Headphones (Bluetooth Stereo)")

    def test_resolve_default_output_falls_back_when_no_match(self):
        pa = _FakePyAudio([
            {"name": "Speakers (Realtek Audio)", "hostApi": 0, "maxOutputChannels": 2},
        ])

        with patch("ui.sounds.get_default_render_device_name", return_value="Headphones"):
            index, name = _resolve_default_output_device(pa)

        self.assertIsNone(index)
        self.assertEqual(name, "Headphones")


if __name__ == "__main__":
    unittest.main()
