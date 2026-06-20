import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class _FakePyAudio:
    def __init__(self, host_apis: list[dict], devices: list[dict], failing_indices=None):
        self._host_apis = host_apis
        self._devices = devices
        self._failing_indices = set(failing_indices or [])
        self.terminated = False

    def get_host_api_count(self):
        return len(self._host_apis)

    def get_host_api_info_by_index(self, index):
        return self._host_apis[index]

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, index):
        return self._devices[index]

    def open(self, **kwargs):
        index = kwargs["input_device_index"]
        if index in self._failing_indices:
            raise OSError("probe failed")
        return _FakeStream()

    def terminate(self):
        self.terminated = True


class _FakeStream:
    def __init__(self):
        self.closed = False
        self.stopped = False

    def is_active(self):
        return True

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class VoiceRecorderDeviceListTests(unittest.TestCase):
    def _record_guard_reasons(self, reasons):
        @contextmanager
        def guard(reason=""):
            reasons.append(reason)
            yield

        return guard

    def test_list_devices_uses_non_wasapi_fallback_inputs(self):
        from core.recorder import VoiceRecorder

        fake_pa = _FakePyAudio(
            host_apis=[
                {"name": "Windows WASAPI"},
                {"name": "MME"},
            ],
            devices=[
                {
                    "name": "Bluetooth Hands-Free",
                    "hostApi": 1,
                    "maxInputChannels": 1,
                    "defaultSampleRate": 44100,
                },
            ],
        )

        with patch("core.recorder.pyaudio.PyAudio", return_value=fake_pa):
            devices = VoiceRecorder.list_devices()

        self.assertEqual(
            devices,
            [
                {
                    "index": 0,
                    "name": "Bluetooth Hands-Free",
                    "host_api": "MME",
                    "open_rate": 44100,
                    "open_channels": 1,
                }
            ],
        )
        self.assertTrue(fake_pa.terminated)

    def test_list_devices_prefers_wasapi_when_names_duplicate(self):
        from core.recorder import VoiceRecorder

        fake_pa = _FakePyAudio(
            host_apis=[
                {"name": "MME"},
                {"name": "Windows WASAPI"},
            ],
            devices=[
                {"name": "Built-in Mic", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 44100},
                {"name": "Built-in Mic", "hostApi": 1, "maxInputChannels": 2, "defaultSampleRate": 48000},
            ],
        )

        with patch("core.recorder.pyaudio.PyAudio", return_value=fake_pa):
            devices = VoiceRecorder.list_devices()

        self.assertEqual(devices[0]["index"], 1)
        self.assertEqual(devices[0]["host_api"], "Windows WASAPI")

    def test_list_devices_skips_inputs_that_cannot_open(self):
        from core.recorder import VoiceRecorder

        fake_pa = _FakePyAudio(
            host_apis=[{"name": "Windows WASAPI"}],
            devices=[
                {"name": "Broken Mic", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 16000},
                {"name": "Working Mic", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 16000},
            ],
            failing_indices={0},
        )

        with patch("core.recorder.pyaudio.PyAudio", return_value=fake_pa):
            devices = VoiceRecorder.list_devices()

        self.assertEqual([dev["name"] for dev in devices], ["Working Mic"])

    def test_reset_portaudio_drops_cached_audio_client(self):
        from core.recorder import VoiceRecorder

        fake_pa = _FakePyAudio(host_apis=[], devices=[])
        fake_stream = _FakeStream()
        recorder = VoiceRecorder()
        recorder._pa = fake_pa
        recorder._stream = fake_stream
        recorder._prepared = True

        changed = recorder.reset_portaudio("device changed")

        self.assertTrue(changed)
        self.assertTrue(fake_stream.stopped)
        self.assertTrue(fake_stream.closed)
        self.assertTrue(fake_pa.terminated)
        self.assertIsNone(recorder._pa)
        self.assertIsNone(recorder._stream)
        self.assertFalse(recorder._prepared)

    def test_list_devices_serializes_enumeration_and_probe(self):
        from core.recorder import VoiceRecorder

        reasons = []
        fake_pa = _FakePyAudio(
            host_apis=[{"name": "Windows WASAPI"}],
            devices=[
                {"name": "Working Mic", "hostApi": 0, "maxInputChannels": 1, "defaultSampleRate": 16000},
            ],
        )

        with patch("core.recorder.pyaudio.PyAudio", return_value=fake_pa):
            with patch("core.recorder.portaudio_session", self._record_guard_reasons(reasons)):
                VoiceRecorder.list_devices()

        self.assertIn("recorder.list_devices", reasons)
        self.assertIn("recorder.probe_input_open", reasons)
        self.assertTrue(fake_pa.terminated)

    def test_reset_portaudio_serializes_stop_close_and_terminate(self):
        from core.recorder import VoiceRecorder

        reasons = []
        fake_pa = _FakePyAudio(host_apis=[], devices=[])
        fake_stream = _FakeStream()
        recorder = VoiceRecorder()
        recorder._pa = fake_pa
        recorder._stream = fake_stream

        with patch("core.recorder.portaudio_session", self._record_guard_reasons(reasons)):
            recorder.reset_portaudio("device changed")

        self.assertIn("recorder.stop_stream", reasons)
        self.assertIn("recorder.close_stream", reasons)
        self.assertIn("recorder.terminate", reasons)


if __name__ == "__main__":
    unittest.main()
