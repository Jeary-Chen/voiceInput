"""Dialog component QSS — built exclusively from ``dialog_tokens``."""

from __future__ import annotations

from ui import dialog_tokens as t


# ── chrome & typography ───────────────────────────────────────────────────────

_DIALOG_CHROME_QSS = f"""
    QDialog {{
        background: {t._SURFACE_PAGE};
        color: {t._TEXT_PRIMARY};
        border: none;
        border-radius: {t._RADIUS_PANEL};
    }}
    QLabel {{
        color: {t._TEXT_PRIMARY};
        font-size: {t._FONT_BODY};
    }}
    QToolTip {{
        background-color: {t._SURFACE_TOOLTIP};
        color: {t._TEXT_BODY};
        border: 1px solid {t._BORDER_SUBTLE};
        padding: {t._SPACING_TOOLTIP_Y} {t._SPACING_TOOLTIP_X};
        border-radius: {t._RADIUS_TOOLTIP};
        font-size: {t._FONT_CAPTION};
        max-width: 420px;
    }}
"""
_DIALOG_TITLE_QSS = (
    f"font-size:{t._FONT_TITLE}; font-weight:600; color:{t._TEXT_PRIMARY};"
)
_DIALOG_SUBTITLE_QSS = (
    f"font-size:{t._FONT_SUBTITLE}; font-weight:600; color:{t._TEXT_PRIMARY};"
)
_DIALOG_META_QSS = (
    f"color:{t._TEXT_MUTED}; font-size:{t._FONT_BODY}; line-height:150%;"
)
_DIALOG_HINT_QSS = f"color:{t._TEXT_MUTED}; font-size:{t._FONT_CAPTION};"
_DIALOG_LBL_FORM_MUTED = _DIALOG_HINT_QSS
_DIALOG_LBL_FORM_STAR = (
    f"font-size:{t._FONT_CAPTION}; color:{t._ACCENT_WARNING}; padding:0 1px;"
)
_DIALOG_LBL_FORM_HINT = (
    f"font-size:{t._FONT_CAPTION}; color:{t._ACCENT_WARNING}; padding:0 2px;"
)

# ── layout chrome ─────────────────────────────────────────────────────────────

_DIALOG_SPLITTER_QSS = (
    f"QSplitter::handle {{ background:{t._SPLITTER_HANDLE}; }}"
)

# ── inputs & text areas ───────────────────────────────────────────────────────

_DIALOG_TEXTEDIT_QSS = f"""
    QTextEdit, QTextBrowser {{
        background: {t._SURFACE_READONLY};
        color: {t._TEXT_BODY};
        border: 1px solid {t._BORDER_READONLY};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_INPUT};
        font-size: {t._FONT_BODY};
        font-family: "Segoe UI", "Microsoft YaHei";
    }}
"""
_DIALOG_INPUT_QSS = f"""
    QLineEdit {{
        background: {t._SURFACE_PANEL};
        color: {t._TEXT_PRIMARY};
        border: 1px solid {t._BORDER_SUBTLE};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_INPUT};
        font-size: {t._FONT_BODY};
    }}
    QLineEdit:focus {{ border: 1px solid {t._ACCENT_FOCUS}; }}
"""
_DIALOG_INPUT_MONO_QSS = _DIALOG_INPUT_QSS + """
    QLineEdit { font-family: Consolas, monospace; }
"""
_DIALOG_INPUT_EDIT_QSS = _DIALOG_INPUT_QSS + f"""
    QLineEdit:read-only {{ color: {t._TEXT_DISABLED}; }}
"""
_DIALOG_INPUT_READONLY_QSS = f"""
    QLineEdit {{
        background: {t._SURFACE_READONLY};
        color: {t._TEXT_DISABLED};
        border: 1px solid {t._BORDER_READONLY};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_INPUT};
        font-size: {t._FONT_BODY};
    }}
"""
_DIALOG_TEXTEDIT_EDIT_QSS = f"""
    QTextEdit {{
        background: {t._SURFACE_PANEL};
        color: {t._TEXT_PRIMARY};
        border: 1px solid {t._BORDER_SUBTLE};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_INPUT};
        font-size: {t._FONT_BODY};
    }}
    QTextEdit:focus {{ border: 1px solid {t._ACCENT_FOCUS}; }}
"""
_DIALOG_TEXTEDIT_READONLY_QSS = f"""
    QTextEdit {{
        background: {t._SURFACE_READONLY};
        color: {t._TEXT_DISABLED};
        border: 1px solid {t._BORDER_READONLY};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_INPUT};
        font-size: {t._FONT_BODY};
    }}
"""
_DIALOG_LIST_QSS = f"""
    QListWidget {{
        background: {t._SURFACE_PANEL};
        border: 1px solid {t._BORDER_SUBTLE};
        border-radius: {t._RADIUS_CARD};
        padding: {t._SPACING_LIST};
        font-size: {t._FONT_BODY};
        color: {t._TEXT_PRIMARY};
        outline: none;
    }}
    QListWidget::item {{
        padding: {t._SPACING_LIST_ITEM_Y} {t._SPACING_LIST_ITEM_X};
        border-radius: {t._RADIUS_LIST_ITEM};
        margin: {t._SPACING_LIST_ITEM_MARGIN};
        border: 1px solid transparent;
        color: {t._TEXT_PRIMARY};
    }}
    QListWidget::item:selected {{
        background: transparent;
        border: 1px solid {t._ACCENT_FOCUS};
        color: {t._TEXT_PRIMARY};
    }}
    QListWidget::item:hover:!selected {{
        background: {t._SURFACE_LIST_ITEM_HOVER};
        color: {t._TEXT_PRIMARY};
    }}
"""

# ── buttons ───────────────────────────────────────────────────────────────────

_DIALOG_BTN_SECONDARY = f"""
    QPushButton {{ background:{t._SURFACE_BTN_SECONDARY}; color:{t._TEXT_PRIMARY};
                  border:1px solid {t._BORDER_SUBTLE};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._SURFACE_BTN_SECONDARY_HOVER};
                         border-color:{t._BORDER_BTN_SECONDARY_HOVER}; }}
    QPushButton:disabled {{ color:{t._TEXT_BTN_SECONDARY_DISABLED};
                            border-color:{t._BORDER_BTN_SECONDARY_DISABLED}; }}
"""
_DIALOG_BTN_PRIMARY = f"""
    QPushButton {{ background:{t._ACCENT_FOCUS}; color:{t._TEXT_PRIMARY};
                  border:1px solid {t._ACCENT_FOCUS};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._ACCENT_FOCUS_HOVER};
                         border-color:{t._ACCENT_FOCUS_HOVER}; }}
    QPushButton:disabled {{ background:{t._SURFACE_BTN_SECONDARY};
                            color:{t._TEXT_BTN_DISABLED};
                            border-color:{t._SURFACE_BTN_SECONDARY}; }}
"""
_DIALOG_BTN_DANGER = f"""
    QPushButton {{ background:{t._ACCENT_DANGER_BTN}; color:{t._TEXT_PRIMARY};
                  border:1px solid {t._ACCENT_DANGER_BTN_BORDER};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._ACCENT_DANGER_BTN_HOVER};
                         border-color:{t._ACCENT_DANGER_BTN_BORDER_HOVER}; }}
"""
_DIALOG_BTN_OUTLINE_DANGER = f"""
    QPushButton {{ background:transparent; color:{t._ACCENT_DANGER_TEXT};
                  border:1px solid {t._BORDER_DANGER};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._ACCENT_DANGER_BG};
                         border-color:{t._BORDER_DANGER_HOVER}; }}
    QPushButton:disabled {{ color:{t._ACCENT_DANGER_TEXT_DISABLED};
                            border-color:{t._BORDER_DANGER_MUTED}; }}
"""
_DIALOG_BTN_GHOST = f"""
    QPushButton {{ background:transparent; color:{t._TEXT_DISABLED};
                  border:1px solid {t._BORDER_GHOST};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._SURFACE_BTN_GHOST_HOVER};
                         color:{t._TEXT_PRIMARY}; }}
"""
_DIALOG_BTN_LINK = f"""
    QPushButton {{ background:transparent; color:{t._ACCENT_FOCUS}; border:none;
                  font-size:{t._FONT_CAPTION}; }}
    QPushButton:hover {{ color:{t._ACCENT_LINK_HOVER}; text-decoration:underline; }}
"""
_DIALOG_BTN_TEXT = f"""
    QPushButton {{ background:transparent; color:{t._TEXT_DISABLED}; border:none;
                  font-size:{t._FONT_CAPTION}; }}
    QPushButton:hover {{ color:{t._TEXT_PRIMARY}; }}
"""
_DIALOG_BTN_SAVE_CLEAN = f"""
    QPushButton {{ background:{t._SURFACE_BTN_SECONDARY}; color:{t._TEXT_SAVE_IDLE};
                  border:1px solid {t._BORDER_SUBTLE};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._SURFACE_BTN_SECONDARY_HOVER};
                         color:{t._TEXT_SAVE_IDLE_HOVER}; }}
"""
_DIALOG_BTN_SAVE_DIRTY = _DIALOG_BTN_PRIMARY
_DIALOG_BTN_ACTIVATE_ON = f"""
    QPushButton {{ background:{t._ACCENT_ACTIVATE_BG}; color:{t._ACCENT_ACTIVATE_TEXT};
                  border:1px solid {t._ACCENT_ACTIVATE_BORDER};
                  {t._BTN_METRICS} }}
"""
_DIALOG_BTN_ACTIVATE_OFF = _DIALOG_BTN_SECONDARY
_DIALOG_BTN_REVERT_INACTIVE = f"""
    QPushButton {{ background:{t._SURFACE_PANEL}; color:{t._TEXT_REVERT_INACTIVE};
                  border:1px solid {t._BORDER_READONLY};
                  {t._BTN_METRICS} }}
    QPushButton:hover {{ background:{t._SURFACE_BTN_SECONDARY};
                         color:{t._TEXT_REVERT_INACTIVE_HOVER};
                         border-color:{t._BORDER_SUBTLE}; }}
"""

# ── message box ───────────────────────────────────────────────────────────────

_DIALOG_MSGBOX_QSS = f"""
    QMessageBox {{
        background-color: {t._SURFACE_PANEL};
        color: {t._TEXT_BODY};
        border: 1px solid {t._BORDER_SUBTLE};
    }}
    QMessageBox QLabel {{
        color: {t._TEXT_BODY};
        font-size: {t._FONT_BODY};
    }}
"""

# ── hotkey capture ────────────────────────────────────────────────────────────

_DIALOG_HOTKEY_CAPTURE_DEFAULT = f"""
    background:{t._SURFACE_PANEL}; color:{t._TEXT_PRIMARY};
    border:1px solid {t._BORDER_SUBTLE};
    border-radius:{t._RADIUS_HOTKEY_CAPTURE};
    font-size:{t._FONT_HOTKEY_CAPTURE}; font-weight:bold;
"""
_DIALOG_HOTKEY_CAPTURE_OK = f"""
    background:{t._ACCENT_SUCCESS_BG}; color:{t._ACCENT_SUCCESS};
    border:1px solid {t._ACCENT_SUCCESS};
    border-radius:{t._RADIUS_HOTKEY_CAPTURE};
    font-size:{t._FONT_HOTKEY_CAPTURE}; font-weight:bold;
"""
_DIALOG_HOTKEY_CAPTURE_ERR = f"""
    background:{t._ACCENT_DANGER_BG}; color:{t._ACCENT_DANGER};
    border:1px solid {t._ACCENT_DANGER};
    border-radius:{t._RADIUS_HOTKEY_CAPTURE};
    font-size:{t._FONT_HOTKEY_CAPTURE}; font-weight:bold;
"""
_DIALOG_STATUS_ERROR = f"font-size:{t._FONT_CAPTION}; color:{t._ACCENT_DANGER_TEXT};"
_DIALOG_STATUS_SUCCESS = f"font-size:{t._FONT_CAPTION}; color:{t._ACCENT_SUCCESS};"
_DIALOG_STATUS_ERROR_STRONG = f"font-size:{t._FONT_CAPTION}; color:{t._ACCENT_DANGER};"
