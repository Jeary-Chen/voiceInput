import sys
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
        cues._last_start_monotonic = 0.0
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

    def test_play_uses_background_winsound_thread(self):
        cues = self._cues()
        started = []

        class FakeThread:
            def __init__(self, *, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon

            def start(self):
                started.append((self.target, self.args, self.daemon))

        with patch("ui.sounds.threading.Thread", FakeThread):
            cues._play("start", b"wav", source="test")

        self.assertEqual(started, [(cues._play_via_winsound, ("start", b"wav"), True)])


if __name__ == "__main__":
    unittest.main()
