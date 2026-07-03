from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager
import time
from typing import TYPE_CHECKING, TypeVar

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QCoreApplication, QObject, QEvent,
)
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QApplication,
    QDialog, QFileDialog,
)

from config import Config, enabled_polish_model_menu_items
from core.log import logger, log_event, flush_log
from core.engine import VoiceEngine
from core.config_sync import ConfigSync
from core.faults import FaultKind
from core.input_devices import InputDeviceSnapshot
from core.autostart import (
    AutostartWatcher,
    read_enabled as read_autostart_enabled,
    write_enabled as write_autostart_enabled,
)
from core.app_paths import autostart_command
from core.updater import UpdateChecker, UpdateInfo, can_self_update
from core.polisher import DEFAULT_INSTRUCTIONS
from ui.mini_window import MiniRecordingWindow
from ui.sounds import AudioCues
from ui import icons
from ui.hotkey import ComboHotkeyThread, _HotkeyDialog, _hotkey_display
from ui.window_focus import widget_is_foreground
from ui.apikey_dialog import _ApiKeyDialog
from ui.update_ui import (
    _UpdateNotesDialog, _UpdateReadyDialog, _UpdateFailedDialog, _UpdateMenuHelper,
    anchor_left_cascade_submenu, apply_tray_menu_style, install_left_cascade_submenu,
)
from ui.prompt_dialog import _PolishPromptDialog

if TYPE_CHECKING:
    from ui.fault_coordinator import FaultCoordinator
    from ui.notifier import Notifier

_T_modal = TypeVar("_T_modal")


_TRAY_MENU_DEFAULT_DEVICE = "__tray_default_device__"
_TRAY_MENU_DEFAULT_PROMPT = "__tray_default_prompt__"
_DEVICE_CHANGE_DEBOUNCE_MS = 500
_DEVICE_STORM_WINDOW_SEC = 4.0
_DEVICE_STORM_EVENT_LIMIT = 4
_DEVICE_STORM_QUIET_SEC = 2.0
_RECORDABLE_RETRY_DELAYS_MS = (1000, 2000, 4000)
# Shutdown budgets: total grace for background workers to finish cleanly, then
# for the PortAudio release helper thread.  Both waits return almost instantly
# on a healthy machine; the budgets only matter when native audio is wedged.
_QUIT_WORKER_WAIT_MS = 3000
_QUIT_RELEASE_WAIT_SEC = 2.0
_NO_DEVICE_CHANGE = object()
_AUTOSTART_CONFIG_ECHO_MS = 500

# (config field, QAction attribute on VoiceTray)
_TRAY_BOOL_MENU_ACTIONS = (
    ("silence_trim", "_act_silence_trim"),
    ("show_countdown", "_act_show_countdown"),
    ("paste_result", "_act_paste_result"),
    ("show_result_text", "_act_show_result_text"),
    ("mini_bar_show_timer", "_act_mini_bar_timer"),
    ("hide_mini_window_when_idle", "_act_hide_idle_mini"),
    ("save_audio", "_act_save_audio"),
)


def _set_action_checked(action: QAction, checked: bool) -> None:
    action.blockSignals(True)
    action.setChecked(checked)
    action.blockSignals(False)


def _sync_checkable_actions(
    actions,
    selected_key,
    *,
    fallback_key=None,
) -> bool:
    """Project config-backed single selection onto checkable menu actions."""
    matched = False
    fallback_action = None
    for action in actions:
        if action.isSeparator() or not action.isCheckable():
            continue
        key = action.data()
        if fallback_key is not None and key == fallback_key:
            fallback_action = action
        checked = key == selected_key
        if checked:
            matched = True
        _set_action_checked(action, checked)

    if not matched and fallback_action is not None:
        _set_action_checked(fallback_action, True)
        matched = True
    return matched


def _device_menu_selected_key(config, snapshot: InputDeviceSnapshot):
    mic_name = getattr(config, "mic_name", "") or ""
    if mic_name and snapshot.find_by_name(mic_name) is not None:
        return mic_name
    return _TRAY_MENU_DEFAULT_DEVICE


def _sync_device_menu_actions(actions, config, snapshot: InputDeviceSnapshot) -> bool:
    return _sync_checkable_actions(
        actions,
        _device_menu_selected_key(config, snapshot),
        fallback_key=_TRAY_MENU_DEFAULT_DEVICE,
    )


class _ForegroundHotkeyGateFilter(QObject):
    """When a gated modeless dialog activates/deactivates, resync recorder hotkey pause."""

    def __init__(self, tray: "VoiceTray"):
        super().__init__(tray)
        self._tray = tray

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in (
            QEvent.Type.WindowActivate,
            QEvent.Type.WindowDeactivate,
            QEvent.Type.ActivationChange,
            QEvent.Type.Hide,
            QEvent.Type.Show,
        ):
            self._tray._schedule_foreground_hotkey_sync()
        return False


class _DeviceRefreshWorker(QThread):
    """Enumerate audio devices off the main thread."""
    result_ready = pyqtSignal(int, object)  # generation, InputDeviceSnapshot

    def __init__(
        self,
        generation: int,
        *,
        open_probe: bool = True,
        recorder=None,
        audio=None,
    ):
        super().__init__()
        self._generation = generation
        self._open_probe = open_probe
        # When provided, drop these PyAudio clients *before* enumerating so the
        # probe sees the fresh process-wide device table.  Performed here (worker
        # thread) instead of on the GUI thread so the tray/event loop never
        # blocks inside native PortAudio teardown during audio-device churn.
        self._recorder = recorder
        self._audio = audio

    def run(self):
        from core.input_devices import get_input_device_snapshot

        if self._recorder is not None:
            try:
                self._recorder.reset_portaudio("audio device changed")
            except Exception:
                logger.opt(exception=True).warning("[Tray] Input PortAudio reset failed")
        if self._audio is not None:
            try:
                self._audio.reset_for_device_rescan()
            except Exception:
                logger.opt(exception=True).warning("[Tray] Output PortAudio reset failed")

        try:
            snapshot = get_input_device_snapshot(open_probe=self._open_probe)
        except Exception:
            logger.opt(exception=True).warning("[Tray] Device refresh failed")
            snapshot = InputDeviceSnapshot.empty()
        self.result_ready.emit(self._generation, snapshot)


class _RecorderPrepareWorker(QThread):
    """Warm the recorder off the UI thread after device changes settle."""
    prepare_done = pyqtSignal(int, str, bool)  # generation, action, ok

    def __init__(
        self,
        generation: int,
        recorder,
        *,
        device_index=_NO_DEVICE_CHANGE,
        preferred_name: str = "",
        invalidate_reason: str = "",
    ):
        super().__init__()
        self._generation = generation
        self._recorder = recorder
        self._device_index = device_index
        self._preferred_name = preferred_name
        self._invalidate_reason = invalidate_reason

    def run(self):
        try:
            if self._device_index is not _NO_DEVICE_CHANGE:
                self._recorder.set_device(self._device_index, self._preferred_name)
                action = "set_device"
            else:
                if self._invalidate_reason:
                    self._recorder.invalidate_stream(self._invalidate_reason)
                action = self._recorder.ensure_prepared()
            self.prepare_done.emit(self._generation, action, True)
        except Exception as e:
            logger.warning(f"[Tray] Deferred recorder prepare failed: {e}")
            self.prepare_done.emit(self._generation, "failed", False)


class _RecordableProbeRetry:
    """Retry while Windows sees an endpoint before PyAudio can open it."""

    def __init__(self, refresh: Callable[[], None]):
        self._attempt = 0
        self._active = False
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(refresh)

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._attempt = 0
        self._active = False
        self._timer.stop()

    def stop(self) -> None:
        self._timer.stop()

    def schedule_for(self, snapshot: InputDeviceSnapshot) -> None:
        if not self.needs_retry(snapshot):
            self.reset()
            return
        if self._attempt >= len(_RECORDABLE_RETRY_DELAYS_MS):
            self._active = False
            return
        delay_ms = _RECORDABLE_RETRY_DELAYS_MS[self._attempt]
        self._attempt += 1
        self._active = True
        logger.info(
            f"[Tray] Visible input endpoint is not recordable yet; "
            f"retrying device probe in {delay_ms}ms "
            f"({self._attempt}/{len(_RECORDABLE_RETRY_DELAYS_MS)})"
        )
        self._timer.start(delay_ms)

    @staticmethod
    def needs_retry(snapshot: InputDeviceSnapshot) -> bool:
        if not snapshot.devices:
            return False
        if not snapshot.has_recordable_device:
            return True
        return bool(snapshot.default_name and not snapshot.recordable_default_name)


class VoiceTray(QSystemTrayIcon):

    def __init__(
        self,
        engine: VoiceEngine,
        mini: MiniRecordingWindow,
        config: Config,
        config_sync: ConfigSync | None = None,
    ):
        super().__init__()
        self._engine = engine
        self._mini = mini
        self._config = config
        self._config_sync = config_sync
        self._prompt_dlg: _PolishPromptDialog | None = None
        self._apikey_dlg: _ApiKeyDialog | None = None
        self._update_notes_dlg: _UpdateNotesDialog | None = None
        self._update_ready_dlg: _UpdateReadyDialog | None = None
        self._update_failed_dlg: _UpdateFailedDialog | None = None
        self._update_status = "idle"  # idle | downloading | ready
        self._hotkey_pause_depth = 0
        self._foreground_hotkey_pause_active = False
        self._hotkey_listener_suppressed = False
        self._fg_gated_dialogs: list[QDialog] = []
        self._fg_hotkey_filter = _ForegroundHotkeyGateFilter(self)
        self._fg_hotkey_sync_timer = QTimer(self)
        self._fg_hotkey_sync_timer.setSingleShot(True)
        self._fg_hotkey_sync_timer.setInterval(0)
        self._fg_hotkey_sync_timer.timeout.connect(self._sync_foreground_hotkey_gate)
        self._fg_hotkey_poll = QTimer(self)
        self._fg_hotkey_poll.setInterval(150)
        self._fg_hotkey_poll.timeout.connect(self._sync_foreground_hotkey_gate)

        self._audio = AudioCues()
        self._audio.set_enabled(config.play_sounds)

        self._faults: FaultCoordinator | None = None
        self._notifier: Notifier | None = None
        self._pending_device_apply = False
        self._device_change_times: list[float] = []
        self._device_storm_until = 0.0
        self._device_change_generation = 0
        self._active_refresh_generation = 0
        self._recorder_prepare_worker: _RecorderPrepareWorker | None = None
        self._recorder_prepare_pending = False
        self._recorder_prepare_pending_job: dict | None = None
        self._pending_record_start_source: str | None = None
        self._recorder_prepare_generation = 0
        self._recorder_prepare_timer = QTimer()
        self._recorder_prepare_timer.setSingleShot(True)
        self._recorder_prepare_timer.setInterval(200)
        self._recorder_prepare_timer.timeout.connect(self._deferred_recorder_prepare)
        self._device_change_timer = QTimer()
        self._device_change_timer.setSingleShot(True)
        self._device_change_timer.setInterval(_DEVICE_CHANGE_DEBOUNCE_MS)
        self._device_change_timer.timeout.connect(self._start_async_refresh)
        self._recordable_retry = _RecordableProbeRetry(self._retry_input_recordable_probe)
        self._input_rescan_needs_portaudio_reset = False
        self._output_reopen_after_device_rescan = False
        self._ignore_autostart_config_echo = False
        self._sync_autostart_state()
        self.refresh_idle_icon()

        self._autostart_watcher = AutostartWatcher(self)
        self._autostart_watcher.changed.connect(
            self._sync_autostart_state,
            Qt.ConnectionType.QueuedConnection,
        )
        self._autostart_watcher.start()

        self._build_menu()

        # Cold-start mic warm-up, off the GUI thread (a wedged audio subsystem
        # once stalled prepare() for 42s).  Using the prepare worker also lets
        # an early hotkey press queue recording until warm-up completes.
        self._start_recorder_prepare_worker(**self._recorder_prepare_target())

        if config.tray_click_to_record:
            self.activated.connect(self._on_activated)

        engine.state_changed.connect(self._on_state)
        engine.transcription_done.connect(self._on_done)
        engine.countdown_tick.connect(self._on_countdown_tick)

        mini.request_record.connect(self._on_tray_click)
        mini.request_stop.connect(self._on_tray_click)
        mini.request_cancel.connect(self._on_cancel)
        mini.request_history.connect(self._open_history)
        mini.mode_changed.connect(self._on_mini_mode_changed)
        mini.show_result_changed.connect(self._on_mini_show_result_changed)

        self._hotkey_hold_active = False
        self._hotkey = ComboHotkeyThread(config.hotkey)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.released.connect(self._on_hotkey_release)
        self._hotkey.start()

        from core.device_watcher import AudioDeviceWatcher
        self._device_watcher = AudioDeviceWatcher()
        self._device_watcher.signals.changed.connect(self._on_audio_device_changed)
        self._device_watcher.start()

        self._can_update = can_self_update()
        if self._can_update:
            self._updater = UpdateChecker()
            self._updater.start(
                on_available=self._on_update_available,
                on_no_update=self._on_no_update,
                on_check_failed=self._on_check_failed,
                on_dl_progress=self._on_download_progress,
                on_dl_done=self._on_download_done,
                on_dl_failed=self._on_download_failed,
                on_stage_progress=self._on_stage_progress,
                on_stage_done=self._on_stage_done,
                on_stage_failed=self._on_stage_failed,
            )
        else:
            self._updater = None
            self._update_widget.set_unsupported()
            logger.info("[Tray] Self-update not available for this launch mode")

    def reveal(self) -> None:
        """Show tray icon — call only after startup config checks pass."""
        if not self.isVisible():
            self.show()

    def set_fault_coordinator(self, coordinator: FaultCoordinator) -> None:
        self._faults = coordinator

    def set_notifier(self, notifier: Notifier) -> None:
        self._notifier = notifier

    @property
    def config(self) -> Config:
        return self._config

    def open_api_key_dialog(self) -> None:
        self._configure_apikey()

    def _build_menu(self):
        menu = QMenu()
        apply_tray_menu_style(menu)

        self._act_record = QAction("开始录音", menu)
        self._act_record.triggered.connect(self._on_tray_click)
        menu.addAction(self._act_record)

        self._act_rec_info = QAction("", menu)
        self._act_rec_info.setEnabled(False)
        self._act_rec_info.setVisible(False)
        menu.addAction(self._act_rec_info)

        self._rec_info_timer = QTimer(self)
        self._rec_info_timer.setInterval(1000)
        self._rec_info_timer.timeout.connect(self._update_rec_info)

        self._act_upload = QAction("上传音频文件", menu)
        self._act_upload.triggered.connect(self._on_upload_audio)
        menu.addAction(self._act_upload)

        # ── 设备与模式 ──
        menu.addSeparator()

        self._device_menu = QMenu("输入设备", menu)
        apply_tray_menu_style(self._device_menu)
        install_left_cascade_submenu(self._device_menu, menu)
        self._device_menu.aboutToShow.connect(self._on_device_menu_show)
        self._input_snapshot = InputDeviceSnapshot.empty()
        self._dev_refresh_running = False
        self._dev_refresh_repeat = False
        self._dev_refresh_ready = False
        self._dev_refresh_worker: _DeviceRefreshWorker | None = None
        self._dev_refresh_workers: list[_DeviceRefreshWorker] = []
        self._dev_menu_dirty = True
        menu.addMenu(self._device_menu)

        self._mode_menu = QMenu("切换模式", menu)
        apply_tray_menu_style(self._mode_menu)
        install_left_cascade_submenu(self._mode_menu, menu)
        for mode_id, mode_name in [("transcribe", "纯转录"), ("polish", "智能润色")]:
            act = QAction(mode_name, self._mode_menu)
            act.setCheckable(True)
            act.setData(mode_id)
            act.setChecked(self._config.mode == mode_id)
            act.triggered.connect(lambda checked, m=mode_id: self._set_mode(m))
            self._mode_menu.addAction(act)
        menu.addMenu(self._mode_menu)

        self._polish_model_menu = QMenu("润色模型", menu)
        apply_tray_menu_style(self._polish_model_menu)
        install_left_cascade_submenu(self._polish_model_menu, menu)
        self._populate_polish_menu()
        menu.addMenu(self._polish_model_menu)

        self._prompt_menu = QMenu("自定义提示词", menu)
        apply_tray_menu_style(self._prompt_menu)
        install_left_cascade_submenu(self._prompt_menu, menu)
        menu.addMenu(self._prompt_menu)

        # ── 录音参数 ──
        menu.addSeparator()

        self._duration_menu = QMenu("录音上限", menu)
        apply_tray_menu_style(self._duration_menu)
        install_left_cascade_submenu(self._duration_menu, menu)
        self._duration_presets = [
            (300, "5 分钟"),
            (600, "10 分钟"),
            (1200, "20 分钟"),
        ]
        for dur_sec, display in self._duration_presets:
            act = QAction(display, self._duration_menu)
            act.setCheckable(True)
            act.setData(dur_sec)
            act.setChecked(self._config.smart_chunk_max_duration_sec == dur_sec)
            act.triggered.connect(
                lambda checked, d=dur_sec: self._set_max_duration(d))
            self._duration_menu.addAction(act)
        menu.addMenu(self._duration_menu)

        self._act_silence_trim = QAction("静音压缩", menu)
        self._act_silence_trim.setCheckable(True)
        self._act_silence_trim.setChecked(self._config.silence_trim)
        self._act_silence_trim.triggered.connect(self._toggle_silence_trim)
        menu.addAction(self._act_silence_trim)

        self._act_show_countdown = QAction("录音结束倒计时", menu)
        self._act_show_countdown.setCheckable(True)
        self._act_show_countdown.setChecked(self._config.show_countdown)
        self._act_show_countdown.triggered.connect(self._toggle_show_countdown)
        menu.addAction(self._act_show_countdown)

        self._act_paste_result = QAction("自动粘贴", menu)
        self._act_paste_result.setCheckable(True)
        self._act_paste_result.setChecked(self._config.paste_result)
        self._act_paste_result.triggered.connect(self._toggle_paste_result)
        menu.addAction(self._act_paste_result)

        self._mini_bar_menu = QMenu("磁吸栏", menu)
        apply_tray_menu_style(self._mini_bar_menu)
        install_left_cascade_submenu(self._mini_bar_menu, menu)
        self._act_show_result_text = QAction("识别后显示原文", self._mini_bar_menu)
        self._act_show_result_text.setCheckable(True)
        self._act_show_result_text.setChecked(self._config.show_result_text)
        self._act_show_result_text.triggered.connect(self._toggle_show_result_text)
        self._mini_bar_menu.addAction(self._act_show_result_text)
        self._act_mini_bar_timer = QAction("悬停显示录音计时", self._mini_bar_menu)
        self._act_mini_bar_timer.setCheckable(True)
        self._act_mini_bar_timer.setChecked(self._config.mini_bar_show_timer)
        self._act_mini_bar_timer.triggered.connect(self._toggle_mini_bar_timer)
        self._mini_bar_menu.addAction(self._act_mini_bar_timer)
        self._act_hide_idle_mini = QAction("空闲时隐藏磁吸栏", self._mini_bar_menu)
        self._act_hide_idle_mini.setCheckable(True)
        self._act_hide_idle_mini.setChecked(self._config.hide_mini_window_when_idle)
        self._act_hide_idle_mini.triggered.connect(self._toggle_hide_idle_mini)
        self._mini_bar_menu.addAction(self._act_hide_idle_mini)
        self._mini_bar_menu.addSeparator()
        act_reset_pos = QAction("重置磁吸栏位置", self._mini_bar_menu)
        act_reset_pos.triggered.connect(self._reset_mini_position)
        self._mini_bar_menu.addAction(act_reset_pos)
        menu.addMenu(self._mini_bar_menu)

        # ── 记录与日志 ──
        menu.addSeparator()

        act_history = QAction("打开历史记录", menu)
        act_history.triggered.connect(self._open_history)
        menu.addAction(act_history)

        act_log = QAction("查看日志", menu)
        act_log.triggered.connect(self._open_log)
        menu.addAction(act_log)

        act_config = QAction("打开配置文件", menu)
        act_config.triggered.connect(self._open_config_file)
        menu.addAction(act_config)

        self._act_save_audio = QAction("保存录音文件", menu)
        self._act_save_audio.setCheckable(True)
        self._act_save_audio.setChecked(self._config.save_audio)
        self._act_save_audio.triggered.connect(self._toggle_save_audio)
        menu.addAction(self._act_save_audio)

        # ── 系统设置 ──
        menu.addSeparator()

        self._act_autostart = QAction("开机自启", menu)
        self._act_autostart.setCheckable(True)
        self._act_autostart.setChecked(self._config.autostart_enabled)
        self._act_autostart.triggered.connect(self._toggle_autostart)
        menu.addAction(self._act_autostart)

        hotkey_display = _hotkey_display(self._config.hotkey)
        self._act_hotkey = QAction(f"快捷键: {hotkey_display}", menu)
        self._act_hotkey.triggered.connect(self._configure_hotkey)
        menu.addAction(self._act_hotkey)

        act_apikey = QAction("配置 API Key", menu)
        act_apikey.triggered.connect(self._configure_apikey)
        menu.addAction(act_apikey)

        menu.addSeparator()

        self._update_widget = _UpdateMenuHelper(menu)
        self._update_widget.bind(self._on_update_click)

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self._menu_refresh_min_interval = 10.0
        self._last_menu_refresh_time = 0.0
        menu.aboutToShow.connect(self._on_menu_about_to_show)
        self.setContextMenu(menu)

        self._rebuild_prompt_menu()
        self._start_async_refresh()

    # ── device refresh (background thread) ──

    def _on_audio_device_changed(self):
        self._recordable_retry.reset()
        self._input_rescan_needs_portaudio_reset = True
        self._output_reopen_after_device_rescan = True
        self._device_change_generation += 1
        now = time.monotonic()
        self._device_change_times = [
            t for t in self._device_change_times
            if now - t <= _DEVICE_STORM_WINDOW_SEC
        ]
        self._device_change_times.append(now)

        delay_ms = _DEVICE_CHANGE_DEBOUNCE_MS
        if len(self._device_change_times) >= _DEVICE_STORM_EVENT_LIMIT:
            self._device_storm_until = max(
                self._device_storm_until,
                now + _DEVICE_STORM_QUIET_SEC,
            )
            delay_ms = int(_DEVICE_STORM_QUIET_SEC * 1000)
            logger.info(
                f"[Tray] Audio device storm detected "
                f"({len(self._device_change_times)} events/"
                f"{_DEVICE_STORM_WINDOW_SEC:.0f}s), delaying audio apply"
            )
        log_event(
            "INFO",
            "audio.device.change.queued",
            "Audio device change queued",
            generation=self._device_change_generation,
            state=self._engine.state,
            storm_events=len(self._device_change_times),
            debounce_ms=delay_ms,
            storm_until=round(self._device_storm_until, 3),
            input_reset_pending=self._input_rescan_needs_portaudio_reset,
            output_reopen_pending=self._output_reopen_after_device_rescan,
        )
        self._device_change_timer.start(delay_ms)

    def _device_change_in_storm(self) -> bool:
        return time.monotonic() < self._device_storm_until

    def _on_menu_about_to_show(self):
        self._sync_config_backed_menu_checks()
        # Device-change notifications (hot-plug, default switch, state change)
        # already drive refreshes, so opening the menu must not enumerate:
        # the enumeration open-probe briefly opens every capture endpoint,
        # which flips Bluetooth headsets into their HFP/duplex profile and
        # audibly degrades playback while the menu is open.
        if self._device_watcher.is_listening:
            return
        # Fallback: notification registration failed — poll on menu open.
        now = time.monotonic()
        if now - self._last_menu_refresh_time < self._menu_refresh_min_interval:
            return
        self._last_menu_refresh_time = now
        self._start_async_refresh()

    def _retry_input_recordable_probe(self):
        self._input_rescan_needs_portaudio_reset = True
        self._start_async_refresh()

    def _start_async_refresh(self):
        if self._dev_refresh_running:
            self._dev_refresh_repeat = True
            return
        open_probe = self._engine.state != "recording"
        # Decide PortAudio resets on the GUI thread, but let the worker perform
        # them (before enumerating) so native teardown never blocks the tray.
        reset_input = open_probe and self._input_rescan_needs_portaudio_reset
        reset_output = open_probe and self._output_reopen_after_device_rescan
        if reset_input:
            self._input_rescan_needs_portaudio_reset = False
        self._dev_refresh_running = True
        generation = self._device_change_generation
        self._active_refresh_generation = generation
        log_event(
            "DEBUG",
            "audio.device.refresh.start",
            "Audio device refresh started",
            generation=generation,
            state=self._engine.state,
            open_probe=open_probe,
            reset_input=reset_input,
            reset_output=reset_output,
        )
        worker = _DeviceRefreshWorker(
            generation,
            open_probe=open_probe,
            recorder=self._engine.recorder if reset_input else None,
            audio=self._audio if reset_output else None,
        )
        worker.result_ready.connect(self._on_refresh_result)
        worker.finished.connect(self._on_device_refresh_worker_finished)
        self._dev_refresh_workers.append(worker)
        self._dev_refresh_worker = worker
        worker.start()

    def _on_device_refresh_worker_finished(self):
        worker = self.sender() if hasattr(self, "sender") else None
        if worker is None:
            worker = self._dev_refresh_worker
        self._forget_background_worker(worker)
        self._delete_worker_later(worker)

    def _on_refresh_result(self, generation_or_snapshot, snapshot: InputDeviceSnapshot | None = None):
        generation = None
        if snapshot is None:
            snapshot = generation_or_snapshot
        else:
            generation = int(generation_or_snapshot)

        if generation is not None and generation < self._device_change_generation:
            log_event(
                "DEBUG",
                "audio.device.refresh.stale",
                "Ignoring stale device refresh",
                generation=generation,
                current_generation=self._device_change_generation,
            )
            self._finish_device_refresh()
            return

        self._input_snapshot = snapshot
        self._dev_refresh_ready = True
        self._last_menu_refresh_time = time.monotonic()
        self._dev_menu_dirty = True
        log_event(
            "INFO",
            "audio.device.refresh.end",
            "Audio device refresh finished",
            generation=generation if generation is not None else self._device_change_generation,
            default=snapshot.default_name,
            devices=len(snapshot.devices),
            recordable_devices=len(snapshot.recordable_devices),
            has_recordable=snapshot.has_recordable_device,
        )
        defer_audio_apply = self._device_change_in_storm()
        if defer_audio_apply:
            log_event(
                "INFO",
                "audio.device.apply.deferred",
                "Device list refreshed during storm; audio apply deferred",
                generation=generation if generation is not None else self._device_change_generation,
            )
            self._queue_recorder_recovery_after_storm()
        else:
            self._sync_system_default_device()
            self._auto_fallback_if_device_gone()
            self._recover_recorder_if_devices_returned()
        self._recordable_retry.schedule_for(snapshot)
        self._rebuild_device_menu()
        self._sync_tray_icon_with_engine()
        repeat_started = self._finish_device_refresh()
        if repeat_started:
            return
        if (
            not defer_audio_apply
            and self._engine.state != "recording"
            and self._recorder_prepare_worker is None
            and not self._engine.recorder.is_ready
            and self._input_snapshot.has_recordable_device
        ):
            self._start_recorder_prepare_worker(**self._recorder_prepare_target())
        if not defer_audio_apply:
            self._reopen_output_after_device_rescan()

    def _finish_device_refresh(self) -> bool:
        self._dev_refresh_running = False
        if self._dev_refresh_repeat:
            self._dev_refresh_repeat = False
            self._start_async_refresh()
            return True
        return False

    def _reopen_output_after_device_rescan(self, *, after_prepare: bool = False):
        if not self._output_reopen_after_device_rescan:
            return
        if self._device_change_in_storm():
            return
        if self._engine.state == "recording":
            return
        if self._recorder_prepare_worker is not None and not after_prepare:
            return
        if self._recorder_prepare_pending:
            return
        self._output_reopen_after_device_rescan = False
        log_event(
            "INFO",
            "audio.output.reopen.scheduled",
            "Output audio reopen scheduled after device rescan",
            after_prepare=after_prepare,
        )
        self._audio.refresh_output_device_async()

    def _on_device_menu_show(self):
        if not self._dev_refresh_ready:
            self._device_menu.clear()
            act = QAction("正在刷新…", self._device_menu)
            act.setEnabled(False)
            self._device_menu.addAction(act)
            return
        if self._dev_menu_dirty:
            self._rebuild_device_menu()

    def _sync_system_default_device(self):
        """If following system default, rebind recorder when default device changed."""
        if self._config.mic_name:
            return
        current_default = self._input_snapshot.recordable_default_name or ""
        recorder_name = self._engine.recorder.device_name or ""
        default_device = self._recordable_system_default_device()
        if (
            not current_default
            or recorder_name in (current_default, getattr(default_device, "display_name", ""))
            or not self._input_snapshot.has_recordable_device
        ):
            return
        if self._engine.state != "recording":
            logger.info(
                f"[Tray] System default device changed: "
                f"'{recorder_name}' -> '{current_default}', "
                f"will reopen on next recording"
            )
            if default_device is not None:
                self._schedule_recorder_device_apply(None, "")
            else:
                self._start_recorder_prepare_worker(
                    invalidate_reason="system default device changed"
                )
        else:
            self._pending_device_apply = True
            logger.info(f"[Tray] System default changed during recording, will apply: '{current_default}'")

    def _auto_fallback_if_device_gone(self):
        """If the selected device is no longer present, switch to system default.

        Device indices are unstable across PyAudio re-init, so we match by
        name.  If the same-named device still exists but its index changed,
        we silently update the stored index.
        """
        if not self._config.mic_name:
            return
        saved_name = self._config.mic_name
        match = self._input_snapshot.find_by_name(saved_name)
        if match is not None:
            if not match.is_recordable:
                logger.info(
                    f"[Tray] Selected device '{saved_name}' is visible but not recordable; "
                    "switching to system default"
                )
            elif match.index != self._config.mic_index:
                old_idx = self._config.mic_index
                self._config.mic_index = match.index
                self._config.save()
                if self._engine.state != "recording":
                    self._schedule_recorder_device_apply(match.index, saved_name)
                else:
                    self._pending_device_apply = True
                logger.info(f"[Tray] Device '{saved_name}' index changed "
                            f"{old_idx} → {match.index}")
                return
            else:
                return
        old = self._config.mic_index
        self._config.mic_index = None
        self._config.mic_name = ""
        self._config.save()
        if self._engine.state != "recording":
            self._start_recorder_prepare_worker(**self._recorder_prepare_target())
        else:
            self._pending_device_apply = True
        logger.info(f"[Tray] Selected device '{saved_name}' (index={old}) unavailable, "
                    f"switched to system default")

    def _deferred_recorder_prepare(self):
        """Warm recorder after stream invalidation settles."""
        if self._engine.state == "recording":
            return
        if self._device_change_in_storm():
            self._recorder_prepare_timer.start()
            return
        job = self._recorder_prepare_pending_job or {}
        self._recorder_prepare_pending_job = None
        self._start_recorder_prepare_worker(**job)

    def _start_recorder_prepare_worker(
        self,
        *,
        device_index=_NO_DEVICE_CHANGE,
        preferred_name: str = "",
        invalidate_reason: str = "",
    ):
        if self._engine.state == "recording":
            return
        if self._recorder_prepare_worker is not None:
            self._recorder_prepare_pending = True
            if device_index is not _NO_DEVICE_CHANGE or invalidate_reason:
                self._recorder_prepare_pending_job = {
                    "device_index": device_index,
                    "preferred_name": preferred_name,
                    "invalidate_reason": invalidate_reason,
                }
            return
        self._recorder_prepare_pending = False
        self._recorder_prepare_generation += 1
        generation = self._recorder_prepare_generation
        worker = _RecorderPrepareWorker(
            generation,
            self._engine.recorder,
            device_index=device_index,
            preferred_name=preferred_name,
            invalidate_reason=invalidate_reason,
        )
        worker.prepare_done.connect(self._on_recorder_prepare_done)
        worker.finished.connect(self._on_recorder_prepare_worker_finished)
        self._recorder_prepare_worker = worker
        logger.info(f"[Tray] Deferred recorder prepare started (gen={generation})")
        worker.start()

    def _on_recorder_prepare_done(self, generation: int, action: str, ok: bool):
        if generation != self._recorder_prepare_generation:
            logger.debug(
                f"[Tray] Ignoring stale recorder prepare "
                f"(gen={generation}, current={self._recorder_prepare_generation})"
            )
            return
        logger.info(
            f"[Tray] Deferred recorder prepare finished "
            f"(gen={generation}, action={action}, ok={ok})"
        )
        self._sync_device_fault_after_prepare(ok)
        if self._recorder_prepare_pending and self._engine.state != "recording":
            self._recorder_prepare_pending = False
            self._recorder_prepare_timer.start()
            return
        if self._pending_record_start_source and self._engine.state == "ready":
            source = self._pending_record_start_source
            self._pending_record_start_source = None
            if not self._recorder_is_ready():
                logger.info(
                    "[Tray] Recorder prepare finished without a usable input; "
                    f"recording not started (source={source})"
                )
                self._engine.mic_unavailable.emit("未找到输入设备")
                return
            self._begin_recording(source)
            return
        self._reopen_output_after_device_rescan(after_prepare=True)

    def _on_recorder_prepare_worker_finished(self):
        worker = self.sender() if hasattr(self, "sender") else None
        if worker is None:
            worker = self._recorder_prepare_worker
        self._forget_background_worker(worker)
        self._delete_worker_later(worker)

    def _sync_device_fault_after_prepare(self, ok: bool):
        if not ok or self._engine.state != "ready":
            return
        if self._faults is not None:
            self._faults.sync_device_from_recorder()
        else:
            self._sync_tray_icon_with_engine()

    def _schedule_recorder_device_apply(self, index: int | None, name: str):
        self._pending_device_apply = False
        self._start_recorder_prepare_worker(
            device_index=index,
            preferred_name=name,
        )

    def _recorder_prepare_target(self) -> dict:
        if self._config.mic_name:
            return {
                "device_index": self._config.mic_index,
                "preferred_name": self._config.mic_name,
            }
        if not self._input_snapshot.has_recordable_device:
            return {}
        return {
            "device_index": None,
            "preferred_name": "",
        }

    def _recordable_system_default_device(self):
        name = self._input_snapshot.recordable_default_name
        return self._input_snapshot.find_by_name(name) if name else None

    def _recorder_is_ready(self) -> bool:
        return self._engine.recorder.is_ready

    def _request_record_start(self, source: str):
        if self._engine.state != "ready":
            return
        if self._recorder_prepare_worker is not None:
            logger.info(
                "[Tray] Recorder prepare in progress; recording will start "
                f"after prepare (source={source})"
            )
            self._pending_record_start_source = source
            return
        if not self._recorder_is_ready():
            logger.info(
                "[Tray] Recorder not pre-warmed; preparing before recording "
                f"(source={source})"
            )
            self._pending_record_start_source = source
            self._start_recorder_prepare_worker(**self._recorder_prepare_target())
            return
        self._begin_recording(source)

    def _begin_recording(self, source: str):
        if self._engine.state != "ready":
            return
        self._audio.play_start(source=source)
        self._engine.toggle_record()

    def _recover_recorder_if_devices_returned(self):
        if self._engine.state == "recording" or not self._engine.recorder.no_device:
            return
        if not self._input_snapshot.has_recordable_device:
            return
        logger.info("[Tray] Input device available again, re-preparing recorder")
        self._start_recorder_prepare_worker(**self._recorder_prepare_target())

    def _queue_recorder_recovery_after_storm(self):
        recorder = getattr(self._engine, "recorder", None)
        if (
            self._engine.state == "recording"
            or recorder is None
            or not getattr(recorder, "no_device", False)
        ):
            return
        if not self._input_snapshot.has_recordable_device:
            return
        logger.info("[Tray] Input device available during storm, recorder recovery deferred")
        self._recorder_prepare_pending_job = self._recorder_prepare_target()
        self._recorder_prepare_timer.start()

    def _rebuild_device_menu(self):
        menu_visible = self._device_menu.isVisible()
        self._device_menu.clear()
        self._dev_menu_dirty = False

        default_name = self._input_snapshot.default_name
        label = f"系统默认 ({default_name})" if default_name else "系统默认"
        act_default = QAction(label, self._device_menu)
        act_default.setCheckable(True)
        act_default.setData(_TRAY_MENU_DEFAULT_DEVICE)
        act_default.triggered.connect(lambda checked: self._set_default_device())
        self._device_menu.addAction(act_default)
        self._device_menu.addSeparator()

        devices = self._input_snapshot.devices
        if not devices:
            act = QAction("(未发现兼容设备)", self._device_menu)
            act.setEnabled(False)
            self._device_menu.addAction(act)
        else:
            for dev in devices:
                if dev.is_recordable:
                    label = dev.display_name
                elif self._recordable_retry.active:
                    label = f"{dev.display_name}（正在初始化）"
                else:
                    label = f"{dev.display_name}（不可录）"
                act = QAction(label, self._device_menu)
                act.setCheckable(True)
                act.setData(dev.name)
                act.setEnabled(dev.is_recordable)
                act.triggered.connect(
                    lambda checked, idx=dev.index, name=dev.name: self._set_device(name, idx)
                )
                self._device_menu.addAction(act)
            if not self._input_snapshot.has_recordable_device:
                self._device_menu.addSeparator()
                act = QAction("(未发现兼容设备)", self._device_menu)
                act.setEnabled(False)
                self._device_menu.addAction(act)

        _sync_device_menu_actions(
            self._device_menu.actions(),
            self._config,
            self._input_snapshot,
        )

        if menu_visible:
            # Keep the popup and its parent-hover relationship intact; only the action
            # list changes, so cursor-leave behavior remains owned by Qt's menu logic.
            self._device_menu.setActiveAction(None)
            parent_menu = self.contextMenu()
            if parent_menu is not None:
                anchor_left_cascade_submenu(self._device_menu, parent_menu)
            else:
                self._device_menu.adjustSize()
            self._device_menu.updateGeometry()
            self._device_menu.update()

    def _sync_device_menu_checks(self):
        _sync_device_menu_actions(
            self._device_menu.actions(),
            self._config,
            self._input_snapshot,
        )

    def _set_mode(self, mode_id: str):
        if self._config.mode == mode_id:
            self._sync_mode_menu()
            return
        self._config.mode = mode_id
        self._config.save()
        self._sync_mode_menu()
        self._mini.sync_mode()
        logger.info(f"[Tray] Mode → {mode_id}")

    def _on_mini_mode_changed(self, mode_id: str):
        self._sync_mode_menu()

    def _on_mini_show_result_changed(self, on: bool):
        _set_action_checked(self._act_show_result_text, on)

    def _sync_config_backed_menu_checks(self) -> None:
        """Refresh checkable menu state in place; never rebuild submenus here."""
        self._sync_autostart_state()
        self._sync_mode_menu()
        self._sync_polish_model_menu_checks()
        self._sync_duration_menu()
        self._sync_prompt_menu_checks()
        if self._dev_refresh_ready and not self._dev_menu_dirty:
            self._sync_device_menu_checks()
        self._sync_bool_config_actions()

    def _sync_bool_config_actions(self, changed: set[str] | None = None) -> None:
        for field, attr in _TRAY_BOOL_MENU_ACTIONS:
            if changed is not None and field not in changed:
                continue
            action = getattr(self, attr, None)
            if action is not None:
                _set_action_checked(action, getattr(self._config, field))

    def _sync_mode_menu(self):
        _sync_checkable_actions(self._mode_menu.actions(), self._config.mode)

    def _rebuild_prompt_menu(self):
        self._prompt_menu.clear()

        act_config = QAction("配置提示词", self._prompt_menu)
        act_config.triggered.connect(self._configure_polish_extra)
        self._prompt_menu.addAction(act_config)
        self._prompt_menu.addSeparator()

        act_none = QAction("默认提示词", self._prompt_menu)
        act_none.setCheckable(True)
        act_none.setData(_TRAY_MENU_DEFAULT_PROMPT)
        act_none.setChecked(not self._config.active_prompt_id)
        act_none.triggered.connect(lambda: self._set_active_prompt(""))
        self._prompt_menu.addAction(act_none)

        for p in self._config.custom_prompts:
            pid, name = p["id"], p.get("name", "未命名")
            act = QAction(name, self._prompt_menu)
            act.setCheckable(True)
            act.setData(pid)
            act.setChecked(self._config.active_prompt_id == pid)
            act.triggered.connect(lambda checked, _id=pid: self._set_active_prompt(_id))
            self._prompt_menu.addAction(act)

    def _sync_prompt_menu_checks(self):
        """仅更新勾选，不在弹出时 clear 子菜单，避免 Windows 上首次展开几何错位。"""
        cur = self._config.active_prompt_id or ""
        selected = cur or _TRAY_MENU_DEFAULT_PROMPT
        _sync_checkable_actions(
            self._prompt_menu.actions(),
            selected,
            fallback_key=_TRAY_MENU_DEFAULT_PROMPT,
        )

    def _set_active_prompt(self, prompt_id: str):
        if (self._config.active_prompt_id or "") == prompt_id:
            self._sync_prompt_menu_checks()
            return
        self._config.active_prompt_id = prompt_id
        self._config.save()
        name = ""
        for p in self._config.custom_prompts:
            if p["id"] == prompt_id:
                name = p.get("name", "")
                break
        logger.info(f"[Tray] Active prompt → {name or '(none)'}")
        self._sync_prompt_menu_checks()
        dlg = self._prompt_dlg
        if dlg is not None:
            dlg.sync_from_config()

    def _configure_polish_extra(self):
        if self._prompt_dlg is not None:
            self._prompt_dlg.raise_()
            self._prompt_dlg.activateWindow()
            return
        self._prompt_menu_rebuilt = False
        dlg = _PolishPromptDialog(
            self._config.custom_prompts,
            self._config.active_prompt_id,
            default_text=DEFAULT_INSTRUCTIONS,
            config=self._config,
            on_active_applied=self._sync_prompt_menu_checks,
            on_prompts_saved=self._on_prompts_menu_rebuilt,
            run_modal_with_hotkey_paused=self.run_modal_with_hotkey_paused,
        )
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.finished.connect(self._on_prompt_dlg_finished)
        self._prompt_dlg = dlg
        self._register_foreground_hotkey_gate(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_prompts_menu_rebuilt(self):
        self._prompt_menu_rebuilt = True
        self._rebuild_prompt_menu()

    def _on_prompt_dlg_finished(self, _result: int):
        # Prompt list is persisted from _PolishPromptDialog._do_save; active_prompt_id
        # may also be updated immediately via「设为当前」.
        # Do not copy dlg state here: dlg.accepted stayed True after a prior save and would
        # re-apply unsaved edits when the user chose「不保存」on close.
        self._prompt_dlg = None
        if not getattr(self, "_prompt_menu_rebuilt", False):
            self._sync_prompt_menu_checks()

    def _menu_parent(self):
        """Parent for modal dialogs so they stay above the tray context."""
        try:
            return QApplication.activeWindow() or QApplication.focusWidget()
        except Exception:
            return None

    def _clear_hotkey_hold_state(self) -> None:
        """卸全局钩子时无法收到 keyup，避免按住态残留影响后续快捷键逻辑。"""
        if not self._hotkey_hold_active:
            return
        self._hotkey_hold_active = False
        self._mini.stop_hotkey_hold()

    def _stop_hotkey_listener(self) -> bool:
        """停止全局 ComboHotkeyThread 并等待 pynput 钩子退出。"""
        self._clear_hotkey_hold_state()
        self._hotkey.stop_hotkey()
        if self._hotkey.wait(2000):
            logger.info("[Hotkey] Listener stopped")
            return True
        logger.warning(
            "[Hotkey] Listener thread did not exit within 2s — "
            "old keyboard hook may linger"
        )
        return False

    def _spawn_hotkey_thread(self, combo: str | None = None) -> None:
        """新建并启动 ComboHotkeyThread（替换 self._hotkey）。调用前应已 stop_hotkey_listener。"""
        key = self._config.hotkey if combo is None else combo
        self._hotkey = ComboHotkeyThread(key)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.released.connect(self._on_hotkey_release)
        self._hotkey.start()
        logger.info(f"[Hotkey] Listener started (combo={key})")

    def _should_suppress_hotkey_listener(self) -> bool:
        return self._hotkey_pause_depth > 0 or self._foreground_hotkey_pause_active

    def _update_hotkey_listener_suppression(self) -> None:
        suppress = self._should_suppress_hotkey_listener()
        if suppress and not self._hotkey_listener_suppressed:
            logger.info(
                f"[Hotkey] Suppressing listener (pause_depth={self._hotkey_pause_depth}, "
                f"fg_gate={self._foreground_hotkey_pause_active})"
            )
            self._stop_hotkey_listener()
            self._hotkey_listener_suppressed = True
        elif not suppress and self._hotkey_listener_suppressed:
            if QCoreApplication.closingDown():
                return
            logger.info("[Hotkey] Resuming listener")
            self._spawn_hotkey_thread()
            self._hotkey_listener_suppressed = False

    def _begin_hotkey_pause(self) -> None:
        self._hotkey_pause_depth += 1
        self._update_hotkey_listener_suppression()

    def _end_hotkey_pause(self) -> None:
        self._hotkey_pause_depth -= 1
        if self._hotkey_pause_depth < 0:
            logger.warning("[Tray] hotkey pause depth underflow")
            self._hotkey_pause_depth = 0
        self._update_hotkey_listener_suppression()

    def _set_foreground_hotkey_pause(self, active: bool) -> None:
        if self._foreground_hotkey_pause_active == active:
            return
        self._foreground_hotkey_pause_active = active
        self._update_hotkey_listener_suppression()

    def _schedule_foreground_hotkey_sync(self) -> None:
        self._fg_hotkey_sync_timer.start()

    def _prune_fg_gated_dialogs(self) -> list[QDialog]:
        self._fg_gated_dialogs = [
            d for d in self._fg_gated_dialogs
            if d is not None and d.isVisible()
        ]
        return self._fg_gated_dialogs

    def _sync_foreground_hotkey_gate(self) -> None:
        dialogs = self._prune_fg_gated_dialogs()
        if not dialogs:
            self._fg_hotkey_poll.stop()
            self._set_foreground_hotkey_pause(False)
            return
        active = any(widget_is_foreground(d) for d in dialogs)
        self._set_foreground_hotkey_pause(active)

    def _register_foreground_hotkey_gate(self, dlg: QDialog) -> None:
        """Pause recorder hotkey only while *dlg* is the system foreground window."""
        if dlg not in self._fg_gated_dialogs:
            self._fg_gated_dialogs.append(dlg)
        dlg.installEventFilter(self._fg_hotkey_filter)
        dlg.finished.connect(lambda: self._unregister_foreground_hotkey_gate(dlg))
        dlg.destroyed.connect(lambda: self._unregister_foreground_hotkey_gate(dlg))
        if not self._fg_hotkey_poll.isActive():
            self._fg_hotkey_poll.start()
        self._schedule_foreground_hotkey_sync()

    def _unregister_foreground_hotkey_gate(self, dlg: QDialog) -> None:
        if dlg in self._fg_gated_dialogs:
            self._fg_gated_dialogs.remove(dlg)
        try:
            dlg.removeEventFilter(self._fg_hotkey_filter)
        except RuntimeError:
            pass
        self._schedule_foreground_hotkey_sync()

    @contextmanager
    def hotkey_paused(self):
        """嵌套安全：模态 UI 期间卸全局热键，退出最后一层时按当前 config.hotkey 恢复。"""
        self._begin_hotkey_pause()
        try:
            yield
        finally:
            self._end_hotkey_pause()

    def run_modal_with_hotkey_paused(self, fn: Callable[[], _T_modal]) -> _T_modal:
        """供提示词等对话框在 QMessageBox.exec 等模态调用外包裹，与 hotkey_paused 同一套 refcount。"""
        with self.hotkey_paused():
            return fn()

    def _polish_model_menu_entries(self) -> list[tuple[str, str]]:
        items = enabled_polish_model_menu_items(
            self._config.polish_models,
            self._config.enabled_polish_models,
        )
        current = self._config.polish_model
        if current and not any(mid == current for mid, _ in items):
            items.insert(0, (current, current))
        return items

    def _populate_polish_menu(self):
        self._polish_model_menu.clear()
        current = self._config.polish_model
        for model_id, display_name in self._polish_model_menu_entries():
            act = QAction(display_name, self._polish_model_menu)
            act.setCheckable(True)
            act.setData(model_id)
            act.setChecked(model_id == current)
            act.triggered.connect(lambda checked, m=model_id: self._set_polish_model(m))
            self._polish_model_menu.addAction(act)

    def _sync_polish_model_menu_checks(self):
        _sync_checkable_actions(
            self._polish_model_menu.actions(),
            self._config.polish_model,
        )

    def _set_polish_model(self, model_id: str):
        if self._config.polish_model == model_id:
            self._sync_polish_model_menu_checks()
            return
        self._config.polish_model = model_id
        self._config.save()
        self._engine.polisher.set_model(model_id)
        self._sync_polish_model_menu_checks()
        logger.info(f"[Tray] Polish model → {model_id}")

    def _configure_hotkey(self):
        self._begin_hotkey_pause()
        try:
            dlg = _HotkeyDialog(self._config.hotkey)
            accepted = (
                dlg.exec() == QDialog.DialogCode.Accepted and bool(dlg.hotkey))
            if accepted:
                combo = dlg.hotkey
                self._config.hotkey = combo
                self._config.save()
                display = _hotkey_display(combo)
                self._act_hotkey.setText(f"快捷键: {display}")
                logger.info(f"[Tray] Hotkey → {display}")
        finally:
            self._end_hotkey_pause()

    def _configure_apikey(self):
        if self._apikey_dlg is not None:
            self._apikey_dlg.raise_()
            self._apikey_dlg.activateWindow()
            return
        dlg = _ApiKeyDialog(self._config.api_key)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.finished.connect(self._on_apikey_dlg_finished)
        self._apikey_dlg = dlg
        self._register_foreground_hotkey_gate(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_apikey_dlg_finished(self, result: int):
        dlg = self._apikey_dlg
        self._apikey_dlg = None
        if dlg is None or result != QDialog.DialogCode.Accepted or dlg.api_key is None:
            return
        self._config.api_key = dlg.api_key
        self._config.save()
        self._engine.asr.api_key = dlg.api_key
        self._engine.polisher.update_api_key(dlg.api_key)
        logger.info("[Tray] API Key updated")
        if self._faults is not None:
            self._faults.set_active(
                FaultKind.CREDENTIAL,
                not bool(dlg.api_key),
                notify=False,
            )
            self.refresh_idle_icon()

    def _toggle_paste_result(self, checked: bool):
        self._config.paste_result = checked
        self._config.save()
        logger.info(f"[Tray] Paste result → {'on' if checked else 'off'}")

    def _toggle_save_audio(self, checked: bool):
        self._config.save_audio = checked
        self._config.save()
        logger.info(f"[Tray] Save audio → {'on' if checked else 'off'}")

    def _toggle_silence_trim(self, checked: bool):
        self._config.silence_trim = checked
        self._config.save()
        logger.info(f"[Tray] Silence trim → {'on' if checked else 'off'}")

    def _toggle_show_countdown(self, checked: bool):
        self._config.show_countdown = checked
        self._config.save()
        logger.info(f"[Tray] Show countdown → {'on' if checked else 'off'}")

    def _toggle_mini_bar_timer(self, checked: bool):
        self._config.mini_bar_show_timer = checked
        self._config.save()
        logger.info(f"[Tray] Mini bar show timer → {'on' if checked else 'off'}")

    def _set_max_duration(self, seconds: int):
        if self._config.smart_chunk_max_duration_sec == seconds:
            self._sync_duration_menu()
            return
        self._config.smart_chunk_max_duration_sec = seconds
        self._config.save()
        self._sync_duration_menu()
        logger.info(f"[Tray] Max recording duration → {seconds}s")

    def _sync_duration_menu(self):
        _sync_checkable_actions(
            self._duration_menu.actions(),
            self._config.smart_chunk_max_duration_sec,
        )

    def _toggle_hide_idle_mini(self, checked: bool):
        self._config.hide_mini_window_when_idle = checked
        self._config.save()
        self._mini.refresh_visibility()
        logger.info(f"[Tray] Hide idle mini window → {'on' if checked else 'off'}")

    def _toggle_show_result_text(self, checked: bool):
        self._config.show_result_text = checked
        self._config.save()
        self._mini.sync_show_result()
        logger.info(f"[Tray] Show result text → {'on' if checked else 'off'}")

    def _clear_autostart_config_echo(self) -> None:
        self._ignore_autostart_config_echo = False

    def _sync_autostart_state(self):
        """Align config + menu with Windows Run/StartupApproved (source of truth)."""
        actual = read_autostart_enabled()
        if self._config.autostart_enabled != actual:
            self._config.autostart_enabled = actual
            self._ignore_autostart_config_echo = True
            try:
                self._config.save(touched=frozenset({"autostart_enabled"}))
            finally:
                QTimer.singleShot(
                    _AUTOSTART_CONFIG_ECHO_MS,
                    self._clear_autostart_config_echo,
                )
        if hasattr(self, "_act_autostart"):
            _set_action_checked(self._act_autostart, actual)

    def _resolve_autostart_command(self) -> str | None:
        return autostart_command()

    def _toggle_autostart(self, checked: bool):
        try:
            command = self._resolve_autostart_command() if checked else ""
            if checked and not command:
                raise RuntimeError("当前运行方式无法确定启动命令，请使用安装包版本后再设置")
            write_autostart_enabled(checked, command)
        except Exception as e:
            logger.warning(f"[Tray] Autostart toggle failed: {e}")
            self.show_tray_message(
                "VoiceInput",
                f"设置开机自启失败：{e}",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
            self._sync_autostart_state()
            return

        self._sync_autostart_state()
        self._autostart_watcher.mark_current()
        logger.info(f"[Tray] Autostart → {'on' if checked else 'off'}")

    def _set_default_device(self):
        if not self._config.mic_name:
            self._sync_device_menu_checks()
            return
        self._config.mic_index = None
        self._config.mic_name = ""
        self._config.save()
        if self._engine.state == "recording":
            self._pending_device_apply = True
            logger.info("[Tray] Input device saved for next session → system default")
            self.show_tray_message(
                "VoiceInput", "输入设备已切换，下次录音生效",
                QSystemTrayIcon.MessageIcon.Information, 2000,
            )
        else:
            self._pending_device_apply = False
            self._schedule_recorder_device_apply(None, "")
            logger.info("[Tray] Input device → system default (index=None)")
        self._rebuild_device_menu()
        self._clear_device_fault()
        self._sync_tray_icon_with_engine()

    def _set_device(self, name: str, idx: int | None = None):
        if self._config.mic_name == name:
            self._sync_device_menu_checks()
            return
        self._config.mic_name = name
        self._config.mic_index = idx
        self._config.save()
        if self._engine.state == "recording":
            self._pending_device_apply = True
            logger.info(f"[Tray] Input device saved for next session "
                        f"→ {name} (index={idx})")
            self.show_tray_message(
                "VoiceInput", "输入设备已切换，下次录音生效",
                QSystemTrayIcon.MessageIcon.Information, 2000,
            )
        else:
            self._pending_device_apply = False
            self._schedule_recorder_device_apply(idx, name)
            logger.info(f"[Tray] Input device → {name} (index={idx})")
        self._rebuild_device_menu()
        self._clear_device_fault()
        self._sync_tray_icon_with_engine()

    def _maybe_apply_deferred_input_device(self):
        if not self._pending_device_apply or self._engine.state == "recording":
            return
        self._pending_device_apply = False
        idx = self._config.mic_index
        name = self._config.mic_name
        self._schedule_recorder_device_apply(idx, name)
        logger.info(f"[Tray] Deferred input device applied (name='{name or 'system default'}', index={idx})")

    def is_idle_for_config_reload(self) -> bool:
        return self._engine.state == "ready"

    def on_config_reloaded(self, changed: frozenset) -> None:
        """Handle external config.json edits: refresh UI and runtime services."""
        self._apply_config_reload(set(changed))

    def _apply_config_reload(self, changed: set[str]) -> None:
        if not changed:
            return

        self._refresh_ui_from_config(changed)

        engine_fields = changed & {
            "api_key", "asr_model", "api_base_url", "polish_model",
            "mic_name", "mic_index",
        }
        mic_fields = engine_fields & {"mic_name", "mic_index"}
        non_mic_fields = engine_fields - mic_fields
        if non_mic_fields:
            self._engine.apply_config(non_mic_fields)
        if mic_fields:
            if self._engine.state == "recording":
                self._pending_device_apply = True
            else:
                self._schedule_recorder_device_apply(
                    self._config.mic_index,
                    self._config.mic_name,
                )

        self._mini.apply_config(changed)

        if "hotkey" in changed:
            self._apply_hotkey_from_config()

    def _apply_hotkey_from_config(self) -> None:
        combo = self._config.hotkey
        self._act_hotkey.setText(f"快捷键: {_hotkey_display(combo)}")
        self._stop_hotkey_listener()
        self._spawn_hotkey_thread(combo)
        logger.info(f"[Tray] Hotkey reloaded → {_hotkey_display(combo)}")

    def _refresh_ui_from_config(self, changed: set[str]) -> None:
        """Sync tray menu / audio state after config reload."""

        if "mode" in changed:
            self._sync_mode_menu()

        if changed & {"polish_models", "enabled_polish_models"}:
            self._populate_polish_menu()
        elif "polish_model" in changed:
            self._sync_polish_model_menu_checks()

        if "custom_prompts" in changed:
            self._rebuild_prompt_menu()
            dlg = self._prompt_dlg
            if dlg is not None:
                dlg.sync_from_config()
        elif "active_prompt_id" in changed:
            self._sync_prompt_menu_checks()
            dlg = self._prompt_dlg
            if dlg is not None:
                dlg.sync_from_config()

        if "smart_chunk_max_duration_sec" in changed:
            self._sync_duration_menu()

        self._sync_bool_config_actions(changed)

        if "autostart_enabled" in changed and not self._ignore_autostart_config_echo:
            self._sync_autostart_state()

        if "play_sounds" in changed:
            self._audio.set_enabled(self._config.play_sounds)

        if "api_key" in changed and self._faults is not None:
            self._faults.sync_credential_from_config()

        if changed & {"mic_name", "mic_index"}:
            if self._dev_refresh_ready and not self._dev_menu_dirty:
                self._sync_device_menu_checks()

        if "tray_click_to_record" in changed:
            try:
                self.activated.disconnect(self._on_activated)
            except TypeError:
                pass
            if self._config.tray_click_to_record:
                self.activated.connect(self._on_activated)

    # ── tray interaction ──

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_tray_click()

    def _on_tray_click(self):
        """Tray icon click: simple toggle, no hold-to-cancel."""
        logger.debug(f"[Tray] Toggle requested via tray/mini (state={self._engine.state})")
        if self._engine.state in ("processing", "cancelling"):
            return
        if self._engine.state == "ready":
            if self._faults and self._faults.guard_recording_start():
                return
            self._request_record_start("tray_or_mini")
            return
        elif self._engine.state == "recording":
            self._audio.play_stop()
        self._engine.toggle_record()

    def _on_upload_audio(self):
        if self._engine.state != "ready":
            return
        if self._faults and self._faults.guard_recording_start():
            return
        with self.hotkey_paused():
            path, _ = QFileDialog.getOpenFileName(
                None,
                "选择音频文件",
                str(Config.history_dir()),
                "音频文件 (*.wav *.mp3 *.flac *.m4a *.ogg *.aac *.opus);;"
                "WAV (*.wav);;所有文件 (*)",
            )
        if path:
            logger.info(f"[Tray] Upload audio file: {path}")
            self._engine.transcribe_file(path)

    def _on_cancel(self):
        self._hotkey_hold_active = False
        if self._engine.state == "recording":
            self._audio.play_stop()
            self._engine.cancel()

    # ── update ──

    def _on_update_click(self):
        """Dispatches based on current updater state."""
        if not self._updater:
            return
        logger.debug(f"[DEBUG] _on_update_click | is_ready={self._updater.is_ready_to_install}, "
                     f"is_downloading={self._updater.is_downloading}, is_staging={self._updater.is_staging}, "
                     f"latest={self._updater.latest}")
        if self._updater.is_ready_to_install:
            logger.debug("[DEBUG] _on_update_click | → show ready dialog")
            self._show_update_ready_dialog()
            return
        if self._updater.is_downloading or self._updater.is_staging:
            logger.debug("[DEBUG] _on_update_click | → busy, ignored")
            return
        if self._updater.latest:
            logger.debug("[DEBUG] _on_update_click | → show release notes")
            self._show_update_notes(self._updater.latest)
            return
        logger.debug("[DEBUG] _on_update_click | → check_now()")
        self._update_widget.set_checking()
        self._updater.check_now()

    def _present_update_dialog(
        self,
        existing: QDialog | None,
        new_dlg: QDialog,
        *,
        assign: Callable[[QDialog], None],
        on_finished,
    ) -> None:
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        new_dlg.setWindowModality(Qt.WindowModality.NonModal)
        new_dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        new_dlg.finished.connect(on_finished)
        assign(new_dlg)
        new_dlg.show()
        new_dlg.raise_()
        new_dlg.activateWindow()

    def _show_update_notes(self, info: UpdateInfo):
        from _version import VERSION
        self._present_update_dialog(
            self._update_notes_dlg,
            _UpdateNotesDialog(info, VERSION),
            assign=lambda dlg: setattr(self, "_update_notes_dlg", dlg),
            on_finished=self._on_update_notes_finished,
        )

    def _on_update_notes_finished(self, result: int):
        dlg = self._update_notes_dlg
        self._update_notes_dlg = None
        if (dlg is None
                or result != QDialog.DialogCode.Accepted
                or not dlg.start_update):
            return
        self._begin_update_download()

    def _begin_update_download(self):
        if self._updater.is_downloading or self._updater.is_staging:
            return
        if self._updater.is_ready_to_install:
            self._set_update_status("ready")
            self._show_update_ready_dialog()
            return
        if not self._updater.latest:
            return
        logger.info("[Updater] User accepted release notes; starting download")
        self._update_widget.set_downloading(0)
        self._set_update_status("downloading")
        self._updater.download_update()

    def _set_update_status(self, status: str):
        self._update_status = status
        if status in ("downloading", "staging"):
            self.setIcon(icons.icon_updating())
            self.setToolTip("Voice Input 正在更新")
        elif status == "ready":
            self.setIcon(icons.icon_updating())
            self.setToolTip("Voice Input 重启更新")
        else:
            self._restore_idle_icon()

    def _show_update_ready_dialog(self):
        version = self._updater.staged_version
        if not version:
            return
        self._present_update_dialog(
            self._update_ready_dlg,
            _UpdateReadyDialog(version),
            assign=lambda dlg: setattr(self, "_update_ready_dlg", dlg),
            on_finished=self._on_update_ready_finished,
        )

    def _show_update_failed_dialog(self, message: str):
        log_path = str(self._updater.install_log_path) if self._updater else ""
        self._present_update_dialog(
            self._update_failed_dlg,
            _UpdateFailedDialog(message, log_path=log_path),
            assign=lambda dlg: setattr(self, "_update_failed_dlg", dlg),
            on_finished=self._on_update_failed_finished,
        )

    def _on_update_failed_finished(self, _result: int):
        self._update_failed_dlg = None

    def _restore_update_ui_after_install_failure(self) -> None:
        self._set_update_status("ready")
        if self._updater.is_ready_to_install:
            self._update_widget.set_ready()
        elif self._updater.latest:
            self._update_widget.set_found(self._updater.latest.version)
        else:
            self._update_widget.set_idle()

    def _on_update_ready_finished(self, result: int):
        dlg = self._update_ready_dlg
        self._update_ready_dlg = None
        if (dlg is not None
                and result == QDialog.DialogCode.Accepted
                and dlg.restart_now):
            if not self._updater.install_ready(dlg.version, quit_fn=self._quit):
                error = self._updater.last_install_error or ""
                logger.info(f"[Updater] Install aborted in UI: {error}")
                self._restore_update_ui_after_install_failure()
                self._show_update_failed_dialog(error)
                return

    def _on_update_available(self, info: UpdateInfo):
        logger.debug(f"[DEBUG] _on_update_available | remote={info.version}")
        if self._update_ready_dlg is not None:
            self._update_ready_dlg.reject()
        self._set_update_status("idle")
        self._update_widget.set_found(info.version)

    def _on_no_update(self):
        logger.debug("[DEBUG] _on_no_update | already latest")
        self._set_update_status("idle")
        self._update_widget.set_no_update()
        QTimer.singleShot(3000, self._update_widget.set_idle)

    def _on_check_failed(self):
        logger.debug("[DEBUG] _on_check_failed | check failed")
        self._set_update_status("idle")
        self._update_widget.set_failed(is_download=False)

    def _on_download_progress(self, percent: int):
        self._update_widget.set_downloading(percent)
        self._set_update_status("downloading")

    def _on_download_done(self):
        logger.debug("[DEBUG] _on_download_done | download complete, starting extraction")
        self._update_widget.set_extracting(0)
        self._set_update_status("staging")

    def _on_download_failed(self, msg: str):
        logger.debug(f"[DEBUG] _on_download_failed | msg={msg}")
        self._set_update_status("idle")
        self._update_widget.set_failed(is_download=True)
        logger.warning(f"[Updater] Download failed: {msg}")

    def _on_stage_progress(self, percent: int):
        self._update_widget.set_extracting(percent)

    def _on_stage_done(self):
        logger.debug("[DEBUG] _on_stage_done | staging complete, ready to install")
        self._update_widget.set_ready()
        self._set_update_status("ready")
        self._show_update_ready_dialog()

    def _on_stage_failed(self, msg: str):
        logger.debug(f"[DEBUG] _on_stage_failed | msg={msg}")
        self._set_update_status("idle")
        self._update_widget.set_failed(is_download=True)
        logger.warning(f"[Updater] Staging failed: {msg}")

    def _on_hotkey(self):
        logger.debug(f"[Tray] Toggle requested via hotkey down (state={self._engine.state})")
        if self._engine.state in ("processing", "cancelling"):
            return
        if self._engine.state == "ready":
            if self._faults and self._faults.guard_recording_start():
                return
            self._request_record_start("hotkey")
        elif self._engine.state == "recording":
            self._hotkey_hold_active = True
            self._mini.start_hotkey_hold()

    def _on_hotkey_release(self, hold_ms: int):
        if not self._hotkey_hold_active:
            return
        self._hotkey_hold_active = False
        if self._engine.state != "recording":
            self._mini.stop_hotkey_hold()
            return
        self._mini.stop_hotkey_hold()
        if hold_ms < self._mini.hotkey_click_threshold_ms():
            self._audio.play_stop()
            self._engine.toggle_record()

    # ── engine state ──

    def _update_tooltip(self, status: str):
        self.setToolTip(f"VoiceInput — {status}")

    def _sync_tray_icon_with_engine(self):
        """Align icon and primary tooltip with engine state (e.g. after menu device refresh)."""
        if self._update_status in ("downloading", "staging", "ready"):
            self._set_update_status(self._update_status)
            return
        st = self._engine.state
        if st == "recording":
            self.setIcon(icons.icon_recording())
            self._update_tooltip("录音中")
        elif st == "processing":
            self.setIcon(icons.icon_processing())
            self._update_tooltip("识别中")
        else:
            self._restore_idle_icon()

    def _on_state(self, state: str):
        if state == "ready":
            if self._input_rescan_needs_portaudio_reset or self._output_reopen_after_device_rescan:
                self._start_async_refresh()
            self._maybe_apply_deferred_input_device()
        if state == "ready" and self._config_sync is not None:
            self._config_sync.flush_pending_reload()
        self._sync_tray_icon_with_engine()
        if state == "recording":
            self._act_record.setText("停止录音")
            self._act_record.setEnabled(True)
            self._act_rec_info.setVisible(True)
            self._act_upload.setVisible(False)
            self._update_rec_info()
            self._rec_info_timer.start()
        elif state in ("processing", "cancelling"):
            self._act_record.setText("处理中...")
            self._act_record.setEnabled(False)
            self._act_rec_info.setVisible(False)
            self._act_upload.setVisible(False)
            self._rec_info_timer.stop()
        elif state == "ready":
            self._act_record.setText("开始录音")
            self._act_record.setEnabled(True)
            self._act_rec_info.setVisible(False)
            self._act_upload.setVisible(True)
            self._rec_info_timer.stop()

    def _update_rec_info(self):
        if self._engine.state != "recording":
            return
        elapsed = self._engine.get_duration()
        e_min, e_sec = int(elapsed) // 60, int(elapsed) % 60
        if self._engine._countdown_active:
            secs = max(0, self._engine._countdown_secs)
            r_min, r_sec = secs // 60, secs % 60
        else:
            max_dur = self._engine.effective_max_duration
            remaining = max(0, max_dur - elapsed)
            r_min, r_sec = int(remaining) // 60, int(remaining) % 60
        self._act_rec_info.setText(
            f"已录 {e_min}:{e_sec:02d} / 剩余 {r_min}:{r_sec:02d}"
        )

    def _on_countdown_tick(self, seconds: int):
        if seconds >= 0 and self._config.show_countdown:
            self._audio.play_tick()

    def _on_done(self, text: str):
        self._audio.play_done()
        if self._update_status in ("downloading", "staging", "ready"):
            self._set_update_status(self._update_status)
            return
        self.setIcon(icons.icon_done())
        self._update_tooltip("就绪")
        QTimer.singleShot(2000, self._restore_idle_icon)

    def show_tray_message(
        self,
        title: str,
        body: str,
        icon: QSystemTrayIcon.MessageIcon,
        milliseconds: int,
    ):
        self.showMessage(title, body, icon, milliseconds)

    def show_notification_spec(self, spec) -> None:
        if self._notifier is not None:
            self._notifier.show(spec)
            return
        icon = QSystemTrayIcon.MessageIcon.Information
        if spec.severity.value == "error":
            icon = QSystemTrayIcon.MessageIcon.Critical
        elif spec.severity.value == "warning":
            icon = QSystemTrayIcon.MessageIcon.Warning
        self.showMessage(spec.title, spec.body, icon, spec.duration_ms)

    @property
    def engine(self) -> VoiceEngine:
        return self._engine

    def _clear_device_fault(self) -> None:
        if self._faults is not None:
            self._faults.set_active(FaultKind.DEVICE, False, notify=False)

    @property
    def credential_fault(self) -> bool:
        return self._faults.credential_fault if self._faults is not None else False

    def refresh_idle_icon(self) -> None:
        self._restore_idle_icon()

    def request_device_refresh(self) -> None:
        self._start_async_refresh()

    def _restore_idle_icon(self):
        if self._engine.state in ("recording", "processing"):
            self._sync_tray_icon_with_engine()
            return
        if self._update_status in ("downloading", "staging", "ready"):
            self._set_update_status(self._update_status)
            return
        profile_state = self._faults.idle_icon_profile() if self._faults else None
        if profile_state is not None:
            _profile, tooltip = profile_state
            self.setIcon(icons.icon_key_invalid())
            self._update_tooltip(tooltip)
        else:
            self.setIcon(icons.icon_idle())
            self._update_tooltip("就绪")

    def _reset_mini_position(self):
        self._mini.reset_position()

    def _open_history(self):
        path = Config.history_dir()
        os.startfile(str(path))

    def _open_log(self):
        from core.log import _LOG_DIR
        os.startfile(str(_LOG_DIR))

    def _open_config_file(self):
        from ui.config_dialog import open_config_file

        open_config_file(config=self._config)

    def _quit(self):
        logger.info("[Tray] Quit requested")
        self.hide()  # drop the tray icon right away so quitting feels instant
        if self._engine.state == "recording":
            self._engine.cancel()
        self._device_watcher.stop()
        self._autostart_watcher.stop()
        self._device_change_timer.stop()
        self._recordable_retry.stop()
        self._recorder_prepare_timer.stop()

        # Graceful path: stop the hotkey hook, let background workers finish so
        # PortAudio tears down cleanly, then release the audio clients.  On a
        # healthy machine all three complete in well under a second and quit
        # stays fully graceful.
        hotkey_done = self._stop_hotkey_listener()
        workers_done = self._wait_for_background_workers()
        release_done = self._release_audio_bounded(_QUIT_RELEASE_WAIT_SEC)

        if hotkey_done and workers_done and release_done:
            QApplication.quit()
            return

        # Last resort: a thread is stuck in an uninterruptible native call
        # (wedged audio subsystem or keyboard hook).  Qt cannot shut down while
        # a QThread is still running, and waiting forever is what previously
        # forced users to kill the process.  End the process cleanly instead;
        # the OS reclaims the handles.
        logger.warning(
            "[Tray] Background threads unresponsive during quit "
            f"(hotkey_done={hotkey_done}, workers_done={workers_done}, "
            f"release_done={release_done}); forcing exit"
        )
        flush_log()
        os._exit(0)

    def _release_audio_bounded(self, timeout_sec: float) -> bool:
        """Release PortAudio clients from a helper thread with a deadline.

        release() blocks on the recorder lifecycle lock and on native PortAudio
        teardown; either can hang indefinitely when the audio subsystem is
        wedged.  Running it off the GUI thread keeps quit bounded — on timeout
        the daemon thread is abandoned and the OS reclaims the handles at
        process exit.
        """
        done = threading.Event()

        def _release():
            try:
                self._audio.release()
            except Exception:
                logger.opt(exception=True).warning("[Tray] Output audio release failed")
            try:
                self._engine.recorder.release()
            except Exception:
                logger.opt(exception=True).warning("[Tray] Recorder release failed")
            done.set()

        threading.Thread(target=_release, name="QuitAudioRelease", daemon=True).start()
        if done.wait(timeout_sec):
            return True
        logger.warning("[Tray] Audio release still blocked at quit deadline")
        return False

    def _wait_for_background_workers(self, budget_ms: int = _QUIT_WORKER_WAIT_MS) -> bool:
        # Share one deadline across all workers so total quit latency is bounded
        # even when several workers are pending.
        deadline = time.monotonic() + budget_ms / 1000.0
        ok = self._wait_for_worker(self._recorder_prepare_worker, "recorder prepare", deadline)
        for worker in list(getattr(self, "_dev_refresh_workers", [])):
            ok = self._wait_for_worker(worker, "device refresh", deadline) and ok
        # Engine pipeline threads (ASR/polish/finalize/…) and updater threads —
        # letting them finish keeps in-flight results intact and avoids tearing
        # down Qt while one of its QThreads is still alive.
        for label, worker in self._engine.background_workers():
            ok = self._wait_for_owned_worker(worker, label, deadline) and ok
        if self._updater is not None:
            for label, worker in self._updater.background_workers():
                ok = self._wait_for_owned_worker(worker, label, deadline) and ok
        return ok

    @staticmethod
    def _wait_for_owned_worker(worker, label: str, deadline: float) -> bool:
        # The owning component handles cleanup (finished → deleteLater), so only
        # wait here — no forget/disconnect bookkeeping like tray-owned workers.
        if not worker.isRunning():
            return True
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        logger.info(f"[Tray] Waiting for {label} worker to finish")
        if worker.wait(remaining_ms):
            return True
        logger.warning(f"[Tray] {label} worker still running during quit")
        return False

    def _forget_background_worker(self, worker) -> None:
        if worker is None:
            return
        workers = getattr(self, "_dev_refresh_workers", [])
        if worker in workers:
            workers.remove(worker)
        if getattr(self, "_dev_refresh_worker", None) is worker:
            self._dev_refresh_worker = None
        if getattr(self, "_recorder_prepare_worker", None) is worker:
            self._recorder_prepare_worker = None

    def _delete_worker_later(self, worker) -> None:
        if worker is None:
            return
        delete_later = getattr(worker, "deleteLater", None)
        if delete_later is not None:
            delete_later()

    def _disconnect_worker_finished(self, worker, slot) -> None:
        if worker is None:
            return
        finished = getattr(worker, "finished", None)
        disconnect = getattr(finished, "disconnect", None)
        if disconnect is None:
            return
        try:
            disconnect(slot)
        except (TypeError, RuntimeError):
            pass

    def _wait_for_worker(self, worker, label: str, deadline: float) -> bool:
        if worker is None:
            return True
        is_running = getattr(worker, "isRunning", None)
        wait = getattr(worker, "wait", None)
        if is_running is not None and wait is not None and is_running():
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            logger.info(f"[Tray] Waiting for {label} worker to finish")
            if not wait(remaining_ms):
                logger.warning(f"[Tray] {label} worker still running during quit")
                return False
        self._forget_background_worker(worker)
        self._disconnect_worker_finished(worker, self._on_recorder_prepare_worker_finished)
        self._disconnect_worker_finished(worker, self._on_device_refresh_worker_finished)
        self._delete_worker_later(worker)
        return True
