"""Compact floating recording indicator — with smooth animations.

Behavior:
  - Idle:      tiny pill stuck to top-center of screen
  - Hover:     expands to show three buttons: record, polish toggle, show-result toggle
  - Recording: capsule with waveform (stop button on hover)
  - Done:      if show-result is on, popup shows final text below
"""
import ctypes
import sys

from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, QRectF,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QCursor, QPainter, QColor, QPainterPath, QPen, QFont, QRegion,
)
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QApplication, QFrame, QScrollArea,
)

from core.log import logger
from ui.theme import Theme
from ui.native_idle_pill import NativeIdlePillWindow
from ui.waveform_widget import WaveformWidget

IDLE_W, IDLE_H = 48, 8
HOVER_W, HOVER_H = 120, 36       # 3 buttons
REC_W, REC_H = 80, 36             # waveform only (with padding)
REC_HOVER_W = 110                  # waveform + stop button on hover
RESULT_W = 340                    # result popup width
RADIUS = 19
HOVER_COLLAPSE_DELAY_MS = 300
HOVER_POLL_INTERVAL_MS = 120
SCREEN_RELAYOUT_DELAY_MS = 120

_BTN_STYLE = """
    QPushButton {{
        background: {bg}; color: {fg};
        border: none; border-radius: {r}px;
        font-size: 13px; outline: none;
        padding: 0px; text-align: center;
    }}
    QPushButton:hover {{ background: {hover}; }}
"""


class _RecStopButton(QWidget):
    """Recording stop button. Click = stop recording. Long-press = cancel (discard)."""
    clicked = pyqtSignal()
    cancelled = pyqtSignal()

    CLICK_THRESHOLD_MS = 300
    HOLD_MS = 500
    _TICK = 25
    SIZE = 26

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("点击停止 | 长按作废")
        self._progress = 0.0
        self._holding = False
        self._completed = False
        self._external_hold = False
        self._elapsed_ms = 0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(self._TICK)
        self._tick_timer.timeout.connect(self._tick)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._holding = True
            self._completed = False
            self._elapsed_ms = 0
            self._progress = 0.0
            self._tick_timer.start()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._external_hold:
            self._tick_timer.stop()
            was_holding = self._holding
            in_click_zone = self._elapsed_ms < self.CLICK_THRESHOLD_MS
            self._holding = False
            self._progress = 0.0
            self._elapsed_ms = 0
            self.update()
            if not self._completed and was_holding and in_click_zone:
                self.clicked.emit()

    def _tick(self):
        self._elapsed_ms += self._TICK
        if self._elapsed_ms <= self.CLICK_THRESHOLD_MS:
            return
        hold_elapsed = self._elapsed_ms - self.CLICK_THRESHOLD_MS
        self._progress = min(hold_elapsed / self.HOLD_MS, 1.0)
        if self._progress >= 1.0:
            self._tick_timer.stop()
            self._progress = 0.0
            self._holding = False
            self._completed = True
            self._external_hold = False
            self._elapsed_ms = 0
            self.update()
            self.cancelled.emit()
            return
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE
        r = s / 2 - 1

        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(0, 0, s, s), s / 2, s / 2)
        p.fillPath(bg_path, QColor(Theme.BG_BUTTON_HOVER if self._holding else Theme.BG_BUTTON))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ff3b30"))
        stop_s = 9
        off = (s - stop_s) / 2
        p.drawRoundedRect(QRectF(off, off, stop_s, stop_s), 2, 2)

        if self._progress > 0:
            pen = QPen(QColor("#ff3b30"), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            margin = 1.5
            arc_rect = QRectF(margin, margin, s - margin * 2, s - margin * 2)
            start_angle = 90 * 16
            span_angle = int(-self._progress * 360 * 16)
            p.drawArc(arc_rect, start_angle, span_angle)

        p.end()

    def enterEvent(self, event):
        self.update()

    def leaveEvent(self, event):
        if self._holding and not self._external_hold:
            self._holding = False
            self._tick_timer.stop()
            self._progress = 0.0
        self.update()

    def start_external_hold(self, skip_click_threshold: bool = False):
        """Begin the long-press progress animation from an external trigger."""
        self._external_hold = True
        self._holding = True
        self._completed = False
        if skip_click_threshold:
            self._elapsed_ms = self.CLICK_THRESHOLD_MS + self._TICK
            self._progress = min(self._TICK / self.HOLD_MS, 1.0)
        else:
            self._elapsed_ms = 0
            self._progress = 0.0
        self._tick_timer.start()
        self.update()

    def cancel_external_hold(self):
        """Cancel an in-progress external hold (hotkey released early)."""
        self._external_hold = False
        self._tick_timer.stop()
        self._holding = False
        self._progress = 0.0
        self._elapsed_ms = 0
        self.update()


class _ResultPopup(QWidget):
    """Floating text popup that shows below the mini window and auto-hides."""
    TOP_MARGIN = 12
    BOTTOM_MARGIN = 12
    MIN_SCROLL_H = 96

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll = QScrollArea()
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidgetResizable(False)
        self._scroll.setStyleSheet("background: transparent; border: none;")

        self._label = QLabel("")
        self._label.setFont(Theme.font(13))
        self._label.setStyleSheet(f"""
            color: {Theme.TEXT_PRIMARY.name()};
            background: {Theme.BG_PRIMARY.name()};
            border: 1px solid rgba(255,255,255,30);
            border-radius: 10px;
            padding: 12px 14px;
        """)
        self._label.setWordWrap(True)
        self._label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._scroll.setWidget(self._label)
        layout.addWidget(self._scroll)

        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self.hide)

    def show_text(self, text: str, anchor_widget: QWidget, duration_ms: int = 3500):
        self._label.setText(text)
        self._label.setFixedWidth(RESULT_W)
        self._label.adjustSize()

        pos = anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft())
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - RESULT_W) // 2
            top_limit = geo.y() + self.TOP_MARGIN
            bottom_limit = geo.bottom() - self.BOTTOM_MARGIN
        else:
            x = pos.x()
            top_limit = 0
            bottom_limit = pos.y() + self._label.height()

        desired_y = pos.y() + 4
        natural_h = self._label.sizeHint().height()
        max_h = max(self.MIN_SCROLL_H, bottom_limit - top_limit + 1)
        popup_h = min(natural_h, max_h)
        y = min(desired_y, bottom_limit - popup_h + 1)
        y = max(top_limit, y)

        self._scroll.setFixedSize(RESULT_W, popup_h)
        self.setFixedSize(RESULT_W, popup_h)
        self.move(x, y)
        self.show()
        self._auto_hide.start(duration_ms)

    def enterEvent(self, event):
        self._auto_hide.stop()

    def leaveEvent(self, event):
        self._auto_hide.start(1500)

    def paintEvent(self, event):
        pass


class _StatusPopup(QWidget):
    """Floating status bar shown below the mini window during recording hover."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel()
        self._label.setFont(Theme.font(11))
        self._label.setStyleSheet(f"""
            color: {Theme.TEXT_SECONDARY.name()};
            background: {Theme.BG_PRIMARY.name()};
            border: 1px solid rgba(255,255,255,20);
            border-radius: 8px;
            padding: 5px 10px;
        """)
        layout.addWidget(self._label)

    def show_status(self, items: list[str], anchor: QWidget):
        if not items:
            self.hide()
            return
        self._label.setText("    ".join(items))
        self.adjustSize()
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = pos.x() + (anchor.width() - self.width()) // 2
            x = max(geo.x(), min(x, geo.x() + geo.width() - self.width()))
        else:
            x = pos.x()
        self.move(x, pos.y() + 3)
        self.show()

    def paintEvent(self, event):
        pass


class _CountdownPopup(QWidget):
    """Floating countdown label shown before auto-stop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._label = QLabel()
        self._label.setFont(Theme.font(11))
        self._label.setStyleSheet(f"""
            color: #ff3b30;
            background: {Theme.BG_PRIMARY.name()};
            border: 1px solid rgba(255,60,48,60);
            border-radius: 8px;
            padding: 5px 10px;
        """)
        layout.addWidget(self._label)

    def show_countdown(self, seconds: int, anchor: QWidget):
        self._label.setText(f"录音将在 {seconds}s 后自动停止")
        self.adjustSize()
        pos = anchor.mapToGlobal(anchor.rect().bottomLeft())
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = pos.x() + (anchor.width() - self.width()) // 2
            x = max(geo.x(), min(x, geo.x() + geo.width() - self.width()))
        else:
            x = pos.x()
        self.move(x, pos.y() + 3)
        self.show()

    def paintEvent(self, event):
        pass


class MiniRecordingWindow(QWidget):
    request_record = pyqtSignal()
    request_stop = pyqtSignal()
    request_cancel = pyqtSignal()
    request_history = pyqtSignal()
    mode_changed = pyqtSignal(str)
    show_result_changed = pyqtSignal(bool)

    def __init__(self, engine):
        super().__init__()
        self._engine = engine
        self._mode = "idle"
        self._drag_pos = None
        self._hovered = False
        self._show_result = engine.config.show_result_text
        self._anchor_x: int | None = engine.config.mini_window_x
        self._native_idle: NativeIdlePillWindow | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._geom_anim = QPropertyAnimation(self, b"geometry")
        self._geom_anim.finished.connect(self._on_anim_finished)
        self._geom_anim.valueChanged.connect(self._on_geometry_anim_value_changed)
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self._opacity_anim.finished.connect(self._on_opacity_anim_finished)
        self._reveal_progress = 1.0
        self._reveal_anim = QPropertyAnimation(self, b"revealProgress")
        self._reveal_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._reveal_anim.finished.connect(self._on_reveal_anim_finished)
        self._native_returning_to_idle = False
        self._fade_target: str | None = None
        self._shape_target: str | None = None
        self._target_size = (IDLE_W, IDLE_H)
        self._anim_interrupting = False
        self._screen_connections: list[tuple[object, object]] = []

        self._result_popup = _ResultPopup()
        self._status_popup = _StatusPopup()
        self._countdown_popup = _CountdownPopup()

        self._build_ui()
        self._set_widgets_for_mode("idle")
        self._position_at(IDLE_W, IDLE_H)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timeout)
        self._hotkey_hold_timer = QTimer(self)
        self._hotkey_hold_timer.setSingleShot(True)
        self._hotkey_hold_timer.timeout.connect(self._begin_hotkey_hold_visuals)
        self._deferred_shrink_timer = QTimer(self)
        self._deferred_shrink_timer.setSingleShot(True)
        self._deferred_shrink_timer.timeout.connect(self._shrink_to_idle)
        self._hover_poll_timer = QTimer(self)
        self._hover_poll_timer.setInterval(HOVER_POLL_INTERVAL_MS)
        self._hover_poll_timer.timeout.connect(self._poll_hover_state)
        self._screen_relayout_timer = QTimer(self)
        self._screen_relayout_timer.setSingleShot(True)
        self._screen_relayout_timer.timeout.connect(
            lambda: self._apply_screen_relayout("debounced-screen-change")
        )
        self._rec_status_timer: QTimer | None = None

        engine.state_changed.connect(self._on_engine_state)
        engine.audio_data.connect(self._on_audio)
        engine.transcription_done.connect(self._on_done)
        engine.countdown_tick.connect(self._on_countdown_tick)
        self._install_screen_watchers()

        self._winevent_hook = None
        self._winevent_cb_ref = None
        if sys.platform == "win32":
            self._native_idle = NativeIdlePillWindow(
                IDLE_W, IDLE_H, self._on_native_idle_enter,
            )
            self._install_foreground_hook()
        QTimer.singleShot(0, lambda: self._apply_windows_surface_tweaks("init"))
        QTimer.singleShot(450, self._prewarm_hover_surface)

    # ── Win32 topmost enforcement ──

    _SWP_FLAGS = 0x0002 | 0x0001 | 0x0010  # NOMOVE | NOSIZE | NOACTIVATE
    _DWMWA_NCRENDERING_POLICY = 2
    _DWMWA_WINDOW_CORNER_PREFERENCE = 33
    _DWMWA_BORDER_COLOR = 34
    _DWMNCRP_DISABLED = 1
    _DWMWCP_DONOTROUND = 1
    _DWMWA_COLOR_NONE = 0xFFFFFFFE

    def _install_foreground_hook(self):
        from ctypes import wintypes
        WINEVENTPROC = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD,
        )
        self._winevent_cb_ref = WINEVENTPROC(self._on_foreground_change)
        EVENT_SYSTEM_FOREGROUND = 0x0003
        WINEVENT_OUTOFCONTEXT = 0x0000
        self._winevent_hook = ctypes.windll.user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
            0, self._winevent_cb_ref, 0, 0, WINEVENT_OUTOFCONTEXT,
        )

    def _on_foreground_change(self, hook, event, hwnd, id_obj, id_child, tid, time):
        my_hwnd = int(self.winId()) if self.isVisible() else 0
        if my_hwnd and hwnd != my_hwnd:
            ctypes.windll.user32.SetWindowPos(
                my_hwnd, -1, 0, 0, 0, 0, self._SWP_FLAGS,
            )

    def _apply_windows_surface_tweaks(self, source: str):
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        logger.debug(
            f"[DEBUG] _apply_windows_surface_tweaks | source={source}, "
            f"hwnd={hwnd}, flags={int(self.windowFlags())}"
        )
        try:
            dwmapi = ctypes.windll.dwmapi
        except Exception as e:
            logger.debug(
                f"[DEBUG] _apply_windows_surface_tweaks | "
                f"DWM unavailable, error={e!r}"
            )
            return

        attrs = [
            (self._DWMWA_NCRENDERING_POLICY, self._DWMNCRP_DISABLED),
            (self._DWMWA_WINDOW_CORNER_PREFERENCE, self._DWMWCP_DONOTROUND),
            (self._DWMWA_BORDER_COLOR, self._DWMWA_COLOR_NONE),
        ]
        for attr, value in attrs:
            c_value = ctypes.c_int(value)
            try:
                result = dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint(attr),
                    ctypes.byref(c_value),
                    ctypes.sizeof(c_value),
                )
                logger.debug(
                    f"[DEBUG] _apply_windows_surface_tweaks | "
                    f"attr={attr}, value={value}, result={result}"
                )
            except Exception as e:
                logger.debug(
                    f"[DEBUG] _apply_windows_surface_tweaks | "
                    f"attr={attr}, value={value}, error={e!r}"
                )

    def _apply_capsule_mask(self, source: str):
        if self.width() <= 0 or self.height() <= 0:
            return
        progress = (
            self._reveal_progress
            if self._mode in ("hover", "shrinking")
            else 1.0
        )
        mask_w = round(IDLE_W + (self.width() - IDLE_W) * progress)
        mask_h = round(IDLE_H + (self.height() - IDLE_H) * progress)
        mask_x = round((self.width() - mask_w) / 2)
        mask_y = 0
        mask_pad = 1
        region_x = mask_x - mask_pad
        region_y = mask_y - mask_pad
        region_w = mask_w + mask_pad * 2
        region_h = mask_h + mask_pad * 2
        radius = min(RADIUS, mask_h // 2)
        region = QRegion(region_x, region_y, region_w, region_h,
                         QRegion.RegionType.Rectangle)
        mask = QRegion(region_x, region_y, region_w, region_h,
                       QRegion.RegionType.Ellipse)
        if mask_h < mask_w:
            center_w = max(0, region_w - region_h)
            region = QRegion(region_x + radius, region_y, center_w, region_h,
                             QRegion.RegionType.Rectangle)
            left = QRegion(region_x, region_y, region_h, region_h,
                           QRegion.RegionType.Ellipse)
            right = QRegion(region_x + region_w - region_h, region_y,
                            region_h, region_h,
                            QRegion.RegionType.Ellipse)
            mask = region.united(left).united(right)
        self.setMask(mask)
        logger.debug(
            f"[DEBUG] _apply_capsule_mask | source={source}, "
            f"size=({self.width()}, {self.height()}), "
            f"mask=({mask_x}, 0, {mask_w}, {mask_h}), radius={radius}"
        )

    def _install_screen_watchers(self):
        app = QApplication.instance()
        if app is None:
            return
        logger.debug("[DEBUG] _install_screen_watchers | installing")
        app.primaryScreenChanged.connect(
            lambda *_: self._schedule_screen_relayout("primaryScreenChanged")
        )
        app.screenAdded.connect(
            lambda *_: self._refresh_screen_watchers("screenAdded")
        )
        app.screenRemoved.connect(
            lambda *_: self._refresh_screen_watchers("screenRemoved")
        )
        self._refresh_screen_watchers("init")

    def _refresh_screen_watchers(self, source: str):
        app = QApplication.instance()
        if app is None:
            return
        for signal, callback in self._screen_connections:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        screens = app.screens()
        logger.debug(
            f"[DEBUG] _refresh_screen_watchers | source={source}, "
            f"screen_count={len(screens)}"
        )
        for screen in screens:
            for signal_name in (
                "geometryChanged",
                "availableGeometryChanged",
                "logicalDotsPerInchChanged",
            ):
                signal = getattr(screen, signal_name, None)
                if signal is None:
                    continue
                callback = (
                    lambda *_, s=source, n=signal_name:
                    self._schedule_screen_relayout(f"{s}:{n}")
                )
                signal.connect(callback)
                self._screen_connections.append((signal, callback))
        self._schedule_screen_relayout(source)

    def _schedule_screen_relayout(self, source: str):
        logger.debug(
            f"[DEBUG] _schedule_screen_relayout | source={source}, "
            f"mode={self._mode}, visible={self.isVisible()}"
        )
        self._screen_relayout_timer.start(SCREEN_RELAYOUT_DELAY_MS)

    def closeEvent(self, event):
        self._hover_poll_timer.stop()
        self._screen_relayout_timer.stop()
        if self._native_idle:
            self._native_idle.destroy()
            self._native_idle = None
        if self._winevent_hook:
            ctypes.windll.user32.UnhookWinEvent(self._winevent_hook)
            self._winevent_hook = None
        for signal, callback in self._screen_connections:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        super().closeEvent(event)

    # ── UI build ──

    def _build_ui(self):
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._top_bar = QWidget()
        self._top_layout = QHBoxLayout(self._top_bar)
        self._top_layout.setContentsMargins(6, 5, 6, 5)
        self._top_layout.setSpacing(6)

        self._top_layout.addStretch(1)

        self._waveform = WaveformWidget(compact=True)
        self._waveform.setFixedSize(56, 26)
        self._top_layout.addWidget(self._waveform)

        self._btn_action = QPushButton("●")
        self._btn_action.setFixedSize(26, 26)
        self._btn_action.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_action.clicked.connect(self._on_action_click)
        self._top_layout.addWidget(self._btn_action)
        self._style_action_record()

        self._btn_polish = QPushButton("✦")
        self._btn_polish.setFixedSize(26, 26)
        self._btn_polish.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_polish.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_polish.clicked.connect(self._toggle_polish)
        self._top_layout.addWidget(self._btn_polish)
        self._update_polish_style()

        self._btn_show_result = QPushButton("◳")
        self._btn_show_result.setFixedSize(26, 26)
        self._btn_show_result.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_show_result.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_show_result.clicked.connect(self._toggle_show_result)
        self._top_layout.addWidget(self._btn_show_result)
        self._update_show_result_style()

        self._btn_rec_stop = _RecStopButton()
        self._btn_rec_stop.clicked.connect(lambda: self.request_stop.emit())
        self._btn_rec_stop.cancelled.connect(self._on_cancel)
        self._top_layout.addWidget(self._btn_rec_stop)
        self._btn_rec_stop.setVisible(False)

        self._top_layout.addStretch(1)

        self._root.addWidget(self._top_bar)

        # Status dot — overlaid at top-right, not in layout
        self._dot_status = QLabel("●", self)
        self._dot_status.setFixedSize(10, 10)
        self._dot_status.setFont(Theme.font(7))
        self._dot_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot_status.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    # ── button styles ──

    def _update_polish_style(self):
        is_on = self._engine.config.mode == "polish"
        if is_on:
            self._btn_polish.setStyleSheet(_BTN_STYLE.format(
                bg=Theme.BG_BUTTON.name(), fg="#ffffff",
                r=13, hover=Theme.BG_BUTTON_HOVER.name(),
            ))
            self._btn_polish.setToolTip("润色: 开")
        else:
            self._btn_polish.setStyleSheet(_BTN_STYLE.format(
                bg=Theme.BG_BUTTON.name(), fg=Theme.TEXT_SECONDARY.name(),
                r=13, hover=Theme.BG_BUTTON_HOVER.name(),
            ))
            self._btn_polish.setToolTip("润色: 关")

    def _toggle_polish(self):
        cfg = self._engine.config
        cfg.mode = "transcribe" if cfg.mode == "polish" else "polish"
        cfg.save(touched=frozenset({"mode"}))
        self._update_polish_style()
        self.mode_changed.emit(cfg.mode)
        logger.info(f"[MiniWin] Mode toggled → {cfg.mode}")

    def sync_mode(self):
        """Refresh polish button style after external mode change."""
        self._update_polish_style()

    def sync_show_result(self):
        """Refresh ◳ button after tray menu toggles show_result_text."""
        self._show_result = self._engine.config.show_result_text
        self._update_show_result_style()

    def apply_config(self, changed: set[str]) -> None:
        """Apply hot-reloaded config to mini bar UI."""
        if "mode" in changed:
            self.sync_mode()
        if "show_result_text" in changed:
            self.sync_show_result()
        if changed & {"hide_mini_window_when_idle", "mini_window_x"}:
            if "mini_window_x" in changed:
                self._anchor_x = self._engine.config.mini_window_x
            self.refresh_visibility()

    def _update_show_result_style(self):
        if self._show_result:
            self._btn_show_result.setStyleSheet(_BTN_STYLE.format(
                bg=Theme.BG_BUTTON.name(), fg="#ffffff",
                r=13, hover=Theme.BG_BUTTON_HOVER.name(),
            ))
            self._btn_show_result.setToolTip("显示原文: 开")
        else:
            self._btn_show_result.setStyleSheet(_BTN_STYLE.format(
                bg=Theme.BG_BUTTON.name(), fg=Theme.TEXT_SECONDARY.name(),
                r=13, hover=Theme.BG_BUTTON_HOVER.name(),
            ))
            self._btn_show_result.setToolTip("显示原文: 关")

    def _toggle_show_result(self):
        self._show_result = not self._show_result
        cfg = self._engine.config
        cfg.show_result_text = self._show_result
        cfg.save(touched=frozenset({"show_result_text"}))
        self._update_show_result_style()
        self.show_result_changed.emit(self._show_result)

    def _style_action_record(self):
        self._btn_action.setText("●")
        self._btn_action.setToolTip("开始录音")
        self._btn_action.setStyleSheet(_BTN_STYLE.format(
            bg=Theme.BG_BUTTON.name(), fg="#ff3b30",
            r=13, hover=Theme.BG_BUTTON_HOVER.name(),
        ))

    def _on_cancel(self):
        self.request_cancel.emit()

    def start_hotkey_hold(self):
        """External trigger: delay stop button until hold becomes a long-press."""
        if self._mode != "recording":
            return
        self._hotkey_hold_timer.start(self.hotkey_click_threshold_ms())

    def stop_hotkey_hold(self):
        """External trigger: cancel the long-press animation (short press release)."""
        self._hotkey_hold_timer.stop()
        self._btn_rec_stop.cancel_external_hold()
        if not self._hovered:
            self._btn_rec_stop.setVisible(False)
            self._animate_to(REC_W, REC_H, 150)

    def hotkey_click_threshold_ms(self) -> int:
        """Return the short-press threshold shared with the stop button."""
        return self._btn_rec_stop.CLICK_THRESHOLD_MS

    def _begin_hotkey_hold_visuals(self):
        """Show the stop button only after the short-press window has passed."""
        if self._mode != "recording":
            return
        self._btn_rec_stop.setVisible(True)
        if not self._hovered:
            self._animate_to(REC_HOVER_W, REC_H, 150)
        self._btn_rec_stop.start_external_hold(skip_click_threshold=True)

    def _show_recording_status(self):
        self._update_recording_status()
        if not self._rec_status_timer:
            self._rec_status_timer = QTimer(self)
            self._rec_status_timer.setInterval(1000)
            self._rec_status_timer.timeout.connect(self._update_recording_status)
        self._rec_status_timer.start()

    def _update_recording_status(self):
        items: list[str] = []
        cfg = self._engine.config
        if cfg.mini_bar_show_timer:
            if self._engine._countdown_active and cfg.show_countdown:
                items.append(f"⏱ {self._engine._countdown_secs}s 后自动停止")
                self._countdown_popup.hide()
            else:
                elapsed = self._engine.get_duration()
                max_dur = self._engine.effective_max_duration
                remaining = max(0, max_dur - elapsed)
                e_min, e_sec = int(elapsed) // 60, int(elapsed) % 60
                r_min, r_sec = int(remaining) // 60, int(remaining) % 60
                items.append(f"⏱ {e_min}:{e_sec:02d} / {r_min}:{r_sec:02d}")
        dev = self._engine.recorder.device_name
        if dev:
            items.append(f"🎤 {dev}")
        if cfg.mode == "polish":
            model = getattr(self._engine.polisher, '_model', 'unknown')
            items.append(f"✦ {model}")
            prompt_name = "默认提示词"
            if cfg.active_prompt_id:
                for p in cfg.custom_prompts:
                    if p.get("id") == cfg.active_prompt_id:
                        prompt_name = p.get("name", "未命名")
                        break
            items.append(f"📝 {prompt_name}")
        self._status_popup.show_status(items, self)

    def _hide_recording_status(self):
        if self._rec_status_timer:
            self._rec_status_timer.stop()
        self._status_popup.hide()

    def _reposition_popups(self):
        """Reposition visible popups to follow the mini bar during drag."""
        for popup in (self._status_popup, self._countdown_popup):
            if popup.isVisible():
                pos = self.mapToGlobal(self.rect().bottomLeft())
                screen = QApplication.primaryScreen()
                if screen:
                    geo = screen.availableGeometry()
                    x = pos.x() + (self.width() - popup.width()) // 2
                    x = max(geo.x(), min(x, geo.x() + geo.width() - popup.width()))
                else:
                    x = pos.x()
                popup.move(x, pos.y() + 3)

    def _cancel_deferred_shrink(self):
        self._deferred_shrink_timer.stop()

    def _schedule_deferred_shrink(self, delay_ms: int):
        self._cancel_deferred_shrink()
        self._deferred_shrink_timer.start(delay_ms)

    def refresh_visibility(self):
        """Apply the current hide-when-idle preference to the minimal idle state."""
        if self._mode == "idle" and self._engine.state == "ready":
            self._show_idle_surface()
            return
        if not self._engine.config.hide_mini_window_when_idle:
            self.show()

    def _on_action_click(self):
        if self._mode == "hover":
            self.request_record.emit()

    def _set_widgets_for_mode(self, mode: str):
        is_idle = mode == "idle"
        is_hover = mode == "hover"
        is_rec = mode == "recording"

        self._waveform.setVisible(mode in ("recording", "processing", "done"))
        self._btn_action.setVisible(is_hover)
        self._btn_rec_stop.setVisible(is_rec)
        self._btn_polish.setVisible(is_hover)
        self._btn_show_result.setVisible(is_hover)
        self._dot_status.setVisible(mode in ("processing", "done"))
        self._top_bar.setVisible(not is_idle)

    # ── animation helpers ──

    def _log_anim(self, event: str, **extra):
        g = self.geometry()
        details = {
            "mode": self._mode,
            "engine": self._engine.state,
            "hovered": self._hovered,
            "visible": self.isVisible(),
            "geom": (g.x(), g.y(), g.width(), g.height()),
            "target": self._target_size,
            "anchor_x": self._anchor_x,
            "reveal": round(self._reveal_progress, 3),
            "opacity": round(self.windowOpacity(), 3),
            "shape_target": self._shape_target,
            "fade_target": self._fade_target,
            "returning": self._native_returning_to_idle,
            "native": self._using_native_idle(),
        }
        details.update(extra)
        logger.debug(f"[DEBUG] _log_anim | event={event}, details={details}")

    def _current_screen_geometry(self) -> tuple[int, int, int, int] | None:
        screen = QApplication.primaryScreen()
        if not screen:
            return None
        geo = screen.availableGeometry()
        return geo.x(), geo.y(), geo.width(), geo.height()

    def _get_reveal_progress(self) -> float:
        return self._reveal_progress

    def _set_reveal_progress(self, value: float):
        self._reveal_progress = max(0.0, min(float(value), 1.0))
        self._apply_capsule_mask("reveal")
        self.update()

    def _reset_reveal_progress(self, value: float, source: str):
        self._reveal_progress = max(0.0, min(float(value), 1.0))
        self._apply_capsule_mask(source)
        self.update()

    revealProgress = pyqtProperty(float, _get_reveal_progress, _set_reveal_progress)

    def _using_native_idle(self) -> bool:
        return bool(self._native_idle and self._native_idle.available)

    def _idle_top_left(self) -> tuple[int, int] | None:
        screen = QApplication.primaryScreen()
        if not screen:
            return None
        geo = screen.availableGeometry()
        return self._get_x_for_width(IDLE_W), geo.y() + 4

    def _native_idle_scale(self) -> float:
        screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
        if not screen:
            return 1.0
        return float(screen.devicePixelRatio())

    def _hover_region(self) -> QRect:
        screen = QApplication.primaryScreen()
        y = screen.availableGeometry().y() + 4 if screen else self.y()
        return QRect(self._get_x_for_width(HOVER_W), y, HOVER_W, HOVER_H)

    def _cursor_in_hover_region(self) -> bool:
        return self._hover_region().contains(QCursor.pos())

    def _sync_hover_tracking(self, source: str):
        if self._mode != "hover":
            return
        self._hovered = self._cursor_in_hover_region()
        self._log_anim("hover_track", source=source, in_region=self._hovered)
        if self._hovered:
            self._hover_timer.stop()
        elif not self._hover_timer.isActive():
            self._hover_timer.start(HOVER_COLLAPSE_DELAY_MS)

    def _start_hover_polling(self, source: str):
        if not self._hover_poll_timer.isActive():
            logger.debug(
                f"[DEBUG] _start_hover_polling | source={source}, "
                f"mode={self._mode}, geom={self.geometry().getRect()}"
            )
            self._hover_poll_timer.start()

    def _stop_hover_polling(self, source: str):
        if self._hover_poll_timer.isActive():
            logger.debug(
                f"[DEBUG] _stop_hover_polling | source={source}, "
                f"mode={self._mode}"
            )
            self._hover_poll_timer.stop()

    def _poll_hover_state(self):
        cursor = QCursor.pos()
        geom = self.geometry()
        inside = geom.contains(cursor)
        logger.debug(
            f"[DEBUG] _poll_hover_state | mode={self._mode}, "
            f"hovered={self._hovered}, cursor=({cursor.x()}, {cursor.y()}), "
            f"geom={geom.getRect()}, inside={inside}"
        )
        if self._mode == "hover":
            previous = self._hovered
            self._hovered = inside or self._cursor_in_hover_region()
            if previous and not self._hovered and not self._hover_timer.isActive():
                self._hover_timer.start(HOVER_COLLAPSE_DELAY_MS)
            if self._hovered:
                self._hover_timer.stop()
            return
        if self._mode == "recording":
            if self._hovered and not inside:
                logger.debug(
                    "[DEBUG] _poll_hover_state | recording hover lost, "
                    "collapsing capsule"
                )
                self._collapse_recording_hover("hover-poll")
            return
        self._stop_hover_polling("mode-not-hoverable")

    def _show_idle_surface(self):
        if (self._engine.config.hide_mini_window_when_idle
                and self._engine.state == "ready"):
            self._position_at(IDLE_W, IDLE_H)
            self._hide_native_idle()
            self.hide()
            return
        if self._using_native_idle():
            pos = self._idle_top_left()
            if pos is None:
                logger.debug(
                    "[DEBUG] _show_idle_surface | native branch skipped: no idle position"
                )
                return
            scale = self._native_idle_scale()
            native_visible_before = getattr(self._native_idle, "_visible", None)
            self._log_anim(
                "show_native_idle",
                pos=pos,
                scale=scale,
                native_visible_before=native_visible_before,
                qt_visible_before=self.isVisible(),
            )
            self._anim_interrupting = True
            self._geom_anim.stop()
            self._anim_interrupting = False
            self._native_returning_to_idle = False
            logger.debug(
                f"[DEBUG] _show_idle_surface | before position_at | "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}"
            )
            self._position_at(IDLE_W, IDLE_H)
            logger.debug(
                f"[DEBUG] _show_idle_surface | before native show_at | "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}, pos={pos}, scale={scale}"
            )
            self._native_idle.show_at(*pos, scale=scale)
            logger.debug(
                f"[DEBUG] _show_idle_surface | after native show_at | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}"
            )
            self.hide()
            logger.debug(
                f"[DEBUG] _show_idle_surface | after qt hide | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}"
            )
        else:
            self._log_anim("show_qt_idle_fallback")
            self.show()

    def _hide_native_idle(self):
        if self._native_idle:
            self._log_anim(
                "hide_native_idle",
                native_visible_before=getattr(self._native_idle, "_visible", None),
            )
            self._native_idle.hide()
            logger.debug(
                f"[DEBUG] _hide_native_idle | after hide | native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}"
            )

    def _prewarm_hover_surface(self):
        if not self._using_native_idle():
            return
        if self._mode != "idle" or self._engine.state != "ready":
            return
        if self.isVisible():
            return
        if not self._idle_top_left():
            return
        logger.debug(
            f"[DEBUG] _prewarm_hover_surface | start | native_visible="
            f"{getattr(self._native_idle, '_visible', None)}, "
            f"geom={self.geometry().getRect()}"
        )
        self._anim_interrupting = True
        self._geom_anim.stop()
        self._reveal_anim.stop()
        self._opacity_anim.stop()
        self._anim_interrupting = False

        self._mode = "hover"
        self._target_size = (HOVER_W, HOVER_H)
        self._fade_target = None
        self._shape_target = None
        self._native_returning_to_idle = False
        self._hovered = False
        self._style_action_record()
        self._update_polish_style()
        self._update_show_result_style()
        self._set_widgets_for_mode("hover")
        self._reset_reveal_progress(0.0, "prewarm_hover")
        self.setFixedSize(HOVER_W, HOVER_H)
        self.move(-HOVER_W * 2, -HOVER_H * 2)
        self.setWindowOpacity(0.0)
        self.show()
        self.hide()

        self._mode = "idle"
        self._target_size = (IDLE_W, IDLE_H)
        self._set_widgets_for_mode("idle")
        self._reset_reveal_progress(1.0, "prewarm_idle_restore")
        self.setWindowOpacity(1.0)
        self._show_idle_surface()

    def _apply_screen_relayout(self, source: str):
        logger.debug(
            f"[DEBUG] _apply_screen_relayout | source={source}, "
            f"mode={self._mode}, engine={self._engine.state}, "
            f"screen={self._current_screen_geometry()}, "
            f"geom={self.geometry().getRect()}, target={self._target_size}, "
            f"anchor_x={self._anchor_x}"
        )
        screen = QApplication.primaryScreen()
        if not screen:
            logger.debug(
                f"[DEBUG] _apply_screen_relayout | source={source}, no screen"
            )
            return
        self._anim_interrupting = True
        self._geom_anim.stop()
        self._anim_interrupting = False
        if self._mode == "idle" and self._engine.state == "ready":
            self._show_idle_surface()
            return
        if self._mode in ("hover", "recording", "processing", "done"):
            w, h = self._target_size
            if self._mode == "recording":
                w = REC_HOVER_W if self._hovered else REC_W
                h = REC_H
            self._position_at(w, h)
            self._reposition_popups()
            self._apply_capsule_mask(source)
            if sys.platform == "win32":
                self._apply_windows_surface_tweaks(source)
            return
        if self._mode == "shrinking":
            self._animate_hover_to_top(120)

    def _fade_to(self, opacity: float, duration: int, target: str | None = None):
        self._fade_target = target
        self._opacity_anim.stop()
        self._opacity_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._opacity_anim.setDuration(duration)
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(opacity)
        self._opacity_anim.start()

    def _reveal_to(self, value: float, duration: int, target: str | None = None):
        self._shape_target = target
        self._log_anim("reveal_start", end=value, duration=duration, target=target)
        self._reveal_anim.stop()
        self._reveal_anim.setDuration(duration)
        self._reveal_anim.setStartValue(self._reveal_progress)
        self._reveal_anim.setEndValue(value)
        self._reveal_anim.start()

    def _animate_hover_to_top(self, duration: int = 220):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        start = self.geometry()
        target_w = max(IDLE_W, start.width())
        target_h = max(IDLE_H, start.height())
        target_x = self._get_x_for_width(IDLE_W) - (target_w - IDLE_W) // 2
        target = QRect(
            target_x,
            screen.availableGeometry().y() + 4,
            target_w,
            target_h,
        )
        self._native_returning_to_idle = True
        self._anim_interrupting = True
        self._geom_anim.stop()
        self._anim_interrupting = False
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(start)
        self._geom_anim.setEasingCurve(QEasingCurve.Type.InOutQuart)
        self._geom_anim.setDuration(duration)
        self._geom_anim.setStartValue(start)
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()
        self._apply_capsule_mask(f"return_to_top:{target_w}x{target_h}")
        if sys.platform == "win32":
            self._apply_windows_surface_tweaks("return_to_top")
        self._log_anim("return_to_top_start", target_geom=(
            target.x(), target.y(), target.width(), target.height(),
        ), duration=duration)

    def _on_opacity_anim_finished(self):
        if self._fade_target != "idle":
            self._fade_target = None
            return
        self._fade_target = None
        if self._mode == "idle" and self._engine.state == "ready":
            self._set_widgets_for_mode("idle")
            self._reset_reveal_progress(1.0, "opacity_finished")
            self.setWindowOpacity(1.0)
            self._show_idle_surface()

    def _on_reveal_anim_finished(self):
        target = self._shape_target
        self._log_anim("reveal_finished", target=target)
        self._shape_target = None
        if target == "idle" and self._mode == "shrinking":
            logger.debug(
                f"[DEBUG] _on_reveal_anim_finished | idle handoff start | "
                f"mode={self._mode}, reveal={self._reveal_progress}, "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}, "
                f"native_visible={getattr(self._native_idle, '_visible', None)}"
            )
            self._set_widgets_for_mode("idle")
            self.setWindowOpacity(1.0)
            # Keep the visible Qt surface clipped to the tiny pill until native takes over.
            self._reset_reveal_progress(0.0, "reveal_finished_idle")
            logger.debug(
                f"[DEBUG] _on_reveal_anim_finished | idle handoff before surface | "
                f"mode={self._mode}, reveal={self._reveal_progress}, "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}"
            )
            self._show_idle_surface()
            self._mode = "idle"
            self._reveal_progress = 1.0
            logger.debug(
                f"[DEBUG] _on_reveal_anim_finished | idle handoff done | "
                f"mode={self._mode}, reveal={self._reveal_progress}, "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}"
            )
        elif target == "hover" and self._mode == "hover":
            logger.debug(
                f"[DEBUG] _on_reveal_anim_finished | hover handoff start | "
                f"mode={self._mode}, reveal={self._reveal_progress}, "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}, "
                f"native_visible={getattr(self._native_idle, '_visible', None)}"
            )
            self._reset_reveal_progress(1.0, "reveal_finished_hover")
            self._top_bar.setVisible(True)
            self.setWindowOpacity(1.0)
            self._sync_hover_tracking("reveal_finished")
            logger.debug(
                f"[DEBUG] _on_reveal_anim_finished | hover handoff done | "
                f"mode={self._mode}, reveal={self._reveal_progress}, "
                f"qt_visible={self.isVisible()}, geom={self.geometry().getRect()}, "
                f"mask={self.mask().boundingRect().getRect()}"
            )

    def _on_native_idle_enter(self):
        if self._mode in ("idle", "shrinking") and self._engine.state == "ready":
            cursor = QCursor.pos()
            hover_region = self._hover_region()
            logger.debug(
                f"[DEBUG] _on_native_idle_enter | enter | mode={self._mode}, "
                f"state={self._engine.state}, cursor=({cursor.x()}, {cursor.y()}), "
                f"hover_region={hover_region.getRect()}, "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"shape_target={self._shape_target}, returning={self._native_returning_to_idle}"
            )
            if not hover_region.contains(cursor):
                logger.debug(
                    "[DEBUG] _on_native_idle_enter | ignored: cursor outside hover region"
                )
                return
            self._log_anim("native_idle_enter")
            logger.debug(
                f"[DEBUG] _on_native_idle_enter | before apply_hover | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, mask={self.mask().boundingRect().getRect()}"
            )
            self._apply_hover()
            logger.debug(
                f"[DEBUG] _on_native_idle_enter | after apply_hover | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, mask={self.mask().boundingRect().getRect()}, "
                f"reveal={self._reveal_progress}, shape_target={self._shape_target}"
            )
            self._sync_hover_tracking("native_idle_enter")

    def _get_x_for_width(self, w: int) -> int:
        screen = QApplication.primaryScreen()
        if not screen:
            return self._anchor_x if self._anchor_x is not None else 0
        geo = screen.availableGeometry()
        if self._anchor_x is not None:
            x = self._anchor_x - w // 2
            x = max(geo.x(), min(x, geo.x() + geo.width() - w))
            return x
        return geo.x() + (geo.width() - w) // 2

    def _animate_to(self, w, h, duration=220,
                    easing=QEasingCurve.Type.OutCubic):
        self._target_size = (w, h)
        screen = QApplication.primaryScreen()
        if not screen:
            return
        if not self.isVisible():
            if self._using_native_idle() and self._mode == "hover":
                self._position_at(w, h)
                self.show()
                return
            self._position_at(IDLE_W, IDLE_H)
            self.show()

        if self._geom_anim.state() == QPropertyAnimation.State.Running:
            start = self._geom_anim.currentValue()
        else:
            start = self.geometry()

        self._anim_interrupting = True
        self._geom_anim.stop()
        self._anim_interrupting = False

        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(start)

        geo = screen.availableGeometry()
        x = self._get_x_for_width(w)
        y = geo.y() + 4
        target = QRect(x, y, w, h)
        logger.debug(
            f"[DEBUG] _animate_to | mode={self._mode}, "
            f"start={start.getRect()}, target={target.getRect()}, "
            f"screen={self._current_screen_geometry()}, duration={duration}"
        )

        self._geom_anim.setEasingCurve(easing)
        self._geom_anim.setDuration(duration)
        self._geom_anim.setStartValue(start)
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()
        self._apply_capsule_mask(f"animate_to:{w}x{h}")
        if sys.platform == "win32":
            self._apply_windows_surface_tweaks("animate_to")

    def _on_anim_finished(self):
        if self._anim_interrupting:
            return
        if self._native_returning_to_idle:
            self._log_anim("return_to_top_finished")
            return
        w, h = self._target_size
        self.setFixedSize(w, h)
        if self._mode == "shrinking":
            self._mode = "idle"
            self.update()
            self._show_idle_surface()

    def _on_geometry_anim_value_changed(self, value):
        if not isinstance(value, QRect):
            return
        self._apply_capsule_mask("geometry_anim")
        if self._mode != "hover":
            return
        cursor = QCursor.pos()
        self._hovered = value.contains(cursor)
        if self._hovered:
            self._hover_timer.stop()
        elif not self._hover_timer.isActive():
            self._hover_timer.start(HOVER_COLLAPSE_DELAY_MS)
        self._log_anim(
            "geometry_hover_track",
            cursor=(cursor.x(), cursor.y()),
            anim_geom=value.getRect(),
            in_region=self._hovered,
        )

    def _position_at(self, w, h):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = self._get_x_for_width(w)
        y = geo.y() + 4
        self.setFixedSize(w, h)
        self.move(x, y)
        self._apply_capsule_mask(f"position_at:{w}x{h}")
        logger.debug(
            f"[DEBUG] _position_at | mode={self._mode}, "
            f"size=({w}, {h}), pos=({x}, {y}), "
            f"screen={self._current_screen_geometry()}, anchor_x={self._anchor_x}"
        )

    # ── state transitions ──

    def _apply_hover(self):
        was_returning = self._native_returning_to_idle or self._shape_target == "idle"
        defer_native_hide = self._using_native_idle() and not self.isVisible()
        self._log_anim("hover_start", was_returning=was_returning)
        logger.debug(
            f"[DEBUG] _apply_hover | enter | was_returning={was_returning}, "
            f"defer_native_hide={defer_native_hide}, "
            f"qt_visible={self.isVisible()}, native_visible="
            f"{getattr(self._native_idle, '_visible', None)}, "
            f"geom={self.geometry().getRect()}, reveal={self._reveal_progress}, "
            f"shape_target={self._shape_target}, returning={self._native_returning_to_idle}"
        )
        self._start_hover_polling("apply_hover")
        self._mode = "hover"
        self._fade_target = None
        self._shape_target = None
        self._native_returning_to_idle = False
        self._opacity_anim.stop()
        self._reveal_anim.stop()
        if not defer_native_hide:
            self._hide_native_idle()
        self._style_action_record()
        self._update_polish_style()
        self._update_show_result_style()
        self._set_widgets_for_mode("hover")
        if defer_native_hide:
            logger.debug(
                f"[DEBUG] _apply_hover | native hidden-qt branch before position | "
                f"native_visible={getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, reveal={self._reveal_progress}"
            )
            self._target_size = (HOVER_W, HOVER_H)
            self._reveal_progress = 0.0
            self._position_at(HOVER_W, HOVER_H)
            self._apply_capsule_mask("hover_native_start")
            self.setWindowOpacity(1.0)
            self._top_bar.setVisible(False)
            logger.debug(
                f"[DEBUG] _apply_hover | native hidden-qt branch before qt show | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, mask={self.mask().boundingRect().getRect()}, "
                f"reveal={self._reveal_progress}"
            )
            self.show()
            logger.debug(
                f"[DEBUG] _apply_hover | native hidden-qt branch after qt show | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, mask={self.mask().boundingRect().getRect()}"
            )
            self._hide_native_idle()
            logger.debug(
                f"[DEBUG] _apply_hover | native hidden-qt branch after native hide | "
                f"qt_visible={self.isVisible()}, native_visible="
                f"{getattr(self._native_idle, '_visible', None)}, "
                f"geom={self.geometry().getRect()}, mask={self.mask().boundingRect().getRect()}"
            )
            self._reveal_to(1.0, 160, "hover")
            QTimer.singleShot(0, lambda: self._sync_hover_tracking("hover_show"))
        else:
            if was_returning and self._geom_anim.state() == QPropertyAnimation.State.Running:
                current = self._geom_anim.currentValue()
                if current is not None:
                    self._geom_anim.stop()
                    self.setGeometry(current)
            if self.windowOpacity() < 1.0:
                self._fade_to(1.0, 90)
            else:
                self.setWindowOpacity(1.0)
            if was_returning:
                self._top_bar.setVisible(False)
                self._reveal_to(1.0, 120, "hover")
            else:
                self._reset_reveal_progress(1.0, "hover_full")
                self._animate_to(HOVER_W, HOVER_H, 220,
                                 QEasingCurve.Type.InOutQuart)
            QTimer.singleShot(0, lambda: self._sync_hover_tracking("hover_apply"))

    def _apply_recording(self):
        self._log_anim("recording_start")
        self._cancel_deferred_shrink()
        self._start_hover_polling("apply_recording")
        self._fade_target = None
        self._shape_target = None
        self._native_returning_to_idle = False
        self._opacity_anim.stop()
        self._reveal_anim.stop()
        self._hide_native_idle()
        self._reset_reveal_progress(1.0, "recording_start")
        self.setWindowOpacity(1.0)
        self._mode = "recording"
        self._waveform.reset()
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_RECORDING.name()};")

        self._waveform.setVisible(True)
        self._btn_action.setVisible(False)
        self._btn_polish.setVisible(False)
        self._btn_show_result.setVisible(False)
        self._dot_status.setVisible(False)
        self._top_bar.setVisible(True)

        if self._hovered:
            self._btn_rec_stop.setVisible(True)
            self._animate_to(REC_HOVER_W, REC_H, 280)
        else:
            self._btn_rec_stop.setVisible(False)
            self._animate_to(REC_W, REC_H, 280)

    def _apply_processing(self):
        self._cancel_deferred_shrink()
        self._stop_hover_polling("processing")
        self._mode = "processing"
        self._hovered = False
        self._hide_recording_status()
        self._waveform.freeze()
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_PROCESSING.name()};")
        self._set_widgets_for_mode("processing")
        self._animate_to(REC_W, REC_H, 150)

    def _apply_done(self):
        self._mode = "done"
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_DONE.name()};")

    def _shrink_to_idle(self):
        if self._engine.state != "ready":
            return
        if self._mode in ("idle", "shrinking"):
            return
        self._stop_hover_polling("shrink_to_idle")
        if self._using_native_idle():
            self._log_anim("shrink_start")
            self._mode = "shrinking"
            self._hide_recording_status()
            self._target_size = (IDLE_W, IDLE_H)
            if self.isVisible():
                self._reset_reveal_progress(1.0, "shrink_start")
                self.setWindowOpacity(1.0)
                self._set_widgets_for_mode("idle")
                self._animate_hover_to_top(160)
                self._reveal_to(0.0, 180, "idle")
            else:
                self._set_widgets_for_mode("idle")
                self._reset_reveal_progress(1.0, "shrink_hidden")
                self.setWindowOpacity(1.0)
                self._show_idle_surface()
                self._mode = "idle"
            return
        self._mode = "shrinking"
        self._hide_recording_status()
        self._set_widgets_for_mode("idle")
        self._animate_to(IDLE_W, IDLE_H, 200,
                         QEasingCurve.Type.InOutQuart)
        self.show()

    # ── engine signals ──

    def _on_engine_state(self, state: str):
        logger.debug(f"[MiniWin] Engine state → {state} (was {self._mode})")
        if state == "recording":
            self._apply_recording()
        elif state == "processing":
            self._countdown_popup.hide()
            self._apply_processing()
        elif state == "ready":
            self._countdown_popup.hide()
            if self._mode in ("recording", "processing"):
                self._shrink_to_idle()

    def _on_audio(self, data: bytes):
        if self._mode == "recording":
            self._waveform.update_data(data)

    def _on_countdown_tick(self, seconds: int):
        if self._mode == "recording" and seconds >= 0 \
                and self._engine.config.show_countdown \
                and not self._status_popup.isVisible():
            self._countdown_popup.show_countdown(seconds, self)
        else:
            self._countdown_popup.hide()

    def _on_done(self, text: str):
        self._apply_done()
        if self._show_result:
            self._result_popup.show_text(text, self)
        delay = 0 if self._engine.config.hide_mini_window_when_idle else 800
        self._schedule_deferred_shrink(delay)

    def _on_hover_timeout(self):
        if not self._hovered and self._mode == "hover":
            self._shrink_to_idle()

    def _collapse_recording_hover(self, source: str):
        if self._mode != "recording":
            return
        self._hovered = False
        self._hide_recording_status()
        self._btn_rec_stop.setVisible(False)
        self._animate_to(REC_W, REC_H, 150)
        logger.debug(
            f"[DEBUG] _collapse_recording_hover | source={source}, "
            f"countdown_active={self._engine._countdown_active}, "
            f"show_countdown={self._engine.config.show_countdown}"
        )
        if self._engine._countdown_active and self._engine.config.show_countdown:
            self._countdown_popup.show_countdown(
                self._engine._countdown_secs, self
            )

    # ── painting ──

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._dot_status.move(self.width() - 13, 3)
        self._dot_status.raise_()
        self._apply_capsule_mask("resize")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        w, h = float(self.width()), float(self.height())
        progress = self._reveal_progress if self._mode in ("hover", "shrinking") else 1.0
        draw_w = IDLE_W + (w - IDLE_W) * progress
        draw_h = IDLE_H + (h - IDLE_H) * progress
        x = (w - draw_w) / 2
        r = min(RADIUS, draw_h / 2)
        path.addRoundedRect(QRectF(x, 0, draw_w, draw_h), r, r)
        bg = QColor(Theme.BG_PRIMARY)
        bg.setAlpha(255)
        p.fillPath(path, bg)
        p.end()

    # ── hover / drag ──

    def enterEvent(self, event):
        logger.debug(
            f"[DEBUG] enterEvent | mode={self._mode}, "
            f"cursor={QCursor.pos().x(), QCursor.pos().y()}, "
            f"geom={self.geometry().getRect()}"
        )
        if self._mode in ("idle", "shrinking"):
            self._apply_hover()
        elif self._mode == "recording":
            self._hovered = True
            self._hover_timer.stop()
            self._start_hover_polling("recording-enter")
            self._btn_rec_stop.setVisible(True)
            self._animate_to(REC_HOVER_W, REC_H, 150)
            self._show_recording_status()
        else:
            self._sync_hover_tracking("enter")
        self.update()

    def leaveEvent(self, event):
        logger.debug(
            f"[DEBUG] leaveEvent | mode={self._mode}, "
            f"cursor={QCursor.pos().x(), QCursor.pos().y()}, "
            f"geom={self.geometry().getRect()}"
        )
        if self._mode == "hover":
            self._sync_hover_tracking("leave")
        elif self._mode == "recording":
            self._collapse_recording_hover("leaveEvent")
        else:
            self._hide_recording_status()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_pos
            self.move(new_pos)
            self._anchor_x = new_pos.x() + self.width() // 2
            self._reposition_popups()
            logger.debug(
                f"[DEBUG] mouseMoveEvent | drag_pos=({new_pos.x()}, "
                f"{new_pos.y()}), anchor_x={self._anchor_x}, "
                f"size=({self.width()}, {self.height()})"
            )

    def mouseReleaseEvent(self, event):
        if self._drag_pos is not None and self._anchor_x is not None:
            self._engine.config.mini_window_x = self._anchor_x
            self._engine.config.save(touched=frozenset({"mini_window_x"}))
            logger.debug(
                f"[DEBUG] mouseReleaseEvent | saved anchor_x={self._anchor_x}"
            )
            if self._engine.state != "recording":
                self._animate_to(
                    self.width(), self.height(), 160,
                    QEasingCurve.Type.InOutQuart,
                )
        self._drag_pos = None

    def reset_position(self):
        self._anchor_x = None
        self._engine.config.mini_window_x = None
        self._engine.config.save(touched=frozenset({"mini_window_x"}))
        w, h = self._target_size
        self._position_at(w, h)
        logger.debug("[DEBUG] reset_position | anchor cleared")

    def contextMenuEvent(self, event):
        event.accept()
