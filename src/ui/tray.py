from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import contextmanager
import time
from typing import TYPE_CHECKING, TypeVar

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QCoreApplication, QObject, QEvent,
)
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import (
    QSystemTrayIcon, QMenu, QApplication,
    QDialog, QFileDialog,
)

from config import Config, enabled_polish_model_menu_items
from core.log import logger
from core.engine import VoiceEngine
from core.config_sync import ConfigSync
from core.faults import FaultKind
from core.updater import UpdateChecker, UpdateInfo, can_self_update
from core.polisher import DEFAULT_INSTRUCTIONS
from ui.mini_window import MiniRecordingWindow
from ui.sounds import AudioCues
from ui import icons
from ui.hotkey import ComboHotkeyThread, _HotkeyDialog, _hotkey_display
from ui.window_focus import widget_is_foreground
from ui.apikey_dialog import _ApiKeyDialog
from ui.update_ui import (
    _UpdateNotesDialog, _UpdateReadyDialog, _UpdateMenuHelper,
    apply_tray_menu_style, install_left_cascade_submenu, popup_tray_submenu,
)
from ui.prompt_dialog import _PolishPromptDialog

if TYPE_CHECKING:
    from ui.fault_coordinator import FaultCoordinator
    from ui.notifier import Notifier

_T_modal = TypeVar("_T_modal")


_AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE_NAME = "VoiceInput"


_TRAY_MENU_DEFAULT_PROMPT = "__tray_default_prompt__"


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
    finished = pyqtSignal(str, list)  # (default_name, devices)

    def __init__(self, release_recorder, release_audio):
        super().__init__()
        self._release_recorder = release_recorder
        self._release_audio = release_audio

    def run(self):
        from core.recorder import VoiceRecorder
        from core.device_watcher import (
            get_default_capture_device_name,
            get_full_device_names,
        )
        try:
            self._release_recorder()
            self._release_audio()
            full_names = get_full_device_names()
            default_name = (
                get_default_capture_device_name()
                or VoiceRecorder.get_default_device_name()
            )
            raw_devices = VoiceRecorder.list_devices()
            raw_by_name = {dev["name"]: dev for dev in raw_devices}
            if full_names:
                devices = [
                    {
                        "name": trunc,
                        "display_name": full,
                        "index": raw_by_name.get(trunc, {}).get("index"),
                    }
                    for trunc, full in full_names.items()
                ]
            else:
                devices = raw_devices
            if default_name:
                trunc = default_name[:31]
                if trunc in full_names:
                    default_name = full_names[trunc]
        except Exception:
            default_name = "Unknown"
            devices = []
        self.finished.emit(default_name, devices)


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
        self._sync_autostart_state(save_if_changed=True)
        self.refresh_idle_icon()

        self._build_menu()

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
        self._device_change_timer = QTimer()
        self._device_change_timer.setSingleShot(True)
        self._device_change_timer.setInterval(500)
        self._device_change_timer.timeout.connect(self._start_async_refresh)
        self._device_watcher.signals.changed.connect(self._device_change_timer.start)
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
        self._cached_default_name = ""
        self._cached_devices: list[dict] = []
        self._dev_refresh_running = False
        self._dev_refresh_repeat = False
        self._dev_refresh_ready = False
        self._dev_refresh_worker: _DeviceRefreshWorker | None = None
        self._dev_menu_dirty = True
        menu.addMenu(self._device_menu)

        self._mode_menu = QMenu("切换模式", menu)
        apply_tray_menu_style(self._mode_menu)
        install_left_cascade_submenu(self._mode_menu, menu)
        for mode_id, mode_name in [("transcribe", "纯转录"), ("polish", "智能润色")]:
            act = QAction(mode_name, self._mode_menu)
            act.setCheckable(True)
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
        self._prompt_menu.aboutToShow.connect(self._sync_prompt_menu_checks)
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
        self._skip_pa_release = True
        self._start_async_refresh()

    # ── device refresh (background thread) ──

    def _on_menu_about_to_show(self):
        now = time.monotonic()
        if now - self._last_menu_refresh_time < self._menu_refresh_min_interval:
            return
        self._last_menu_refresh_time = now
        self._start_async_refresh()

    def _release_recorder_pa(self):
        if (self._engine.state != "recording"
                and self._engine.recorder._pa is not None):
            self._engine.recorder._release_pa()

    def _restore_all_pa(self):
        if not self._audio._stream_ready:
            self._audio._init_stream()
        if self._engine.recorder._pa is None and self._engine.state != "recording":
            self._engine.recorder.prepare()

    def _start_async_refresh(self):
        if self._dev_refresh_running:
            self._dev_refresh_repeat = True
            return
        self._dev_refresh_running = True
        skip = getattr(self, '_skip_pa_release', False)
        self._skip_pa_release = False
        self._skip_pa_restore = skip
        release_rec = (lambda: None) if skip else self._release_recorder_pa
        release_audio = (lambda: None) if skip else self._audio.release
        worker = _DeviceRefreshWorker(release_rec, release_audio)
        worker.finished.connect(self._on_refresh_result)
        worker.finished.connect(worker.deleteLater)
        self._dev_refresh_worker = worker
        worker.start()

    def _on_refresh_result(self, default_name: str, devices: list):
        unchanged = (
            self._dev_refresh_ready
            and default_name == self._cached_default_name
            and devices == self._cached_devices
        )
        self._cached_default_name = default_name
        self._cached_devices = devices
        self._dev_refresh_ready = True
        self._last_menu_refresh_time = time.monotonic()
        logger.info(f"[Tray] Device refresh done: "
                    f"default='{default_name}', {len(devices)} device(s)")
        if not self._skip_pa_restore:
            self._restore_all_pa()
        self._skip_pa_restore = False
        self._sync_system_default_device()
        self._auto_fallback_if_device_gone()
        if not unchanged:
            self._rebuild_device_menu()
        self._sync_tray_icon_with_engine()
        self._dev_refresh_running = False
        if self._dev_refresh_repeat:
            self._dev_refresh_repeat = False
            self._start_async_refresh()

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
        current_default = self._cached_default_name or ""
        recorder_name = self._engine.recorder.device_name or ""
        if not current_default or recorder_name == current_default:
            return
        if self._engine.state != "recording":
            self._engine.recorder.set_device(None, "")
            logger.info(f"[Tray] System default device changed: '{recorder_name}' -> '{current_default}'")
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
        match = next((d for d in self._cached_devices if d["name"] == saved_name), None)
        if match is not None:
            if match["index"] is None and self._config.mic_index is not None:
                self._config.mic_index = None
                self._config.save()
                if self._engine.state != "recording":
                    self._engine.recorder.set_device(None, saved_name)
                else:
                    self._pending_device_apply = True
                logger.info(f"[Tray] Device '{saved_name}' temporarily unresolved, cleared stale index")
                return
            if match["index"] is not None and match["index"] != self._config.mic_index:
                old_idx = self._config.mic_index
                self._config.mic_index = match["index"]
                self._config.save()
                if self._engine.state != "recording":
                    self._engine.recorder.set_device(match["index"], saved_name)
                else:
                    self._pending_device_apply = True
                logger.info(f"[Tray] Device '{saved_name}' index changed "
                            f"{old_idx} → {match['index']}")
            return
        old = self._config.mic_index
        self._config.mic_index = None
        self._config.mic_name = ""
        self._config.save()
        if self._engine.state != "recording":
            self._engine.recorder.set_device(None, "")
        else:
            self._pending_device_apply = True
        logger.info(f"[Tray] Selected device '{saved_name}' (index={old}) gone, "
                    f"switched to system default")

    def _rebuild_device_menu(self):
        # Qt does not repaint an open QMenu popup after clear()+rebuild; close and
        # reopen so hot-plugged devices appear while the submenu stays under the cursor.
        reopen_pos = QCursor.pos() if self._device_menu.isVisible() else None
        if reopen_pos is not None:
            self._device_menu.close()

        self._device_menu.clear()
        self._dev_menu_dirty = False

        label = f"系统默认 ({self._cached_default_name})" if self._cached_default_name else "系统默认"
        act_default = QAction(label, self._device_menu)
        act_default.setCheckable(True)
        act_default.setChecked(not self._config.mic_name)
        act_default.triggered.connect(lambda checked: self._set_default_device())
        self._device_menu.addAction(act_default)
        self._device_menu.addSeparator()

        if not self._cached_devices:
            act = QAction("(未发现兼容设备)", self._device_menu)
            act.setEnabled(False)
            self._device_menu.addAction(act)
        else:
            for dev in self._cached_devices:
                display = dev.get("display_name", dev["name"])
                act = QAction(display, self._device_menu)
                act.setCheckable(True)
                act.setChecked(self._config.mic_name == dev["name"])
                act.triggered.connect(
                    lambda checked, idx=dev.get("index"), name=dev["name"]: self._set_device(name, idx))
                self._device_menu.addAction(act)

        if reopen_pos is not None:
            QTimer.singleShot(
                0,
                lambda p=reopen_pos: popup_tray_submenu(self._device_menu, p),
            )

    def _set_mode(self, mode_id: str):
        self._config.mode = mode_id
        self._config.save()
        self._sync_mode_menu()
        self._mini.sync_mode()
        logger.info(f"[Tray] Mode → {mode_id}")

    def _on_mini_mode_changed(self, mode_id: str):
        self._sync_mode_menu()

    def _on_mini_show_result_changed(self, on: bool):
        self._act_show_result_text.blockSignals(True)
        self._act_show_result_text.setChecked(on)
        self._act_show_result_text.blockSignals(False)

    def _sync_mode_menu(self):
        for act in self._mode_menu.actions():
            mode_name_map = {"纯转录": "transcribe", "智能润色": "polish"}
            act.setChecked(mode_name_map.get(act.text(), "") == self._config.mode)

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
        for act in self._prompt_menu.actions():
            if act.isSeparator() or not act.isCheckable():
                continue
            d = act.data()
            if d == _TRAY_MENU_DEFAULT_PROMPT:
                act.setChecked(not cur)
            elif isinstance(d, str) and d:
                act.setChecked(cur == d)

    def _set_active_prompt(self, prompt_id: str):
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
        dlg = _PolishPromptDialog(
            self._config.custom_prompts,
            self._config.active_prompt_id,
            default_text=DEFAULT_INSTRUCTIONS,
            config=self._config,
            on_active_applied=self._sync_prompt_menu_checks,
            on_prompts_saved=self._rebuild_prompt_menu,
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

    def _on_prompt_dlg_finished(self, _result: int):
        # Prompt list is persisted from _PolishPromptDialog._do_save; active_prompt_id
        # may also be updated immediately via「设为当前」.
        # Do not copy dlg state here: dlg.accepted stayed True after a prior save and would
        # re-apply unsaved edits when the user chose「不保存」on close.
        self._prompt_dlg = None
        self._rebuild_prompt_menu()

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

    def _stop_hotkey_listener(self) -> None:
        """停止全局 ComboHotkeyThread 并等待 pynput 钩子退出。"""
        self._clear_hotkey_hold_state()
        self._hotkey.stop_hotkey()
        self._hotkey.wait(2000)

    def _spawn_hotkey_thread(self, combo: str | None = None) -> None:
        """新建并启动 ComboHotkeyThread（替换 self._hotkey）。调用前应已 stop_hotkey_listener。"""
        key = self._config.hotkey if combo is None else combo
        self._hotkey = ComboHotkeyThread(key)
        self._hotkey.triggered.connect(self._on_hotkey)
        self._hotkey.released.connect(self._on_hotkey_release)
        self._hotkey.start()

    def _should_suppress_hotkey_listener(self) -> bool:
        return self._hotkey_pause_depth > 0 or self._foreground_hotkey_pause_active

    def _update_hotkey_listener_suppression(self) -> None:
        suppress = self._should_suppress_hotkey_listener()
        if suppress and not self._hotkey_listener_suppressed:
            self._stop_hotkey_listener()
            self._hotkey_listener_suppressed = True
        elif not suppress and self._hotkey_listener_suppressed:
            if QCoreApplication.closingDown():
                return
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
            act.setChecked(model_id == current)
            act.triggered.connect(lambda checked, m=model_id: self._set_polish_model(m))
            self._polish_model_menu.addAction(act)

    def _set_polish_model(self, model_id: str):
        self._config.polish_model = model_id
        self._config.save()
        self._engine.polisher.set_model(model_id)
        for act in self._polish_model_menu.actions():
            mid = next(
                (m for m, d in self._polish_model_menu_entries() if d == act.text()),
                "",
            )
            act.setChecked(mid == model_id)
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
        self._config.smart_chunk_max_duration_sec = seconds
        self._config.save()
        for i, (dur_sec, _) in enumerate(self._duration_presets):
            self._duration_menu.actions()[i].setChecked(dur_sec == seconds)
        logger.info(f"[Tray] Max recording duration → {seconds}s")

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

    def _sync_autostart_state(self, save_if_changed: bool = False):
        actual = self._read_autostart_enabled()
        changed = self._config.autostart_enabled != actual
        self._config.autostart_enabled = actual
        if save_if_changed and changed:
            self._config.save()
        if hasattr(self, "_act_autostart"):
            self._act_autostart.blockSignals(True)
            self._act_autostart.setChecked(actual)
            self._act_autostart.blockSignals(False)

    def _read_autostart_enabled(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTOSTART_RUN_KEY,
                0,
                winreg.KEY_QUERY_VALUE,
            ) as key:
                value, _ = winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
            return bool(str(value).strip())
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def _resolve_autostart_command(self) -> str | None:
        src_dir = os.path.dirname(os.path.abspath(__file__))
        app_root = os.path.dirname(src_dir)
        if os.path.basename(app_root).lower() == "src":
            app_root = os.path.dirname(app_root)
        exe = os.path.join(app_root, "VoiceInput.exe")
        if not os.path.isfile(exe):
            return None
        return f'"{exe}"'

    def _write_autostart_enabled(self, enabled: bool):
        if sys.platform != "win32":
            raise RuntimeError("当前平台不支持开机自启")

        import winreg

        if enabled:
            command = self._resolve_autostart_command()
            if not command:
                raise RuntimeError("当前运行方式无法确定启动命令，请使用安装包版本后再设置")
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_RUN_KEY) as key:
                winreg.SetValueEx(key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, command)
            return

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTOSTART_RUN_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
        except FileNotFoundError:
            return

    def _toggle_autostart(self, checked: bool):
        try:
            self._write_autostart_enabled(checked)
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

        self._config.autostart_enabled = checked
        self._config.save()
        logger.info(f"[Tray] Autostart → {'on' if checked else 'off'}")

    def _set_default_device(self):
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
            self._engine.recorder.set_device(None, "")
            logger.info("[Tray] Input device → system default (index=None)")
        self._rebuild_device_menu()
        self._clear_device_fault()
        self._sync_tray_icon_with_engine()

    def _set_device(self, name: str, idx: int | None = None):
        resolved = idx if idx is not None else None
        self._config.mic_name = name
        self._config.mic_index = resolved
        self._config.save()
        if self._engine.state == "recording":
            self._pending_device_apply = True
            logger.info(f"[Tray] Input device saved for next session "
                        f"→ {name} (index={resolved})")
            self.show_tray_message(
                "VoiceInput", "输入设备已切换，下次录音生效",
                QSystemTrayIcon.MessageIcon.Information, 2000,
            )
        else:
            self._pending_device_apply = False
            self._engine.recorder.set_device(resolved, name)
            logger.info(f"[Tray] Input device → {name} (index={resolved})")
        self._rebuild_device_menu()
        self._clear_device_fault()
        self._sync_tray_icon_with_engine()

    def _maybe_apply_deferred_input_device(self):
        if not self._pending_device_apply or self._engine.state == "recording":
            return
        self._pending_device_apply = False
        idx = self._config.mic_index
        name = self._config.mic_name
        self._engine.recorder.set_device(idx, name)
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
        if engine_fields:
            self._engine.apply_config(engine_fields)

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

        def _set_checked(action, value: bool) -> None:
            if action is None:
                return
            action.blockSignals(True)
            action.setChecked(value)
            action.blockSignals(False)

        if "mode" in changed:
            self._sync_mode_menu()

        if changed & {"polish_model", "polish_models", "enabled_polish_models"}:
            self._populate_polish_menu()

        if changed & {"custom_prompts", "active_prompt_id"}:
            self._rebuild_prompt_menu()
            dlg = self._prompt_dlg
            if dlg is not None:
                dlg.sync_from_config()

        if "smart_chunk_max_duration_sec" in changed:
            cur = self._config.smart_chunk_max_duration_sec
            for act in self._duration_menu.actions():
                for dur_sec, display in self._duration_presets:
                    if act.text() == display:
                        _set_checked(act, dur_sec == cur)
                        break

        for field, action in (
            ("silence_trim", self._act_silence_trim),
            ("show_countdown", self._act_show_countdown),
            ("paste_result", self._act_paste_result),
            ("show_result_text", self._act_show_result_text),
            ("mini_bar_show_timer", self._act_mini_bar_timer),
            ("hide_mini_window_when_idle", self._act_hide_idle_mini),
            ("save_audio", self._act_save_audio),
        ):
            if field in changed:
                _set_checked(action, getattr(self._config, field))

        if "autostart_enabled" in changed:
            self._sync_autostart_state()

        if "play_sounds" in changed:
            self._audio.set_enabled(self._config.play_sounds)

        if "api_key" in changed and self._faults is not None:
            self._faults.sync_credential_from_config()

        if "hotkey" in changed:
            self._act_hotkey.setText(
                f"快捷键: {_hotkey_display(self._config.hotkey)}"
            )

        if changed & {"mic_name", "mic_index"}:
            self._dev_menu_dirty = True

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
        if self._engine.state == "processing":
            return
        if self._engine.state == "ready":
            if self._faults and self._faults.guard_recording_start():
                return
            self._audio.play_start(source="tray_or_mini")
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

    def _show_update_notes(self, info: UpdateInfo):
        if self._update_notes_dlg is not None:
            self._update_notes_dlg.raise_()
            self._update_notes_dlg.activateWindow()
            return
        from _version import VERSION
        dlg = _UpdateNotesDialog(info, VERSION)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.finished.connect(self._on_update_notes_finished)
        self._update_notes_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

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
        if not self._updater.latest:
            return
        if self._update_ready_dlg is not None:
            self._update_ready_dlg.raise_()
            self._update_ready_dlg.activateWindow()
            return
        dlg = _UpdateReadyDialog(self._updater.latest.version)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.finished.connect(self._on_update_ready_finished)
        self._update_ready_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_update_ready_finished(self, result: int):
        dlg = self._update_ready_dlg
        self._update_ready_dlg = None
        if (dlg is not None
                and result == QDialog.DialogCode.Accepted
                and dlg.restart_now):
            self._updater.install()

    def _on_update_available(self, info: UpdateInfo):
        logger.debug(f"[DEBUG] _on_update_available | remote={info.version}")
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
        if self._engine.state == "processing":
            return
        if self._engine.state == "ready":
            if self._faults and self._faults.guard_recording_start():
                return
            self._audio.play_start(source="hotkey")
            self._engine.toggle_record()
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
        if state != "recording":
            self._maybe_apply_deferred_input_device()
        if state == "ready" and self._config_sync is not None:
            self._config_sync.flush_pending_reload()
        self._sync_tray_icon_with_engine()
        if state == "recording":
            self._act_record.setText("停止录音")
            self._act_rec_info.setVisible(True)
            self._act_upload.setVisible(False)
            self._update_rec_info()
            self._rec_info_timer.start()
        elif state == "processing":
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
        if self._engine.state == "recording":
            self._engine.cancel()
        self._device_watcher.stop()
        self._engine.recorder.release()
        self._stop_hotkey_listener()
        QApplication.quit()
