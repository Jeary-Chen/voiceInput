"""Shared dialog color palette and QSS templates.

Used by update dialogs, API-key dialog, and prompt manager dialog
to maintain a consistent dark-theme appearance.
"""

_DIALOG_PANEL_BG = "#2a2a2a"
_DIALOG_PANEL_BORDER = "#555"
_DIALOG_READONLY_BG = "#252525"
_DIALOG_READONLY_BORDER = "#444"
_DIALOG_BG = "#1e1e1e"
_DIALOG_FOCUS = "#007aff"
_DIALOG_MUTED = "#aaa"
_DIALOG_TEXT = "#ececec"
_DIALOG_TOOLTIP_BG = "#2d2d2d"
_DIALOG_SB_HANDLE = "#555"
_DIALOG_SB_HANDLE_HOVER = "#666"


def _dialog_qss_scrollbar(widget_prefix: str, track_bg: str) -> str:
    return f"""
    {widget_prefix} QScrollBar:vertical {{ background: {track_bg}; }}
    {widget_prefix} QScrollBar::handle:vertical {{ background: {_DIALOG_SB_HANDLE}; }}
    {widget_prefix} QScrollBar::handle:vertical:hover {{ background: {_DIALOG_SB_HANDLE_HOVER}; }}
    {widget_prefix} QScrollBar:horizontal {{ background: {track_bg}; }}
    {widget_prefix} QScrollBar::handle:horizontal {{ background: {_DIALOG_SB_HANDLE}; }}
    {widget_prefix} QScrollBar::handle:horizontal:hover {{ background: {_DIALOG_SB_HANDLE_HOVER}; }}
    """


_DIALOG_CHROME_QSS = f"""
    QDialog {{
        background: {_DIALOG_BG};
        color: #fff;
        border: none;
        border-radius: 0px;
    }}
    QLabel {{
        color: #fff;
        font-size: 13px;
    }}
    QToolTip {{
        background-color: {_DIALOG_TOOLTIP_BG};
        color: {_DIALOG_TEXT};
        border: 1px solid {_DIALOG_PANEL_BORDER};
        padding: 6px 9px;
        border-radius: 4px;
        font-size: 12px;
        max-width: 420px;
    }}
"""
_DIALOG_TITLE_QSS = "font-size:18px; font-weight:600; color:#fff;"
_DIALOG_SUBTITLE_QSS = "font-size:14px; font-weight:600; color:#fff;"
_DIALOG_META_QSS = f"color:{_DIALOG_MUTED}; font-size:13px; line-height:150%;"
_DIALOG_HINT_QSS = f"color:{_DIALOG_MUTED}; font-size:12px;"
_DIALOG_TEXTEDIT_QSS = f"""
    QTextEdit, QTextBrowser {{
        background: {_DIALOG_READONLY_BG};
        color: {_DIALOG_TEXT};
        border: 1px solid {_DIALOG_READONLY_BORDER};
        border-radius: 6px;
        padding: 8px;
        font-size: 13px;
        font-family: "Segoe UI", "Microsoft YaHei";
    }}
""" + _dialog_qss_scrollbar("QTextEdit", _DIALOG_READONLY_BG) + _dialog_qss_scrollbar("QTextBrowser", _DIALOG_READONLY_BG)
_DIALOG_BTN_METRICS = "border-radius: 6px; padding: 4px 14px; font-size: 13px; min-height: 26px;"
_DIALOG_BTN_SECONDARY = f"""
    QPushButton {{ background:#333; color:#fff; border:1px solid #555;
                  {_DIALOG_BTN_METRICS} }}
    QPushButton:hover {{ background:#444; border-color:#666; }}
    QPushButton:disabled {{ color:#555; border-color:#444; }}
"""
_DIALOG_BTN_PRIMARY = f"""
    QPushButton {{ background:{_DIALOG_FOCUS}; color:#fff; border:1px solid {_DIALOG_FOCUS};
                  {_DIALOG_BTN_METRICS} }}
    QPushButton:hover {{ background:#0066dd; border-color:#0066dd; }}
"""
