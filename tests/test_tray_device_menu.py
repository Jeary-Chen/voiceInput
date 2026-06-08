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

    def test_system_default_change_schedules_async_reopen(self):
        from ui.tray import VoiceTray

        calls = []
        recorder = SimpleNamespace(
            device_name="Old Default",
            invalidate_stream=lambda reason="": calls.append(reason) or True,
        )
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _cached_default_name="New Default",
            _engine=SimpleNamespace(state="ready", recorder=recorder),
            _pending_device_apply=False,
            _start_recorder_prepare_worker=lambda **kwargs: calls.append(kwargs),
        )

        VoiceTray._sync_system_default_device(tray)

        self.assertEqual(calls, [{"invalidate_reason": "system default device changed"}])
        self.assertFalse(tray._pending_device_apply)

    def test_device_storm_delays_audio_apply(self):
        from ui.tray import VoiceTray

        starts = []
        audio_refreshes = []
        tray = SimpleNamespace(
            _audio=SimpleNamespace(
                refresh_output_device_async=lambda: audio_refreshes.append(True),
            ),
            _device_change_times=[0.0, 1.0, 2.0],
            _device_storm_until=0.0,
            _device_change_timer=SimpleNamespace(start=lambda delay: starts.append(delay)),
        )

        with patch("ui.tray.time.monotonic", return_value=3.0):
            VoiceTray._on_audio_device_changed(tray)

        self.assertEqual(audio_refreshes, [True])
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
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=True),
            ),
            _recorder_prepare_worker=None,
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
            _recorder_prepare_pending_job=None,
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
            _recorder_prepare_worker=None,
        )
        tray._recorder_is_ready = lambda: True
        tray._begin_recording = lambda source: VoiceTray._begin_recording(tray, source)

        VoiceTray._request_record_start(tray, "hotkey")

        self.assertEqual(calls, ["sound:hotkey", "toggle"])

    def test_request_record_start_prepares_first_when_not_ready(self):
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
            _recorder_prepare_worker=None,
            _pending_record_start_source=None,
            _start_recorder_prepare_worker=lambda: calls.append("prepare"),
        )
        tray._recorder_is_ready = lambda: False
        tray._begin_recording = lambda source: VoiceTray._begin_recording(tray, source)

        VoiceTray._request_record_start(tray, "tray_or_mini")

        self.assertEqual(calls, ["prepare"])
        self.assertEqual(tray._pending_record_start_source, "tray_or_mini")

    def test_prepare_done_reschedules_pending_prepare(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=True,
            _recorder_prepare_timer=SimpleNamespace(start=lambda: calls.append("timer")),
            _pending_record_start_source=None,
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNone(tray._recorder_prepare_worker)
        self.assertFalse(tray._recorder_prepare_pending)
        self.assertEqual(calls, ["timer"])

    def test_set_default_device_skips_when_already_default(self):
        from ui.tray import VoiceTray

        config = SimpleNamespace(mic_name="", mic_index=None, save=lambda: (_ for _ in ()).throw(RuntimeError("save")))
        tray = SimpleNamespace(
            _config=config,
            _engine=SimpleNamespace(state="recording"),
            _pending_device_apply=False,
            _rebuild_device_menu=lambda: (_ for _ in ()).throw(RuntimeError("rebuild")),
            _clear_device_fault=lambda: (_ for _ in ()).throw(RuntimeError("clear")),
            _sync_tray_icon_with_engine=lambda: (_ for _ in ()).throw(RuntimeError("icon")),
            show_tray_message=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("message")),
        )

        VoiceTray._set_default_device(tray)

    def test_set_device_skips_when_already_selected(self):
        from ui.tray import VoiceTray

        config = SimpleNamespace(
            mic_name="Headphones",
            mic_index=1,
            save=lambda: (_ for _ in ()).throw(RuntimeError("save")),
        )
        tray = SimpleNamespace(
            _config=config,
            _engine=SimpleNamespace(state="recording"),
            _pending_device_apply=False,
            _rebuild_device_menu=lambda: (_ for _ in ()).throw(RuntimeError("rebuild")),
            _clear_device_fault=lambda: (_ for _ in ()).throw(RuntimeError("clear")),
            _sync_tray_icon_with_engine=lambda: (_ for _ in ()).throw(RuntimeError("icon")),
            show_tray_message=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("message")),
        )

        VoiceTray._set_device(tray, "Headphones", 1)

    def test_set_mode_skips_when_unchanged(self):
        from ui.tray import VoiceTray

        config = SimpleNamespace(mode="polish", save=lambda: (_ for _ in ()).throw(RuntimeError("save")))
        tray = SimpleNamespace(
            _config=config,
            _sync_mode_menu=lambda: (_ for _ in ()).throw(RuntimeError("sync")),
            _mini=SimpleNamespace(sync_mode=lambda: (_ for _ in ()).throw(RuntimeError("mini"))),
        )

        VoiceTray._set_mode(tray, "polish")

    def test_prepare_done_starts_pending_recording_when_idle(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=False,
            _pending_record_start_source="hotkey",
            _begin_recording=lambda source: calls.append(source),
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNone(tray._recorder_prepare_worker)
        self.assertIsNone(tray._pending_record_start_source)
        self.assertEqual(calls, ["hotkey"])


if __name__ == "__main__":
    unittest.main()
