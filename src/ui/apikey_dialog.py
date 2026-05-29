"""DashScope API-key configuration dialog."""
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
)

from ui import icons
from ui.dialog_styles import (
    _DIALOG_BTN_GHOST,
    _DIALOG_BTN_LINK,
    _DIALOG_BTN_PRIMARY,
    _DIALOG_BTN_TEXT,
    _DIALOG_INPUT_MONO_QSS,
    apply_dialog_chrome,
)


class _ApiKeyDialog(QDialog):
    """Dialog to configure DashScope API key."""

    def __init__(self, current_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置 API Key")
        self.setWindowIcon(icons.app_icon())
        self.setFixedSize(420, 150)
        apply_dialog_chrome(self)
        self._result: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        hint = QLabel("DashScope API Key（阿里云百炼）：")
        hint_row.addWidget(hint)
        hint_row.addStretch()
        btn_open = QPushButton("获取 ↗")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_open.setStyleSheet(_DIALOG_BTN_LINK)
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://bailian.console.aliyun.com/cn-beijing/?tab=model#/api-key")))
        hint_row.addWidget(btn_open)
        layout.addLayout(hint_row)

        self._input = QLineEdit(current_key)
        self._input.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setStyleSheet(_DIALOG_INPUT_MONO_QSS)
        layout.addWidget(self._input)

        self._toggle_vis = QPushButton("显示")
        self._toggle_vis.setFixedWidth(50)
        self._toggle_vis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._toggle_vis.setStyleSheet(_DIALOG_BTN_TEXT)
        self._toggle_vis.clicked.connect(self._toggle_visibility)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._toggle_vis)
        btn_row.addStretch()

        btn_ok = QPushButton("保存")
        btn_ok.setFixedWidth(80)
        btn_ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_ok.setStyleSheet(_DIALOG_BTN_PRIMARY)
        btn_ok.clicked.connect(self._do_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_cancel.setStyleSheet(_DIALOG_BTN_GHOST)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _toggle_visibility(self):
        if self._input.echoMode() == QLineEdit.EchoMode.Password:
            self._input.setEchoMode(QLineEdit.EchoMode.Normal)
            self._toggle_vis.setText("隐藏")
        else:
            self._input.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle_vis.setText("显示")

    def _do_save(self):
        key = self._input.text().strip()
        self._result = key
        self.accept()

    @property
    def api_key(self) -> str | None:
        return self._result
