"""DashScope API-key configuration dialog."""
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
)

from ui import icons


class _ApiKeyDialog(QDialog):
    """Dialog to configure DashScope API key."""

    def __init__(self, current_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置 API Key")
        self.setWindowIcon(icons.app_icon())
        self.setFixedSize(420, 150)
        self.setStyleSheet("background:#1e1e1e; color:#fff;")
        self._result: str | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        hint = QLabel("DashScope API Key（阿里云百炼）：")
        hint.setStyleSheet("font-size:13px;")
        hint_row.addWidget(hint)
        hint_row.addStretch()
        btn_open = QPushButton("获取 ↗")
        btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_open.setStyleSheet("""
            QPushButton { background:transparent; color:#007aff; border:none;
                          font-size:12px; }
            QPushButton:hover { color:#339aff; text-decoration:underline; }
        """)
        btn_open.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://bailian.console.aliyun.com/cn-beijing/?tab=model#/api-key")))
        hint_row.addWidget(btn_open)
        layout.addLayout(hint_row)

        self._input = QLineEdit(current_key)
        self._input.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._input.setEchoMode(QLineEdit.EchoMode.Password)
        self._input.setStyleSheet("""
            QLineEdit {
                background:#2a2a2a; color:#fff; border:1px solid #555;
                border-radius:6px; padding:8px; font-size:13px;
                font-family: Consolas, monospace;
            }
            QLineEdit:focus { border:1px solid #007aff; }
        """)
        layout.addWidget(self._input)

        self._toggle_vis = QPushButton("显示")
        self._toggle_vis.setFixedWidth(50)
        self._toggle_vis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._toggle_vis.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:none;
                          font-size:12px; }
            QPushButton:hover { color:#fff; }
        """)
        self._toggle_vis.clicked.connect(self._toggle_visibility)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._toggle_vis)
        btn_row.addStretch()

        btn_ok = QPushButton("保存")
        btn_ok.setFixedWidth(80)
        btn_ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_ok.setStyleSheet("""
            QPushButton { background:#007aff; color:#fff; border:none;
                          border-radius:6px; padding:6px 14px; font-size:13px; }
            QPushButton:hover { background:#0066dd; }
        """)
        btn_ok.clicked.connect(self._do_save)
        btn_row.addWidget(btn_ok)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_cancel.setStyleSheet("""
            QPushButton { background:transparent; color:#999; border:1px solid #444;
                          border-radius:6px; padding:6px 14px; font-size:13px; }
            QPushButton:hover { background:#2a2a2a; color:#fff; }
        """)
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
