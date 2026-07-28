"""Input-device inventory and recordability model.

Recordability is an explicit three-state value — never inferred solely from
``index is None``, which previously conflated "probed unopenable" with
"probe skipped while recording".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from core.device_names import (
    device_identity_key,
    is_pyaudio_name_truncation_pair,
    is_system_capture_alias,
    pyaudio_truncated_name,
    same_device_name,
)
from core.device_watcher import get_default_capture_device_name, get_full_device_names
from core.recorder import VoiceRecorder


class Recordability(Enum):
    """Per-endpoint openability relative to the last PyAudio probe."""

    KNOWN_RECORDABLE = "known_recordable"
    KNOWN_UNRECORDABLE = "known_unrecordable"
    PROBE_SKIPPED = "probe_skipped"


ProbeStatus = Literal["probed", "probe_skipped"]


@dataclass(frozen=True)
class InputDevice:
    name: str
    display_name: str
    index: int | None
    recordability: Recordability | None = None

    def __post_init__(self):
        # Infer from index when callers (tests/helpers) omit the explicit state.
        recordability = self.recordability
        if recordability is None:
            recordability = (
                Recordability.KNOWN_RECORDABLE
                if self.index is not None
                else Recordability.KNOWN_UNRECORDABLE
            )
            object.__setattr__(self, "recordability", recordability)
        if recordability is Recordability.KNOWN_RECORDABLE and self.index is None:
            raise ValueError("KNOWN_RECORDABLE devices require a PortAudio index")

    @property
    def is_recordable(self) -> bool:
        return self.recordability is Recordability.KNOWN_RECORDABLE

    @property
    def is_known_unrecordable(self) -> bool:
        return self.recordability is Recordability.KNOWN_UNRECORDABLE

    @property
    def probe_skipped(self) -> bool:
        return self.recordability is Recordability.PROBE_SKIPPED


@dataclass(frozen=True)
class InputDeviceSnapshot:
    default_name: str
    recordable_default_name: str
    devices: tuple[InputDevice, ...]
    recordable_devices: tuple[InputDevice, ...] = ()
    probe_status: ProbeStatus = "probed"

    @property
    def has_recordable_device(self) -> bool:
        return bool(self._recordable_candidates)

    @property
    def needs_recordable_retry(self) -> bool:
        """True only after a real probe found endpoints that are not yet openable."""
        if self.probe_status != "probed":
            return False
        if not self.devices:
            return False
        if not self.has_recordable_device:
            return True
        return bool(self.default_name and not self.recordable_default_name)

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
            return tuple(
                device for device in self.recordable_devices
                if device.is_recordable
            )
        return tuple(device for device in self.devices if device.is_recordable)

    @classmethod
    def empty(cls) -> "InputDeviceSnapshot":
        return cls(
            default_name="",
            recordable_default_name="",
            devices=(),
            probe_status="probed",
        )


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
    """Build a raw inventory snapshot.

    When ``open_probe`` is true, PyAudio decides recordability.
    When false (live recording), every visible endpoint is ``PROBE_SKIPPED``;
    call :func:`finalize_snapshot` with the previous snapshot to restore known
    openability before exposing the result to menus / retry logic.
    """
    system_default_name = get_default_capture_device_name() or ""
    raw_devices = VoiceRecorder.list_devices() if open_probe else []
    full_names = get_full_device_names()

    raw_index = _RawDeviceIndex(raw_devices)
    full_index = _FullNameIndex(full_names)
    probe_status: ProbeStatus = "probed" if open_probe else "probe_skipped"
    default_recordability = (
        Recordability.KNOWN_UNRECORDABLE if open_probe else Recordability.PROBE_SKIPPED
    )

    recordable_devices = tuple(
        InputDevice(
            name=device["name"],
            display_name=full_index.display_name(device["name"]),
            index=device["index"],
            recordability=Recordability.KNOWN_RECORDABLE,
        )
        for device in raw_devices
    )
    devices = _merge_visible_devices(
        raw_index,
        full_names,
        full_index,
        default_recordability=default_recordability,
    )
    # Prefer PyAudio recordable rows for default binding (stable open indices);
    # fall back to visible rows when the probe list is empty.
    recordable_default_name = (
        _recordable_default_name(
            recordable_devices or devices,
            system_default_name,
        )
        if open_probe
        else ""
    )
    return InputDeviceSnapshot(
        default_name=system_default_name or recordable_default_name,
        recordable_default_name=recordable_default_name,
        devices=devices,
        recordable_devices=recordable_devices,
        probe_status=probe_status,
    )


def finalize_snapshot(
    snapshot: InputDeviceSnapshot,
    *,
    previous: InputDeviceSnapshot | None,
) -> InputDeviceSnapshot:
    """Resolve probe-skipped inventories against the last known openability."""
    if snapshot.probe_status != "probe_skipped":
        return snapshot
    return merge_probe_skipped_snapshot(previous or InputDeviceSnapshot.empty(), snapshot)


def merge_probe_skipped_snapshot(
    previous: InputDeviceSnapshot,
    current: InputDeviceSnapshot,
) -> InputDeviceSnapshot:
    """Carry forward known recordability across a probe-skipped refresh."""
    if current.probe_status != "probe_skipped":
        return current
    if current.has_recordable_device:
        return current
    if not previous.devices and not previous.recordable_devices:
        return current

    prev_by_name: dict[str, InputDevice] = {}
    for device in (*previous.devices, *previous.recordable_devices):
        for key in (device.name, device.display_name):
            if key and key not in prev_by_name:
                prev_by_name[key] = device

    def _previous_match(name: str, display_name: str) -> InputDevice | None:
        for key in (name, display_name):
            if key in prev_by_name:
                return prev_by_name[key]
        for device in (*previous.devices, *previous.recordable_devices):
            if (
                same_device_name(device.name, name)
                or same_device_name(device.display_name, name)
                or same_device_name(device.name, display_name)
                or same_device_name(device.display_name, display_name)
            ):
                return device
        return None

    merged_devices: list[InputDevice] = []
    for device in current.devices:
        if device.is_recordable:
            merged_devices.append(device)
            continue
        prior = _previous_match(device.name, device.display_name)
        if prior is None:
            merged_devices.append(device)
            continue
        if prior.is_recordable:
            merged_devices.append(
                InputDevice(
                    name=device.name,
                    display_name=device.display_name,
                    index=prior.index,
                    recordability=Recordability.KNOWN_RECORDABLE,
                )
            )
        elif prior.is_known_unrecordable:
            merged_devices.append(
                InputDevice(
                    name=device.name,
                    display_name=device.display_name,
                    index=None,
                    recordability=Recordability.KNOWN_UNRECORDABLE,
                )
            )
        else:
            merged_devices.append(device)

    if previous.recordable_devices:
        recordable_devices = tuple(
            device for device in previous.recordable_devices if device.is_recordable
        )
    else:
        recordable_devices = tuple(
            device for device in merged_devices if device.is_recordable
        )

    recordable_default_name = _recordable_default_name(
        tuple(merged_devices),
        current.default_name,
    )
    if not recordable_default_name and previous.recordable_default_name:
        if any(
            same_device_name(device.name, previous.recordable_default_name)
            or same_device_name(device.display_name, previous.recordable_default_name)
            for device in recordable_devices
        ):
            recordable_default_name = previous.recordable_default_name

    return InputDeviceSnapshot(
        default_name=current.default_name or previous.default_name,
        recordable_default_name=recordable_default_name,
        devices=tuple(merged_devices),
        recordable_devices=recordable_devices,
        # Still probe_skipped: consumers must not treat this as a failed probe.
        probe_status="probe_skipped",
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
    *,
    default_recordability: Recordability,
) -> tuple[InputDevice, ...]:
    """One menu row per Windows capture endpoint.

    COM friendly names are the row source of truth. PyAudio only supplies
    openability / index when a probe ran.
    """
    devices: list[InputDevice] = []
    seen: set[str] = set()

    for trunc, full_name in full_names.items():
        # Prefer full name match first: WASAPI often keeps the untruncated form.
        raw_device = raw_index.lookup(full_name, trunc)
        if raw_device is not None:
            devices.append(
                InputDevice(
                    name=trunc,
                    display_name=full_name,
                    index=raw_device["index"],
                    recordability=Recordability.KNOWN_RECORDABLE,
                )
            )
        else:
            devices.append(
                InputDevice(
                    name=trunc,
                    display_name=full_name,
                    index=None,
                    recordability=default_recordability,
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
                recordability=Recordability.KNOWN_RECORDABLE,
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
