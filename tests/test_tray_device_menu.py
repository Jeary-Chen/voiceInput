import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMenu


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class TrayDeviceMenuRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_rebuild_updates_visible_submenu_in_place(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        parent_menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _cached_default_name="Built-in Mic",
            _cached_devices=[
                {"name": "Headphones", "display_name": "Headphones", "index": 1},
            ],
            _dev_menu_dirty=True,
            _device_menu=menu,
            contextMenu=lambda: parent_menu,
        )

        with patch.object(menu, "isVisible", return_value=True):
            with patch.object(menu, "close") as close:
                with patch.object(menu, "setActiveAction") as set_active:
                    with patch("ui.tray.anchor_left_cascade_submenu") as anchor:
                        with patch.object(menu, "updateGeometry") as update_geometry:
                            with patch.object(menu, "update") as update:
                                VoiceTray._rebuild_device_menu(tray)

        close.assert_not_called()
        set_active.assert_called_once_with(None)
        anchor.assert_called_once_with(menu, parent_menu)
        update_geometry.assert_called_once()
        update.assert_called_once()
        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("Headphones", labels)
        self.assertFalse(tray._dev_menu_dirty)

    def test_system_default_change_invalidates_stream_and_schedules_prepare(self):
        from ui.tray import VoiceTray

        calls = []
        prepare_scheduled = []
        recorder = SimpleNamespace(
            device_name="Old Default",
            invalidate_stream=lambda reason="": calls.append(reason) or True,
        )
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _cached_default_name="New Default",
            _engine=SimpleNamespace(state="ready", recorder=recorder),
            _pending_device_apply=False,
            _recorder_prepare_timer=SimpleNamespace(
                start=lambda: prepare_scheduled.append(True),
            ),
        )

        VoiceTray._sync_system_default_device(tray)

        self.assertEqual(calls, ["system default device changed"])
        self.assertFalse(tray._pending_device_apply)
        self.assertTrue(prepare_scheduled, "deferred prepare should be scheduled")

    def test_device_storm_delays_audio_apply(self):
        from ui.tray import VoiceTray

        starts = []
        tray = SimpleNamespace(
            _device_change_times=[0.0, 1.0, 2.0],
            _device_storm_until=0.0,
            _device_change_timer=SimpleNamespace(start=lambda delay: starts.append(delay)),
        )

        with patch("ui.tray.time.monotonic", return_value=3.0):
            VoiceTray._on_audio_device_changed(tray)

        self.assertEqual(starts, [2000])
        self.assertAlmostEqual(tray._device_storm_until, 5.0)

    def test_refresh_during_storm_only_updates_menu_cache(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _dev_refresh_ready=True,
            _cached_default_name="Old Mic",
            _cached_devices=[],
            _last_menu_refresh_time=0.0,
            _device_storm_until=999.0,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _device_change_in_storm=lambda: True,
            _sync_system_default_device=lambda: calls.append("sync"),
            _auto_fallback_if_device_gone=lambda: calls.append("fallback"),
            _recover_recorder_if_devices_returned=lambda: calls.append("recover"),
            _rebuild_device_menu=lambda: calls.append("rebuild"),
            _sync_tray_icon_with_engine=lambda: calls.append("icon"),
        )

        with patch("ui.tray.time.monotonic", return_value=10.0):
            VoiceTray._on_refresh_result(
                tray,
                "New Mic",
                [{"name": "New Mic", "display_name": "New Mic", "index": 1}],
            )

        self.assertEqual(calls, ["rebuild", "icon"])
        self.assertEqual(tray._cached_default_name, "New Mic")
        self.assertFalse(tray._dev_refresh_running)

    def test_deferred_recorder_prepare_starts_worker_when_quiet(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _device_change_in_storm=lambda: False,
            _start_recorder_prepare_worker=lambda: calls.append("worker"),
        )

        VoiceTray._deferred_recorder_prepare(tray)

        self.assertEqual(calls, ["worker"])

    def test_deferred_recorder_prepare_waits_during_storm(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _device_change_in_storm=lambda: True,
            _recorder_prepare_timer=SimpleNamespace(
                start=lambda: calls.append("timer"),
            ),
            _start_recorder_prepare_worker=lambda: calls.append("worker"),
        )

        VoiceTray._deferred_recorder_prepare(tray)

        self.assertEqual(calls, ["timer"])

    def test_request_record_start_begins_immediately_when_ready(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=True),
                toggle_record=lambda: calls.append("toggle"),
            ),
            _audio=SimpleNamespace(
                play_start=lambda source="": calls.append(f"sound:{source}"),
            ),
        )
        tray._recorder_is_ready = lambda: True
        tray._begin_recording = lambda source: VoiceTray._begin_recording(tray, source)

        VoiceTray._request_record_start(tray, "hotkey")

        self.assertEqual(calls, ["sound:hotkey", "toggle"])

    def test_request_record_start_begins_immediately_when_not_ready(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=False),
                toggle_record=lambda: calls.append("toggle"),
            ),
            _audio=SimpleNamespace(
                play_start=lambda source="": calls.append(f"sound:{source}"),
            ),
        )
        tray._recorder_is_ready = lambda: False
        tray._begin_recording = lambda source: VoiceTray._begin_recording(tray, source)

        VoiceTray._request_record_start(tray, "tray_or_mini")

        self.assertEqual(calls, ["sound:tray_or_mini", "toggle"])

    def test_prepare_done_reschedules_pending_prepare(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=True,
            _recorder_prepare_timer=SimpleNamespace(start=lambda: calls.append("timer")),
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNone(tray._recorder_prepare_worker)
        self.assertFalse(tray._recorder_prepare_pending)
        self.assertEqual(calls, ["timer"])


if __name__ == "__main__":
    unittest.main()
