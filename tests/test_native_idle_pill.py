import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import ui.native_idle_pill as native_idle_pill  # noqa: E402
from ui.native_idle_pill import NativeIdlePillWindow  # noqa: E402


class _Bits:
    def __init__(self, data: bytes):
        self._data = data

    def setsize(self, size: int):
        pass

    def __bytes__(self) -> bytes:
        return self._data


class NativeIdlePillTests(unittest.TestCase):
    def test_wnd_proc_forces_arrow_cursor_on_native_pill(self):
        with patch("ui.native_idle_pill.sys.platform", "linux"):
            pill = NativeIdlePillWindow(48, 8, lambda: None)
        pill._hwnd = 100
        native_idle_pill._WINDOWS[pill._hwnd] = pill
        self.addCleanup(lambda: native_idle_pill._WINDOWS.pop(pill._hwnd, None))

        with patch("ui.native_idle_pill._ARROW_CURSOR", 200):
            with patch("ui.native_idle_pill._user32") as user32:
                result = native_idle_pill._wnd_proc(
                    pill._hwnd, native_idle_pill.WM_SETCURSOR, 0, 0,
                )

        self.assertEqual(result, 1)
        user32.SetCursor.assert_called_once_with(200)

    def test_show_at_uses_scaled_size_and_position_for_win32_pixels(self):
        with patch("ui.native_idle_pill.sys.platform", "linux"):
            pill = NativeIdlePillWindow(48, 8, lambda: None)
        pill._hwnd = 100
        bits = _Bits(b"\x00" * (60 * 10 * 4))
        pill._render_image = MagicMock()
        pill._render_image.return_value.bits.return_value = bits
        pill._render_image.return_value.sizeInBytes.return_value = 60 * 10 * 4

        captured: dict[str, object] = {}

        def update_layered_window(hwnd, screen_dc, pt_dst, size, mem_dc,
                                  pt_src, color_key, blend, flags):
            captured["x"] = pt_dst._obj.x
            captured["y"] = pt_dst._obj.y
            captured["cx"] = size._obj.cx
            captured["cy"] = size._obj.cy
            return True

        with patch("ui.native_idle_pill._user32") as user32:
            with patch("ui.native_idle_pill._gdi32") as gdi32:
                with patch("ui.native_idle_pill.ctypes.memmove", return_value=None):
                    user32.GetDC.return_value = 1
                    user32.UpdateLayeredWindow.side_effect = update_layered_window
                    user32.SetWindowPos.return_value = True
                    user32.ShowWindow.return_value = True
                    user32.ReleaseDC.return_value = True
                    gdi32.CreateCompatibleDC.return_value = 2
                    gdi32.CreateDIBSection.return_value = 3
                    gdi32.SelectObject.return_value = 4
                    gdi32.DeleteObject.return_value = True
                    gdi32.DeleteDC.return_value = True

                    pill.show_at(100, 4, scale=1.25)

        self.assertEqual(captured, {"x": 125, "y": 5, "cx": 60, "cy": 10})
        pill._render_image.assert_called_once_with(60, 10)


if __name__ == "__main__":
    unittest.main()
