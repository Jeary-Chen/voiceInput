import ctypes
import os
import time

from PyQt6.QtCore import Qt, QEvent, QThread, pyqtSignal, QTimer, QUrl
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


_MOD_KEYS = frozenset({
    "ctrl", "shift", "alt",
    "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt",
})

_VK_TO_NAME: dict[int, str] = {}
_NAME_TO_VK: dict[str, int] = {}


def _build_vk_maps():
    for i in range(0x41, 0x5B):
        n = chr(i).lower()
        _NAME_TO_VK[n] = i
        _VK_TO_NAME[i] = n
    for i in range(10):
        _NAME_TO_VK[str(i)] = 0x30 + i
        _VK_TO_NAME[0x30 + i] = str(i)
    for i in range(1, 25):
        _NAME_TO_VK[f"f{i}"] = 0x70 + i - 1
        _VK_TO_NAME[0x70 + i - 1] = f"f{i}"
    extras = {
        "space": 0x20, "enter": 0x0D, "tab": 0x09, "escape": 0x1B,
        "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
        "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91,
        "printscreen": 0x2C, "pause": 0x13,
        ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
        "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    }
    for n, vk in extras.items():
        _NAME_TO_VK[n] = vk
        _VK_TO_NAME[vk] = n
    _VK_TO_NAME[0xA0] = "lshift"
    _VK_TO_NAME[0xA1] = "rshift"
    _VK_TO_NAME[0xA2] = "lctrl"
    _VK_TO_NAME[0xA3] = "rctrl"
    _VK_TO_NAME[0xA4] = "lalt"
    _VK_TO_NAME[0xA5] = "ralt"
    _VK_TO_NAME.setdefault(0x10, "lshift")
    _VK_TO_NAME.setdefault(0x11, "lctrl")
    _VK_TO_NAME.setdefault(0x12, "lalt")
    _NAME_TO_VK.update({
        "lctrl": 0xA2, "rctrl": 0xA3,
        "lshift": 0xA0, "rshift": 0xA1,
        "lalt": 0xA4, "ralt": 0xA5,
    })


_build_vk_maps()

_DISPLAY = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
    "lctrl": "L-Ctrl", "rctrl": "R-Ctrl",
    "lshift": "L-Shift", "rshift": "R-Shift",
    "lalt": "L-Alt", "ralt": "R-Alt",
    "space": "Space", "enter": "Enter", "tab": "Tab",
    "escape": "Esc", "backspace": "Backspace", "delete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "PageUp", "pagedown": "PageDown",
    "up": "↑", "down": "↓", "left": "←", "right": "→",
    "capslock": "CapsLock", "numlock": "NumLock",
    "scrolllock": "ScrollLock", "printscreen": "PrtSc", "pause": "Pause",
}


def _canonical(parts) -> str:
    parts = list({p.strip().lower() for p in parts})
    mods = sorted(p for p in parts if p in _MOD_KEYS)
    others = sorted(p for p in parts if p not in _MOD_KEYS)
    return "+".join(mods + others)


def _hotkey_display(combo: str) -> str:
    parts = combo.split("+")
    return " + ".join(_DISPLAY.get(p, p.upper()) for p in parts)


_PYN_KEYS: dict = {}


def _init_pynput():
    if _PYN_KEYS:
        return
    from pynput.keyboard import Key
    _PYN_KEYS.update({
        Key.ctrl_l: "lctrl", Key.ctrl_r: "rctrl",
        Key.shift_l: "lshift", Key.shift_r: "rshift",
        Key.alt_l: "lalt", Key.alt_r: "ralt", Key.alt_gr: "ralt",
        Key.space: "space", Key.enter: "enter", Key.tab: "tab",
        Key.esc: "escape", Key.backspace: "backspace", Key.delete: "delete",
        Key.insert: "insert", Key.home: "home", Key.end: "end",
        Key.page_up: "pageup", Key.page_down: "pagedown",
        Key.up: "up", Key.down: "down", Key.left: "left", Key.right: "right",
        Key.caps_lock: "capslock", Key.num_lock: "numlock",
        Key.scroll_lock: "scrolllock",
        Key.print_screen: "printscreen", Key.pause: "pause",
    })
    for i in range(1, 21):
        fk = getattr(Key, f"f{i}", None)
        if fk:
            _PYN_KEYS[fk] = f"f{i}"


def _pyn_key(key) -> str | None:
    from pynput.keyboard import KeyCode
    _init_pynput()
    if key in _PYN_KEYS:
        return _PYN_KEYS[key]
    if isinstance(key, KeyCode):
        if key.char and key.char.isprintable():
            return key.char.lower()
        if key.vk:
            return _VK_TO_NAME.get(key.vk)
    return None

class ComboHotkeyThread(QThread):
    """Global hotkey — order-independent combo detection with key suppression."""
    triggered = pyqtSignal()
    released = pyqtSignal(int)  # hold duration in ms

    def __init__(self, hotkey_str: str):
        super().__init__()
        parts = [p.strip().lower() for p in hotkey_str.split("+")]
        self._combo = frozenset(parts)
        self._pressed: set[str] = set()
        self._active = False
        self._active_time: float = 0.0
        self._kb_listener = None

    def _is_combo_key(self, name: str | None) -> bool:
        """True if this physical key belongs to the configured shortcut (incl. generic ctrl/shift/alt)."""
        if not name:
            return False
        if name in self._combo:
            return True
        if "ctrl" in self._combo and name in ("lctrl", "rctrl"):
            return True
        if "shift" in self._combo and name in ("lshift", "rshift"):
            return True
        if "alt" in self._combo and name in ("lalt", "ralt"):
            return True
        return False

    def _combo_fully_pressed(self) -> bool:
        """Order-independent: every combo slot satisfied (generic mods match either side key)."""
        for key in self._combo:
            if key in self._pressed:
                continue
            if key == "ctrl" and ("lctrl" in self._pressed or "rctrl" in self._pressed):
                continue
            if key == "shift" and ("lshift" in self._pressed or "rshift" in self._pressed):
                continue
            if key == "alt" and ("lalt" in self._pressed or "ralt" in self._pressed):
                continue
            return False
        return True

    def run(self):
        try:
            from pynput.keyboard import Listener as KBL
        except Exception:
            logger.error("[Hotkey] Failed to import pynput listeners", exc_info=True)
            return

        def kb_filter(msg, data):
            name = _VK_TO_NAME.get(data.vkCode)
            if not name:
                return
            combo_key = self._is_combo_key(name)
            if msg in (0x0100, 0x0104):
                was_new = name not in self._pressed
                self._pressed.add(name)
                if was_new and self._combo_fully_pressed() and not self._active:
                    self._active = True
                    self._active_time = time.monotonic()
                    self.triggered.emit()
                if combo_key:
                    self._kb_listener.suppress_event()
            elif msg in (0x0101, 0x0105):
                if self._active and combo_key:
                    hold_ms = int((time.monotonic() - self._active_time) * 1000)
                    self._active = False
                    self.released.emit(hold_ms)
                self._pressed.discard(name)
                if combo_key:
                    self._kb_listener.suppress_event()

        try:
            self._kb_listener = KBL(win32_event_filter=kb_filter)
            self._kb_listener.start()
            self._kb_listener.join()
        except Exception:
            logger.error("[Hotkey] Listener crashed", exc_info=True)

    def stop_hotkey(self):
        if self._kb_listener:
            self._kb_listener.stop()


_QT_KEY_NAMES = {
    Qt.Key.Key_Space: "space", Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter", Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Escape: "escape", Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "pageup", Qt.Key.Key_PageDown: "pagedown",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down",
    Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right",
    Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_NumLock: "numlock",
    Qt.Key.Key_ScrollLock: "scrolllock",
    Qt.Key.Key_Print: "printscreen", Qt.Key.Key_Pause: "pause",
}

def _qt_key(key_code: int) -> str | None:
    if key_code in _QT_KEY_NAMES:
        return _QT_KEY_NAMES[key_code]
    text = QKeySequence(key_code).toString().lower()
    return text if text else None


_LR_MOD_MAP = {
    Qt.Key.Key_Shift: [(0xA0, "lshift"), (0xA1, "rshift")],
    Qt.Key.Key_Control: [(0xA2, "lctrl"), (0xA3, "rctrl")],
    Qt.Key.Key_Alt: [(0xA4, "lalt"), (0xA5, "ralt")],
}


def _lr_mod_press(qt_key: int) -> str | None:
    pairs = _LR_MOD_MAP.get(qt_key)
    if not pairs:
        return None
    user32 = ctypes.windll.user32
    for vk, name in pairs:
        if user32.GetKeyState(vk) & 0x8000:
            return name
    return None


def _lr_mod_release(qt_key: int, pressed: set) -> None:
    pairs = _LR_MOD_MAP.get(qt_key)
    if not pairs:
        return
    user32 = ctypes.windll.user32
    for vk, name in pairs:
        if name in pressed and not (user32.GetKeyState(vk) & 0x8000):
            pressed.discard(name)


class _HotkeyDialog(QDialog):
    """Captures a key/mouse combo — order-independent, with conflict check."""

    _STYLE_DEFAULT = """
        background:#2a2a2a; color:#fff; border:1px solid #555;
        border-radius:8px; font-size:18px; font-weight:bold;
    """
    _STYLE_OK = """
        background:#1a3a1a; color:#34c759; border:1px solid #34c759;
        border-radius:8px; font-size:18px; font-weight:bold;
    """
    _STYLE_ERR = """
        background:#3a1a1a; color:#ff3b30; border:1px solid #ff3b30;
        border-radius:8px; font-size:18px; font-weight:bold;
    """

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置快捷键")
        self.setWindowIcon(icons.app_icon())
        self.setFixedSize(360, 180)
        self.setStyleSheet("background:#1e1e1e; color:#fff;")
        self._current = _canonical(current.split("+"))
        self._result: str | None = None
        self._captured: str | None = None
        self._available = False

        self._pressed: set[str] = set()
        self._best: set[str] = set()

        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(150)
        self._settle.timeout.connect(self._finalize)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint = QLabel("同时按下新的快捷键组合：")
        hint.setStyleSheet("font-size:13px;")
        layout.addWidget(hint)

        self._key_display = QLabel(_hotkey_display(self._current))
        self._key_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key_display.setFixedHeight(44)
        self._key_display.setStyleSheet(self._STYLE_DEFAULT)
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
        self._btn_ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_cancel.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:1px solid #444;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#2a2a2a; color:#fff; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        event.accept()
        key = event.key()
        if key in (Qt.Key.Key_unknown, Qt.Key.Key_Meta):
            return
        name = _lr_mod_press(key) or _qt_key(key)
        if not name:
            return
        if name == "escape" and not self._pressed:
            self.reject()
            return
        self._settle.stop()
        self._pressed.add(name)
        if len(self._pressed) >= len(self._best):
            self._best = set(self._pressed)
            self._show_best()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        event.accept()
        key = event.key()
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt):
            _lr_mod_release(key, self._pressed)
        else:
            name = _qt_key(key)
            if name:
                self._pressed.discard(name)
        if not self._pressed:
            self._settle.start()

    def _show_best(self):
        if self._best:
            combo = _canonical(self._best)
            self._key_display.setText(_hotkey_display(combo))
            self._key_display.setStyleSheet(self._STYLE_DEFAULT)
            self._status.setText("")
            self._btn_ok.setEnabled(False)
            self._available = False

    def _finalize(self):
        if not self._best:
            return
        combo = _canonical(self._best)
        self._captured = combo
        self._best = set()
        self._validate(combo)

    def _validate(self, combo: str):
        parts = combo.split("+")

        if len(parts) == 1:
            p = parts[0]
            is_fkey = p.startswith("f") and p[1:].isdigit()
            if not (p in _MOD_KEYS or is_fkey or p.startswith("mouse_")):
                self._key_display.setStyleSheet(self._STYLE_ERR)
                self._status.setText("单个普通键容易误触，请搭配其他键")
                self._status.setStyleSheet("font-size:12px; color:#ff6b60;")
                self._btn_ok.setEnabled(False)
                return

        if combo == self._current:
            self._status.setText("与当前快捷键相同")
            self._status.setStyleSheet("font-size:12px; color:#999;")
            self._btn_ok.setEnabled(False)
            return

        conflict = _test_system_conflict(combo)
        if conflict is False:
            self._key_display.setStyleSheet(self._STYLE_ERR)
            self._status.setText("✕ 已被系统或其他程序占用")
            self._status.setStyleSheet("font-size:12px; color:#ff3b30;")
            self._btn_ok.setEnabled(False)
            self._available = False
            return

        self._key_display.setStyleSheet(self._STYLE_OK)
        self._status.setText("✓ 快捷键可用")
        self._status.setStyleSheet("font-size:12px; color:#34c759;")
        self._btn_ok.setEnabled(True)
        self._available = True

    def _do_accept(self):
        if self._captured and self._available:
            self._result = self._captured
            self.accept()

    @property
    def hotkey(self) -> str | None:
        return self._result


_MOD_FLAGS = {
    "ctrl": 0x0002, "lctrl": 0x0002, "rctrl": 0x0002,
    "shift": 0x0004, "lshift": 0x0004, "rshift": 0x0004,
    "alt": 0x0001, "lalt": 0x0001, "ralt": 0x0001,
}


def _test_system_conflict(combo: str) -> bool | None:
    """True=available, False=system occupied, None=can't test (non-standard combo)."""
    parts = combo.split("+")
    mods = [p for p in parts if p in _MOD_KEYS]
    non_mods = [p for p in parts if p not in _MOD_KEYS]
    if len(non_mods) != 1 or non_mods[0].startswith("mouse_"):
        return None
    vk = _NAME_TO_VK.get(non_mods[0], 0)
    if not vk:
        return None
    mod = 0
    for m in mods:
        mod |= _MOD_FLAGS.get(m, 0)
    user32 = ctypes.windll.user32
    tid = 0x7FFF
    ok = user32.RegisterHotKey(None, tid, mod, vk)
    if ok:
        user32.UnregisterHotKey(None, tid)
    return bool(ok)


class _ApiKeyDialog(QDialog):
    """Dialog to configure DashScope API key."""

    def __init__(self, current_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置 API Key")
        self.setWindowIcon(icons.app_icon())
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
        btn_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._toggle_vis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        btn_ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_ok.setStyleSheet("""
            QPushButton { background:#007aff; color:#fff; border:none;
                          border-radius:6px; padding:6px; font-size:13px; }
            QPushButton:hover { background:#0066dd; }
        """)
        btn_ok.clicked.connect(self._do_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._pending_device_apply = False
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
        engine.api_key_invalid.connect(self._on_key_invalid)
        engine.mic_unavailable.connect(self._on_mic_unavailable)

        mini.request_record.connect(self._on_tray_click)
        mini.request_stop.connect(self._on_tray_click)
        mini.request_cancel.connect(self._on_cancel)
        mini.request_history.connect(self._open_history)
        mini.mode_changed.connect(self._on_mini_mode_changed)
        mini.show_result_changed.connect(self._on_mini_show_result_changed)

        self._hotkey_hold_active = False
        self._hotkey = ComboHotkeyThread(config.hotkey)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.released.connect(self._on_hotkey_release)
        self._hotkey.start()

        from core.device_watcher import AudioDeviceWatcher
        self._device_watcher = AudioDeviceWatcher()
        self._device_change_timer = QTimer()
        self._device_change_timer.setSingleShot(True)
        self._device_change_timer.setInterval(500)
        self._device_change_timer.timeout.connect(self._start_async_refresh)
        self._device_watcher.signals.changed.connect(self._device_change_timer.start)
        self._device_watcher.start()

        self.show()

    def _build_menu(self):
        menu = QMenu()
        menu.setStyleSheet(MENU_STYLE)

        self._act_record = QAction("开始录音", menu)
        self._act_record.triggered.connect(self._on_tray_click)
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

        self._polish_model_menu = QMenu("润色模型", menu)
        self._polish_model_menu.setStyleSheet(MENU_STYLE)
        self._polish_models = [
            ("qwen3.5-flash", "Qwen3.5 Flash"),
            ("qwen-flash", "Qwen Flash"),
            ("qwen-plus", "Qwen Plus"),
            ("qwen-max", "Qwen Max"),
        ]
        for model_id, display_name in self._polish_models:
            act = QAction(display_name, self._polish_model_menu)
            act.setCheckable(True)
            act.setChecked(self._config.polish_model == model_id)
            act.triggered.connect(lambda checked, m=model_id: self._set_polish_model(m))
            self._polish_model_menu.addAction(act)
        menu.addMenu(self._polish_model_menu)

        self._device_menu = QMenu("输入设备", menu)
        self._device_menu.setStyleSheet(MENU_STYLE)
        self._device_menu.aboutToShow.connect(self._on_device_menu_show)
        self._cached_default_name = ""
        self._cached_devices: list[dict] = []
        self._dev_refresh_running = False
        self._dev_refresh_scheduled = False
        self._dev_refresh_repeat = False
        self._dev_refresh_ready = False
        menu.addMenu(self._device_menu)

        self._act_show_result_text = QAction("显示识别原文", menu)
        self._act_show_result_text.setCheckable(True)
        self._act_show_result_text.setChecked(self._config.show_result_text)
        self._act_show_result_text.triggered.connect(self._toggle_show_result_text)
        menu.addAction(self._act_show_result_text)

        self._act_save_audio = QAction("保存录音文件", menu)
        self._act_save_audio.setCheckable(True)
        self._act_save_audio.setChecked(self._config.save_audio)
        self._act_save_audio.triggered.connect(self._toggle_save_audio)
        menu.addAction(self._act_save_audio)

        self._act_hide_idle_mini = QAction("空闲时隐藏顶部磁吸栏", menu)
        self._act_hide_idle_mini.setCheckable(True)
        self._act_hide_idle_mini.setChecked(self._config.hide_mini_window_when_idle)
        self._act_hide_idle_mini.triggered.connect(self._toggle_hide_idle_mini)
        menu.addAction(self._act_hide_idle_mini)

        menu.addSeparator()

        hotkey_display = _hotkey_display(self._config.hotkey)
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

        menu.aboutToShow.connect(self._start_async_refresh)
        self.setContextMenu(menu)

        self._start_async_refresh()

    # ── device refresh (async) ──

    def _release_all_pa(self):
        """Release ALL PyAudio instances so PortAudio re-scans on next init."""
        if (self._engine.state != "recording"
                and self._engine.recorder._pa is not None):
            self._engine.recorder._release_pa()
        self._audio.release()

    def _restore_all_pa(self):
        """Re-create PyAudio instances after enumeration (idempotent)."""
        if not self._audio._stream_ready:
            self._audio._init_stream()
        if self._engine.recorder._pa is None and self._engine.state != "recording":
            self._engine.recorder.prepare()

    def _start_async_refresh(self):
        """Schedule a device refresh after the menu is shown."""
        if self._dev_refresh_running or self._dev_refresh_scheduled:
            self._dev_refresh_repeat = True
            return
        self._dev_refresh_ready = False
        self._dev_refresh_scheduled = True
        QTimer.singleShot(0, self._run_refresh_devices)

    def _run_refresh_devices(self):
        """Main-thread refresh: release PyAudio, enumerate, then restore."""
        self._dev_refresh_scheduled = False
        if self._dev_refresh_running:
            self._dev_refresh_repeat = True
            return
        self._dev_refresh_running = True

        from core.recorder import VoiceRecorder
        from core.device_watcher import (
            get_default_capture_device_name,
            get_full_device_names,
        )
        try:
            self._release_all_pa()
            full_names = get_full_device_names()
            default_name = get_default_capture_device_name() or VoiceRecorder.get_default_device_name()
            raw_devices = VoiceRecorder.list_devices()
            raw_by_name = {dev["name"]: dev for dev in raw_devices}
            if full_names:
                devices = [
                    {
                        "name": trunc,
                        "display_name": full,
                        "index": raw_by_name.get(trunc, {}).get("index"),
                    }
                    for trunc, full in full_names.items()
                ]
            else:
                devices = raw_devices
            if default_name:
                trunc = default_name[:31]
                if trunc in full_names:
                    default_name = full_names[trunc]
        except Exception:
            default_name = "Unknown"
            devices = []
        self._cached_default_name = default_name
        self._cached_devices = devices
        self._dev_refresh_ready = True
        logger.info(f"[Tray] Device refresh done: "
                    f"default='{default_name}', {len(devices)} device(s)")
        self._on_refresh_done()
        self._dev_refresh_running = False
        if self._dev_refresh_repeat:
            self._dev_refresh_repeat = False
            self._start_async_refresh()

    def _on_refresh_done(self):
        """Main-thread callback after background refresh completes."""
        self._restore_all_pa()
        self._sync_system_default_device()
        self._auto_fallback_if_device_gone()
        self._rebuild_device_menu()
        self._sync_tray_icon_with_engine()

    def _on_device_menu_show(self):
        """When user opens '输入设备', wait for the ongoing refresh to finish."""
        if not self._dev_refresh_ready:
            deadline = time.monotonic() + 3.0
            while not self._dev_refresh_ready and time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.01)
        self._rebuild_device_menu()

    def _sync_system_default_device(self):
        """If following system default, rebind recorder when default device changed."""
        if self._config.mic_name:
            return
        current_default = self._cached_default_name or ""
        recorder_name = self._engine.recorder.device_name or ""
        if not current_default or recorder_name == current_default:
            return
        if self._engine.state != "recording":
            self._engine.recorder.set_device(None, "")
            logger.info(f"[Tray] System default device changed: '{recorder_name}' -> '{current_default}'")
        else:
            self._pending_device_apply = True
            logger.info(f"[Tray] System default changed during recording, will apply: '{current_default}'")

    def _auto_fallback_if_device_gone(self):
        """If the selected device is no longer present, switch to system default.

        Device indices are unstable across PyAudio re-init, so we match by
        name.  If the same-named device still exists but its index changed,
        we silently update the stored index.
        """
        if not self._config.mic_name:
            return
        saved_name = self._config.mic_name
        match = next((d for d in self._cached_devices if d["name"] == saved_name), None)
        if match is not None:
            if match["index"] is None and self._config.mic_index is not None:
                self._config.mic_index = None
                self._config.save()
                if self._engine.state != "recording":
                    self._engine.recorder.set_device(None, saved_name)
                else:
                    self._pending_device_apply = True
                logger.info(f"[Tray] Device '{saved_name}' temporarily unresolved, cleared stale index")
                return
            if match["index"] is not None and match["index"] != self._config.mic_index:
                old_idx = self._config.mic_index
                self._config.mic_index = match["index"]
                self._config.save()
                if self._engine.state != "recording":
                    self._engine.recorder.set_device(match["index"], saved_name)
                else:
                    self._pending_device_apply = True
                logger.info(f"[Tray] Device '{saved_name}' index changed "
                            f"{old_idx} → {match['index']}")
            return
        old = self._config.mic_index
        self._config.mic_index = None
        self._config.mic_name = ""
        self._config.save()
        if self._engine.state != "recording":
            self._engine.recorder.set_device(None, "")
        else:
            self._pending_device_apply = True
        logger.info(f"[Tray] Selected device '{saved_name}' (index={old}) gone, "
                    f"switched to system default")

    def _rebuild_device_menu(self):
        self._device_menu.clear()

        label = f"系统默认 ({self._cached_default_name})" if self._cached_default_name else "系统默认"
        act_default = QAction(label, self._device_menu)
        act_default.setCheckable(True)
        act_default.setChecked(not self._config.mic_name)
        act_default.triggered.connect(lambda checked: self._set_default_device())
        self._device_menu.addAction(act_default)
        self._device_menu.addSeparator()

        if not self._cached_devices:
            act = QAction("(未发现兼容设备)", self._device_menu)
            act.setEnabled(False)
            self._device_menu.addAction(act)
            return

        for dev in self._cached_devices:
            display = dev.get("display_name", dev["name"])
            act = QAction(display, self._device_menu)
            act.setCheckable(True)
            act.setChecked(self._config.mic_name == dev["name"])
            act.triggered.connect(
                lambda checked, idx=dev.get("index"), name=dev["name"]: self._set_device(name, idx))
            self._device_menu.addAction(act)

    def _set_mode(self, mode_id: str):
        self._config.mode = mode_id
        self._config.save()
        self._sync_mode_menu()
        self._mini.sync_mode()
        logger.info(f"[Tray] Mode → {mode_id}")

    def _on_mini_mode_changed(self, mode_id: str):
        self._sync_mode_menu()

    def _on_mini_show_result_changed(self, on: bool):
        self._act_show_result_text.blockSignals(True)
        self._act_show_result_text.setChecked(on)
        self._act_show_result_text.blockSignals(False)

    def _sync_mode_menu(self):
        for act in self._mode_menu.actions():
            mode_name_map = {"纯转录": "transcribe", "智能润色": "polish"}
            act.setChecked(mode_name_map.get(act.text(), "") == self._config.mode)

    def _set_polish_model(self, model_id: str):
        self._config.polish_model = model_id
        self._config.save()
        self._engine.polisher.set_model(model_id)
        for act in self._polish_model_menu.actions():
            mid = next((m for m, d in self._polish_models if d == act.text()), "")
            act.setChecked(mid == model_id)
        logger.info(f"[Tray] Polish model → {model_id}")

    def _configure_hotkey(self):
        self._hotkey.stop_hotkey()
        self._hotkey.wait(2000)

        dlg = _HotkeyDialog(self._config.hotkey)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.hotkey:
            self._hotkey = ComboHotkeyThread(self._config.hotkey)
            self._hotkey.triggered.connect(self._on_hotkey)
            self._hotkey.released.connect(self._on_hotkey_release)
            self._hotkey.start()
            return

        new_key = dlg.hotkey
        self._config.hotkey = new_key
        self._config.save()
        self._hotkey = ComboHotkeyThread(new_key)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.released.connect(self._on_hotkey_release)
        self._hotkey.start()
        display = _hotkey_display(new_key)
        self._act_hotkey.setText(f"快捷键: {display}")
        logger.info(f"[Tray] Hotkey → {display}")

    def _configure_apikey(self):
        dlg = _ApiKeyDialog(self._config.api_key)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.api_key is None:
            return
        self._config.api_key = dlg.api_key
        self._config.save()
        self._engine.asr.api_key = dlg.api_key
        self._engine.polisher.update_api_key(dlg.api_key)
        logger.info("[Tray] API Key updated")
        if dlg.api_key:
            self._set_key_warning(False)

    def _toggle_save_audio(self, checked: bool):
        self._config.save_audio = checked
        self._config.save()
        logger.info(f"[Tray] Save audio → {'on' if checked else 'off'}")

    def _toggle_hide_idle_mini(self, checked: bool):
        self._config.hide_mini_window_when_idle = checked
        self._config.save()
        self._mini.refresh_visibility()
        logger.info(f"[Tray] Hide idle mini window → {'on' if checked else 'off'}")

    def _toggle_show_result_text(self, checked: bool):
        self._config.show_result_text = checked
        self._config.save()
        self._mini.sync_show_result()
        logger.info(f"[Tray] Show result text → {'on' if checked else 'off'}")

    def _set_default_device(self):
        self._config.mic_index = None
        self._config.mic_name = ""
        self._config.save()
        if self._engine.state == "recording":
            self._pending_device_apply = True
            logger.info("[Tray] Input device saved for next session → system default")
            self.showMessage("VoiceInput", "输入设备已切换，下次录音生效",
                             QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            self._pending_device_apply = False
            self._engine.recorder.set_device(None, "")
            logger.info("[Tray] Input device → system default (index=None)")
        self._rebuild_device_menu()
        self._mic_warning = False
        self._sync_tray_icon_with_engine()

    def _set_device(self, name: str, idx: int | None = None):
        resolved = idx if idx is not None else None
        self._config.mic_name = name
        self._config.mic_index = resolved
        self._config.save()
        if self._engine.state == "recording":
            self._pending_device_apply = True
            logger.info(f"[Tray] Input device saved for next session "
                        f"→ {name} (index={resolved})")
            self.showMessage("VoiceInput", "输入设备已切换，下次录音生效",
                             QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            self._pending_device_apply = False
            self._engine.recorder.set_device(resolved, name)
            logger.info(f"[Tray] Input device → {name} (index={resolved})")
        self._rebuild_device_menu()
        self._mic_warning = False
        self._sync_tray_icon_with_engine()

    def _maybe_apply_deferred_input_device(self):
        if not self._pending_device_apply or self._engine.state == "recording":
            return
        self._pending_device_apply = False
        idx = self._config.mic_index
        name = self._config.mic_name
        self._engine.recorder.set_device(idx, name)
        logger.info(f"[Tray] Deferred input device applied (name='{name or 'system default'}', index={idx})")

    # ── tray interaction ──

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_tray_click()

    def _on_tray_click(self):
        """Tray icon click: simple toggle, no hold-to-cancel."""
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

    def _on_cancel(self):
        self._hotkey_hold_active = False
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
            self._engine.toggle_record()
        elif self._engine.state == "recording":
            self._hotkey_hold_active = True
            self._mini.start_hotkey_hold()

    def _on_hotkey_release(self, hold_ms: int):
        if not self._hotkey_hold_active:
            return
        self._hotkey_hold_active = False
        if self._engine.state != "recording":
            self._mini.stop_hotkey_hold()
            return
        self._mini.stop_hotkey_hold()
        if hold_ms < self._mini.hotkey_click_threshold_ms():
            self._audio.play_stop()
            self._engine.toggle_record()

    # ── engine state ──

    def _update_tooltip(self, status: str):
        self.setToolTip(f"VoiceInput — {status}")

    def _sync_tray_icon_with_engine(self):
        """Align icon and primary tooltip with engine state (e.g. after menu device refresh)."""
        st = self._engine.state
        if st == "recording":
            self.setIcon(icons.icon_recording())
            self._update_tooltip("录音中")
        elif st == "processing":
            self.setIcon(icons.icon_processing())
            self._update_tooltip("识别中")
        else:
            self._restore_idle_icon()

    def _on_state(self, state: str):
        if state != "recording":
            self._maybe_apply_deferred_input_device()
        if state == "recording":
            self._mic_warning = False
        self._sync_tray_icon_with_engine()
        if state == "recording":
            self._act_record.setText("停止录音")
        elif state == "processing":
            self._act_record.setText("处理中...")
            self._act_record.setEnabled(False)
        elif state == "ready":
            self._act_record.setText("开始录音")
            self._act_record.setEnabled(True)

    def _on_done(self, text: str):
        self._audio.play_done()
        self.setIcon(icons.icon_done())
        self._update_tooltip("就绪")
        QTimer.singleShot(2000, self._restore_idle_icon)

    def _on_key_invalid(self):
        self._key_warning = True

    def _on_mic_unavailable(self):
        if self._engine.recorder.no_device:
            msg = "未找到输入设备"
        else:
            self._mic_warning = True
            msg = "无法打开麦克风，请检查设备连接或在右键菜单中切换输入设备"
        logger.warning(f"[Tray] Mic unavailable: {msg}")
        self.showMessage("VoiceInput", msg,
                         QSystemTrayIcon.MessageIcon.Warning, 5000)

    def _set_key_warning(self, warning: bool):
        self._key_warning = warning
        self._restore_idle_icon()

    def _restore_idle_icon(self):
        if self._engine.state in ("recording", "processing"):
            self._sync_tray_icon_with_engine()
            return
        if self._key_warning:
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip("API Key 无效，右键点击配置")
        elif self._engine.recorder.no_device:
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip("未找到输入设备")
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
        logger.info("[Tray] Quit requested")
        if self._engine.state == "recording":
            self._engine.cancel()
        self._device_watcher.stop()
        self._engine.recorder.release()
        self._hotkey.stop_hotkey()
        self._hotkey.wait(2000)
        QApplication.quit()
