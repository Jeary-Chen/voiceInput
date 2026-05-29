import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QMenu


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TrayDeviceMenuRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_rebuild_reopens_visible_submenu_after_refresh(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _cached_default_name="Built-in Mic",
            _cached_devices=[
                {"name": "Headphones", "display_name": "Headphones", "index": 1},
            ],
            _dev_menu_dirty=True,
            _device_menu=menu,
        )

        popup_calls = []
        timer_calls = []

        def fake_single_shot(_delay, callback):
            timer_calls.append(_delay)
            callback()

        with patch.object(menu, "isVisible", return_value=True):
            with patch.object(menu, "close") as close:
                with patch("ui.tray.popup_tray_submenu", side_effect=lambda m, pos: popup_calls.append(pos)):
                    with patch("ui.tray.QCursor.pos", return_value=QPoint(100, 200)):
                        with patch("ui.tray.QTimer.singleShot", side_effect=fake_single_shot):
                            VoiceTray._rebuild_device_menu(tray)

        close.assert_called_once()
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("Headphones", labels)
        self.assertEqual(popup_calls, [QPoint(100, 200)])
        self.assertEqual(timer_calls, [0])
        self.assertFalse(tray._dev_menu_dirty)


if __name__ == "__main__":
    unittest.main()
