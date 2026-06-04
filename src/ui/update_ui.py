"""Update-related UI widgets: release-notes dialog, restart dialog, and tray menu helpers."""
from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QMenu, QWidgetAction,
)

from core.updater import UpdateInfo
from ui import icons
from ui.dialog_styles import (
    _DIALOG_BTN_SECONDARY,
    _DIALOG_BTN_PRIMARY,
    _DIALOG_HINT_QSS,
    _DIALOG_TEXTEDIT_QSS,
    _DIALOG_TITLE_QSS,
    _DIALOG_SUBTITLE_QSS,
    _DIALOG_META_QSS,
    apply_dialog_chrome,
    apply_dialog_scroll_area,
    create_dialog_root_layout,
)


def _format_update_size(size: int) -> str:
    if size <= 0:
        return "未知大小"
    mb = size / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB"
    return f"{size / 1024:.1f} KB"


def _strip_release_body_title(body: str, version: str) -> str:
    lines = (body or "").strip().splitlines()
    if not lines:
        return ""
    first = lines[0].strip().lstrip("#").strip().lower()
    titles = {f"voiceinput v{version}".lower(), f"voice input v{version}".lower()}
    if first in titles:
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


class _UpdateNotesDialog(QDialog):
    """Shows release notes before starting the silent update download."""

    def __init__(self, info: UpdateInfo, current_version: str, parent=None):
        super().__init__(parent)
        self._info = info
        self._start_update = False
        self.setWindowTitle(f"发现新版本 v{info.version}")
        self.setWindowIcon(icons.app_icon())
        self.setFixedSize(560, 520)
        apply_dialog_chrome(self)

        root = create_dialog_root_layout(self, spacing=12)

        title = QLabel(f"VoiceInput v{info.version} 可用")
        title.setStyleSheet(_DIALOG_TITLE_QSS)
        root.addWidget(title)

        meta = QLabel(
            f"当前版本：v{current_version}\n"
            f"更新文件：{info.filename}（{_format_update_size(info.size)}）"
        )
        meta.setStyleSheet(_DIALOG_META_QSS)
        root.addWidget(meta)

        note = QLabel("更新日志")
        note.setStyleSheet(_DIALOG_SUBTITLE_QSS)
        root.addWidget(note)

        body = _strip_release_body_title(info.body, info.version) or "暂无更新日志。"
        if info.html_url:
            body = f"{body}\n\nRelease 页面：[在浏览器中打开]({info.html_url})"
        self._notes = QTextBrowser()
        self._notes.setReadOnly(True)
        self._notes.setOpenExternalLinks(True)
        apply_dialog_scroll_area(self._notes, _DIALOG_TEXTEDIT_QSS)
        self._notes.setMarkdown(body)
        root.addWidget(self._notes, 1)

        hint = QLabel("点击\u201c开始更新\u201d后，本窗口会关闭，程序将切换至右下角托盘图标进行静默更新；可在托盘菜单中查看下载进度。")
        hint.setWordWrap(True)
        hint.setStyleSheet(_DIALOG_HINT_QSS)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.addStretch()
        btn_later = QPushButton("稍后")
        btn_later.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_later.setStyleSheet(_DIALOG_BTN_SECONDARY)
        btn_later.clicked.connect(self.reject)
        row.addWidget(btn_later)

        btn_start = QPushButton("开始更新")
        btn_start.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_start.setStyleSheet(_DIALOG_BTN_PRIMARY)
        btn_start.clicked.connect(self._accept_start)
        row.addWidget(btn_start)
        root.addLayout(row)

    @property
    def start_update(self) -> bool:
        return self._start_update

    def _accept_start(self):
        self._start_update = True
        self.accept()


class _UpdateReadyDialog(QDialog):
    """Prompts the user to restart after the update payload is downloaded."""

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self._restart_now = False
        self.setWindowTitle("更新已准备好")
        self.setWindowIcon(icons.app_icon())
        self.setFixedSize(420, 180)
        apply_dialog_chrome(self)

        root = create_dialog_root_layout(self, spacing=12)

        title = QLabel(f"v{version} 已下载完成")
        title.setStyleSheet(_DIALOG_TITLE_QSS)
        root.addWidget(title)

        body = QLabel("点击\u201c重启更新\u201d后，VoiceInput 将退出并启动安装/覆盖更新。")
        body.setWordWrap(True)
        body.setStyleSheet(_DIALOG_META_QSS)
        root.addWidget(body)

        row = QHBoxLayout()
        row.addStretch()
        btn_later = QPushButton("稍后")
        btn_later.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_later.setStyleSheet(_DIALOG_BTN_SECONDARY)
        btn_later.clicked.connect(self.reject)
        row.addWidget(btn_later)

        btn_restart = QPushButton("重启更新")
        btn_restart.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_restart.setStyleSheet(_DIALOG_BTN_PRIMARY)
        btn_restart.clicked.connect(self._accept_restart)
        row.addWidget(btn_restart)
        root.addLayout(row)

    @property
    def restart_now(self) -> bool:
        return self._restart_now

    def _accept_restart(self):
        self._restart_now = True
        self.accept()


class _MenuLabel(QLabel):
    """QLabel styled as a menu item with hover highlight, for QWidgetAction."""

    _BG_NORMAL = "transparent"
    _BG_HOVER = "#3a3a3a"
    _PAD = "padding:7px 28px 7px 24px;"

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._color_css = ""
        self._hover_enabled = True
        self._swallow_clicks = False

    def set_hover_enabled(self, on: bool):
        self._hover_enabled = on
        if not on:
            self._apply(self._BG_NORMAL)

    def set_swallow_clicks(self, on: bool):
        self._swallow_clicks = on

    def set_color(self, color: str):
        self._color_css = f"color:{color};"
        self._apply(self._BG_NORMAL)

    def _apply(self, bg: str):
        self.setStyleSheet(
            f"font-size:13px; {self._PAD} {self._color_css}"
            f" background:{bg}; border:none; outline:none;"
        )

    def enterEvent(self, event):
        menu = self._find_parent_menu()
        if menu is not None:
            menu.setActiveAction(None)
        if self._hover_enabled:
            self._apply(self._BG_HOVER)
        super().enterEvent(event)

    def _find_parent_menu(self):
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QMenu):
                return w
            w = w.parentWidget()
        return None

    def leaveEvent(self, event):
        self._apply(self._BG_NORMAL)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._swallow_clicks:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._swallow_clicks:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _UpdateMenuHelper:
    """Two QWidgetAction rows for update UI. Both use _MenuLabel so they
    align with each other, don't close the menu, and support custom colors.
    """

    _CLR_DIMMED = "#666"
    _CLR_WHITE = "#fff"
    _CLR_GREEN = "#66BB6A"
    _CLR_RED = "#EF5350"
    _CLR_BLUE = "#42A5F5"

    def __init__(self, menu: QMenu):
        from _version import VERSION
        self._local_version = VERSION
        self._on_action = None
        self._clickable = True

        self._lbl_ver = _MenuLabel(f"v{VERSION}")
        self._lbl_ver.set_color(self._CLR_DIMMED)
        self._lbl_ver.set_swallow_clicks(True)
        self._lbl_ver.set_hover_enabled(False)
        wa_ver = QWidgetAction(menu)
        wa_ver.setDefaultWidget(self._lbl_ver)
        menu.addAction(wa_ver)

        self._lbl_act = _MenuLabel("检查更新")
        self._lbl_act.setTextFormat(Qt.TextFormat.RichText)
        self._lbl_act.set_color(self._CLR_WHITE)
        self._lbl_act.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_act.set_swallow_clicks(True)
        self._lbl_act.mousePressEvent = self._on_click
        wa_act = QWidgetAction(menu)
        wa_act.setDefaultWidget(self._lbl_act)
        menu.addAction(wa_act)

    def bind(self, callback):
        self._on_action = callback

    def _on_click(self, _event):
        if self._clickable and self._on_action:
            self._on_action()

    def _set_action(self, html: str, color: str, clickable: bool = True):
        self._lbl_act.setText(html)
        self._lbl_act.set_color(color)
        self._lbl_act.set_hover_enabled(clickable)
        self._lbl_act.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable
            else Qt.CursorShape.ArrowCursor
        )
        self._clickable = clickable

    def set_idle(self):
        self._lbl_ver.setText(f"v{self._local_version}")
        self._set_action("检查更新", self._CLR_WHITE)

    def set_checking(self):
        self._set_action("正在检查…", self._CLR_DIMMED, clickable=False)

    def set_found(self, version: str):
        self._lbl_ver.setText(f"v{self._local_version}")
        dot = f'<span style="color:{self._CLR_RED}; font-size:9px;">\u25cf</span>'
        self._set_action(f'v{version} 可用 {dot}', self._CLR_WHITE)

    def set_downloading(self, percent: int):
        self._set_action(
            f'正在下载… {percent}%', self._CLR_BLUE, clickable=False
        )

    def set_extracting(self, percent: int):
        self._set_action(
            f'正在解压… {percent}%', self._CLR_BLUE, clickable=False
        )

    def set_ready(self):
        self._set_action(
            f'<span style="color:{self._CLR_GREEN};">\u2713</span> 重启更新',
            self._CLR_GREEN,
        )

    def set_failed(self, is_download=False):
        text = "下载失败，点击重试" if is_download else "检查失败，点击重试"
        self._set_action(text, self._CLR_WHITE)

    def set_unsupported(self):
        self._set_action("该版本不支持更新", self._CLR_DIMMED, clickable=False)

    def set_no_update(self):
        self._set_action("已是最新版本", self._CLR_DIMMED, clickable=False)


MENU_STYLE = """
    QMenu {
        background: #2a2a2a;
        color: #ffffff;
        border: 1px solid #444;
        border-radius: 8px;
        padding: 6px 0;
    }
    QMenu::item {
        padding: 7px 28px 7px 16px;
        font-size: 13px;
    }
    QMenu::item:selected {
        background: #3a3a3a;
    }
    QMenu::item:disabled {
        color: #666;
    }
    QMenu::separator {
        height: 1px;
        background: #3a3a3a;
        margin: 4px 12px;
    }
"""


def apply_tray_menu_style(menu: QMenu) -> None:
    menu.setStyleSheet(MENU_STYLE)


def install_left_cascade_submenu(submenu: QMenu, parent_menu: QMenu) -> None:
    """Keep v1.4.2 LTR visuals; anchor hover cascade popups left of *parent_menu*."""
    def anchor_left() -> None:
        if not submenu.isVisible():
            return
        submenu.ensurePolished()
        submenu.adjustSize()
        parent_left = parent_menu.frameGeometry().x()
        submenu.move(parent_left - submenu.width(), submenu.y())

    submenu.aboutToShow.connect(lambda: QTimer.singleShot(0, anchor_left))


def popup_tray_submenu(menu: QMenu, global_pos: QPoint) -> None:
    """Re-show a submenu with its right edge at *global_pos* (opens leftward)."""
    menu.ensurePolished()
    menu.adjustSize()
    width = menu.sizeHint().width()
    menu.popup(QPoint(global_pos.x() - width, global_pos.y()))
