import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from ui import tray


WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


class _FakeKeyData:
    def __init__(self, vk_code: int):
        self.vkCode = vk_code
        self.flags = 0


class _FakeListener:
    events: list[tuple[int, int]] = []
    suppressed: list[tuple[int, int]] = []

    def __init__(self, *, win32_event_filter):
        self._filter = win32_event_filter
        self._current: tuple[int, int] | None = None

    def start(self):
        type(self).suppressed = []
        for event in type(self).events:
            self._current = event
            msg, vk_code = event
            self._filter(msg, _FakeKeyData(vk_code))
        self._current = None

    def join(self):
        return None

    def stop(self):
        return None

    def suppress_event(self):
        if self._current is not None:
            type(self).suppressed.append(self._current)


class HotkeyStateKeyTests(unittest.TestCase):
    def test_lock_keys_in_combo_are_suppressed_before_combo_is_complete(self):
        keyboard_module = types.SimpleNamespace(Listener=_FakeListener)

        for key_name in ("capslock", "numlock", "scrolllock"):
            with self.subTest(key_name=key_name):
                _FakeListener.events = [
                    (WM_KEYDOWN, tray._NAME_TO_VK[key_name]),
                    (WM_KEYUP, tray._NAME_TO_VK[key_name]),
                ]

                with patch.dict(sys.modules, {"pynput.keyboard": keyboard_module}):
                    tray.ComboHotkeyThread(f"{key_name}+a").run()

                self.assertEqual(_FakeListener.suppressed, _FakeListener.events)


if __name__ == "__main__":
    unittest.main()
