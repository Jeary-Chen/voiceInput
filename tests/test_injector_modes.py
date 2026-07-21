"""TextInjector: copy / paste_only / paste_and_copy."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TextInjectorModeTests(unittest.TestCase):
    def test_paste_only_does_not_touch_clipboard(self):
        from core.injector import TextInjector
        import pyperclip

        marker = f"KEEP_{time.time()}"
        pyperclip.copy(marker)
        with patch("core.injector.type_unicode", return_value=True) as typed:
            ok = TextInjector().paste_only("hello 你好")
        self.assertTrue(ok)
        typed.assert_called_once_with("hello 你好")
        self.assertEqual(pyperclip.paste(), marker)

    def test_paste_and_copy_writes_clipboard_then_types(self):
        from core.injector import TextInjector

        order: list[str] = []

        with patch("core.injector.pyperclip.copy",
                   side_effect=lambda text: order.append(f"copy:{text}")), \
             patch("core.injector.type_unicode",
                   side_effect=lambda text, **kwargs: (
                       order.append(f"type:{text}") or True
                   )):
            ok = TextInjector().paste_and_copy("RESULT")

        self.assertTrue(ok)
        self.assertEqual(order, ["copy:RESULT", "type:RESULT"])

    def test_copy_only_writes_clipboard_without_typing(self):
        from core.injector import TextInjector

        with patch("core.injector.pyperclip.copy") as copy_mock, \
             patch("core.injector.type_unicode") as typed:
            ok = TextInjector().copy_only("ONLY")
        self.assertTrue(ok)
        copy_mock.assert_called_once_with("ONLY")
        typed.assert_not_called()

    def test_type_unicode_encodes_cjk_newline_and_tab_as_characters(self):
        from core.injector import KEYEVENTF_UNICODE, _events_for_text

        events = _events_for_text("啊\n\t")
        # 啊 / LF / TAB — each as Unicode down+up (not VK_RETURN / VK_TAB).
        self.assertEqual(len(events), 6)
        for event in events:
            self.assertTrue(event.union.ki.dwFlags & KEYEVENTF_UNICODE)
            self.assertEqual(event.union.ki.wVk, 0)
        self.assertEqual(events[0].union.ki.wScan, ord("啊"))
        self.assertEqual(events[2].union.ki.wScan, ord("\n"))
        self.assertEqual(events[4].union.ki.wScan, ord("\t"))

    def test_type_unicode_drops_cr_keeps_lf_from_crlf(self):
        from core.injector import KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, _events_for_text

        events = _events_for_text("a\r\nb")
        scans = [
            e.union.ki.wScan
            for e in events
            if (e.union.ki.dwFlags & KEYEVENTF_UNICODE)
            and not (e.union.ki.dwFlags & KEYEVENTF_KEYUP)
        ]
        self.assertEqual(scans, [ord("a"), ord("\n"), ord("b")])

    def test_type_unicode_sendinput_full_batch(self):
        from core.injector import _events_for_text, type_unicode

        text = "测试abc"
        self.assertTrue(type_unicode(text))
        self.assertGreater(len(_events_for_text(text)), 0)

    def test_paste_only_live_leaves_clipboard_alone(self):
        from core.injector import TextInjector
        import pyperclip

        marker = f"LIVE_{time.time()}"
        pyperclip.copy(marker)
        TextInjector().paste_only("transient")
        self.assertEqual(pyperclip.paste(), marker)


if __name__ == "__main__":
    unittest.main()
