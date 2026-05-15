import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QObject, QPoint, QPointF, pyqtSignal, Qt
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QColor, QImage, QPainter
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
    REC_HOVER_W,
    REC_W,
    _ResultPopup,
)
from ui.native_idle_pill import NativeIdlePillWindow  # noqa: E402


class _Config:
    show_result_text = False
    mini_window_x = None
    hide_mini_window_when_idle = False
    show_countdown = True
    mini_bar_show_timer = True
    mode = "transcribe"
    active_prompt_id = ""
    custom_prompts = []

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
        self.recorder = type("Recorder", (), {"device_name": ""})()
        self.polisher = type("Polisher", (), {"_model": "test-model"})()

    def get_duration(self):
        return 0

    @property
    def effective_max_duration(self):
        return 600


class _Screen:
    def __init__(self, rect: QRect):
        self._rect = rect

    def availableGeometry(self):
        return self._rect


class _MouseEvent:
    def __init__(self, global_pos: QPoint, buttons=Qt.MouseButton.LeftButton):
        self._global_pos = global_pos
        self._buttons = buttons

    def button(self):
        return Qt.MouseButton.LeftButton

    def buttons(self):
        return self._buttons

    def globalPosition(self):
        return QPointF(self._global_pos)


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

    def test_windows_native_idle_hover_schedules_collapse_if_cursor_left(self):
        screen = _Screen(QRect(0, 0, 1200, 900))
        inside_hover = QPoint(600, 10)
        outside_hover = QPoint(20, 10)

        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.QApplication.primaryScreen", return_value=screen):
                with patch("ui.mini_window.QCursor.pos",
                           side_effect=[inside_hover, outside_hover]):
                    with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                        with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                            native = native_cls.return_value
                            native.available = True

                            window = MiniRecordingWindow(_Engine())
                            self.addCleanup(window.close)
                            window._on_native_idle_enter()

        self.assertEqual(window._mode, "hover")
        self.assertFalse(window._hovered)
        self.assertTrue(window._hover_timer.isActive())
        native.hide.assert_called()

    def test_windows_native_idle_hover_tracks_cursor_inside_panel(self):
        screen = _Screen(QRect(0, 0, 1200, 900))
        inside_hover = QPoint(600, 10)

        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.QApplication.primaryScreen", return_value=screen):
                with patch("ui.mini_window.QCursor.pos",
                           side_effect=[inside_hover, inside_hover]):
                    with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                        with patch("ui.mini_window.NativeIdlePillWindow") as native_cls:
                            native = native_cls.return_value
                            native.available = True

                            window = MiniRecordingWindow(_Engine())
                            self.addCleanup(window.close)
                            window._on_native_idle_enter()

        self.assertEqual(window._mode, "hover")
        self.assertTrue(window._hovered)
        self.assertFalse(window._hover_timer.isActive())
        native.hide.assert_called()

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

    def test_windows_qt_surface_disables_system_shadow(self):
        with patch("ui.mini_window.sys.platform", "win32"):
            with patch("ui.mini_window.MiniRecordingWindow._install_foreground_hook"):
                with patch("ui.mini_window.NativeIdlePillWindow"):
                    window = MiniRecordingWindow(_Engine())
                    self.addCleanup(window.close)

        self.assertTrue(
            window.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
        )

    def test_capsule_paints_opaque_center(self):
        window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "hover"
        window.setFixedSize(HOVER_W, HOVER_H)
        window.show()

        image = QImage(HOVER_W, HOVER_H, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        window.render(painter)
        painter.end()

        center = QColor(image.pixelColor(HOVER_W // 2, HOVER_H // 2))
        self.assertEqual(center.alpha(), 255)

    def test_native_idle_pill_paints_opaque_center(self):
        pill = NativeIdlePillWindow(IDLE_W, IDLE_H, lambda: None)

        image = pill._render_image(IDLE_W, IDLE_H)
        center = QColor(image.pixelColor(IDLE_W // 2, IDLE_H // 2))

        self.assertEqual(center.alpha(), 255)

    def test_screen_metrics_change_repositions_recording_capsule(self):
        first_screen = _Screen(QRect(0, 0, 1200, 900))
        second_screen = _Screen(QRect(0, 0, 800, 600))

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=first_screen):
            window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "recording"
        window._anchor_x = 1180
        window.setFixedSize(REC_W, REC_H)
        window.move(1140, 4)
        window.show()

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=second_screen):
            window._apply_screen_relayout("unit-test")

        geo = second_screen.availableGeometry()
        self.assertEqual(window.y(), geo.y() + 4)
        self.assertLessEqual(window.geometry().right(), geo.right())

    def test_recording_hover_poll_collapses_when_leave_event_is_missed(self):
        window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "recording"
        window._hovered = True
        window._btn_rec_stop.setVisible(True)
        window.setFixedSize(REC_HOVER_W, REC_H)
        window.move(500, 4)
        window.show()

        with patch("ui.mini_window.QCursor.pos", return_value=QPoint(10, 10)):
            window._poll_hover_state()

        self.assertFalse(window._hovered)
        self.assertFalse(window._btn_rec_stop.isVisible())
        self.assertEqual(window._target_size, (REC_W, REC_H))

    def test_processing_hides_recording_stop_button_after_hovered_stop(self):
        screen = _Screen(QRect(0, 0, 1200, 900))
        engine = _Engine()
        engine.state = "recording"

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=screen):
            window = MiniRecordingWindow(engine)
        self.addCleanup(window.close)
        window._mode = "recording"
        window._hovered = True
        window._btn_rec_stop.setVisible(True)
        window.setFixedSize(REC_HOVER_W, REC_H)
        window.move(545, 4)
        window.show()

        window._apply_processing()

        target = window._geom_anim.endValue()
        self.assertFalse(window._hovered)
        self.assertFalse(window._btn_rec_stop.isVisible())
        self.assertEqual((target.width(), target.height()), (REC_W, REC_H))
        self.assertTrue(window._waveform.isVisible())
        self.assertTrue(window._dot_status.isVisible())

    def test_next_recording_after_hovered_stop_starts_collapsed(self):
        engine = _Engine()
        window = MiniRecordingWindow(engine)
        self.addCleanup(window.close)
        window._mode = "recording"
        window._hovered = True
        window._btn_rec_stop.setVisible(True)

        window._apply_processing()
        engine.state = "ready"
        window._shrink_to_idle()
        engine.state = "recording"

        window._apply_recording()

        self.assertFalse(window._hovered)
        self.assertFalse(window._btn_rec_stop.isVisible())
        self.assertFalse(window._status_popup.isVisible())
        self.assertEqual(window._target_size, (REC_W, REC_H))

    def test_revealing_hover_mask_matches_visible_capsule_width(self):
        window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "hover"
        window.setFixedSize(HOVER_W, HOVER_H)
        window._reveal_progress = 0.0

        window._apply_capsule_mask("unit-test")

        expected_x = (HOVER_W - IDLE_W) // 2
        mask_rect = window.mask().boundingRect()
        self.assertEqual(mask_rect, QRect(expected_x, 0, IDLE_W, IDLE_H))
        self.assertFalse(window.mask().contains(QPoint(0, HOVER_H - 1)))

    def test_idle_hover_drag_can_move_down_but_release_animates_to_top(self):
        screen = _Screen(QRect(0, 0, 1200, 900))

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=screen):
            window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "hover"
        window._target_size = (HOVER_W, HOVER_H)
        window.setFixedSize(HOVER_W, HOVER_H)
        window.move(540, 4)
        window.show()

        window.mousePressEvent(_MouseEvent(QPoint(600, 14)))
        window.mouseMoveEvent(_MouseEvent(QPoint(640, 120)))

        self.assertEqual(window.y(), 110)

        window.mouseReleaseEvent(_MouseEvent(QPoint(640, 120)))

        target = window._geom_anim.endValue()
        self.assertEqual(window.y(), 110)
        self.assertEqual(target.y(), screen.availableGeometry().y() + 4)
        self.assertEqual((target.width(), target.height()), (HOVER_W, HOVER_H))
        self.assertEqual(window._anchor_x, 640)

    def test_idle_hover_return_animation_starts_collapse_when_cursor_left(self):
        screen = _Screen(QRect(0, 0, 1200, 900))

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=screen):
            window = MiniRecordingWindow(_Engine())
        self.addCleanup(window.close)
        window._mode = "hover"
        window._hovered = True
        window._target_size = (HOVER_W, HOVER_H)
        window.setFixedSize(HOVER_W, HOVER_H)
        window.move(540, 110)
        window.show()

        with patch("ui.mini_window.QCursor.pos", return_value=QPoint(640, 120)):
            window.mousePressEvent(_MouseEvent(QPoint(600, 120)))
            window.mouseReleaseEvent(_MouseEvent(QPoint(600, 120)))
            window._geom_anim.valueChanged.emit(QRect(580, 70, HOVER_W, HOVER_H))

        self.assertFalse(window._hovered)
        self.assertTrue(window._hover_timer.isActive())

    def test_processing_drag_release_animates_to_top_before_ready(self):
        screen = _Screen(QRect(0, 0, 1200, 900))
        engine = _Engine()
        engine.state = "processing"

        with patch("ui.mini_window.QApplication.primaryScreen",
                   return_value=screen):
            window = MiniRecordingWindow(engine)
        self.addCleanup(window.close)
        window._mode = "processing"
        window._target_size = (REC_W, REC_H)
        window.setFixedSize(REC_W, REC_H)
        window.move(560, 4)
        window.show()

        window.mousePressEvent(_MouseEvent(QPoint(600, 14)))
        window.mouseMoveEvent(_MouseEvent(QPoint(640, 120)))
        window.mouseReleaseEvent(_MouseEvent(QPoint(640, 120)))

        target = window._geom_anim.endValue()
        self.assertEqual(window.y(), 110)
        self.assertEqual(target.y(), screen.availableGeometry().y() + 4)
        self.assertEqual((target.width(), target.height()), (REC_W, REC_H))
        self.assertEqual(window._anchor_x, 640)


if __name__ == "__main__":
    unittest.main()
