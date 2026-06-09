import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class InputDeviceSnapshotTests(unittest.TestCase):
    def test_snapshot_uses_pyaudio_as_recordable_source(self):
        from core.input_devices import get_input_device_snapshot

        with patch("core.input_devices.VoiceRecorder.list_devices", return_value=[]):
            with patch(
                "core.input_devices.get_default_capture_device_name",
                return_value="耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)",
            ):
                with patch(
                    "core.input_devices.get_full_device_names",
                    return_value={
                        "耳机 (HUAWEI FreeBuds SE 2 Hand": (
                            "耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)"
                        )
                    },
                ):
                    snapshot = get_input_device_snapshot()

        self.assertEqual(
            snapshot.default_name,
            "耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)",
        )
        self.assertEqual(snapshot.recordable_default_name, "")
        self.assertEqual(len(snapshot.devices), 1)
        self.assertEqual(
            snapshot.devices[0].display_name,
            "耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)",
        )
        self.assertFalse(snapshot.devices[0].is_recordable)
        self.assertFalse(snapshot.has_recordable_device)

    def test_snapshot_decorates_recordable_device_with_full_name(self):
        from core.input_devices import get_input_device_snapshot

        full_name = "耳机 (HUAWEI FreeBuds SE 2 Hands-Free AG Audio)"
        raw_name = full_name[:31]
        with patch(
            "core.input_devices.VoiceRecorder.list_devices",
            return_value=[{"name": raw_name, "index": 3}],
        ):
            with patch(
                "core.input_devices.get_default_capture_device_name",
                return_value=full_name,
            ):
                with patch(
                    "core.input_devices.get_full_device_names",
                    return_value={raw_name: full_name},
                ):
                    snapshot = get_input_device_snapshot()

        self.assertEqual(snapshot.default_name, full_name)
        self.assertEqual(snapshot.recordable_default_name, raw_name)
        self.assertEqual(snapshot.devices[0].display_name, full_name)
        self.assertTrue(snapshot.has_recordable_device)

    def test_snapshot_falls_back_to_pyaudio_default_when_com_has_none(self):
        from core.input_devices import get_input_device_snapshot

        with patch(
            "core.input_devices.VoiceRecorder.list_devices",
            return_value=[{"name": "Built-in Mic", "index": 1}],
        ):
            with patch("core.input_devices.get_default_capture_device_name", return_value=None):
                with patch("core.input_devices.get_full_device_names", return_value={}):
                    with patch(
                        "core.input_devices.VoiceRecorder.get_default_device_name",
                        return_value="Built-in Mic",
                    ):
                        snapshot = get_input_device_snapshot()

        self.assertEqual(snapshot.default_name, "Built-in Mic")
        self.assertEqual(snapshot.recordable_default_name, "Built-in Mic")
        self.assertTrue(snapshot.has_recordable_device)

    def test_snapshot_hides_system_aliases_from_visible_menu_devices(self):
        from core.input_devices import get_input_device_snapshot

        with patch(
            "core.input_devices.VoiceRecorder.list_devices",
            return_value=[
                {"name": "Built-in Mic", "index": 20},
                {"name": "主声音捕获驱动程序", "index": 8},
                {"name": "Microsoft 声音映射器 - Input", "index": 0},
            ],
        ):
            with patch("core.input_devices.get_default_capture_device_name", return_value="Built-in Mic"):
                with patch(
                    "core.input_devices.get_full_device_names",
                    return_value={"Built-in Mic": "Built-in Mic"},
                ):
                    snapshot = get_input_device_snapshot()

        self.assertEqual([device.name for device in snapshot.devices], ["Built-in Mic"])
        self.assertEqual(
            [device.name for device in snapshot.recordable_devices],
            ["Built-in Mic", "主声音捕获驱动程序", "Microsoft 声音映射器 - Input"],
        )

    def test_snapshot_does_not_use_pyaudio_default_when_com_default_is_unmatched(self):
        from core.input_devices import get_input_device_snapshot

        with patch(
            "core.input_devices.VoiceRecorder.list_devices",
            return_value=[
                {"name": "Built-in Mic", "index": 15},
                {"name": "主声音捕获驱动程序", "index": 6},
            ],
        ):
            with patch(
                "core.input_devices.get_default_capture_device_name",
                return_value="耳机 (2- HUAWEI FreeBuds SE 2)",
            ):
                with patch(
                    "core.input_devices.get_full_device_names",
                    return_value={
                        "耳机 (2- HUAWEI FreeBuds SE 2)": "耳机 (2- HUAWEI FreeBuds SE 2)",
                        "Built-in Mic": "Built-in Mic",
                    },
                ):
                    with patch(
                        "core.input_devices.VoiceRecorder.get_default_device_name",
                        return_value="Built-in Mic",
                    ):
                        snapshot = get_input_device_snapshot()

        self.assertEqual(snapshot.default_name, "耳机 (2- HUAWEI FreeBuds SE 2)")
        self.assertEqual(snapshot.recordable_default_name, "")
        self.assertFalse(snapshot.devices[0].is_recordable)
        self.assertTrue(snapshot.has_recordable_device)


if __name__ == "__main__":
    unittest.main()
