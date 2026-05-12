import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from ui.mini_window import (  # noqa: E402
    HOVER_H,
    HOVER_W,
    IDLE_H,
    IDLE_W,
    MiniRecordingWindow,
    REC_H,
    REC_W,
    _ResultPopup,
)


class _Config:
    show_result_text = False
    mini_window_x = None
    hide_mini_window_when_idle = False
    show_countdown = True
    mode = "transcribe"

    def save(self):
        pass


class _Engine(QObject):
    state_changed = pyqtSignal(str)
    audio_data = pyqtSignal(bytes)
    transcription_done = pyqtSignal(str)
    countdown_tick = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.config = _Config()
        self.state = "ready"
        self._countdown_active = False
        self._countdown_secs = 0


class _Screen:
    def __init__(self, rect: QRect):
        self._rect = rect

    def availableGeometry(self):
        return self._rect


class MiniWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_idle_window_uses_real_pill_size(self):
        window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)

        self.assertEqual((IDLE_W, IDLE_H), (48, 8))
        self.assertEqual((window.width(), window.height()), (IDLE_W, IDLE_H))

    def test_result_popup_clamps_to_screen_top_and_scrolls_long_text(self):
        anchor = MiniRecordingWindow(_Engine())
        popup = _ResultPopup()
        self.addCleanup(anchor.close)
        self.addCleanup(popup.close)
        anchor.move(540, 900)
        anchor.setFixedSize(HOVER_W, HOVER_H)

        long_text = "很长的识别原文\n" * 200
        screen = _Screen(QRect(0, 0, 1200, 900))

        with patch("ui.mini_window.QApplication.primaryScreen", return_value=screen):
            popup.show_text(long_text, anchor)

        top_limit = screen.availableGeometry().y() + popup.TOP_MARGIN
        bottom_limit = screen.availableGeometry().bottom() - popup.BOTTOM_MARGIN

        self.assertGreaterEqual(popup.y(), top_limit)
        self.assertLessEqual(popup.geometry().bottom(), bottom_limit)
        self.assertLess(popup._scroll.height(), popup._label.sizeHint().height())

    def test_hover_and_recording_capsules_share_visual_height(self):
        window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)

        margins = window._top_layout.contentsMargins()

        self.assertEqual(HOVER_H, REC_H)
        self.assertEqual(HOVER_H, 36)
        self.assertEqual((margins.top(), margins.bottom()), (5, 5))

    def test_windows_idle_uses_native_pill_surface(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._native_idle_scale = lambda: 1.25
                    window.refresh_visibility()

        native.show_at.assert_called_once()
        _, kwargs = native.show_at.call_args
        self.assertEqual(kwargs, {"scale": 1.25})
        self.assertFalse(window.isVisible())

    def test_windows_native_idle_hover_fades_in_full_hover_panel(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window.refresh_visibility()
                    native.show_at.reset_mock()

                    window._apply_hover()

        native.hide.assert_called()
        self.assertTrue(window.isVisible())
        self.assertEqual((window.width(), window.height()), (HOVER_W, HOVER_H))
        self.assertEqual(window._target_size, (HOVER_W, HOVER_H))
        self.assertEqual(window.windowOpacity(), 1.0)
        self.assertEqual(window._reveal_anim.endValue(), 1.0)

    def test_windows_native_idle_shrink_shapes_down_before_showing_idle_pill(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._apply_hover()
                    native.show_at.reset_mock()

                    window._shrink_to_idle()

        native.show_at.assert_not_called()
        self.assertTrue(window.isVisible())
        self.assertEqual(window._target_size, (IDLE_W, IDLE_H))
        self.assertEqual(window._reveal_anim.endValue(), 0.0)
        self.assertEqual(window.windowOpacity(), 1.0)

    def test_windows_native_idle_shrink_slides_hover_panel_to_top_before_idle(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._apply_hover()
                    window.move(320, 240)
                    window._anchor_x = 380

                    window._shrink_to_idle()

        target = window._geom_anim.endValue()
        self.assertEqual((target.width(), target.height()), (HOVER_W, HOVER_H))
        self.assertEqual(target.y(), 4)
        collapsed_left = target.x() + (target.width() - IDLE_W) // 2
        self.assertEqual(collapsed_left, window._get_x_for_width(IDLE_W))
        self.assertEqual(window._reveal_anim.endValue(), 0.0)
        native.show_at.assert_not_called()

    def test_windows_native_idle_shrink_aligns_compact_recording_width(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._mode = "done"
                    window._anchor_x = 1150
                    window.setFixedSize(REC_W, REC_H)
                    window.move(1110, 4)
                    window.show()

                    window._shrink_to_idle()

        target = window._geom_anim.endValue()
        collapsed_left = target.x() + (target.width() - IDLE_W) // 2
        self.assertEqual((target.width(), target.height()), (REC_W, REC_H))
        self.assertEqual(collapsed_left, window._get_x_for_width(IDLE_W))
        native.show_at.assert_not_called()

    def test_windows_native_idle_completes_shape_collapse_to_native_pill(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._apply_hover()

                    window._shrink_to_idle()

                    self.assertEqual(window._mode, "shrinking")
                    self.assertFalse(window._top_bar.isVisible())

                    window._on_reveal_anim_finished()

        self.assertFalse(window._top_bar.isVisible())
        native.show_at.assert_called()

    def test_windows_native_idle_hover_interrupts_shrink_animation(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._apply_hover()
                    window._shrink_to_idle()
                    native.show_at.reset_mock()

                    window._apply_hover()
                    window._on_reveal_anim_finished()

        self.assertEqual(window._mode, "hover")
        self.assertFalse(window._native_returning_to_idle)
        self.assertIsNone(window._shape_target)
        self.assertEqual(window._reveal_anim.endValue(), 1.0)
        native.show_at.assert_not_called()

    def test_windows_native_idle_recording_starts_from_idle_geometry(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                    native = native_cls.return_value
                    native.available = True

                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)
                    window._apply_hover()
                    window._shrink_to_idle()
                    window._on_reveal_anim_finished()
                    native.hide.reset_mock()

                    window._apply_recording()

        start = window._geom_anim.startValue()
        target = window._geom_anim.endValue()
        self.assertEqual((start.width(), start.height()), (IDLE_W, IDLE_H))
        self.assertEqual((target.width(), target.height()), (REC_W, REC_H))
        native.hide.assert_called()


if __name__ == "__main__":
    unittest.main()
