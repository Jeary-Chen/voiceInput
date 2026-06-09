from __future__ import annotations

from dataclasses import dataclass

from core.device_watcher import get_default_capture_device_name, get_full_device_names
from core.recorder import VoiceRecorder


@dataclass(frozen=True)
class InputDevice:
    name: str
    display_name: str
    index: int | None

    @property
    def is_recordable(self) -> bool:
        return self.index is not None


@dataclass(frozen=True)
class InputDeviceSnapshot:
    default_name: str
    recordable_default_name: str
    devices: tuple[InputDevice, ...]
    recordable_devices: tuple[InputDevice, ...] = ()

    @property
    def has_recordable_device(self) -> bool:
        return bool(self._recordable_candidates)

    def find_by_name(self, name: str) -> InputDevice | None:
        visible = next((device for device in self.devices if device.name == name), None)
        if visible is not None:
            return visible
        return next((device for device in self._recordable_candidates if device.name == name), None)

    @property
    def _recordable_candidates(self) -> tuple[InputDevice, ...]:
        if self.recordable_devices:
            return self.recordable_devices
        return tuple(device for device in self.devices if device.is_recordable)

    @classmethod
    def empty(cls) -> "InputDeviceSnapshot":
        return cls(default_name="", recordable_default_name="", devices=())


def get_input_device_snapshot() -> InputDeviceSnapshot:
    """Return the single source of truth for recordable input devices.

    PyAudio decides which devices are actually usable for recording. Windows
    Core Audio is used only to decorate PyAudio devices with full friendly names
    and to identify the current default when it maps back to a PyAudio device.
    """
    system_default_name = get_default_capture_device_name() or ""
    raw_devices = VoiceRecorder.list_devices()
    full_names = get_full_device_names()
    raw_by_name = {dev["name"]: dev for dev in raw_devices}
    devices = _merge_visible_and_recordable_devices(raw_by_name, full_names)
    recordable_devices = tuple(
        InputDevice(
            name=dev["name"],
            display_name=full_names.get(dev["name"], dev["name"]),
            index=dev["index"],
        )
        for dev in raw_devices
    )

    recordable_default_name = _recordable_default_name(
        recordable_devices,
        system_default_name,
    )
    return InputDeviceSnapshot(
        default_name=system_default_name or recordable_default_name,
        recordable_default_name=recordable_default_name,
        devices=devices,
        recordable_devices=recordable_devices,
    )


def _merge_visible_and_recordable_devices(
    raw_by_name: dict[str, dict],
    full_names: dict[str, str],
) -> tuple[InputDevice, ...]:
    devices: list[InputDevice] = []
    seen: set[str] = set()

    for raw_name, full_name in full_names.items():
        raw_device = raw_by_name.get(raw_name)
        devices.append(
            InputDevice(
                name=raw_name,
                display_name=full_name,
                index=raw_device["index"] if raw_device is not None else None,
            )
        )
        seen.add(raw_name)

    for raw_name, raw_device in raw_by_name.items():
        if raw_name in seen:
            continue
        if _is_system_alias_device(raw_name):
            continue
        devices.append(
            InputDevice(
                name=raw_name,
                display_name=raw_name,
                index=raw_device["index"],
            )
        )

    return tuple(devices)


def _is_system_alias_device(name: str) -> bool:
    normalized = _normalize_device_name(name)
    aliases = {
        "microsoft声音映射器input",
        "microsoftsoundmapperinput",
        "主声音捕获驱动程序",
        "primarysoundcapturedriver",
    }
    return normalized in aliases


def _recordable_default_name(
    devices: tuple[InputDevice, ...],
    system_default_name: str,
) -> str:
    recordable_devices = [device for device in devices if device.is_recordable]
    if not recordable_devices:
        return ""

    if system_default_name:
        truncated = system_default_name[:31]
        for device in recordable_devices:
            if device.name == truncated or _same_device_name(device.display_name, system_default_name):
                return device.name
        # During hot-plug, PyAudio can briefly keep reporting the old default.
        # Do not bind that stale device to the current Windows default endpoint.
        return ""

    fallback_name = VoiceRecorder.get_default_device_name()
    if fallback_name == "Unknown":
        fallback_name = ""
    for device in recordable_devices:
        if fallback_name and device.name == fallback_name:
            return device.name

    return ""


def _same_device_name(left: str, right: str) -> bool:
    return _normalize_device_name(left) == _normalize_device_name(right)


def _normalize_device_name(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())
