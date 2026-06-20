import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QApplication, QMenu


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _snapshot(
    default_name: str = "",
    devices: list[dict] | None = None,
    recordable_default_name: str | None = None,
):
    from core.input_devices import InputDevice, InputDeviceSnapshot

    return InputDeviceSnapshot(
        default_name=default_name,
        recordable_default_name=(
            default_name if recordable_default_name is None else recordable_default_name
        ),
        devices=tuple(InputDevice(**device) for device in (devices or [])),
    )


def _bind_device_helpers(tray):
    from ui.tray import VoiceTray

    tray._recordable_system_default_device = (
        lambda: VoiceTray._recordable_system_default_device(tray)
    )
    tray._recorder_prepare_target = (
        lambda: VoiceTray._recorder_prepare_target(tray)
    )
    tray._schedule_recorder_device_apply = (
        lambda index, name: VoiceTray._schedule_recorder_device_apply(tray, index, name)
    )
    return tray


class _FakeRecordableProbeRetry:
    def __init__(self, active: bool = False, activate_on_schedule: bool = False):
        self.active = active
        self.activate_on_schedule = activate_on_schedule
        self.reset_calls = 0
        self.stop_calls = 0
        self.scheduled_snapshots = []

    def reset(self):
        self.reset_calls += 1
        self.active = False

    def stop(self):
        self.stop_calls += 1

    def schedule_for(self, snapshot):
        self.scheduled_snapshots.append(snapshot)
        if self.activate_on_schedule:
            self.active = True


def _bind_recordable_retry_state(
    tray,
    *,
    active: bool = False,
    activate_on_schedule: bool = False,
):
    tray._recordable_retry = _FakeRecordableProbeRetry(
        active=active,
        activate_on_schedule=activate_on_schedule,
    )
    return tray


class TrayDeviceMenuRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_recordable_probe_retry_schedules_transient_unrecordable_snapshot(self):
        from ui.tray import _RecordableProbeRetry

        class FakeTimer:
            def __init__(self):
                self.starts = []
                self.stops = 0
                self.timeout = SimpleNamespace(connect=lambda callback: None)

            def setSingleShot(self, value):
                self.single_shot = value

            def start(self, delay):
                self.starts.append(delay)

            def stop(self):
                self.stops += 1

        timer = FakeTimer()
        with patch("ui.tray.QTimer", return_value=timer):
            retry = _RecordableProbeRetry(lambda: None)

        retry.schedule_for(
            _snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                ],
                recordable_default_name="",
            )
        )

        self.assertTrue(retry.active)
        self.assertEqual(timer.starts, [1000])

        retry.schedule_for(
            _snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": 1,
                    },
                ],
            )
        )

        self.assertFalse(retry.active)
        self.assertEqual(timer.stops, 1)

    def test_recordable_probe_retry_schedules_when_default_is_unrecordable(self):
        from ui.tray import _RecordableProbeRetry

        class FakeTimer:
            def __init__(self):
                self.starts = []
                self.timeout = SimpleNamespace(connect=lambda callback: None)

            def setSingleShot(self, value):
                pass

            def start(self, delay):
                self.starts.append(delay)

            def stop(self):
                pass

        timer = FakeTimer()
        with patch("ui.tray.QTimer", return_value=timer):
            retry = _RecordableProbeRetry(lambda: None)

        retry.schedule_for(
            _snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                    {
                        "name": "Built-in Mic",
                        "display_name": "Built-in Mic",
                        "index": 15,
                    },
                ],
                recordable_default_name="",
            )
        )

        self.assertTrue(retry.active)
        self.assertEqual(timer.starts, [1000])

    def test_rebuild_updates_visible_submenu_in_place(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        parent_menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _input_snapshot=_snapshot("Built-in Mic", [
                {"name": "Headphones", "display_name": "Headphones", "index": 1},
            ]),
            _dev_menu_dirty=True,
            _device_menu=menu,
            contextMenu=lambda: parent_menu,
        )
        _bind_recordable_retry_state(tray)

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

    def test_rebuild_shows_unrecordable_visible_device_disabled(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        parent_menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _input_snapshot=_snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                ],
                recordable_default_name="",
            ),
            _dev_menu_dirty=True,
            _device_menu=menu,
            contextMenu=lambda: parent_menu,
        )
        _bind_recordable_retry_state(tray)

        VoiceTray._rebuild_device_menu(tray)

        actions = [action for action in menu.actions() if not action.isSeparator()]
        labels = [action.text() for action in actions]
        self.assertIn("系统默认 (Bluetooth Mic)", labels)
        self.assertIn("Bluetooth Mic（不可录）", labels)
        self.assertIn("(未发现兼容设备)", labels)
        unrecordable = next(action for action in actions if action.text() == "Bluetooth Mic（不可录）")
        self.assertFalse(unrecordable.isEnabled())

    def test_rebuild_shows_initializing_while_recordable_retry_active(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _input_snapshot=_snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                ],
                recordable_default_name="",
            ),
            _dev_menu_dirty=True,
            _device_menu=menu,
            contextMenu=lambda: None,
        )
        _bind_recordable_retry_state(tray, active=True)

        VoiceTray._rebuild_device_menu(tray)

        labels = [action.text() for action in menu.actions() if not action.isSeparator()]
        self.assertIn("Bluetooth Mic（正在初始化）", labels)

    def test_system_default_change_schedules_async_reopen(self):
        from ui.tray import VoiceTray

        calls = []
        recorder = SimpleNamespace(
            device_name="Old Default",
            invalidate_stream=lambda reason="": calls.append(reason) or True,
        )
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _input_snapshot=_snapshot(
                "New Default",
                [{"name": "New Default", "display_name": "New Default", "index": 2}],
            ),
            _engine=SimpleNamespace(state="ready", recorder=recorder),
            _pending_device_apply=False,
            _start_recorder_prepare_worker=lambda **kwargs: calls.append(kwargs),
        )
        _bind_device_helpers(tray)
        _bind_recordable_retry_state(tray)

        VoiceTray._sync_system_default_device(tray)

        self.assertEqual(calls, [{"device_index": None, "preferred_name": ""}])
        self.assertFalse(tray._pending_device_apply)

    def test_display_only_system_default_does_not_reopen_recorder(self):
        from ui.tray import VoiceTray

        calls = []
        recorder = SimpleNamespace(device_name="Old Default")
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name=""),
            _input_snapshot=_snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                ],
                recordable_default_name="",
            ),
            _engine=SimpleNamespace(state="ready", recorder=recorder),
            _pending_device_apply=False,
            _start_recorder_prepare_worker=lambda **kwargs: calls.append(kwargs),
        )
        _bind_device_helpers(tray)
        _bind_recordable_retry_state(tray)

        VoiceTray._sync_system_default_device(tray)

        self.assertEqual(calls, [])
        self.assertFalse(tray._pending_device_apply)

    def test_device_storm_delays_audio_apply(self):
        from ui.tray import VoiceTray

        starts = []
        calls = []
        tray = SimpleNamespace(
            _audio=SimpleNamespace(
                reset_for_device_rescan=lambda: calls.append("audio_reset"),
            ),
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(
                    reset_portaudio=lambda reason: calls.append(("reset", reason)),
                ),
            ),
            _device_change_times=[0.0, 1.0, 2.0],
            _device_storm_until=0.0,
            _device_change_generation=0,
            _device_change_timer=SimpleNamespace(start=lambda delay: starts.append(delay)),
            _input_rescan_needs_portaudio_reset=False,
            _output_reopen_after_device_rescan=False,
        )
        _bind_recordable_retry_state(tray)

        with patch("ui.tray.time.monotonic", return_value=3.0):
            VoiceTray._on_audio_device_changed(tray)

        self.assertEqual(calls, [])
        self.assertTrue(tray._input_rescan_needs_portaudio_reset)
        self.assertTrue(tray._output_reopen_after_device_rescan)
        self.assertEqual(tray._device_change_generation, 1)
        self.assertEqual(starts, [2000])
        self.assertAlmostEqual(tray._device_storm_until, 5.0)

    def test_device_change_defers_portaudio_reset_while_recording(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _audio=SimpleNamespace(
                reset_for_device_rescan=lambda: calls.append("audio_reset"),
            ),
            _engine=SimpleNamespace(
                state="recording",
                recorder=SimpleNamespace(
                    reset_portaudio=lambda reason: calls.append(("reset", reason)),
                ),
            ),
            _device_change_times=[],
            _device_storm_until=0.0,
            _device_change_generation=0,
            _device_change_timer=SimpleNamespace(start=lambda delay: calls.append(("timer", delay))),
            _input_rescan_needs_portaudio_reset=False,
            _output_reopen_after_device_rescan=False,
        )
        _bind_recordable_retry_state(tray)

        with patch("ui.tray.time.monotonic", return_value=3.0):
            VoiceTray._on_audio_device_changed(tray)

        self.assertEqual(calls, [("timer", 500)])
        self.assertTrue(tray._input_rescan_needs_portaudio_reset)
        self.assertTrue(tray._output_reopen_after_device_rescan)

    def test_start_refresh_resets_audio_once_after_debounce(self):
        from ui.tray import VoiceTray

        calls = []

        class FakeSignal:
            def connect(self, callback):
                calls.append(("connect", callback.__name__))

        class FakeWorker:
            result_ready = FakeSignal()
            finished = FakeSignal()

            def __init__(self, generation, *, open_probe=True):
                calls.append(("worker", generation, open_probe))

            def start(self):
                calls.append("start")

        tray = SimpleNamespace(
            _dev_refresh_running=False,
            _dev_refresh_repeat=False,
            _input_rescan_needs_portaudio_reset=True,
            _output_reopen_after_device_rescan=True,
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(
                    reset_portaudio=lambda reason: calls.append(("reset", reason)),
                ),
            ),
            _audio=SimpleNamespace(
                reset_for_device_rescan=lambda: calls.append("audio_reset"),
            ),
            _device_change_generation=3,
            _dev_refresh_workers=[],
            _on_refresh_result=lambda *args: None,
            _on_device_refresh_worker_finished=lambda: None,
        )
        tray._reset_input_portaudio_before_rescan = (
            lambda: VoiceTray._reset_input_portaudio_before_rescan(tray)
        )
        tray._reset_output_portaudio_before_rescan = (
            lambda: VoiceTray._reset_output_portaudio_before_rescan(tray)
        )

        with patch("ui.tray._DeviceRefreshWorker", FakeWorker):
            VoiceTray._start_async_refresh(tray)

        self.assertIn(("reset", "audio device changed"), calls)
        self.assertIn("audio_reset", calls)
        self.assertIn(("worker", 3, True), calls)
        self.assertFalse(tray._input_rescan_needs_portaudio_reset)

    def test_device_refresh_worker_skips_open_probe_when_requested(self):
        from ui.tray import _DeviceRefreshWorker

        with patch("core.input_devices.get_input_device_snapshot") as snapshot:
            _DeviceRefreshWorker(4, open_probe=False).run()

        snapshot.assert_called_once_with(open_probe=False)

    def test_refresh_during_storm_only_updates_menu_cache(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _dev_refresh_ready=True,
            _input_snapshot=_snapshot("Old Mic"),
            _last_menu_refresh_time=0.0,
            _device_storm_until=999.0,
            _device_change_generation=0,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=True, no_device=False),
            ),
            _recorder_prepare_worker=None,
            _device_change_in_storm=lambda: True,
            _sync_system_default_device=lambda: calls.append("sync"),
            _auto_fallback_if_device_gone=lambda: calls.append("fallback"),
            _recover_recorder_if_devices_returned=lambda: calls.append("recover"),
            _reopen_output_after_device_rescan=lambda: calls.append("audio"),
            _rebuild_device_menu=lambda: calls.append("rebuild"),
            _sync_tray_icon_with_engine=lambda: calls.append("icon"),
        )
        _bind_device_helpers(tray)
        _bind_recordable_retry_state(tray)
        tray._queue_recorder_recovery_after_storm = (
            lambda: VoiceTray._queue_recorder_recovery_after_storm(tray)
        )

        with patch("ui.tray.time.monotonic", return_value=10.0):
            VoiceTray._on_refresh_result(
                tray,
                _snapshot(
                    "New Mic",
                    [{"name": "New Mic", "display_name": "New Mic", "index": 1}],
                ),
            )

        self.assertEqual(calls, ["rebuild", "icon"])
        self.assertEqual(tray._input_snapshot.default_name, "New Mic")
        self.assertFalse(tray._dev_refresh_running)

    def test_refresh_rebuilds_menu_even_when_snapshot_unchanged(self):
        from ui.tray import VoiceTray

        calls = []
        snapshot = _snapshot(
            "New Mic",
            [{"name": "New Mic", "display_name": "New Mic", "index": 1}],
        )
        tray = SimpleNamespace(
            _dev_refresh_ready=True,
            _input_snapshot=snapshot,
            _last_menu_refresh_time=0.0,
            _device_change_generation=0,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=True, no_device=False),
            ),
            _recorder_prepare_worker=None,
            _device_change_in_storm=lambda: False,
            _sync_system_default_device=lambda: calls.append("sync"),
            _auto_fallback_if_device_gone=lambda: calls.append("fallback"),
            _recover_recorder_if_devices_returned=lambda: calls.append("recover"),
            _reopen_output_after_device_rescan=lambda: None,
            _rebuild_device_menu=lambda: calls.append("rebuild"),
            _sync_tray_icon_with_engine=lambda: calls.append("icon"),
        )
        _bind_recordable_retry_state(tray)

        VoiceTray._on_refresh_result(tray, snapshot)

        self.assertEqual(calls, ["sync", "fallback", "recover", "rebuild", "icon"])
        self.assertFalse(tray._dev_refresh_running)

    def test_refresh_during_storm_queues_recovery_when_no_device(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _dev_refresh_ready=True,
            _input_snapshot=_snapshot(),
            _last_menu_refresh_time=0.0,
            _device_storm_until=999.0,
            _device_change_generation=0,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _config=SimpleNamespace(mic_index=None, mic_name=""),
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=False, no_device=True),
            ),
            _recorder_prepare_worker=None,
            _recorder_prepare_pending_job=None,
            _recorder_prepare_timer=SimpleNamespace(start=lambda: calls.append("timer")),
            _device_change_in_storm=lambda: True,
            _sync_system_default_device=lambda: calls.append("sync"),
            _auto_fallback_if_device_gone=lambda: calls.append("fallback"),
            _recover_recorder_if_devices_returned=lambda: calls.append("recover"),
            _reopen_output_after_device_rescan=lambda: None,
            _rebuild_device_menu=lambda: calls.append("rebuild"),
            _sync_tray_icon_with_engine=lambda: calls.append("icon"),
        )
        tray._queue_recorder_recovery_after_storm = (
            lambda: VoiceTray._queue_recorder_recovery_after_storm(tray)
        )
        _bind_device_helpers(tray)
        _bind_recordable_retry_state(tray)

        VoiceTray._on_refresh_result(
            tray,
            _snapshot(
                "Recovered Mic",
                [{"name": "Recovered Mic", "display_name": "Recovered Mic", "index": 1}],
            ),
        )

        self.assertEqual(calls, ["timer", "rebuild", "icon"])
        self.assertEqual(
            tray._recorder_prepare_pending_job,
            {"device_index": None, "preferred_name": ""},
        )

    def test_refresh_schedules_retry_for_visible_but_unrecordable_device(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _dev_refresh_ready=True,
            _input_snapshot=_snapshot(),
            _last_menu_refresh_time=0.0,
            _device_change_generation=0,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=False, no_device=True),
            ),
            _recorder_prepare_worker=None,
            _device_change_in_storm=lambda: False,
            _sync_system_default_device=lambda: calls.append("sync"),
            _auto_fallback_if_device_gone=lambda: calls.append("fallback"),
            _recover_recorder_if_devices_returned=lambda: calls.append("recover"),
            _reopen_output_after_device_rescan=lambda: None,
            _rebuild_device_menu=lambda: calls.append(
                ("rebuild", tray._recordable_retry.active)
            ),
            _sync_tray_icon_with_engine=lambda: calls.append("icon"),
            _start_async_refresh=lambda: calls.append("refresh"),
        )
        _bind_device_helpers(tray)
        _bind_recordable_retry_state(tray, activate_on_schedule=True)

        VoiceTray._on_refresh_result(
            tray,
            _snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": None,
                    },
                ],
                recordable_default_name="",
            ),
        )

        self.assertEqual(len(tray._recordable_retry.scheduled_snapshots), 1)
        self.assertTrue(tray._recordable_retry.active)
        self.assertIn(("rebuild", True), calls)

    def test_stale_refresh_result_is_ignored(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _device_change_generation=2,
            _dev_refresh_running=True,
            _dev_refresh_repeat=False,
            _finish_device_refresh=lambda: VoiceTray._finish_device_refresh(tray),
            _sync_system_default_device=lambda: calls.append("sync"),
            _rebuild_device_menu=lambda: calls.append("rebuild"),
        )

        VoiceTray._on_refresh_result(
            tray,
            1,
            _snapshot("Old Mic", [{"name": "Old Mic", "display_name": "Old Mic", "index": 1}]),
        )

        self.assertEqual(calls, [])
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
            _config=SimpleNamespace(mic_name="", mic_index=None),
            _input_snapshot=_snapshot(),
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
        _bind_device_helpers(tray)
        tray._recorder_is_ready = lambda: False
        tray._begin_recording = lambda source: VoiceTray._begin_recording(tray, source)

        VoiceTray._request_record_start(tray, "tray_or_mini")

        self.assertEqual(calls, ["prepare"])
        self.assertEqual(tray._pending_record_start_source, "tray_or_mini")

    def test_request_record_start_uses_recordable_system_default_target(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _config=SimpleNamespace(mic_name="", mic_index=None),
            _input_snapshot=_snapshot(
                "Bluetooth Mic",
                [
                    {
                        "name": "Bluetooth Mic",
                        "display_name": "Bluetooth Mic",
                        "index": 7,
                    },
                ],
                recordable_default_name="Bluetooth Mic",
            ),
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(is_ready=False),
            ),
            _recorder_prepare_worker=None,
            _pending_record_start_source=None,
            _start_recorder_prepare_worker=lambda **kwargs: calls.append(kwargs),
        )
        _bind_device_helpers(tray)
        tray._recorder_is_ready = lambda: False

        VoiceTray._request_record_start(tray, "hotkey")

        self.assertEqual(calls, [{"device_index": None, "preferred_name": ""}])
        self.assertEqual(tray._pending_record_start_source, "hotkey")

    def test_prepare_done_reschedules_pending_prepare(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=True,
            _recorder_prepare_generation=1,
            _recorder_prepare_timer=SimpleNamespace(start=lambda: calls.append("timer")),
            _pending_record_start_source=None,
            _sync_device_fault_after_prepare=lambda ok: None,
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNotNone(tray._recorder_prepare_worker)
        self.assertFalse(tray._recorder_prepare_pending)
        self.assertEqual(calls, ["timer"])

    def test_set_default_device_skips_when_already_default(self):
        from ui.tray import VoiceTray

        calls = []
        config = SimpleNamespace(mic_name="", mic_index=None, save=lambda: (_ for _ in ()).throw(RuntimeError("save")))
        tray = SimpleNamespace(
            _config=config,
            _engine=SimpleNamespace(state="recording"),
            _sync_device_menu_checks=lambda: calls.append("sync"),
            _pending_device_apply=False,
            _rebuild_device_menu=lambda: (_ for _ in ()).throw(RuntimeError("rebuild")),
            _clear_device_fault=lambda: (_ for _ in ()).throw(RuntimeError("clear")),
            _sync_tray_icon_with_engine=lambda: (_ for _ in ()).throw(RuntimeError("icon")),
            show_tray_message=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("message")),
        )

        VoiceTray._set_default_device(tray)

        self.assertEqual(calls, ["sync"])

    def test_set_device_skips_when_already_selected(self):
        from ui.tray import VoiceTray

        calls = []
        config = SimpleNamespace(
            mic_name="Headphones",
            mic_index=1,
            save=lambda: (_ for _ in ()).throw(RuntimeError("save")),
        )
        tray = SimpleNamespace(
            _config=config,
            _engine=SimpleNamespace(state="recording"),
            _sync_device_menu_checks=lambda: calls.append("sync"),
            _pending_device_apply=False,
            _rebuild_device_menu=lambda: (_ for _ in ()).throw(RuntimeError("rebuild")),
            _clear_device_fault=lambda: (_ for _ in ()).throw(RuntimeError("clear")),
            _sync_tray_icon_with_engine=lambda: (_ for _ in ()).throw(RuntimeError("icon")),
            show_tray_message=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("message")),
        )

        VoiceTray._set_device(tray, "Headphones", 1)

        self.assertEqual(calls, ["sync"])

    def test_reselecting_default_device_action_keeps_it_checked(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(
                mic_name="",
                mic_index=None,
                save=lambda: (_ for _ in ()).throw(RuntimeError("save")),
            ),
            _input_snapshot=_snapshot(
                "Built-in Mic",
                [{"name": "Built-in Mic", "display_name": "Built-in Mic", "index": 1}],
            ),
            _dev_menu_dirty=True,
            _device_menu=menu,
            _engine=SimpleNamespace(state="ready"),
            contextMenu=lambda: None,
        )
        _bind_recordable_retry_state(tray)
        tray._sync_device_menu_checks = lambda: VoiceTray._sync_device_menu_checks(tray)
        tray._set_default_device = lambda: VoiceTray._set_default_device(tray)
        tray._set_device = lambda name, idx=None: VoiceTray._set_device(tray, name, idx)

        VoiceTray._rebuild_device_menu(tray)
        default_action = next(action for action in menu.actions() if action.isCheckable())

        self.assertTrue(default_action.isChecked())
        default_action.trigger()

        self.assertTrue(default_action.isChecked())
        self.assertEqual(tray._config.mic_name, "")

    def test_reselecting_device_action_keeps_it_checked(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        tray = SimpleNamespace(
            _config=SimpleNamespace(
                mic_name="Headphones",
                mic_index=2,
                save=lambda: (_ for _ in ()).throw(RuntimeError("save")),
            ),
            _input_snapshot=_snapshot(
                "Built-in Mic",
                [
                    {"name": "Built-in Mic", "display_name": "Built-in Mic", "index": 1},
                    {"name": "Headphones", "display_name": "Headphones", "index": 2},
                ],
            ),
            _dev_menu_dirty=True,
            _device_menu=menu,
            _engine=SimpleNamespace(state="ready"),
            contextMenu=lambda: None,
        )
        _bind_recordable_retry_state(tray)
        tray._sync_device_menu_checks = lambda: VoiceTray._sync_device_menu_checks(tray)
        tray._set_default_device = lambda: VoiceTray._set_default_device(tray)
        tray._set_device = lambda name, idx=None: VoiceTray._set_device(tray, name, idx)

        VoiceTray._rebuild_device_menu(tray)
        device_action = next(action for action in menu.actions() if action.text() == "Headphones")

        self.assertTrue(device_action.isChecked())
        device_action.trigger()

        self.assertTrue(device_action.isChecked())
        self.assertEqual(tray._config.mic_name, "Headphones")

    def test_set_mode_skips_when_unchanged(self):
        from ui.tray import VoiceTray

        calls = []
        config = SimpleNamespace(mode="polish", save=lambda: (_ for _ in ()).throw(RuntimeError("save")))
        tray = SimpleNamespace(
            _config=config,
            _sync_mode_menu=lambda: calls.append("sync"),
            _mini=SimpleNamespace(sync_mode=lambda: (_ for _ in ()).throw(RuntimeError("mini"))),
        )

        VoiceTray._set_mode(tray, "polish")

        self.assertEqual(calls, ["sync"])

    def test_reselecting_mode_restores_checked_action(self):
        from ui.tray import VoiceTray

        menu = QMenu()
        transcribe = QAction("纯转录", menu)
        transcribe.setCheckable(True)
        transcribe.setData("transcribe")
        menu.addAction(transcribe)
        polish = QAction("智能润色", menu)
        polish.setCheckable(True)
        polish.setData("polish")
        polish.setChecked(False)
        menu.addAction(polish)
        tray = SimpleNamespace(
            _config=SimpleNamespace(
                mode="polish",
                save=lambda: (_ for _ in ()).throw(RuntimeError("save")),
            ),
            _mode_menu=menu,
            _mini=SimpleNamespace(sync_mode=lambda: (_ for _ in ()).throw(RuntimeError("mini"))),
        )
        tray._sync_mode_menu = lambda: VoiceTray._sync_mode_menu(tray)

        VoiceTray._set_mode(tray, "polish")

        self.assertFalse(transcribe.isChecked())
        self.assertTrue(polish.isChecked())

    def test_prepare_done_starts_pending_recording_when_idle(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(state="ready"),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=False,
            _recorder_prepare_generation=1,
            _pending_record_start_source="hotkey",
            _begin_recording=lambda source: calls.append(source),
            _sync_device_fault_after_prepare=lambda ok: None,
            _recorder_is_ready=lambda: True,
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNotNone(tray._recorder_prepare_worker)
        self.assertIsNone(tray._pending_record_start_source)
        self.assertEqual(calls, ["hotkey"])

    def test_prepare_done_reports_device_fault_when_pending_record_still_unready(self):
        from ui.tray import VoiceTray

        calls = []
        tray = SimpleNamespace(
            _engine=SimpleNamespace(
                state="ready",
                mic_unavailable=SimpleNamespace(
                    emit=lambda message: calls.append(("fault", message))
                ),
            ),
            _recorder_prepare_worker=object(),
            _recorder_prepare_pending=False,
            _recorder_prepare_generation=1,
            _pending_record_start_source="hotkey",
            _begin_recording=lambda source: calls.append(("begin", source)),
            _sync_device_fault_after_prepare=lambda ok: None,
            _recorder_is_ready=lambda: False,
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "quick_reopen", True)

        self.assertIsNone(tray._pending_record_start_source)
        self.assertEqual(calls, [("fault", "未找到输入设备")])

    def test_prepare_done_syncs_faults_after_device_recovers(self):
        from ui.tray import VoiceTray

        calls = []
        worker = object()
        tray = SimpleNamespace(
            _engine=SimpleNamespace(
                state="ready",
                recorder=SimpleNamespace(no_device=False),
            ),
            _recorder_prepare_worker=worker,
            _recorder_prepare_pending=False,
            _recorder_prepare_generation=1,
            _pending_record_start_source=None,
            _output_reopen_after_device_rescan=False,
            _faults=SimpleNamespace(
                sync_device_from_recorder=lambda: calls.append("sync_device"),
            ),
            _reopen_output_after_device_rescan=lambda **kwargs: calls.append(("reopen", kwargs)),
        )
        tray._sync_device_fault_after_prepare = (
            lambda ok: VoiceTray._sync_device_fault_after_prepare(tray, ok)
        )

        VoiceTray._on_recorder_prepare_done(tray, 1, "set_device", True)

        self.assertIs(tray._recorder_prepare_worker, worker)
        self.assertEqual(calls, ["sync_device", ("reopen", {"after_prepare": True})])

    def test_prepare_worker_reference_is_released_after_finished(self):
        from ui.tray import VoiceTray

        calls = []
        worker = SimpleNamespace(deleteLater=lambda: calls.append("delete"))
        tray = SimpleNamespace(_recorder_prepare_worker=worker)
        tray._forget_background_worker = (
            lambda worker_arg: VoiceTray._forget_background_worker(tray, worker_arg)
        )
        tray._delete_worker_later = (
            lambda worker_arg: VoiceTray._delete_worker_later(tray, worker_arg)
        )

        VoiceTray._on_recorder_prepare_worker_finished(tray)

        self.assertIsNone(tray._recorder_prepare_worker)
        self.assertEqual(calls, ["delete"])


if __name__ == "__main__":
    unittest.main()
