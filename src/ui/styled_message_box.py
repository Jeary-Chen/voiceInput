"""Styled alert dialogs using the shared dark-theme component library."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.notification_spec import NotificationSeverity
from ui import icons
from ui.dialog_styles import (
    _DIALOG_BTN_DANGER,
    _DIALOG_BTN_GHOST,
    _DIALOG_BTN_OUTLINE_DANGER,
    _DIALOG_BTN_PRIMARY,
    _DIALOG_BTN_SECONDARY,
    _DIALOG_MSGBOX_QSS,
    _DIALOG_HINT_QSS,
    _DIALOG_INPUT_READONLY_QSS,
    _DIALOG_META_QSS,
    _DIALOG_SUBTITLE_QSS,
    _DIALOG_TITLE_QSS,
    apply_dialog_chrome,
    create_dialog_root_layout,
)

_VARIANT_QSS = {
    "primary": _DIALOG_BTN_PRIMARY,
    "secondary": _DIALOG_BTN_SECONDARY,
    "ghost": _DIALOG_BTN_GHOST,
    "danger": _DIALOG_BTN_DANGER,
    "outline_danger": _DIALOG_BTN_OUTLINE_DANGER,
}


@dataclass(frozen=True)
class DialogButton:
    label: str
    variant: str = "secondary"
    default: bool = False


class _StyledAlertDialog(QDialog):
    """Layout-aligned with update / API-key dialogs (chrome + titled sections)."""

    def __init__(
        self,
        *,
        window_title: str,
        heading: str,
        body: str,
        parent: QWidget | None = None,
        detail_label: str | None = None,
        detail_text: str | None = None,
        hints: list[str] | None = None,
        buttons: list[DialogButton],
        min_width: int = 480,
    ):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setWindowIcon(icons.app_icon())
        self.setMinimumWidth(min_width)
        apply_dialog_chrome(self)

        self._chosen: str | None = None

        root = create_dialog_root_layout(self, spacing=12)

        title = QLabel(heading)
        title.setStyleSheet(_DIALOG_TITLE_QSS)
        title.setWordWrap(True)
        root.addWidget(title)

        summary = QLabel(body)
        summary.setWordWrap(True)
        summary.setStyleSheet(_DIALOG_META_QSS)
        root.addWidget(summary)

        if detail_text:
            section = QLabel(detail_label or "详情")
            section.setStyleSheet(_DIALOG_SUBTITLE_QSS)
            root.addWidget(section)

            from PyQt6.QtWidgets import QLineEdit

            detail = QLineEdit(detail_text)
            detail.setReadOnly(True)
            detail.setStyleSheet(_DIALOG_INPUT_READONLY_QSS)
            root.addWidget(detail)

        if hints:
            hint_title = QLabel("可选操作")
            hint_title.setStyleSheet(_DIALOG_SUBTITLE_QSS)
            root.addWidget(hint_title)

            for line in hints:
                item = QLabel(line)
                item.setWordWrap(True)
                item.setStyleSheet(_DIALOG_HINT_QSS)
                root.addWidget(item)

        root.addSpacing(4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        default_btn: QPushButton | None = None
        for spec in buttons:
            btn = QPushButton(spec.label)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet(_VARIANT_QSS.get(spec.variant, _DIALOG_BTN_SECONDARY))
            btn.clicked.connect(lambda _checked=False, label=spec.label: self._choose(label))
            btn_row.addWidget(btn)
            if spec.default:
                default_btn = btn

        root.addLayout(btn_row)

        if default_btn is not None:
            default_btn.setDefault(True)
            default_btn.setAutoDefault(True)

        self.adjustSize()

    def _choose(self, label: str) -> None:
        self._chosen = label
        self.accept()

    @property
    def chosen_label(self) -> str | None:
        return self._chosen


def show_styled_dialog(
    *,
    parent: QWidget | None,
    window_title: str,
    heading: str,
    body: str,
    detail_label: str | None = None,
    detail_text: str | None = None,
    hints: list[str] | None = None,
    buttons: list[DialogButton],
    min_width: int = 480,
) -> str | None:
    """Show a styled alert dialog. Returns the clicked button label, or None."""
    dialog = _StyledAlertDialog(
        parent=parent,
        window_title=window_title,
        heading=heading,
        body=body,
        detail_label=detail_label,
        detail_text=detail_text,
        hints=hints,
        buttons=buttons,
        min_width=min_width,
    )
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen_label


def show_styled_message_box(
    *,
    parent: QWidget | None,
    title: str,
    text: str,
    informative_text: str | None = None,
    severity: NotificationSeverity = NotificationSeverity.WARNING,
    buttons: list[tuple[str, object]] | None = None,
    default_index: int = 0,
) -> str | None:
    """Backward-compatible wrapper around :func:`show_styled_dialog`."""
    del severity  # heading/body carry the message; no platform icon strip

    if buttons:
        mapped: list[DialogButton] = []
        for i, (label, role) in enumerate(buttons):
            from PyQt6.QtWidgets import QMessageBox

            if role in (
                QMessageBox.ButtonRole.AcceptRole,
                QMessageBox.ButtonRole.ActionRole,
            ):
                variant = "primary"
            elif role is QMessageBox.ButtonRole.DestructiveRole:
                variant = "outline_danger"
            else:
                variant = "secondary"
            mapped.append(
                DialogButton(label, variant=variant, default=(i == default_index))
            )
    else:
        mapped = [DialogButton("确定", variant="primary", default=True)]

    hints: list[str] | None = None
    detail_text: str | None = None
    if informative_text:
        lines = [ln.strip() for ln in informative_text.splitlines() if ln.strip()]
        if len(lines) == 1 and ("\\" in lines[0] or ":/" in lines[0]):
            detail_text = lines[0]
        elif lines and lines[0].startswith("•"):
            hints = lines
        else:
            detail_text = informative_text

    return show_styled_dialog(
        parent=parent,
        window_title=title,
        heading=title,
        body=text,
        detail_text=detail_text,
        hints=hints,
        buttons=mapped,
    )


# Legacy QMessageBox styling — prompt_dialog still uses raw QMessageBox + this helper.
from PyQt6.QtWidgets import QMessageBox  # noqa: E402


def apply_message_box_style(
    box: QMessageBox,
    *,
    destructive: str = "solid",
) -> None:
    box.setStyleSheet(_DIALOG_MSGBOX_QSS)
    destructive_qss = (
        _DIALOG_BTN_OUTLINE_DANGER
        if destructive == "outline"
        else _DIALOG_BTN_DANGER
    )
    for btn in box.buttons():
        role = box.buttonRole(btn)
        if role in (
            QMessageBox.ButtonRole.AcceptRole,
            QMessageBox.ButtonRole.ActionRole,
        ):
            btn.setStyleSheet(_DIALOG_BTN_PRIMARY)
        elif role is QMessageBox.ButtonRole.DestructiveRole:
            btn.setStyleSheet(destructive_qss)
        else:
            btn.setStyleSheet(_DIALOG_BTN_SECONDARY)
