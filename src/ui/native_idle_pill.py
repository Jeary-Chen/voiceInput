"""Native Win32 layered idle pill for avoiding Qt translucent window borders."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath

from core.log import logger
from ui.theme import Theme

_HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
_HICON = getattr(wintypes, "HICON", wintypes.HANDLE)
_HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
_HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)


if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    _kernel32 = ctypes.windll.kernel32
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = wintypes.LPARAM
    _user32.LoadCursorW.restype = _HCURSOR
    _user32.SetCursor.argtypes = [_HCURSOR]
    _user32.SetCursor.restype = _HCURSOR
else:  # pragma: no cover - module is a no-op outside Windows.
    _user32 = None
    _gdi32 = None
    _kernel32 = None


WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
DIB_RGB_COLORS = 0
BI_RGB = 0
WM_MOUSEMOVE = 0x0200
WM_SETCURSOR = 0x0020
IDC_ARROW = 32512


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


_WNDPROC = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", _HINSTANCE),
        ("hIcon", _HICON),
        ("hCursor", _HCURSOR),
        ("hbrBackground", _HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


_WINDOWS: dict[int, "NativeIdlePillWindow"] = {}
_CLASS_NAME = "VoiceInputNativeIdlePill"
_WNDPROC_REF = None
_CLASS_REGISTERED = False
_ARROW_CURSOR = None


def _wnd_proc(hwnd, msg, wparam, lparam):
    window = _WINDOWS.get(int(hwnd))
    if window is not None and msg == WM_SETCURSOR:
        window._apply_arrow_cursor()
        return 1
    if window is not None and msg == WM_MOUSEMOVE:
        window._queue_enter()
        return 0
    return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _register_class() -> None:
    global _WNDPROC_REF, _CLASS_REGISTERED, _ARROW_CURSOR
    if _CLASS_REGISTERED:
        return
    _WNDPROC_REF = _WNDPROC(_wnd_proc)
    hinstance = _kernel32.GetModuleHandleW(None)
    _ARROW_CURSOR = _user32.LoadCursorW(None, IDC_ARROW)
    wndclass = _WNDCLASS()
    wndclass.lpfnWndProc = _WNDPROC_REF
    wndclass.hInstance = hinstance
    wndclass.hCursor = _ARROW_CURSOR
    wndclass.lpszClassName = _CLASS_NAME
    atom = _user32.RegisterClassW(ctypes.byref(wndclass))
    if not atom:
        err = ctypes.get_last_error()
        # ERROR_CLASS_ALREADY_EXISTS
        if err != 1410:
            raise ctypes.WinError(err)
    _CLASS_REGISTERED = True


class NativeIdlePillWindow:
    """A tiny native layered HWND used only for the idle top-center pill."""

    def __init__(self, width: int, height: int, on_enter: Callable[[], None]):
        self.width = width
        self.height = height
        self._on_enter = on_enter
        self._hwnd = 0
        self._visible = False
        self._enter_queued = False
        if sys.platform == "win32":
            self._create()

    @property
    def available(self) -> bool:
        return bool(self._hwnd)

    def _create(self) -> None:
        try:
            _register_class()
            hinstance = _kernel32.GetModuleHandleW(None)
            ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            hwnd = _user32.CreateWindowExW(
                ex_style,
                _CLASS_NAME,
                "",
                WS_POPUP,
                0,
                0,
                self.width,
                self.height,
                None,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = int(hwnd)
            _WINDOWS[self._hwnd] = self
        except Exception as e:
            logger.debug(f"[NativeIdlePill] Failed to create native idle pill: {e}")
            self._hwnd = 0

    def _queue_enter(self) -> None:
        logger.debug(
            f"[DEBUG] NativeIdlePillWindow._queue_enter | "
            f"visible={self._visible}, enter_queued={self._enter_queued}, hwnd={self._hwnd}"
        )
        if self._enter_queued:
            return
        self._enter_queued = True
        QTimer.singleShot(0, self._emit_enter)

    def _emit_enter(self) -> None:
        self._enter_queued = False
        logger.debug(
            f"[DEBUG] NativeIdlePillWindow._emit_enter | "
            f"visible={self._visible}, hwnd={self._hwnd}"
        )
        if self._visible:
            self._on_enter()

    def _apply_arrow_cursor(self) -> None:
        if _ARROW_CURSOR:
            _user32.SetCursor(_ARROW_CURSOR)

    def _render_image(self, width: int | None = None, height: int | None = None) -> QImage:
        width = self.width if width is None else width
        height = self.height if height is None else height
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        radius = height / 2
        path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        bg = QColor(Theme.BG_PRIMARY)
        bg.setAlpha(255)
        painter.fillPath(path, bg)
        painter.end()
        return image

    def show_at(self, x: int, y: int, *, scale: float = 1.0) -> None:
        if not self._hwnd:
            logger.debug(
                "[DEBUG] NativeIdlePillWindow.show_at | skipped: hwnd unavailable"
            )
            return
        scale = max(float(scale), 1.0)
        pixel_x = round(x * scale)
        pixel_y = round(y * scale)
        pixel_width = round(self.width * scale)
        pixel_height = round(self.height * scale)
        logger.debug(
            f"[DEBUG] NativeIdlePillWindow.show_at | before | "
            f"visible={self._visible}, hwnd={self._hwnd}, logical=({x}, {y}, "
            f"{self.width}, {self.height}), scale={scale}, pixels=({pixel_x}, "
            f"{pixel_y}, {pixel_width}, {pixel_height})"
        )
        image = self._render_image(pixel_width, pixel_height)
        bits = image.bits()
        bits.setsize(image.sizeInBytes())
        data = bytes(bits)

        screen_dc = _user32.GetDC(None)
        mem_dc = _gdi32.CreateCompatibleDC(screen_dc)
        old_bitmap = None
        bitmap = None
        try:
            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = pixel_width
            bmi.bmiHeader.biHeight = -pixel_height
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = BI_RGB
            pixel_bits = ctypes.c_void_p()
            bitmap = _gdi32.CreateDIBSection(
                mem_dc,
                ctypes.byref(bmi),
                DIB_RGB_COLORS,
                ctypes.byref(pixel_bits),
                None,
                0,
            )
            if not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(pixel_bits, data, len(data))
            old_bitmap = _gdi32.SelectObject(mem_dc, bitmap)

            pt_dst = _POINT(pixel_x, pixel_y)
            size = _SIZE(pixel_width, pixel_height)
            pt_src = _POINT(0, 0)
            blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            ok = _user32.UpdateLayeredWindow(
                self._hwnd,
                screen_dc,
                ctypes.byref(pt_dst),
                ctypes.byref(size),
                mem_dc,
                ctypes.byref(pt_src),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
            logger.debug(
                f"[DEBUG] NativeIdlePillWindow.show_at | UpdateLayeredWindow ok | "
                f"hwnd={self._hwnd}, pixels=({pixel_x}, {pixel_y}, "
                f"{pixel_width}, {pixel_height})"
            )
            _user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
            _user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            self._visible = True
            logger.debug(
                f"[DEBUG] NativeIdlePillWindow.show_at | after | "
                f"visible={self._visible}, hwnd={self._hwnd}"
            )
        except Exception as e:
            logger.debug(f"[NativeIdlePill] Failed to show native idle pill: {e}")
        finally:
            if old_bitmap:
                _gdi32.SelectObject(mem_dc, old_bitmap)
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            if mem_dc:
                _gdi32.DeleteDC(mem_dc)
            if screen_dc:
                _user32.ReleaseDC(None, screen_dc)

    def hide(self) -> None:
        logger.debug(
            f"[DEBUG] NativeIdlePillWindow.hide | before | "
            f"visible={self._visible}, hwnd={self._hwnd}"
        )
        if self._hwnd:
            _user32.ShowWindow(self._hwnd, SW_HIDE)
        self._visible = False
        logger.debug(
            f"[DEBUG] NativeIdlePillWindow.hide | after | "
            f"visible={self._visible}, hwnd={self._hwnd}"
        )

    def destroy(self) -> None:
        self.hide()
        if self._hwnd:
            _WINDOWS.pop(self._hwnd, None)
            _user32.DestroyWindow(self._hwnd)
            self._hwnd = 0
