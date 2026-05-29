"""Global hotkey subsystem — order-independent combo detection, key grabbing, and hotkey config dialog."""
import ctypes
import sys
import threading
import time

from PyQt6.QtCore import (
    Qt, QEvent, QObject, QThread, pyqtSignal, QTimer,
)
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
)

from core.log import logger
from ui import icons


_MOD_KEYS = frozenset({
    "ctrl", "shift", "alt", "capslock",
    "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt",
})

_LOCK_STATE_KEYS = frozenset({"capslock", "numlock", "scrolllock"})

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


# KBDLLHOOKSTRUCT.flags 位：事件由 SendInput/keybd_event 合成（非物理按键）。
# pynput Controller 使用 SendInput，TextInjector 的 Shift+Insert 粘贴会置此位；
# 过滤器命中此位时直接放行，避免钩子拦截自家注入事件导致修饰键残留。
_LLKHF_INJECTED = 0x10


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
        self._hook_suppressed: set[str] = set()
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
            if data.flags & _LLKHF_INJECTED:
                return
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
                    logger.debug(
                        f"[Hotkey] Triggered combo={sorted(self._combo)} "
                        f"pressed={sorted(self._pressed)} key={name}"
                    )
                    self.triggered.emit()
                elif combo_key and self._combo_fully_pressed() and self._active:
                    logger.debug(
                        f"[Hotkey] Ignored repeat while active "
                        f"pressed={sorted(self._pressed)} key={name} was_new={was_new}"
                    )
                suppress_before_complete = combo_key and name in _LOCK_STATE_KEYS
                if combo_key and (self._combo_fully_pressed() or suppress_before_complete):
                    if was_new:
                        self._hook_suppressed.add(name)
                    self._kb_listener.suppress_event()
            elif msg in (0x0101, 0x0105):
                if self._active and combo_key:
                    hold_ms = int((time.monotonic() - self._active_time) * 1000)
                    self._active = False
                    logger.debug(
                        f"[Hotkey] Released combo key={name} hold_ms={hold_ms} "
                        f"pressed_before={sorted(self._pressed)}"
                    )
                    self.released.emit(hold_ms)
                self._pressed.discard(name)
                # 只抑制「曾被抑制过 KEYDOWN」的键的 KEYUP；否则系统仍认为 Shift/Ctrl 未弹起（粘键），
                # 会导致滚轮变横滚、点击异常等。
                if name in self._hook_suppressed:
                    self._hook_suppressed.discard(name)
                    self._kb_listener.suppress_event()
                if not any(self._is_combo_key(n) for n in self._pressed):
                    self._hook_suppressed.clear()

        try:
            self._kb_listener = KBL(win32_event_filter=kb_filter)
            self._kb_listener.start()
            self._kb_listener.join()
        except Exception:
            logger.error("[Hotkey] Listener crashed", exc_info=True)

    def stop_hotkey(self):
        if self._kb_listener:
            self._kb_listener.stop()


class _HotkeyGrabSignals(QObject):
    key_down = pyqtSignal(str)
    key_up = pyqtSignal(str)


class _PynputHotkeyGrabWorker(threading.Thread):
    """快捷键设置窗：pynput ``WH_KEYBOARD_LL`` + ``win32_event_filter`` 全局拦键。

    ``suppress_event()`` 会抛 ``SuppressException`` 以拦键；须先 ``emit`` 再 ``suppress``。
    若 ``emit`` 时 ``QObject`` 已析构（关窗与卸钩竞态），捕获 ``RuntimeError`` 并
    ``return False``，本键交还系统，**不得**再 ``suppress``，以免整机键盘卡死。
    """
    def __init__(self, sigs: _HotkeyGrabSignals):
        super().__init__(name="HotkeyGrabPynput", daemon=True)
        self._sigs = sigs
        self._stop_requested = False
        self._kb_listener = None

    def stop_grab(self):
        self._stop_requested = True
        listener = self._kb_listener
        if listener is not None:
            listener.stop()

    def run(self):
        if sys.platform != "win32":
            return
        try:
            from pynput.keyboard import Listener as KBL
        except Exception:
            logger.error("[HotkeyGrab] Failed to import pynput", exc_info=True)
            return
        if self._stop_requested:
            return

        self_ref = self

        def kb_filter(msg, data):
            if msg not in (0x0100, 0x0101, 0x0104, 0x0105):
                return True
            if data.flags & _LLKHF_INJECTED:
                return True
            name = _VK_TO_NAME.get(data.vkCode)
            if not name:
                logger.debug("[HotkeyGrab] pass-through unmapped vk=0x%02X", data.vkCode)
                return True
            try:
                if msg in (0x0100, 0x0104):
                    self_ref._sigs.key_down.emit(name)
                elif msg in (0x0101, 0x0105):
                    self_ref._sigs.key_up.emit(name)
            except RuntimeError:
                # 对话框已关、_grab_sig 已删时 emit 失败；勿 suppress，否则按键整桌失效
                logger.debug("[HotkeyGrab] emit failed (dialog closed?), vk=0x%02X — passing through", data.vkCode)
                return False
            self_ref._kb_listener.suppress_event()

        try:
            self._kb_listener = KBL(win32_event_filter=kb_filter)
        except Exception:
            logger.error("[HotkeyGrab] Failed to create pynput Listener", exc_info=True)
            return
        if self._stop_requested:
            self._kb_listener = None
            return
        try:
            logger.info("[HotkeyGrab] pynput listener started")
            self._kb_listener.start()
            self._kb_listener.join()
        except Exception:
            logger.error("[HotkeyGrab] Listener crashed", exc_info=True)
        finally:
            self._kb_listener = None
            logger.info("[HotkeyGrab] pynput listener stopped")


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

        hint = QLabel("按下新的快捷键或快捷键组合：")
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
                          border-radius:6px; padding:6px 14px; font-size:13px; }
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
                          border-radius:6px; padding:6px 14px; font-size:13px; }
            QPushButton:hover { background:#2a2a2a; color:#fff; }
        """)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._grab_sig = _HotkeyGrabSignals(self)
        self._grab_sig.key_down.connect(
            self._on_grab_key_down, Qt.ConnectionType.QueuedConnection)
        self._grab_sig.key_up.connect(
            self._on_grab_key_up, Qt.ConnectionType.QueuedConnection)
        self._grab_worker: _PynputHotkeyGrabWorker | None = None
        self._hotkey_grab_disposed = False
        self._fg_poll = QTimer(self)
        self._fg_poll.setInterval(150)
        self._fg_poll.timeout.connect(self.sync_hotkey_grab_with_activation)
        self._sync_grab_debounce = QTimer(self)
        self._sync_grab_debounce.setSingleShot(True)
        self._sync_grab_debounce.setInterval(0)
        self._sync_grab_debounce.timeout.connect(self.sync_hotkey_grab_with_activation)

    def _hotkey_dialog_is_foreground(self) -> bool:
        """本对话框是否为 Win32 前台窗口（比 isActiveWindow 可靠）。"""
        if sys.platform != "win32":
            return self.isActiveWindow()
        try:
            hwnd = int(self.winId())
        except Exception:
            return False
        if not hwnd:
            return False
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        if fg == hwnd:
            return True
        GA_ROOT = 2
        return bool(user32.GetAncestor(fg, GA_ROOT) == hwnd)

    def showEvent(self, event):
        super().showEvent(event)
        self._fg_poll.start()
        # 挂在 self 上的单发定时器，避免关窗后仍投递 singleShot 再次起钩子
        self._sync_grab_debounce.start()

    def changeEvent(self, event):
        if event.type() in (
                QEvent.Type.WindowActivate,
                QEvent.Type.WindowDeactivate,
                QEvent.Type.ActivationChange):
            self._sync_grab_debounce.start()
        super().changeEvent(event)

    def closeEvent(self, event):
        # 须先于停定时器，防止 sync 与 _start 在关窗过程中再起线程
        self._hotkey_grab_disposed = True
        self._sync_grab_debounce.stop()
        self._fg_poll.stop()
        self._stop_hotkey_grab()
        super().closeEvent(event)

    def sync_hotkey_grab_with_activation(self):
        """仅当本对话框为系统前台时挂全局钩子；否则撤钩并复位 chord。"""
        if self._hotkey_grab_disposed:
            return
        if self._hotkey_dialog_is_foreground():
            self._start_hotkey_grab_if_needed()
        else:
            self._release_hotkey_grab_on_deactivate()

    def _release_hotkey_grab_on_deactivate(self):
        """失焦/切到其他应用：撤钩子并清空 chord，避免收不到 keyup 导致状态错乱。"""
        self._stop_hotkey_grab()
        self._settle.stop()
        self._pressed.clear()
        self._best.clear()
        combo = self._captured if self._captured else self._current
        self._key_display.setText(_hotkey_display(combo))
        self._validate(combo)

    def _start_hotkey_grab_if_needed(self):
        if self._hotkey_grab_disposed:
            return
        if self._grab_worker is not None or sys.platform != "win32":
            return
        if not self._hotkey_dialog_is_foreground():
            return
        try:
            from pynput.keyboard import Listener as _KBL  # noqa: F401
        except Exception:
            logger.warning("[HotkeyDialog] No global key grab (pynput missing)")
            return
        logger.info("[HotkeyDialog] Starting pynput keyboard grab")
        self._grab_worker = _PynputHotkeyGrabWorker(self._grab_sig)
        self._grab_worker.start()

    def _stop_hotkey_grab(self):
        if self._grab_worker is None:
            return
        logger.info("[HotkeyDialog] Stopping pynput keyboard grab")
        self._grab_worker.stop_grab()
        self._grab_worker.join(timeout=5.0)
        if self._grab_worker.is_alive():
            logger.error(
                "[HotkeyDialog] Grab thread did not exit in 5s — "
                "keyboard may still be stuck")
        self._grab_worker = None

    def _on_grab_key_down(self, name: str):
        if name == "escape" and not self._pressed:
            self.reject()
            return
        if name in self._pressed:
            return
        self._settle.stop()
        self._pressed.add(name)
        if len(self._pressed) >= len(self._best):
            self._best = set(self._pressed)
            self._show_best()

    def _on_grab_key_up(self, name: str):
        self._pressed.discard(name)
        if not self._pressed:
            self._settle.start()

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, event):
        if self._grab_worker is not None:
            event.accept()
            return
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
        if self._grab_worker is not None:
            event.accept()
            return
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
                self._status.setText("不允许使用单个常用按键，请搭配其他键使用。")
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
