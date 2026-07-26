"""Waveform freeze / active color behavior."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.theme import Theme  # noqa: E402
from ui.waveform_widget import WaveformWidget  # noqa: E402


_app = QApplication.instance() or QApplication([])


class WaveformWidgetTests(unittest.TestCase):
    def test_freeze_switches_to_frozen_color_and_repaints(self):
        widget = WaveformWidget(compact=True)
        self.addCleanup(widget.close)
        widget.reset()
        self.assertEqual(widget._color, Theme.WAVEFORM_ACTIVE)
        self.assertTrue(widget._timer.isActive())

        with patch.object(widget, "update") as update:
            widget.freeze()

        self.assertTrue(widget._frozen)
        self.assertEqual(widget._color, Theme.WAVEFORM_FROZEN)
        self.assertFalse(widget._timer.isActive())
        update.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
