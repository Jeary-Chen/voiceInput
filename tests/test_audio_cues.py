import collections
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from ui.sounds import AudioCues  # noqa: E402


class AudioCuesTests(unittest.TestCase):
    def _cues(self) -> AudioCues:
        cues = AudioCues.__new__(AudioCues)
        cues._enabled = True
        cues._sounds = {"start": b"start"}
        cues._lock = threading.Lock()
        cues._buf = collections.deque()
        cues._stream_ready = True
        cues._last_start_monotonic = 0.0
        return cues

    def test_start_sound_suppresses_immediate_duplicate(self):
        cues = self._cues()

        with patch("ui.sounds.time.monotonic", side_effect=[10.0, 10.05]):
            with patch.object(cues, "_enqueue") as enqueue:
                cues.play_start(source="hotkey")
                cues.play_start(source="hotkey")

        enqueue.assert_called_once_with("start", b"start")

    def test_start_sound_allows_later_playback(self):
        cues = self._cues()

        with patch("ui.sounds.time.monotonic", side_effect=[10.0, 10.5]):
            with patch.object(cues, "_enqueue") as enqueue:
                cues.play_start(source="hotkey")
                cues.play_start(source="hotkey")

        self.assertEqual(enqueue.call_count, 2)


if __name__ == "__main__":
    unittest.main()
