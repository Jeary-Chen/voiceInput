import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import ui.dialog_styles as dialog_styles  # noqa: E402


class _FakeWidget:
    def __init__(self):
        self.stylesheet = ""

    def setStyleSheet(self, stylesheet: str) -> None:
        self.stylesheet = stylesheet


class DialogStylesTests(unittest.TestCase):
    def test_apply_dialog_chrome_updates_qt_and_native_window_chrome(self):
        widget = _FakeWidget()

        with patch.object(
            dialog_styles,
            "_apply_windows_dark_frame",
        ) as apply_windows_dark_frame:
            dialog_styles.apply_dialog_chrome(widget)

        self.assertIn("QDialog", widget.stylesheet)
        apply_windows_dark_frame.assert_called_once_with(widget)


if __name__ == "__main__":
    unittest.main()
