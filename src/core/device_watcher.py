"""Monitor Windows audio device changes via IMMNotificationClient (COM).

Emits a Qt signal whenever devices are added, removed, or the default changes.
"""

from __future__ import annotations

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, COMObject
from ctypes import POINTER, Structure, c_int
from ctypes.wintypes import DWORD, LPCWSTR

from PyQt6.QtCore import QObject, pyqtSignal

from core.log import logger

_TAG = "[DeviceWatcher]"

# ── Struct / COM interface definitions ─────────────────────────────────


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class IMMNotificationClient(comtypes.IUnknown):
    _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
    _methods_ = [
        COMMETHOD([], HRESULT, "OnDeviceStateChanged",
                  (["in"], LPCWSTR, "pwstrDeviceId"),
                  (["in"], DWORD, "dwNewState")),
        COMMETHOD([], HRESULT, "OnDeviceAdded",
                  (["in"], LPCWSTR, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDeviceRemoved",
                  (["in"], LPCWSTR, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDefaultDeviceChanged",
                  (["in"], c_int, "flow"),
                  (["in"], c_int, "role"),
                  (["in"], LPCWSTR, "pwstrDefaultDeviceId")),
        COMMETHOD([], HRESULT, "OnPropertyValueChanged",
                  (["in"], LPCWSTR, "pwstrDeviceId"),
                  (["in"], PROPERTYKEY, "key")),
    ]


_CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class IMMDeviceEnumerator(comtypes.IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], DWORD, "dataFlow"),
                  (["in"], DWORD, "dwStateMask"),
                  (["out"], POINTER(POINTER(comtypes.IUnknown)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], DWORD, "dataFlow"),
                  (["in"], DWORD, "role"),
                  (["out"], POINTER(POINTER(comtypes.IUnknown)), "ppEndpoint")),
        COMMETHOD([], HRESULT, "GetDevice",
                  (["in"], LPCWSTR, "pwstrId"),
                  (["out"], POINTER(POINTER(comtypes.IUnknown)), "ppDevice")),
        COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                  (["in"], POINTER(IMMNotificationClient), "pClient")),
        COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                  (["in"], POINTER(IMMNotificationClient), "pClient")),
    ]


# ── Qt signal carrier ─────────────────────────────────────────────────

class DeviceChangeSignal(QObject):
    """Thin QObject wrapper that carries a signal for device changes."""
    changed = pyqtSignal()


# ── Callback implementation ───────────────────────────────────────────

class _NotificationClient(COMObject):
    """Receives device-change callbacks from Windows and fires a Qt signal."""

    _com_interfaces_ = [IMMNotificationClient]

    def __init__(self, emitter: DeviceChangeSignal):
        super().__init__()
        self._emitter = emitter

    def OnDeviceStateChanged(self, pwstrDeviceId, dwNewState):
        logger.debug(f"{_TAG} OnDeviceStateChanged id={pwstrDeviceId} state={dwNewState}")
        self._emitter.changed.emit()
        return 0

    def OnDeviceAdded(self, pwstrDeviceId):
        logger.debug(f"{_TAG} OnDeviceAdded id={pwstrDeviceId}")
        self._emitter.changed.emit()
        return 0

    def OnDeviceRemoved(self, pwstrDeviceId):
        logger.debug(f"{_TAG} OnDeviceRemoved id={pwstrDeviceId}")
        self._emitter.changed.emit()
        return 0

    def OnDefaultDeviceChanged(self, flow, role, pwstrDefaultDeviceId):
        logger.debug(f"{_TAG} OnDefaultDeviceChanged flow={flow} role={role}")
        self._emitter.changed.emit()
        return 0

    def OnPropertyValueChanged(self, pwstrDeviceId, key):
        return 0


# ── Public API ─────────────────────────────────────────────────────────

class AudioDeviceWatcher:
    """Register / unregister Windows audio device change notifications.

    Usage::

        watcher = AudioDeviceWatcher()
        watcher.signals.changed.connect(my_refresh_slot)
        watcher.start()   # begins listening
        ...
        watcher.stop()     # cleanup
    """

    def __init__(self):
        self.signals = DeviceChangeSignal()
        self._enumerator = None
        self._client = None

    def start(self):
        if self._enumerator is not None:
            return
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except OSError:
            pass  # already initialized in this thread
        try:
            self._enumerator = comtypes.CoCreateInstance(
                _CLSID_MMDeviceEnumerator,
                IMMDeviceEnumerator,
            )
            self._client = _NotificationClient(self.signals)
            self._enumerator.RegisterEndpointNotificationCallback(self._client)
            logger.info(f"{_TAG} Listening for audio device changes")
        except Exception:
            logger.opt(exception=True).error(
                f"{_TAG} Failed to register device notifications")
            self._enumerator = None
            self._client = None

    def stop(self):
        if self._enumerator is None:
            return
        try:
            self._enumerator.UnregisterEndpointNotificationCallback(self._client)
        except Exception:
            pass
        self._enumerator = None
        self._client = None
        logger.info(f"{_TAG} Stopped listening for audio device changes")
