"""Polish-prompt manager dialog — split-pane editor for custom prompts."""
import copy
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QListWidget, QListWidgetItem,
    QStyledItemDelegate, QStyleOptionViewItem,
    QMessageBox, QSplitter, QWidget,
)

from config import Config
from core.log import logger
from core.prompt_templates import default_prompt_templates
from ui import icons
from ui.dialog_styles import (
    _DIALOG_PANEL_BG, _DIALOG_PANEL_BORDER,
    _DIALOG_READONLY_BG, _DIALOG_READONLY_BORDER,
    _DIALOG_BG, _DIALOG_FOCUS,
    _DIALOG_TEXT, _DIALOG_TOOLTIP_BG,
    _DIALOG_SB_HANDLE, _DIALOG_SB_HANDLE_HOVER,
    _DIALOG_BTN_METRICS,
)

_T_modal = TypeVar("_T_modal")


class _KeepWhiteTextDelegate(QStyledItemDelegate):
    """Force white text in all states so selection/hover never flips to black."""

    def initStyleOption(self, option: QStyleOptionViewItem, index):
        super().initStyleOption(option, index)
        option.palette.setColor(option.palette.ColorRole.Text, QColor("#fff"))
        option.palette.setColor(option.palette.ColorRole.HighlightedText, QColor("#fff"))


class _DragReorderListWidget(QListWidget):
    """QListWidget with live drag-to-reorder: items swap as the cursor passes over them.

    Row 0 (默认提示词) is pinned and cannot be dragged or swapped into.
    Emits *orderChanged(int, int)* after each swap with (from_row, to_row).
    """

    orderChanged = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_row: int = -1
        self._dragging = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            row = self.row(self.itemAt(event.pos()))
            if row >= 1:
                self._drag_row = row
                self._dragging = True
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_row >= 1:
            target = self.row(self.itemAt(event.pos()))
            if target >= 1 and target != self._drag_row:
                self._swap_rows(self._drag_row, target)
                self._drag_row = target
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._drag_row = -1
        super().mouseReleaseEvent(event)

    def _swap_rows(self, a: int, b: int):
        if a < 1 or b < 1:
            return
        item_a = self.item(a)
        item_b = self.item(b)
        if item_a is None or item_b is None:
            return
        text_a, data_a = item_a.text(), item_a.data(Qt.ItemDataRole.UserRole)
        text_b, data_b = item_b.text(), item_b.data(Qt.ItemDataRole.UserRole)
        item_a.setText(text_b)
        item_a.setData(Qt.ItemDataRole.UserRole, data_b)
        item_b.setText(text_a)
        item_b.setData(Qt.ItemDataRole.UserRole, data_a)
        self.orderChanged.emit(a, b)
        self.setCurrentRow(b)


_DEFAULT_PROMPT_ID = "__default__"

# ── QSS 调色板与组合样式（颜色单源；滚动条仅一处实现） ──
_PROMPT_QSS_PANEL_BG = _DIALOG_PANEL_BG
_PROMPT_QSS_PANEL_BORDER = _DIALOG_PANEL_BORDER
_PROMPT_QSS_READONLY_BG = _DIALOG_READONLY_BG
_PROMPT_QSS_READONLY_BORDER = _DIALOG_READONLY_BORDER
_PROMPT_QSS_DIALOG_BG = _DIALOG_BG
_PROMPT_QSS_SPLITTER_HANDLE = "#444"
_PROMPT_QSS_SB_HANDLE = _DIALOG_SB_HANDLE
_PROMPT_QSS_SB_HANDLE_HOVER = _DIALOG_SB_HANDLE_HOVER
_PROMPT_QSS_FOCUS = _DIALOG_FOCUS
_PROMPT_QSS_MSGBOX_FG = _DIALOG_TEXT
_PROMPT_QSS_TOOLTIP_BG = _DIALOG_TOOLTIP_BG


def _prompt_qss_scrollbar(widget_prefix: str, track_bg: str) -> str:
    """列表/编辑区滚动条：轨道与面板同色，滑块统一灰阶；避免在多处复制 QSS。"""
    h, hh = _PROMPT_QSS_SB_HANDLE, _PROMPT_QSS_SB_HANDLE_HOVER
    return f"""
    {widget_prefix} QScrollBar:vertical {{ background: {track_bg}; }}
    {widget_prefix} QScrollBar::handle:vertical {{ background: {h}; }}
    {widget_prefix} QScrollBar::handle:vertical:hover {{ background: {hh}; }}
    {widget_prefix} QScrollBar:horizontal {{ background: {track_bg}; }}
    {widget_prefix} QScrollBar::handle:horizontal {{ background: {h}; }}
    {widget_prefix} QScrollBar::handle:horizontal:hover {{ background: {hh}; }}
    """


# 主对话框外壳（与二级 QMessageBox、列表面板样式表分离，避免互相套用）
_PROMPT_DIALOG_CHROME_QSS = f"""
    QDialog {{
        background: {_PROMPT_QSS_DIALOG_BG};
        color: #fff;
        border: none;
        border-radius: 0px;
    }}
    QToolTip {{
        background-color: {_PROMPT_QSS_TOOLTIP_BG};
        color: {_PROMPT_QSS_MSGBOX_FG};
        border: 1px solid {_PROMPT_QSS_PANEL_BORDER};
        padding: 6px 9px;
        border-radius: 4px;
        font-size: 12px;
        max-width: 420px;
    }}
"""

_INPUT_STYLE = f"""
    QLineEdit {{
        background: {_PROMPT_QSS_PANEL_BG};
        color: #fff;
        border: 1px solid {_PROMPT_QSS_PANEL_BORDER};
        border-radius: 6px;
        padding: 8px;
        font-size: 13px;
    }}
    QLineEdit:focus {{ border: 1px solid {_PROMPT_QSS_FOCUS}; }}
    QLineEdit:read-only {{ color: #999; }}
"""
_TEXTEDIT_STYLE = f"""
    QTextEdit {{
        background: {_PROMPT_QSS_PANEL_BG};
        color: #fff;
        border: 1px solid {_PROMPT_QSS_PANEL_BORDER};
        border-radius: 6px;
        padding: 8px;
        font-size: 13px;
    }}
    QTextEdit:focus {{ border: 1px solid {_PROMPT_QSS_FOCUS}; }}
""" + _prompt_qss_scrollbar("QTextEdit", _PROMPT_QSS_PANEL_BG)

_TEXTEDIT_READONLY_STYLE = f"""
    QTextEdit {{
        background: {_PROMPT_QSS_READONLY_BG};
        color: #999;
        border: 1px solid {_PROMPT_QSS_READONLY_BORDER};
        border-radius: 6px;
        padding: 8px;
        font-size: 13px;
    }}
""" + _prompt_qss_scrollbar("QTextEdit", _PROMPT_QSS_READONLY_BG)

_INPUT_READONLY_STYLE = f"""
    QLineEdit {{
        background: {_PROMPT_QSS_READONLY_BG};
        color: #999;
        border: 1px solid {_PROMPT_QSS_READONLY_BORDER};
        border-radius: 6px;
        padding: 8px;
        font-size: 13px;
    }}
"""
_LIST_STYLE = f"""
    QListWidget {{
        background: {_PROMPT_QSS_PANEL_BG};
        border: 1px solid {_PROMPT_QSS_PANEL_BORDER};
        border-radius: 6px;
        padding: 4px;
        font-size: 13px;
        color: #fff;
        outline: none;
    }}
    QListWidget::item {{
        padding: 8px 10px;
        border-radius: 4px;
        margin: 2px 0;
        border: 1px solid transparent;
        color: #fff;
    }}
    QListWidget::item:selected {{
        background: transparent;
        border: 1px solid {_PROMPT_QSS_FOCUS};
        color: #fff;
    }}
    QListWidget::item:hover:!selected {{ background: #333; color: #fff; }}
""" + _prompt_qss_scrollbar("QListWidget", _PROMPT_QSS_PANEL_BG)

# 管理提示词按钮：统一尺寸；主色按钮用与底色同色的 1px 边框（勿用 border:none），否则比灰底按钮少 2px 高
_PROMPT_QSS_BTN_METRICS = _DIALOG_BTN_METRICS

_BTN = f"""
    QPushButton {{ background:#333; color:#fff; border:1px solid #555;
                  {_PROMPT_QSS_BTN_METRICS} }}
    QPushButton:hover {{ background:#444; border-color:#666; }}
    QPushButton:disabled {{ color:#555; border-color:#444; }}
"""
_BTN_DANGER = f"""
    QPushButton {{ background:transparent; color:#ff6b60; border:1px solid #553030;
                  {_PROMPT_QSS_BTN_METRICS} }}
    QPushButton:hover {{ background:#3a1a1a; border-color:#ff3b30; }}
    QPushButton:disabled {{ color:#553030; border-color:#444; }}
"""
_BTN_PRIMARY = f"""
    QPushButton {{ background:{_PROMPT_QSS_FOCUS}; color:#fff; border:1px solid {_PROMPT_QSS_FOCUS};
                  {_PROMPT_QSS_BTN_METRICS} }}
    QPushButton:hover {{ background:#0066dd; border-color:#0066dd; }}
"""
# 二级 QMessageBox：背景与列表面板一致；按钮样式与主面板底栏相同（_BTN / _BTN_PRIMARY / _BTN_DANGER）
_PROMPT_MSGBOX_STYLE = f"""
    QMessageBox {{
        background-color: {_PROMPT_QSS_PANEL_BG};
        color: {_PROMPT_QSS_MSGBOX_FG};
        border: 1px solid {_PROMPT_QSS_PANEL_BORDER};
    }}
    QMessageBox QLabel {{
        color: {_PROMPT_QSS_MSGBOX_FG};
        font-size: 13px;
    }}
"""


class _PolishPromptDialog(QDialog):
    """Split-pane prompt manager.

    Left: prompt list (click = browse, double-click or button = activate).
    Right: inline name + content editor.

    数据约定：`_prompts` 为自定义条目的有序列表；列表第 0 行为内置「默认提示词」
    （不在 `_prompts` 内）。右侧编辑区始终绑定 `_editing_prompt_id` 所指的 dict；
    写回内存时只按 id 查找，绝不使用「行号 − 1」当下标，以免拖拽重排后错位。
    """

    _BTN_SAVE_CLEAN = f"""
        QPushButton {{ background:#333; color:#888; border:1px solid #555;
                      {_PROMPT_QSS_BTN_METRICS} }}
        QPushButton:hover {{ background:#444; color:#aaa; }}
    """
    _BTN_SAVE_DIRTY = f"""
        QPushButton {{ background:{_PROMPT_QSS_FOCUS}; color:#fff; border:1px solid {_PROMPT_QSS_FOCUS};
                      {_PROMPT_QSS_BTN_METRICS} }}
        QPushButton:hover {{ background:#0066dd; border-color:#0066dd; }}
    """
    _BTN_ACTIVATE_ON = f"""
        QPushButton {{ background:#0a5c2a; color:#4cdf90; border:1px solid #1a8040;
                      {_PROMPT_QSS_BTN_METRICS} }}
    """
    _BTN_ACTIVATE_OFF = f"""
        QPushButton {{ background:#333; color:#fff; border:1px solid #555;
                      {_PROMPT_QSS_BTN_METRICS} }}
        QPushButton:hover {{ background:#444; border-color:#666; }}
    """
    # 与 _BTN 同尺寸；用于「还原此项」不可用时的视觉灰化（保持 enabled 以便显示悬停提示）
    _BTN_REVERT_INACTIVE = f"""
        QPushButton {{ background:{_PROMPT_QSS_PANEL_BG}; color:#666; border:1px solid #444;
                      {_PROMPT_QSS_BTN_METRICS} }}
        QPushButton:hover {{ background:#333; color:#888; border-color:#555; }}
    """

    def __init__(self, prompts: list, active_id: str, default_text: str = "",
                 config: Config | None = None, parent=None,
                 on_active_applied: Callable[[], None] | None = None,
                 on_prompts_saved: Callable[[], None] | None = None,
                 run_modal_with_hotkey_paused: Callable[
                     [Callable[[], Any]], Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle("管理提示词")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(680, 420)
        self.setStyleSheet(_PROMPT_DIALOG_CHROME_QSS)

        self._prompts: list[dict] = copy.deepcopy(prompts) if prompts else []
        self._active_id: str = active_id or ""
        self._default_text: str = default_text
        self._config_ref: Config | None = config
        self._on_active_applied = on_active_applied
        self._on_prompts_saved = on_prompts_saved
        self._run_modal_with_hotkey_paused = run_modal_with_hotkey_paused
        self._accepted = False
        self._switching = False
        self._last_row: int = -1
        self._editing_prompt_id: str = ""

        root = QVBoxLayout(self)
        root.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            f"QSplitter::handle {{ background:{_PROMPT_QSS_SPLITTER_HANDLE}; }}")

        # ── left panel ──
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 4, 0)
        left_lay.setSpacing(6)

        self._list = _DragReorderListWidget()
        self._list.setStyleSheet(_LIST_STYLE)
        self._list.setItemDelegate(_KeepWhiteTextDelegate(self._list))
        self._list.orderChanged.connect(self._on_list_order_swapped)
        left_lay.addWidget(self._list)

        left_btns = QHBoxLayout()
        left_btns.setSpacing(6)
        left_btns.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        btn_add = QPushButton("+ 新增")
        btn_add.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_add.setStyleSheet(_BTN)
        btn_add.clicked.connect(self._add_item)
        left_btns.addWidget(btn_add)
        self._btn_duplicate = QPushButton("复制")
        self._btn_duplicate.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_duplicate.setStyleSheet(_BTN)
        self._btn_duplicate.clicked.connect(self._duplicate_item)
        left_btns.addWidget(self._btn_duplicate)
        self._btn_delete = QPushButton("删除")
        self._btn_delete.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_delete.setStyleSheet(_BTN_DANGER)
        self._btn_delete.clicked.connect(self._delete_item)
        left_btns.addWidget(self._btn_delete)
        left_btns.addStretch()
        left_lay.addLayout(left_btns)

        splitter.addWidget(left)

        # ── right panel ──
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 0, 0, 0)
        right_lay.setSpacing(8)

        _LBL_MUTED = "font-size:12px; color:#aaa;"
        _LBL_STAR = "font-size:12px; color:#ff9f0a; padding:0 1px;"
        _LBL_HINT = "font-size:12px; color:#ff9f0a; padding:0 2px;"

        top_row = QHBoxLayout()
        top_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        name_title = QHBoxLayout()
        name_title.setSpacing(0)
        self._lbl_name_text = QLabel("名称")
        self._lbl_name_text.setStyleSheet(_LBL_MUTED)
        self._lbl_name_star = QLabel("")
        self._lbl_name_star.setStyleSheet(_LBL_STAR)
        self._lbl_name_star.setVisible(False)
        name_title.addWidget(self._lbl_name_text)
        name_title.addWidget(self._lbl_name_star)
        top_row.addLayout(name_title)
        top_row.addStretch()
        self._btn_revert_row = QPushButton("还原此项")
        self._btn_revert_row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)
        self._btn_revert_row.setToolTip("将本条名称与内容恢复为已保存的磁盘版本")
        self._btn_revert_row.clicked.connect(self._revert_current_row_from_disk)
        top_row.addWidget(self._btn_revert_row)
        self._btn_activate = QPushButton("设为当前")
        self._btn_activate.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_activate.setStyleSheet(self._BTN_ACTIVATE_OFF)
        self._btn_activate.clicked.connect(self._activate_selected)
        top_row.addWidget(self._btn_activate)
        right_lay.addLayout(top_row)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("例：学术论文风格")
        self._name_input.setStyleSheet(_INPUT_STYLE)
        right_lay.addWidget(self._name_input)

        content_row = QHBoxLayout()
        content_title = QHBoxLayout()
        content_title.setSpacing(0)
        self._lbl_content_text = QLabel("提示词内容")
        self._lbl_content_text.setStyleSheet(_LBL_MUTED)
        self._lbl_content_star = QLabel("")
        self._lbl_content_star.setStyleSheet(_LBL_STAR)
        self._lbl_content_star.setVisible(False)
        content_title.addWidget(self._lbl_content_text)
        content_title.addWidget(self._lbl_content_star)
        content_row.addLayout(content_title)
        content_row.addStretch()
        self._lbl_row_unsaved = QLabel("")
        self._lbl_row_unsaved.setStyleSheet(_LBL_HINT)
        self._lbl_row_unsaved.setVisible(False)
        self._lbl_row_unsaved.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        content_row.addWidget(self._lbl_row_unsaved)
        right_lay.addLayout(content_row)

        self._content_edit = QTextEdit()
        self._content_edit.setPlaceholderText("输入润色提示词内容…")
        self._content_edit.setStyleSheet(_TEXTEDIT_STYLE)
        right_lay.addWidget(self._content_edit)

        right_btns = QHBoxLayout()
        right_btns.setSpacing(8)
        right_btns.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._btn_restore_factory = QPushButton("恢复默认模板")
        self._btn_restore_factory.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_restore_factory.setStyleSheet(_BTN)
        self._btn_restore_factory.clicked.connect(self._restore_factory_defaults)
        right_btns.addWidget(self._btn_restore_factory)
        right_btns.addStretch()
        self._btn_revert_all = QPushButton("全部还原")
        self._btn_revert_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_revert_all.setStyleSheet(self._BTN_REVERT_INACTIVE)
        self._btn_revert_all.setToolTip(
            "将全部提示词列表恢复为最后一次保存的磁盘版本（丢弃未保存修改）")
        self._btn_revert_all.clicked.connect(self._revert_all_from_disk)
        right_btns.addWidget(self._btn_revert_all)
        self._btn_save = QPushButton("保存")
        self._btn_save.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_save.setStyleSheet(self._BTN_SAVE_CLEAN)
        self._btn_save.clicked.connect(self._do_save)
        right_btns.addWidget(self._btn_save)
        btn_close = QPushButton("关闭")
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.setStyleSheet(_BTN)
        btn_close.clicked.connect(self.close)
        right_btns.addWidget(btn_close)
        right_lay.addLayout(right_btns)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter)

        self._name_input.textChanged.connect(self._on_editor_changed)
        self._content_edit.textChanged.connect(self._on_editor_changed)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._refresh_list()
        self._select_active_row()
        self._update_save_button()

    # ── 底部「保存」= 全局写入名称/内容/列表；「当前使用项」单独立即写入磁盘，不计入未保存 ──

    def _prompt_data_differs_from_disk(self) -> bool:
        """与磁盘不一致时视为未保存：仅名称、提示词内容、增删列表；不含 active_prompt_id。
        调用方须已调用过 flush 将编辑区写回 _prompts。"""
        if self._config_ref is None:
            return False
        disk = self._config_ref.custom_prompts
        if len(self._prompts) != len(disk):
            return True
        if [p["id"] for p in self._prompts] != [p["id"] for p in disk]:
            return True
        disk_map = {p["id"]: p for p in disk}
        for p in self._prompts:
            d = disk_map.get(p["id"])
            if d is None:
                return True
            if (p.get("name") or "") != (d.get("name") or "") or (
                p.get("content") or "") != (d.get("content") or ""):
                return True
        return False

    def _is_custom_entry_unsaved(self, pid: str) -> bool:
        """当前内存中的该条与磁盘 config 中是否不一致（不含 active_id，仅名称与内容）。"""
        if self._config_ref is None or not pid:
            return False
        p = next((x for x in self._prompts if x["id"] == pid), None)
        if p is None:
            return True
        d = next(
            (x for x in self._config_ref.custom_prompts if x["id"] == pid),
            None,
        )
        if d is None:
            return True
        return (p.get("name") or "") != (d.get("name") or "") or (
            p.get("content") or "") != (d.get("content") or "")

    def _disk_custom_entry(self, pid: str):
        if self._config_ref is None or not pid:
            return None
        return next(
            (x for x in self._config_ref.custom_prompts if x["id"] == pid),
            None,
        )

    def _is_custom_name_unsaved(self, pid: str) -> bool:
        if self._config_ref is None or not pid:
            return False
        p = next((x for x in self._prompts if x["id"] == pid), None)
        if p is None:
            return True
        d = self._disk_custom_entry(pid)
        if d is None:
            return True
        return (p.get("name") or "") != (d.get("name") or "")

    def _is_custom_content_unsaved(self, pid: str) -> bool:
        if self._config_ref is None or not pid:
            return False
        p = next((x for x in self._prompts if x["id"] == pid), None)
        if p is None:
            return True
        d = self._disk_custom_entry(pid)
        if d is None:
            return True
        return (p.get("content") or "") != (d.get("content") or "")

    def _format_row_text(self, row: int) -> str:
        if row == 0:
            return ("● " if not self._active_id else "   ") + "默认提示词"
        idx = row - 1
        if idx < 0 or idx >= len(self._prompts):
            return ""
        p = self._prompts[idx]
        pid = p["id"]
        prefix = "● " if self._active_id == pid else "   "
        name = (p.get("name") or "").strip() or "未命名"
        mark = " *" if self._is_custom_entry_unsaved(pid) else ""
        return f"{prefix}{name}{mark}"

    def _update_prompt_list_labels(self):
        """同步左侧列表文案（名称、未保存 *），不整表 clear，避免闪烁。

        调用方须已调用过 flush 将编辑区写回 _prompts。
        """
        if self._list.count() != 1 + len(self._prompts):
            self._refresh_list()
            return
        self._switching = True
        for row in range(self._list.count()):
            it = self._list.item(row)
            if it is not None:
                it.setText(self._format_row_text(row))
        self._switching = False

    def _update_right_unsaved_hint(self):
        """刷新右侧编辑区的未保存标记。使用 _last_row，调用方须已 flush。"""
        row = self._last_row
        try:
            if row < 0:
                self._lbl_row_unsaved.setVisible(False)
                self._lbl_name_star.setVisible(False)
                self._lbl_content_star.setVisible(False)
                return
            if self._is_default_row(row):
                self._lbl_name_star.setVisible(False)
                self._lbl_content_star.setVisible(False)
                self._lbl_row_unsaved.setVisible(False)
                return
            pid = self._selected_prompt_id(row)
            self._lbl_name_star.setText("*")
            self._lbl_name_star.setVisible(self._is_custom_name_unsaved(pid))
            self._lbl_content_star.setText("*")
            self._lbl_content_star.setVisible(self._is_custom_content_unsaved(pid))
            if self._is_custom_entry_unsaved(pid):
                self._lbl_row_unsaved.setText("本条修改尚未保存")
                self._lbl_row_unsaved.setVisible(True)
            else:
                self._lbl_row_unsaved.setVisible(False)
        finally:
            self._update_revert_row_button()

    def _update_revert_row_button(self):
        """「还原此项」可点击当且仅当：自定义项、磁盘已有该条、本条相对磁盘未保存。
        保持 QPushButton 为 enabled，用样式区分可点/灰显。调用方须已 flush。"""
        row = self._last_row
        if self._config_ref is None or row < 0:
            self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)
            return
        if self._is_default_row(row):
            self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)
            return
        pid = self._selected_prompt_id(row)
        if not pid:
            self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)
            return
        d = self._disk_custom_entry(pid)
        if d is None:
            self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)
            return
        if self._is_custom_entry_unsaved(pid):
            self._btn_revert_row.setStyleSheet(_BTN)
        else:
            self._btn_revert_row.setStyleSheet(self._BTN_REVERT_INACTIVE)

    def _update_save_button(self):
        if self._prompt_data_differs_from_disk():
            self._btn_save.setStyleSheet(self._BTN_SAVE_DIRTY)
            self._btn_save.setText("● 保存")
        else:
            self._btn_save.setStyleSheet(self._BTN_SAVE_CLEAN)
            self._btn_save.setText("保存")
        self._update_revert_all_button()

    def _update_revert_all_button(self):
        """「全部还原」：有磁盘快照且与内存不一致时可点，与「保存」脏状态一致。"""
        if self._config_ref is None:
            self._btn_revert_all.setStyleSheet(self._BTN_REVERT_INACTIVE)
            return
        if self._prompt_data_differs_from_disk():
            self._btn_revert_all.setStyleSheet(_BTN)
        else:
            self._btn_revert_all.setStyleSheet(self._BTN_REVERT_INACTIVE)

    def sync_from_config(self) -> None:
        """托盘等处已更新 config 时，将对话框与磁盘状态对齐。"""
        if self._config_ref is None:
            return
        self._prompts = copy.deepcopy(self._config_ref.custom_prompts)
        self._active_id = self._config_ref.active_prompt_id or ""
        self._refresh_list()
        self._select_active_row()
        self._update_save_button()

    def _on_editor_changed(self):
        if self._switching:
            return
        self._flush_editing_prompt()
        self._update_prompt_list_labels()
        self._update_right_unsaved_hint()
        self._update_save_button()

    # ── list ──

    def _refresh_list(self):
        """重建左侧列表项。不修改 _last_row——由调用方在操作完成后显式设置。"""
        self._switching = True
        prev = self._list.currentRow()
        self._list.clear()
        item = QListWidgetItem(self._format_row_text(0))
        item.setData(Qt.ItemDataRole.UserRole, _DEFAULT_PROMPT_ID)
        self._list.addItem(item)
        for i in range(len(self._prompts)):
            row = i + 1
            p = self._prompts[i]
            it = QListWidgetItem(self._format_row_text(row))
            it.setData(Qt.ItemDataRole.UserRole, p["id"])
            self._list.addItem(it)
        if prev >= 0 and prev < self._list.count():
            self._list.setCurrentRow(prev)
        self._switching = False

    def _select_active_row(self):
        target = 0
        if self._active_id:
            for i, p in enumerate(self._prompts):
                if p["id"] == self._active_id:
                    target = i + 1
                    break
        self._switching = True
        self._list.setCurrentRow(target)
        self._switching = False
        self._last_row = target
        self._load_editor(target)

    def _is_default_row(self, row: int) -> bool:
        if row < 0:
            return True
        item = self._list.item(row)
        return item is not None and item.data(Qt.ItemDataRole.UserRole) == _DEFAULT_PROMPT_ID

    def _selected_prompt_id(self, row: int) -> str:
        if row < 0:
            return ""
        item = self._list.item(row)
        if item is None:
            return ""
        pid = item.data(Qt.ItemDataRole.UserRole)
        return "" if pid == _DEFAULT_PROMPT_ID else (pid or "")

    # ── selection (browse only, no activation) ──

    def _on_row_changed(self, row: int):
        if self._switching:
            return
        self._flush_editing_prompt()
        self._last_row = row
        self._load_editor(row)
        self._update_save_button()

    def _on_list_order_swapped(self, row_a: int, row_b: int):
        """Visual rows swapped — rebuild _prompts order from the list widget."""
        self._flush_editing_prompt()
        id_to_prompt = {p["id"]: p for p in self._prompts}
        new_order: list[dict] = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it is None:
                continue
            pid = it.data(Qt.ItemDataRole.UserRole)
            if pid == _DEFAULT_PROMPT_ID:
                continue
            p = id_to_prompt.get(pid)
            if p is not None:
                new_order.append(p)
        self._prompts = new_order
        self._persist_order_to_disk()
        self._update_prompt_list_labels()
        self._update_save_button()

    def _load_editor(self, row: int):
        self._switching = True
        is_default = self._is_default_row(row)
        if is_default:
            self._editing_prompt_id = _DEFAULT_PROMPT_ID
            self._name_input.setText("默认提示词")
            self._content_edit.setPlainText(self._default_text)
            self._name_input.setReadOnly(True)
            self._content_edit.setReadOnly(True)
            self._name_input.setStyleSheet(_INPUT_READONLY_STYLE)
            self._content_edit.setStyleSheet(_TEXTEDIT_READONLY_STYLE)
        else:
            pid = self._selected_prompt_id(row)
            self._editing_prompt_id = pid or ""
            p = next((x for x in self._prompts if x["id"] == pid), None) if pid else None
            if p is not None:
                self._name_input.setText(p.get("name", ""))
                self._content_edit.setPlainText(p.get("content", ""))
            self._name_input.setReadOnly(False)
            self._content_edit.setReadOnly(False)
            self._name_input.setStyleSheet(_INPUT_STYLE)
            self._content_edit.setStyleSheet(_TEXTEDIT_STYLE)
        self._btn_delete.setEnabled(not is_default)
        self._btn_duplicate.setEnabled(not is_default)
        self._update_activate_button(row)
        self._switching = False
        self._update_prompt_list_labels()
        self._update_right_unsaved_hint()

    def _update_activate_button(self, row: int):
        pid = self._selected_prompt_id(row)
        is_default = self._is_default_row(row)
        already_active = (is_default and not self._active_id) or \
                         (not is_default and pid == self._active_id)
        if already_active:
            self._btn_activate.setText("✓ 当前使用中")
            self._btn_activate.setStyleSheet(self._BTN_ACTIVATE_ON)
            self._btn_activate.setEnabled(False)
        else:
            self._btn_activate.setText("设为当前")
            self._btn_activate.setStyleSheet(self._BTN_ACTIVATE_OFF)
            self._btn_activate.setEnabled(True)

    def _flush_editing_prompt(self) -> None:
        """将右侧编辑区写回 `_editing_prompt_id` 在 `_prompts` 中对应条目。

        唯一写回入口：行切换、重排、保存、关闭等凡需落内存处均调用本方法，
        不根据 QListWidget 行号当下标，避免拖拽后顺序与下标不一致。
        """
        pid = self._editing_prompt_id
        if not pid or pid == _DEFAULT_PROMPT_ID:
            return
        name = self._name_input.text().strip()
        content = self._content_edit.toPlainText().strip()
        for p in self._prompts:
            if p["id"] == pid:
                p["name"] = name
                p["content"] = content
                return

    def _persist_order_to_disk(self) -> None:
        """将当前内存中的条目顺序写盘，但保留磁盘版的 name/content。

        - 磁盘已有的条目：按内存 _prompts 的新顺序排列，name/content 保持磁盘值不变。
        - 磁盘没有的条目（新增未保存）：不写入磁盘，等用户手动保存。
        - 磁盘有但内存已删除的条目：不写入磁盘（删除的效果随拖拽生效）。
        """
        if self._config_ref is None:
            return
        disk_map = {p["id"]: p for p in self._config_ref.custom_prompts}
        reordered = [disk_map[p["id"]] for p in self._prompts if p["id"] in disk_map]
        self._config_ref.custom_prompts = reordered
        self._config_ref.save(touched=frozenset({"custom_prompts"}))
        if self._on_prompts_saved is not None:
            self._on_prompts_saved()

    def _run_modal_guarding_hotkey(self, fn: Callable[[], _T_modal]) -> _T_modal:
        """经 VoiceTray.run_modal_with_hotkey_paused：模态框期间卸全局热键，避免 pynput 与 Qt 抢键。"""
        runner = self._run_modal_with_hotkey_paused
        if runner is None:
            return fn()
        return runner(fn)

    # ── activation ──

    def _activate_selected(self):
        row = self._list.currentRow()
        self._flush_editing_prompt()

        if self._is_default_row(row):
            new_active = ""
        else:
            pid = self._selected_prompt_id(row)
            if not pid:
                return
            new_active = pid

        if new_active == self._active_id:
            return

        if self._prompt_data_differs_from_disk():
            def _ask_activate():
                box = QMessageBox(self)
                box.setWindowTitle("未保存的修改")
                box.setText(
                    "名称、提示词内容或列表的修改尚未保存。\n"
                    "请先保存后再设为当前使用项，或使用「保存并设为当前」。")
                box.setIcon(QMessageBox.Icon.Warning)
                btn_save = box.addButton("保存并设为当前", QMessageBox.ButtonRole.AcceptRole)
                btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(btn_save)
                box.setStyleSheet(_PROMPT_MSGBOX_STYLE)
                btn_save.setStyleSheet(_BTN_PRIMARY)
                btn_cancel.setStyleSheet(_BTN)
                box.exec()
                return box.clickedButton() is btn_save

            if not self._run_modal_guarding_hotkey(_ask_activate):
                return
            self._active_id = new_active
            self._do_save()
            self._update_activate_button(self._list.currentRow())
            if self._on_active_applied is not None:
                self._on_active_applied()
            return

        self._active_id = new_active
        if self._config_ref is not None:
            self._config_ref.active_prompt_id = self._active_id
            self._config_ref.save(touched=frozenset({"active_prompt_id"}))
            logger.info("[PromptDlg] Active prompt applied (prompt data unchanged)")
        self._refresh_list()
        self._update_activate_button(row)
        self._update_right_unsaved_hint()
        self._update_save_button()
        if self._on_active_applied is not None:
            self._on_active_applied()

    def _on_item_double_clicked(self, item):
        row = self._list.row(item)
        if row == self._list.currentRow():
            self._activate_selected()

    # ── add / delete / restore ──

    def _add_item(self):
        self._flush_editing_prompt()
        pid = uuid.uuid4().hex[:8]
        self._prompts.append({"id": pid, "name": "新提示词", "content": ""})
        new_row = len(self._prompts)
        self._refresh_list()
        self._switching = True
        self._list.setCurrentRow(new_row)
        self._switching = False
        self._last_row = new_row
        self._load_editor(new_row)
        self._name_input.setFocus()
        self._name_input.selectAll()
        self._update_save_button()

    def _duplicate_item(self):
        self._flush_editing_prompt()
        row = self._list.currentRow()
        if self._is_default_row(row):
            return
        idx = row - 1
        if idx < 0 or idx >= len(self._prompts):
            return
        src = self._prompts[idx]
        pid = uuid.uuid4().hex[:8]
        self._prompts.append(
            {
                "id": pid,
                "name": str(src.get("name") or ""),
                "content": str(src.get("content") or ""),
            },
        )
        new_row = len(self._prompts)
        self._refresh_list()
        self._switching = True
        self._list.setCurrentRow(new_row)
        self._switching = False
        self._last_row = new_row
        self._load_editor(new_row)
        self._name_input.setFocus()
        self._name_input.selectAll()
        self._update_save_button()

    def _delete_item(self):
        """删除列表中蓝框选中的那条自定义提示词。

        以 QListWidget.currentRow() 为唯一行号来源（删除按钮 focusPolicy=NoFocus，
        点击它不会改变列表选中项）。不依赖 _last_row，避免其被其他路径污染。
        """
        row = self._list.currentRow()
        if row < 0 or self._is_default_row(row):
            return
        idx = row - 1
        if idx < 0 or idx >= len(self._prompts):
            return
        len_before = len(self._prompts)
        removed = self._prompts.pop(idx)
        if self._active_id == removed["id"]:
            self._active_id = ""
        len_after = len(self._prompts)
        if idx < len_before - 1:
            target_row = row
        else:
            target_row = row - 1
        target_row = max(0, min(target_row, len_after))
        self._last_row = -1
        self._refresh_list()
        self._switching = True
        self._list.setCurrentRow(target_row)
        self._switching = False
        self._last_row = target_row
        self._load_editor(target_row)
        self._update_save_button()

    def _revert_current_row_from_disk(self):
        """仅将当前选中自定义项的名称与内容恢复为磁盘 config 中的版本。"""
        self._flush_editing_prompt()
        row = self._last_row
        if row < 0 or self._is_default_row(row):
            return
        idx = row - 1
        if idx < 0 or idx >= len(self._prompts):
            return
        pid = self._prompts[idx]["id"]
        d = self._disk_custom_entry(pid)
        if d is None or not self._is_custom_entry_unsaved(pid):
            return
        self._prompts[idx]["name"] = str(d.get("name") or "")
        self._prompts[idx]["content"] = str(d.get("content") or "")
        self._last_row = row
        self._load_editor(row)
        self._update_save_button()

    def _revert_all_from_disk(self):
        """将提示词列表与名称/内容恢复为磁盘 config 中最后一次保存的状态。"""
        if self._config_ref is None:
            return
        self._flush_editing_prompt()
        if not self._prompt_data_differs_from_disk():
            return

        def _ask_revert_all():
            box = QMessageBox(self)
            box.setWindowTitle("全部还原")
            box.setText("将放弃本次全部更改，是否继续？")
            box.setIcon(QMessageBox.Icon.Warning)
            btn_yes = box.addButton("是", QMessageBox.ButtonRole.AcceptRole)
            btn_no = box.addButton("否", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_no)
            box.setStyleSheet(_PROMPT_MSGBOX_STYLE)
            btn_yes.setStyleSheet(_BTN_DANGER)
            btn_no.setStyleSheet(_BTN)
            box.exec()
            return box.clickedButton() is btn_yes

        if not self._run_modal_guarding_hotkey(_ask_revert_all):
            return
        self._prompts = copy.deepcopy(self._config_ref.custom_prompts)
        self._active_id = self._config_ref.active_prompt_id or ""
        self._refresh_list()
        self._select_active_row()
        self._update_save_button()
        self._update_right_unsaved_hint()

    def _restore_factory_defaults(self):
        def _ask_restore():
            box = QMessageBox(self)
            box.setWindowTitle("恢复默认模板")
            box.setText(
                "将提示词列表恢复为默认模板，当前编辑将丢失。是否继续？")
            box.setIcon(QMessageBox.Icon.Warning)
            btn_yes = box.addButton("是", QMessageBox.ButtonRole.AcceptRole)
            btn_no = box.addButton("否", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(btn_no)
            box.setStyleSheet(_PROMPT_MSGBOX_STYLE)
            btn_yes.setStyleSheet(_BTN_DANGER)
            btn_no.setStyleSheet(_BTN)
            box.exec()
            return box.clickedButton() is btn_yes

        if not self._run_modal_guarding_hotkey(_ask_restore):
            return
        self._prompts = copy.deepcopy(default_prompt_templates())
        self._active_id = ""
        self._last_row = 0
        self._refresh_list()
        self._switching = True
        self._list.setCurrentRow(0)
        self._switching = False
        self._load_editor(0)
        self._update_save_button()

    # ── save / close ──

    def _do_save(self):
        self._flush_editing_prompt()
        self._accepted = True
        if self._config_ref is not None:
            self._config_ref.custom_prompts = copy.deepcopy(self._prompts)
            self._config_ref.active_prompt_id = self._active_id
            self._config_ref.save(
                touched=frozenset({"custom_prompts", "active_prompt_id"}),
            )
            logger.info("[PromptDlg] Saved prompts to config")
            if self._on_prompts_saved is not None:
                self._on_prompts_saved()
        self._refresh_list()
        self._update_save_button()
        self._update_right_unsaved_hint()

    def closeEvent(self, event):
        self._flush_editing_prompt()
        if self._prompt_data_differs_from_disk():
            def _ask_close():
                box = QMessageBox(self)
                box.setWindowTitle("未保存的修改")
                box.setText("当前有未保存的修改，关闭后将丢失。")
                box.setIcon(QMessageBox.Icon.Warning)
                btn_save = box.addButton("保存并关闭", QMessageBox.ButtonRole.AcceptRole)
                btn_discard = box.addButton("不保存", QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                box.setDefaultButton(btn_save)
                box.setStyleSheet(_PROMPT_MSGBOX_STYLE)
                btn_save.setStyleSheet(_BTN_PRIMARY)
                btn_discard.setStyleSheet(_BTN_DANGER)
                btn_cancel.setStyleSheet(_BTN)
                box.exec()
                clicked = box.clickedButton()
                if clicked is btn_save:
                    return "save"
                if clicked is btn_discard:
                    return "discard"
                return "cancel"

            choice = self._run_modal_guarding_hotkey(_ask_close)
            if choice == "save":
                self._do_save()
                self.accept()
                event.accept()
            elif choice == "discard":
                self.reject()
                event.accept()
            else:
                event.ignore()
            return
        if self._accepted:
            self.accept()
        else:
            self.reject()
        event.accept()
