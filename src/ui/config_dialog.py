"""Config file fault UI: modal recovery dialog and runtime handler."""
from __future__ import annotations

import sys
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from config import (
    Config,
    LoadStatus,
    _config_path,
    delete_config_file,
    open_config_in_editor,
)
from core.log import logger
from ui.styled_message_box import DialogButton, show_styled_dialog

if TYPE_CHECKING:
    from core.config_sync import ConfigSync
    from ui.fault_coordinator import FaultCoordinator

_TAG = "[ConfigFault]"

_POLL_MS = 500


class ConfigFaultAction(str, Enum):
    OPEN = "open"
    RESTORE = "restore"
    DELETE = "delete"
    DISMISS = "dismiss"
    QUIT = "quit"


def disk_is_readable(*, fill_env_api_key: bool = False) -> bool:
    status = Config.read_outcome(fill_env_api_key=fill_env_api_key).status
    return status not in (LoadStatus.CORRUPT, LoadStatus.MISSING)


def _fault_body_text() -> str:
    return (
        "配置文件无法读取，程序已暂停正常使用。\n"
        "请修复、写回或重建配置；确认无误后自动恢复。"
    )


def _fault_hints() -> list[str]:
    return [
        "打开配置文件 — 在外部编辑器中修改，保存后自动检测",
        "写回当前设置 — 用内存中的设置覆盖磁盘文件",
        "删除并重建 — 删除损坏文件并写入当前内存设置",
    ]


def prompt_config_fault(
    *,
    runtime: bool,
    parent: QWidget | None = None,
) -> ConfigFaultAction:
    """Modal config-fault dialog using the shared styled alert layout."""
    path = _config_path()
    dismiss_label = "取消" if runtime else "退出"
    buttons = [
        DialogButton("打开配置文件", variant="primary", default=True),
        DialogButton("写回当前设置", variant="secondary"),
        DialogButton("删除并重建", variant="outline_danger"),
        DialogButton(dismiss_label, variant="ghost"),
    ]

    label = show_styled_dialog(
        parent=parent,
        window_title="VoiceInput — 配置文件异常",
        heading="配置文件异常",
        body=_fault_body_text(),
        detail_label="配置文件路径",
        detail_text=str(path),
        hints=_fault_hints(),
        buttons=buttons,
        min_width=520,
    )
    if label is None:
        return ConfigFaultAction.DISMISS if runtime else ConfigFaultAction.QUIT

    if label == "打开配置文件":
        return ConfigFaultAction.OPEN
    if label == "写回当前设置":
        return ConfigFaultAction.RESTORE
    if label == "删除并重建":
        return ConfigFaultAction.DELETE
    return ConfigFaultAction.DISMISS if runtime else ConfigFaultAction.QUIT


def wait_until_disk_readable(
    *,
    poll_ms: int = _POLL_MS,
    process_events: bool = True,
) -> bool:
    """Block until config.json reads successfully (for startup / post-edit wait)."""
    while not disk_is_readable(fill_env_api_key=True):
        if process_events:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
        time.sleep(poll_ms / 1000)
    return True


def resolve_config_at_startup(*, parent: QWidget | None = None) -> Config:
    """Startup: loop dialog + recovery until disk is readable."""
    while True:
        outcome = Config.read_outcome(fill_env_api_key=True)
        if outcome.status is not LoadStatus.CORRUPT:
            return Config.finish_load(outcome)

        path = _config_path()
        logger.error(f"{_TAG} Config unreadable at startup: {path}")

        action = prompt_config_fault(runtime=False, parent=parent)
        if action is ConfigFaultAction.QUIT:
            sys.exit(1)
        _apply_startup_action(action, outcome.cfg)
        if disk_is_readable(fill_env_api_key=True):
            continue


def _apply_startup_action(action: ConfigFaultAction, memory_cfg: Config) -> None:
    if action is ConfigFaultAction.OPEN:
        open_config_in_editor(cfg=memory_cfg)
        wait_until_disk_readable()
        return
    if action is ConfigFaultAction.RESTORE:
        memory_cfg._persist_all()
        logger.info(f"{_TAG} Wrote in-memory config to {_config_path()}")
        return
    if action is ConfigFaultAction.DELETE:
        delete_config_file()
        memory_cfg._persist_all()
        logger.info(f"{_TAG} Deleted corrupt file and recreated from memory defaults")


def open_config_file(*, config: Config | None = None) -> None:
    open_config_in_editor(cfg=config)


# Backward-compatible alias
load_config_at_startup = resolve_config_at_startup


class ConfigFaultHandler(QObject):
    """Runtime config fault: modal dialog, no toast; blocks until recovered."""

    def __init__(
        self,
        config: Config,
        config_sync: ConfigSync | None,
        coordinator: FaultCoordinator,
        *,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._config = config
        self._config_sync = config_sync
        self._coordinator = coordinator
        self._dialog_active = False
        self._app_ready = False
        self._poll = QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    @property
    def is_blocking(self) -> bool:
        from core.faults import FaultKind
        return self._coordinator.is_active(FaultKind.CONFIG_DISK)

    def mark_app_ready(self) -> None:
        """Allow runtime fault dialogs after startup config checks and UI init."""
        self._app_ready = True

    def present(self) -> None:
        """Show recovery dialog."""
        if not self._app_ready:
            return
        QTimer.singleShot(0, self._run_interaction)

    def start_watching(self) -> None:
        if not self._poll.isActive():
            self._poll.start()

    def stop_watching(self) -> None:
        self._poll.stop()

    def _run_interaction(self) -> None:
        from core.faults import FaultKind

        if not self._coordinator.is_active(FaultKind.CONFIG_DISK):
            return
        if self._dialog_active:
            return
        self._dialog_active = True
        try:
            while self._coordinator.is_active(FaultKind.CONFIG_DISK):
                action = prompt_config_fault(runtime=True)
                if action is ConfigFaultAction.DISMISS:
                    logger.info(f"{_TAG} User dismissed config fault dialog")
                    return
                self._apply_runtime_action(action)
                if action is ConfigFaultAction.OPEN:
                    return
                if self._try_recover():
                    logger.info(f"{_TAG} Config recovered after user action")
                    return
        finally:
            self._dialog_active = False

    def _apply_runtime_action(self, action: ConfigFaultAction) -> None:
        if action is ConfigFaultAction.OPEN:
            open_config_in_editor(cfg=self._config)
            self.start_watching()
            return
        if action is ConfigFaultAction.RESTORE:
            logger.info(f"{_TAG} User requested write-back to disk")
            self._config._persist_all()
            return
        if action is ConfigFaultAction.DELETE:
            logger.info(f"{_TAG} User requested delete and recreate")
            delete_config_file()
            self._config._persist_all()
            return

    def _try_recover(self) -> bool:
        if not disk_is_readable(fill_env_api_key=False):
            return False
        if self._config_sync is None:
            return True
        if self._config_sync.try_recover_from_disk():
            self.stop_watching()
            return True
        return False

    def _on_poll(self) -> None:
        from core.faults import FaultKind

        if not self._coordinator.is_active(FaultKind.CONFIG_DISK):
            self.stop_watching()
            return
        if self._dialog_active:
            return
        if self._try_recover():
            logger.info(f"{_TAG} Config recovered (auto-detect)")
