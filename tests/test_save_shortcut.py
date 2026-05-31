import sys
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from ui.save_shortcut import CtrlSSaveFilter, _hotkey_is_ctrl_s  # noqa: E402


class SaveShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _dialog_with_button(self, label: str, *, enabled: bool = True):
        dlg = QDialog()
        layout = QVBoxLayout(dlg)
        button = QPushButton(label)
        button.setEnabled(enabled)
        layout.addWidget(button)
        dlg.show()
        self._app.processEvents()
        self.addCleanup(dlg.close)
        return dlg, button

    def test_finds_clickable_save_button(self):
        dlg, button = self._dialog_with_button("保存")

        self.assertIs(CtrlSSaveFilter.find_save_button(dlg), button)

    def test_ignores_disabled_save_button(self):
        dlg, _button = self._dialog_with_button("保存", enabled=False)

        self.assertIsNone(CtrlSSaveFilter.find_save_button(dlg))

    def test_ignores_do_not_save_button(self):
        dlg, _button = self._dialog_with_button("不保存")

        self.assertIsNone(CtrlSSaveFilter.find_save_button(dlg))

    def test_configured_ctrl_s_disables_save_shortcut(self):
        for combo in ("ctrl+s", "lctrl+s", "rctrl+s"):
            with self.subTest(combo=combo):
                self.assertTrue(_hotkey_is_ctrl_s(combo))

    def test_other_save_like_shortcuts_do_not_disable_save_shortcut(self):
        for combo in ("lctrl+lshift+s", "lalt+s", "lctrl+p"):
            with self.subTest(combo=combo):
                self.assertFalse(_hotkey_is_ctrl_s(combo))


if __name__ == "__main__":
    unittest.main()
