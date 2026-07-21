from __future__ import annotations

from dataclasses import dataclass

from core.device_names import (
    device_identity_key,
    is_pyaudio_name_truncation_pair,
    is_system_capture_alias,
    pyaudio_truncated_name,
    same_device_name,
)
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
        for device in self.devices:
            if same_device_name(device.name, name) or same_device_name(device.display_name, name):
                return device
        return next((device for device in self._recordable_candidates if device.name == name), None)

    @property
    def _recordable_candidates(self) -> tuple[InputDevice, ...]:
        if self.recordable_devices:
            return self.recordable_devices
        return tuple(device for device in self.devices if device.is_recordable)

    @classmethod
    def empty(cls) -> "InputDeviceSnapshot":
        return cls(default_name="", recordable_default_name="", devices=())


class _RawDeviceIndex:
    def __init__(self, raw_devices: list[dict]):
        self._exact = {device["name"]: device for device in raw_devices}
        self._by_identity: dict[str, dict] = {}
        for device in raw_devices:
            key = device_identity_key(device["name"])
            if key:
                self._by_identity.setdefault(key, device)

    def lookup(self, *names: str) -> dict | None:
        for name in names:
            if name and name in self._exact:
                return self._exact[name]
        for name in names:
            key = device_identity_key(name)
            if key:
                device = self._by_identity.get(key)
                if device is not None:
                    return device
            # Trunc↔full are different identity keys; still the same endpoint.
            for raw_name, device in self._exact.items():
                if is_pyaudio_name_truncation_pair(name, raw_name):
                    return device
        return None

    def iter_names(self):
        return self._exact.keys()


class _FullNameIndex:
    def __init__(self, full_names: dict[str, str]):
        self._by_trunc = full_names
        self._by_identity: dict[str, str] = {}
        for trunc, full in full_names.items():
            for name in (trunc, full):
                key = device_identity_key(name)
                if key:
                    self._by_identity.setdefault(key, full)

    def display_name(self, raw_name: str) -> str:
        if raw_name in self._by_trunc:
            return self._by_trunc[raw_name]
        key = device_identity_key(raw_name)
        if key and key in self._by_identity:
            return self._by_identity[key]
        for trunc, full in self._by_trunc.items():
            if is_pyaudio_name_truncation_pair(raw_name, trunc) or is_pyaudio_name_truncation_pair(
                raw_name, full
            ):
                return full
        return raw_name


def get_input_device_snapshot(*, open_probe: bool = True) -> InputDeviceSnapshot:
    """Build menu/runtime snapshot.

    PyAudio decides recordability when ``open_probe`` is true.  During active
    recording, callers can pass ``open_probe=False`` to avoid creating another
    PortAudio client while the input callback stream is live.
    """
    system_default_name = get_default_capture_device_name() or ""
    raw_devices = VoiceRecorder.list_devices() if open_probe else []
    full_names = get_full_device_names()

    raw_index = _RawDeviceIndex(raw_devices)
    full_index = _FullNameIndex(full_names)

    recordable_devices = tuple(
        InputDevice(
            name=device["name"],
            display_name=full_index.display_name(device["name"]),
            index=device["index"],
        )
        for device in raw_devices
    )
    recordable_default_name = _recordable_default_name(
        recordable_devices,
        system_default_name,
    )
    return InputDeviceSnapshot(
        default_name=system_default_name or recordable_default_name,
        recordable_default_name=recordable_default_name,
        devices=_merge_visible_devices(raw_index, full_names, full_index),
        recordable_devices=recordable_devices,
    )


def _remember_endpoint_names(seen: set[str], *names: str | None) -> None:
    """Mark all known spellings of one endpoint so trunc/full cannot double-list."""
    for name in names:
        if not name:
            continue
        seen.add(name)
        trunc = pyaudio_truncated_name(name)
        if trunc:
            seen.add(trunc)


def _endpoint_already_listed(name: str, seen: set[str]) -> bool:
    if name in seen:
        return True
    return any(is_pyaudio_name_truncation_pair(name, prior) for prior in seen)


def _merge_visible_devices(
    raw_index: _RawDeviceIndex,
    full_names: dict[str, str],
    full_index: _FullNameIndex,
) -> tuple[InputDevice, ...]:
    """One menu row per Windows capture endpoint.

    COM friendly names are the row source of truth. PyAudio only supplies
    openability / index. Truncated and full spellings of the same endpoint are
    collapsed with :func:`is_pyaudio_name_truncation_pair` — never by loose
    display-name similarity.
    """
    devices: list[InputDevice] = []
    seen: set[str] = set()

    for trunc, full_name in full_names.items():
        # Prefer full name match first: WASAPI often keeps the untruncated form.
        raw_device = raw_index.lookup(full_name, trunc)
        devices.append(
            InputDevice(
                name=trunc,
                display_name=full_name,
                index=raw_device["index"] if raw_device is not None else None,
            )
        )
        _remember_endpoint_names(
            seen,
            trunc,
            full_name,
            raw_device["name"] if raw_device is not None else None,
        )

    for raw_name in raw_index.iter_names():
        if _endpoint_already_listed(raw_name, seen) or is_system_capture_alias(raw_name):
            continue
        raw_device = raw_index.lookup(raw_name)
        if raw_device is None:
            continue
        devices.append(
            InputDevice(
                name=raw_name,
                display_name=full_index.display_name(raw_name),
                index=raw_device["index"],
            )
        )
        _remember_endpoint_names(seen, raw_name, full_index.display_name(raw_name))

    return tuple(devices)


def _recordable_default_name(
    devices: tuple[InputDevice, ...],
    system_default_name: str,
) -> str:
    recordable = [device for device in devices if device.is_recordable]
    if not recordable:
        return ""

    if system_default_name:
        for device in recordable:
            if same_device_name(device.name, system_default_name) or same_device_name(
                device.display_name, system_default_name
            ):
                return device.name
        return ""

    fallback_name = VoiceRecorder.get_default_device_name()
    if fallback_name == "Unknown":
        return ""
    for device in recordable:
        if same_device_name(device.name, fallback_name):
            return device.name
    return ""
