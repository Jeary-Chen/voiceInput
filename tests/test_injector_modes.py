"""TextInjector delivery policy + Unicode typing mechanisms."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class DeliveryPolicyTests(unittest.TestCase):
    """output_mode rules with injectable focus / type / clipboard."""

    def _inj(self, *, can_type: bool, typed_ok: bool = True, copy_ok: bool = True):
        from core.injector import TextInjector

        calls: list[str] = []

        def type_text(text: str) -> bool:
            calls.append(f"type:{text}")
            return typed_ok

        def copy_text(text: str) -> bool:
            calls.append(f"copy:{text}")
            return copy_ok

        inj = TextInjector(
            can_type=lambda: can_type,
            type_text=type_text,
            copy_text=copy_text,
        )
        return inj, calls

    def test_copy_always_clipboard_never_types(self):
        inj, calls = self._inj(can_type=True)
        self.assertEqual(inj.deliver("T", "copy"), "copied")
        self.assertEqual(calls, ["copy:T"])

    def test_paste_with_focus_types_without_clipboard(self):
        inj, calls = self._inj(can_type=True, typed_ok=True)
        self.assertEqual(inj.deliver("T", "paste"), "pasted")
        self.assertEqual(calls, ["type:T"])

    def test_paste_without_focus_falls_back_to_clipboard(self):
        inj, calls = self._inj(can_type=False)
        self.assertEqual(inj.deliver("T", "paste"), "copied")
        self.assertEqual(calls, ["copy:T"])

    def test_paste_type_failure_falls_back_to_clipboard(self):
        inj, calls = self._inj(can_type=True, typed_ok=False)
        self.assertEqual(inj.deliver("T", "paste"), "copied")
        self.assertEqual(calls, ["type:T", "copy:T"])

    def test_paste_copy_always_copies_and_types_when_focused(self):
        inj, calls = self._inj(can_type=True, typed_ok=True)
        self.assertEqual(inj.deliver("T", "paste_copy"), "pasted_copied")
        self.assertEqual(calls, ["copy:T", "type:T"])

    def test_paste_copy_without_focus_still_copies(self):
        inj, calls = self._inj(can_type=False)
        self.assertEqual(inj.deliver("T", "paste_copy"), "pasted_copied")
        self.assertEqual(calls, ["copy:T"])

    def test_paste_copy_type_failure_still_counts_as_pasted_copied(self):
        inj, calls = self._inj(can_type=True, typed_ok=False)
        self.assertEqual(inj.deliver("T", "paste_copy"), "pasted_copied")
        self.assertEqual(calls, ["copy:T", "type:T"])

    def test_paste_copy_clipboard_failure_is_failed(self):
        inj, calls = self._inj(can_type=True, copy_ok=False)
        self.assertEqual(inj.deliver("T", "paste_copy"), "failed")
        self.assertEqual(calls, ["copy:T"])

    def test_empty_text_fails(self):
        inj, calls = self._inj(can_type=True)
        self.assertEqual(inj.deliver("", "paste"), "failed")
        self.assertEqual(calls, [])


class TypeUnicodeMechanismTests(unittest.TestCase):
    def test_type_unicode_encodes_cjk_newline_and_tab_as_characters(self):
        from core.injector import KEYEVENTF_UNICODE, _events_for_text

        events = _events_for_text("啊\n\t")
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

    def test_write_clipboard(self):
        from core.injector import write_clipboard
        import pyperclip
        import time

        marker = f"CLIP_{time.time()}"
        self.assertTrue(write_clipboard(marker))
        self.assertEqual(pyperclip.paste(), marker)


class FocusTargetTests(unittest.TestCase):
    def test_can_accept_typed_text_requires_external_focus(self):
        from core.focus_target import FocusTarget, can_accept_typed_text

        with patch("core.focus_target.probe_focus_target", return_value=None):
            self.assertFalse(can_accept_typed_text())

        no_focus = FocusTarget(1, 0, 0, 99)
        with patch("core.focus_target.probe_focus_target", return_value=no_focus):
            self.assertFalse(can_accept_typed_text())

        with patch("core.focus_target.probe_focus_target",
                   return_value=FocusTarget(1, 42, 0, 99)), \
             patch("core.focus_target.ctypes.windll.kernel32.GetCurrentProcessId",
                   return_value=7):
            self.assertTrue(can_accept_typed_text())

        with patch("core.focus_target.probe_focus_target",
                   return_value=FocusTarget(1, 42, 0, 7)), \
             patch("core.focus_target.ctypes.windll.kernel32.GetCurrentProcessId",
                   return_value=7):
            self.assertFalse(can_accept_typed_text())


if __name__ == "__main__":
    unittest.main()
