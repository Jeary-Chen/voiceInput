"""Compact floating recording indicator — with smooth animations.

Behavior:
  - Idle:      tiny pill stuck to top-center of screen
  - Hover:     expands to show three buttons: record, polish toggle, show-result toggle
  - Recording: capsule with waveform (stop button on hover)
  - Done:      if show-result is on, popup shows final text below
"""
from PyQt6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QAction
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QApplication, QMenu,
)

from ui.theme import Theme
from ui.waveform_widget import WaveformWidget

IDLE_W, IDLE_H = 48, 8
HOVER_W, HOVER_H = 120, 36       # 3 buttons
REC_W, REC_H = 90, 38            # waveform only
REC_HOVER_W = 122                 # waveform + stop button on hover
RESULT_W = 340                    # result popup width
RADIUS = 19

_BTN_STYLE = """
    QPushButton {{
        background: {bg}; color: {fg};
        border: none; border-radius: {r}px;
        font-size: 13px;
    }}
    QPushButton:hover {{ background: {hover}; }}
"""


class _ResultPopup(QWidget):
    """Floating text popup that shows below the mini window and auto-hides."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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
        layout.addWidget(self._label)

        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self.hide)

    def show_text(self, text: str, anchor_widget: QWidget, duration_ms: int = 3500):
        self._label.setText(text)
        self.setFixedWidth(RESULT_W)
        self.adjustSize()

        pos = anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft())
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = geo.x() + (geo.width() - RESULT_W) // 2
        else:
            x = pos.x()
        self.move(x, pos.y() + 4)
        self.show()
        self._auto_hide.start(duration_ms)

    def enterEvent(self, event):
        self._auto_hide.stop()

    def leaveEvent(self, event):
        self._auto_hide.start(1500)

    def paintEvent(self, event):
        pass


class MiniRecordingWindow(QWidget):
    request_record = pyqtSignal()
    request_stop = pyqtSignal()
    request_history = pyqtSignal()

    def __init__(self, engine):
        super().__init__()
        self._engine = engine
        self._mode = "idle"
        self._drag_pos = None
        self._hovered = False
        self._show_result = False
        self._anchor_x: int | None = None  # custom x after drag, None = center

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._geom_anim = QPropertyAnimation(self, b"geometry")
        self._geom_anim.finished.connect(self._on_anim_finished)
        self._target_size = (IDLE_W, IDLE_H)

        self._result_popup = _ResultPopup()

        self._build_ui()
        self._set_widgets_for_mode("idle")
        self._position_at(IDLE_W, IDLE_H)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timeout)

        engine.state_changed.connect(self._on_engine_state)
        engine.audio_data.connect(self._on_audio)
        engine.transcription_done.connect(self._on_done)
        engine.error_occurred.connect(self._on_error)

    # ── UI build ──

    def _build_ui(self):
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        self._top_bar = QWidget()
        self._top_layout = QHBoxLayout(self._top_bar)
        self._top_layout.setContentsMargins(5, 5, 5, 5)
        self._top_layout.setSpacing(6)

        self._waveform = WaveformWidget(compact=True)
        self._waveform.setFixedSize(70, 26)
        self._top_layout.addWidget(self._waveform)

        # Button 1: record / stop
        self._btn_action = QPushButton("●")
        self._btn_action.setFixedSize(26, 26)
        self._btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_action.clicked.connect(self._on_action_click)
        self._top_layout.addWidget(self._btn_action)
        self._style_action_record()

        # Button 2: toggle polish
        self._btn_polish = QPushButton("✦")
        self._btn_polish.setFixedSize(26, 26)
        self._btn_polish.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_polish.clicked.connect(self._toggle_polish)
        self._top_layout.addWidget(self._btn_polish)
        self._update_polish_style()

        # Button 3: toggle result display
        self._btn_show_result = QPushButton("◳")
        self._btn_show_result.setFixedSize(26, 26)
        self._btn_show_result.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_show_result.clicked.connect(self._toggle_show_result)
        self._top_layout.addWidget(self._btn_show_result)
        self._update_show_result_style()

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
        cfg.save()
        self._update_polish_style()

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
        self._update_show_result_style()

    def _style_action_record(self):
        self._btn_action.setText("●")
        self._btn_action.setToolTip("开始录音")
        self._btn_action.setStyleSheet(_BTN_STYLE.format(
            bg=Theme.BG_BUTTON.name(), fg="#ff3b30",
            r=13, hover=Theme.BG_BUTTON_HOVER.name(),
        ))

    def _on_action_click(self):
        if self._mode == "hover":
            self.request_record.emit()
        elif self._mode == "recording":
            self.request_stop.emit()

    def _set_widgets_for_mode(self, mode: str):
        is_idle = mode == "idle"
        is_hover = mode == "hover"

        self._waveform.setVisible(mode in ("recording", "processing", "done"))
        self._btn_action.setVisible(is_hover or mode == "recording")
        self._btn_polish.setVisible(is_hover)
        self._btn_show_result.setVisible(is_hover)
        self._dot_status.setVisible(mode in ("processing", "done"))
        self._top_bar.setVisible(not is_idle)

    # ── animation helpers ──

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
        if not screen or not self.isVisible():
            self._position_at(w, h)
            return

        geo = screen.availableGeometry()
        x = self._get_x_for_width(w)
        y = geo.y() + 4
        target = QRect(x, y, w, h)

        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)

        self._geom_anim.stop()
        self._geom_anim.setEasingCurve(easing)
        self._geom_anim.setDuration(duration)
        self._geom_anim.setStartValue(self.geometry())
        self._geom_anim.setEndValue(target)
        self._geom_anim.start()

    def _on_anim_finished(self):
        w, h = self._target_size
        self.setFixedSize(w, h)
        if self._mode == "shrinking":
            self._mode = "idle"
            self.update()

    def _position_at(self, w, h):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = self._get_x_for_width(w)
        y = geo.y() + 4
        self.setFixedSize(w, h)
        self.move(x, y)

    # ── state transitions ──

    def _apply_hover(self):
        self._mode = "hover"
        self._style_action_record()
        self._update_polish_style()
        self._update_show_result_style()
        self._animate_to(HOVER_W, HOVER_H, 300,
                         QEasingCurve.Type.InOutQuart)
        QTimer.singleShot(200, self._reveal_hover_content)

    def _reveal_hover_content(self):
        if self._mode == "hover":
            self._set_widgets_for_mode("hover")

    def _apply_recording(self):
        self._mode = "recording"
        self._waveform.reset()
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_RECORDING.name()};")

        self._waveform.setVisible(True)
        self._btn_polish.setVisible(False)
        self._btn_show_result.setVisible(False)
        self._dot_status.setVisible(False)
        self._top_bar.setVisible(True)

        self._style_action_record()
        self._btn_action.setToolTip("停止录音")

        if self._hovered:
            self._btn_action.setVisible(True)
            self._animate_to(REC_HOVER_W, REC_H, 280)
        else:
            self._btn_action.setVisible(False)
            self._animate_to(REC_W, REC_H, 280)

    def _apply_processing(self):
        self._mode = "processing"
        self._waveform.freeze()
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_PROCESSING.name()};")
        self._btn_action.setVisible(False)
        self._dot_status.setVisible(True)

    def _apply_done(self):
        self._mode = "done"
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_DONE.name()};")

    def _shrink_to_idle(self):
        if self._mode in ("idle", "shrinking"):
            return
        self._mode = "shrinking"
        self._set_widgets_for_mode("idle")
        self._animate_to(IDLE_W, IDLE_H, 280,
                         QEasingCurve.Type.InOutQuart)
        self.show()

    # ── engine signals ──

    def _on_engine_state(self, state: str):
        if state == "recording":
            self._apply_recording()
            self.show()
        elif state == "processing":
            self._apply_processing()
        elif state == "ready":
            if self._mode in ("recording", "processing"):
                self._shrink_to_idle()

    def _on_audio(self, data: bytes):
        if self._mode == "recording":
            self._waveform.update_data(data)

    def _on_done(self, text: str):
        self._apply_done()
        if self._show_result:
            self._result_popup.show_text(text, self)
        QTimer.singleShot(800, self._shrink_to_idle)

    def _on_error(self, msg: str):
        self._dot_status.setStyleSheet(f"color: {Theme.COLOR_WARNING.name()};")
        QTimer.singleShot(1500, self._shrink_to_idle)

    def _on_hover_timeout(self):
        if not self._hovered and self._mode == "hover":
            self._shrink_to_idle()

    # ── painting ──

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._dot_status.move(self.width() - 13, 3)
        self._dot_status.raise_()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        w, h = float(self.width()), float(self.height())
        r = min(RADIUS, h / 2)
        path.addRoundedRect(0, 0, w, h, r, r)
        bg = QColor(Theme.BG_PRIMARY)
        bg.setAlpha(245)
        p.fillPath(path, bg)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
        p.drawPath(path)
        p.end()

    # ── hover / drag ──

    def enterEvent(self, event):
        self._hovered = True
        self._hover_timer.stop()
        if self._mode in ("idle", "shrinking"):
            self._apply_hover()
        elif self._mode == "recording":
            self._btn_action.setVisible(True)
            self._animate_to(REC_HOVER_W, REC_H, 150)
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        if self._mode == "hover":
            self._hover_timer.start(300)
        elif self._mode == "recording":
            self._btn_action.setVisible(False)
            self._animate_to(REC_W, REC_H, 150)
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

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#2a2a2a; color:#fff; border:1px solid #444;
                    padding:4px 0; }
            QMenu::item { padding:6px 20px; }
            QMenu::item:selected { background:#3a3a3a; }
        """)
        act_history = QAction("📂  历史记录", menu)
        act_history.triggered.connect(lambda: self.request_history.emit())
        menu.addAction(act_history)
        menu.exec(event.globalPos())
