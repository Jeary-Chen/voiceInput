import ctypes
import ctypes.wintypes as wintypes
import os
from datetime import date

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit,
)

from config import Config
from core.log import logger
from core.engine import VoiceEngine
from ui.mini_window import MiniRecordingWindow
from ui.sounds import AudioCues
from ui import icons


_MOD_MAP = {"ctrl": 0x0002, "shift": 0x0004, "alt": 0x0001}
_VK_MAP = {chr(i).lower(): i for i in range(0x41, 0x5B)}  # a-z
_VK_MAP.update({str(i): 0x30 + i for i in range(10)})  # 0-9
_VK_MAP.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})  # F1-F24
_VK_MAP.update({
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "numlock": 0x90, "scrolllock": 0x91, "capslock": 0x14,
    "printscreen": 0x2C, "pause": 0x13,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
})


def _parse_hotkey(s: str) -> tuple[int, int]:
    mod = 0
    vk = 0
    for part in s.lower().split("+"):
        part = part.strip()
        if part in _MOD_MAP:
            mod |= _MOD_MAP[part]
        elif part in _VK_MAP:
            vk = _VK_MAP[part]
    return mod, vk


class HotkeyThread(QThread):
    triggered = pyqtSignal()

    _HOTKEY_ID = 1

    def __init__(self, hotkey_str: str):
        super().__init__()
        self._mod, self._vk = _parse_hotkey(hotkey_str)
        self._thread_id: int = 0

    def run(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, self._HOTKEY_ID, self._mod, self._vk):
            logger.warning(f"Failed to register hotkey (mod=0x{self._mod:X}, vk=0x{self._vk:X})")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == 0x0312 and msg.wParam == self._HOTKEY_ID:
                self.triggered.emit()
        user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def stop_hotkey(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)


class _HotkeyDialog(QDialog):
    """Captures a keyboard shortcut by pressing keys, with real-time conflict check."""

    _QT_MOD_NAMES = {
        Qt.KeyboardModifier.ControlModifier: "ctrl",
        Qt.KeyboardModifier.ShiftModifier: "shift",
        Qt.KeyboardModifier.AltModifier: "alt",
    }

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setFixedSize(360, 170)
        self.setStyleSheet("background:#1e1e1e; color:#fff;")
        self._result: str | None = None
        self._current = current
        self._captured: str | None = None
        self._available = False

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel("按下新的快捷键组合：")
        hint.setStyleSheet("font-size:13px;")
        layout.addWidget(hint)

        self._key_display = QLabel(current.replace("+", " + ").upper())
        self._key_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_display.setFixedHeight(44)
        self._key_display.setStyleSheet("""
            background:#2a2a2a; color:#fff; border:1px solid #555;
            border-radius:8px; font-size:18px; font-weight:bold;
        """)
        layout.addWidget(self._key_display)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet("font-size:12px; color:#999;")
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._btn_ok = QPushButton("保存")
        self._btn_ok.setFixedWidth(80)
        self._btn_ok.setEnabled(False)
        self._btn_ok.setStyleSheet("""
            QPushButton { background:#007aff; color:#fff; border:none;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#0066dd; }
            QPushButton:disabled { background:#333; color:#666; }
        """)
        self._btn_ok.clicked.connect(self._do_accept)
        btn_row.addWidget(self._btn_ok)
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:1px solid #444;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#2a2a2a; color:#fff; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                   Qt.Key.Key_Meta, Qt.Key.Key_unknown):
            return

        if key == Qt.Key.Key_Escape and not (mods & ~Qt.KeyboardModifier.NoModifier):
            self.reject()
            return

        parts = []
        for qt_mod, name in self._QT_MOD_NAMES.items():
            if mods & qt_mod:
                parts.append(name)

        key_text = QKeySequence(key).toString().lower()
        if not key_text:
            return

        is_safe_single = key_text.startswith("f") and key_text[1:].isdigit()
        if not parts and not is_safe_single:
            self._status.setText("普通键需搭配修饰键 (Ctrl/Shift/Alt)，功能键可单独使用")
            self._status.setStyleSheet("font-size:12px; color:#ff6b60;")
            self._btn_ok.setEnabled(False)
            return

        parts.append(key_text)

        combo = "+".join(parts)
        display = combo.replace("+", " + ").upper()
        self._key_display.setText(display)
        self._captured = combo

        if combo == self._current:
            self._status.setText("与当前快捷键相同")
            self._status.setStyleSheet("font-size:12px; color:#999;")
            self._btn_ok.setEnabled(False)
            return

        available = _test_hotkey_register(combo)
        if available:
            self._key_display.setStyleSheet("""
                background:#1a3a1a; color:#34c759; border:1px solid #34c759;
                border-radius:8px; font-size:18px; font-weight:bold;
            """)
            self._status.setText("✓ 快捷键可用")
            self._status.setStyleSheet("font-size:12px; color:#34c759;")
            self._btn_ok.setEnabled(True)
            self._available = True
        else:
            self._key_display.setStyleSheet("""
                background:#3a1a1a; color:#ff3b30; border:1px solid #ff3b30;
                border-radius:8px; font-size:18px; font-weight:bold;
            """)
            self._status.setText("✕ 已被系统或其他程序占用")
            self._status.setStyleSheet("font-size:12px; color:#ff3b30;")
            self._btn_ok.setEnabled(False)
            self._available = False

    def _do_accept(self):
        if self._captured and self._available:
            self._result = self._captured
            self.accept()

    @property
    def hotkey(self) -> str | None:
        return self._result


def _test_hotkey_register(hotkey_str: str) -> bool:
    """Try to register a global hotkey. Returns True if available."""
    mod, vk = _parse_hotkey(hotkey_str)
    if not vk:
        return False
    user32 = ctypes.windll.user32
    test_id = 0x7FFF
    ok = user32.RegisterHotKey(None, test_id, mod, vk)
    if ok:
        user32.UnregisterHotKey(None, test_id)
    return bool(ok)


class _ApiKeyDialog(QDialog):
    """Dialog to configure DashScope API key."""

    def __init__(self, current_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置 API Key")
        self.setFixedSize(420, 150)
        self.setStyleSheet("background:#1e1e1e; color:#fff;")
        self._result: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        hint = QLabel("DashScope API Key（阿里云百炼）：")
        hint.setStyleSheet("font-size:13px;")
        hint_row.addWidget(hint)
        hint_row.addStretch()
        btn_open = QPushButton("获取 ↗")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setStyleSheet("""
            QPushButton { background:transparent; color:#007aff; border:none;
                          font-size:12px; }
            QPushButton:hover { color:#339aff; text-decoration:underline; }
        """)
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://bailian.console.aliyun.com/cn-beijing/?tab=model#/api-key")))
        hint_row.addWidget(btn_open)
        layout.addLayout(hint_row)

        self._input = QLineEdit(current_key)
        self._input.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setStyleSheet("""
            QLineEdit {
                background:#2a2a2a; color:#fff; border:1px solid #555;
                border-radius:6px; padding:8px; font-size:13px;
                font-family: Consolas, monospace;
            }
            QLineEdit:focus { border:1px solid #007aff; }
        """)
        layout.addWidget(self._input)

        self._toggle_vis = QPushButton("显示")
        self._toggle_vis.setFixedWidth(50)
        self._toggle_vis.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:none;
                          font-size:12px; }
            QPushButton:hover { color:#fff; }
        """)
        self._toggle_vis.clicked.connect(self._toggle_visibility)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._toggle_vis)
        btn_row.addStretch()

        btn_ok = QPushButton("保存")
        btn_ok.setFixedWidth(80)
        btn_ok.setStyleSheet("""
            QPushButton { background:#007aff; color:#fff; border:none;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#0066dd; }
        """)
        btn_ok.clicked.connect(self._do_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:1px solid #444;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#2a2a2a; color:#fff; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _toggle_visibility(self):
        if self._input.echoMode() == QLineEdit.EchoMode.Password:
            self._input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_vis.setText("隐藏")
        else:
            self._input.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_vis.setText("显示")

    def _do_save(self):
        key = self._input.text().strip()
        self._result = key
        self.accept()

    @property
    def api_key(self) -> str | None:
        return self._result


MENU_STYLE = """
    QMenu {
        background: #2a2a2a;
        color: #ffffff;
        border: 1px solid #444;
        border-radius: 8px;
        padding: 6px 0;
    }
    QMenu::item {
        padding: 7px 28px 7px 16px;
        font-size: 13px;
    }
    QMenu::item:selected {
        background: #3a3a3a;
    }
    QMenu::item:disabled {
        color: #666;
    }
    QMenu::separator {
        height: 1px;
        background: #3a3a3a;
        margin: 4px 12px;
    }
"""


class VoiceTray(QSystemTrayIcon):
    def __init__(self, engine: VoiceEngine, mini: MiniRecordingWindow, config: Config):
        super().__init__()
        self._engine = engine
        self._mini = mini
        self._config = config

        self._audio = AudioCues()
        self._audio.set_enabled(config.play_sounds)

        self._key_warning = not config.api_key
        self._mic_warning = False
        if self._key_warning:
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip("API Key 未配置，右键点击配置")
        else:
            self.setIcon(icons.icon_idle())
            self._update_tooltip("就绪")

        self._build_menu()

        if config.tray_click_to_record:
            self.activated.connect(self._on_activated)

        engine.state_changed.connect(self._on_state)
        engine.transcription_done.connect(self._on_done)
        engine.error_occurred.connect(self._on_error)
        engine.api_key_invalid.connect(self._on_key_invalid)
        engine.mic_unavailable.connect(self._on_mic_unavailable)

        mini.request_record.connect(self._on_hotkey)
        mini.request_stop.connect(self._on_hotkey)
        mini.request_cancel.connect(self._on_cancel)
        mini.request_history.connect(self._open_history)

        self._hotkey = HotkeyThread(config.hotkey)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.start()

        self.show()

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(MENU_STYLE)

        self._act_record = QAction("开始录音", menu)
        self._act_record.triggered.connect(self._on_hotkey)
        menu.addAction(self._act_record)

        menu.addSeparator()

        act_history = QAction("打开历史记录", menu)
        act_history.triggered.connect(self._open_history)
        menu.addAction(act_history)

        act_log = QAction("查看处理日志", menu)
        act_log.triggered.connect(self._open_log)
        menu.addAction(act_log)

        menu.addSeparator()

        self._mode_menu = QMenu("切换模式", menu)
        self._mode_menu.setStyleSheet(MENU_STYLE)
        for mode_id, mode_name in [("transcribe", "纯转录"), ("polish", "智能润色")]:
            act = QAction(mode_name, self._mode_menu)
            act.setCheckable(True)
            act.setChecked(self._config.mode == mode_id)
            act.triggered.connect(lambda checked, m=mode_id: self._set_mode(m))
            self._mode_menu.addAction(act)
        menu.addMenu(self._mode_menu)

        self._device_menu = QMenu("输入设备", menu)
        self._device_menu.setStyleSheet(MENU_STYLE)
        self._refresh_devices()
        menu.addMenu(self._device_menu)

        menu.addSeparator()

        hotkey_display = self._config.hotkey.replace("+", " + ").upper()
        self._act_hotkey = QAction(f"快捷键: {hotkey_display}", menu)
        self._act_hotkey.triggered.connect(self._configure_hotkey)
        menu.addAction(self._act_hotkey)

        act_apikey = QAction("API Key", menu)
        act_apikey.triggered.connect(self._configure_apikey)
        menu.addAction(act_apikey)

        act_reset_pos = QAction("重置指示器位置", menu)
        act_reset_pos.triggered.connect(self._reset_mini_position)
        menu.addAction(act_reset_pos)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def _refresh_devices(self):
        self._device_menu.clear()
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    name = info.get("name", f"Device {i}")
                    act = QAction(name, self._device_menu)
                    act.setCheckable(True)
                    act.setChecked(self._config.mic_index == i)
                    act.triggered.connect(lambda checked, idx=i: self._set_device(idx))
                    self._device_menu.addAction(act)
            pa.terminate()
        except Exception:
            act = QAction("(无法枚举设备)", self._device_menu)
            act.setEnabled(False)
            self._device_menu.addAction(act)

    def _set_mode(self, mode_id: str):
        self._config.mode = mode_id
        self._config.save()
        for act in self._mode_menu.actions():
            mode_name_map = {"纯转录": "transcribe", "智能润色": "polish"}
            act.setChecked(mode_name_map.get(act.text(), "") == mode_id)

    def _configure_hotkey(self):
        self._hotkey.stop_hotkey()
        self._hotkey.wait(2000)

        dlg = _HotkeyDialog(self._config.hotkey)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.hotkey:
            self._hotkey = HotkeyThread(self._config.hotkey)
            self._hotkey.triggered.connect(self._on_hotkey)
            self._hotkey.start()
            return

        new_key = dlg.hotkey
        self._config.hotkey = new_key
        self._config.save()
        self._hotkey = HotkeyThread(new_key)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.start()
        display = new_key.replace("+", " + ").upper()
        self._act_hotkey.setText(f"快捷键: {display}")
        logger.info(f"Hotkey changed to: {display}")

    def _configure_apikey(self):
        dlg = _ApiKeyDialog(self._config.api_key)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.api_key is None:
            return
        self._config.api_key = dlg.api_key
        self._config.save()
        self._engine.asr.api_key = dlg.api_key
        self._engine.polisher._api_key = dlg.api_key
        logger.info("API Key updated")
        if dlg.api_key:
            self._set_key_warning(False)

    def _set_device(self, idx: int):
        self._config.mic_index = idx
        self._config.save()
        self._engine.recorder.set_device(idx)
        self._refresh_devices()
        if self._mic_warning:
            self._mic_warning = False
            self._restore_idle_icon()

    # ── tray interaction ──

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_hotkey()

    def _on_cancel(self):
        if self._engine.state == "recording":
            self._audio.play_stop()
            self._engine.cancel()

    def _on_hotkey(self):
        if self._engine.state == "processing":
            return
        if self._engine.state == "ready":
            if not self._config.api_key:
                self._configure_apikey()
                return
            self._audio.play_start()
        elif self._engine.state == "recording":
            self._audio.play_stop()
        self._engine.toggle_record()

    # ── engine state ──

    def _update_tooltip(self, status: str):
        self.setToolTip(f"VoiceInput — {status}")

    def _on_state(self, state: str):
        if state == "recording":
            self._mic_warning = False
            self.setIcon(icons.icon_recording())
            self._update_tooltip("录音中")
            self._act_record.setText("停止录音")
        elif state == "processing":
            self.setIcon(icons.icon_processing())
            self._update_tooltip("识别中")
            self._act_record.setText("处理中...")
            self._act_record.setEnabled(False)
        elif state == "ready":
            self._restore_idle_icon()
            self._act_record.setText("开始录音")
            self._act_record.setEnabled(True)

    def _on_done(self, text: str):
        self._audio.play_done()
        self.setIcon(icons.icon_done())
        self._update_tooltip("就绪")
        QTimer.singleShot(2000, self._restore_idle_icon)

    def _on_error(self, msg: str):
        self.setIcon(icons.icon_warning())
        self._update_tooltip("就绪")
        QTimer.singleShot(2000, self._restore_idle_icon)

    def _on_key_invalid(self):
        self._key_warning = True

    def _on_mic_unavailable(self):
        self._mic_warning = True
        self.showMessage(
            "VoiceInput",
            "无法打开麦克风，请检查设备连接或在右键菜单中切换输入设备",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _set_key_warning(self, warning: bool):
        self._key_warning = warning
        self._restore_idle_icon()

    def _restore_idle_icon(self):
        if self._key_warning:
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip("API Key 无效，右键点击配置")
        elif self._mic_warning:
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip("麦克风不可用，右键切换输入设备")
        else:
            self.setIcon(icons.icon_idle())
            self._update_tooltip("就绪")

    def _reset_mini_position(self):
        self._mini.reset_position()

    def _open_history(self):
        path = Config.history_dir()
        os.startfile(str(path))

    def _open_log(self):
        from core.log import _LOG_DIR
        os.startfile(str(_LOG_DIR))

    def _quit(self):
        if self._engine.state == "recording":
            self._engine.cancel()
        self._hotkey.stop_hotkey()
        self._hotkey.wait(2000)
        QApplication.quit()
