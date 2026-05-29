"""Dialog design tokens — single source of raw visual values.

Feature code should import QSS from ``dialog_components`` or ``dialog_styles``,
not hex literals from here.
"""

# ── surface ──────────────────────────────────────────────────────────────────

_SURFACE_PAGE = "#1e1e1e"
_SURFACE_PANEL = "#2a2a2a"
_SURFACE_READONLY = "#252525"
_SURFACE_TOOLTIP = "#2d2d2d"
_SURFACE_BTN_SECONDARY = "#333"
_SURFACE_BTN_SECONDARY_HOVER = "#444"
_SURFACE_BTN_GHOST_HOVER = "#2a2a2a"
_SURFACE_LIST_ITEM_HOVER = _SURFACE_BTN_SECONDARY

# ── text ─────────────────────────────────────────────────────────────────────

_TEXT_PRIMARY = "#fff"
_TEXT_BODY = "#ececec"
_TEXT_MUTED = "#aaa"
_TEXT_DISABLED = "#999"
_TEXT_BTN_DISABLED = "#666"
_TEXT_BTN_SECONDARY_DISABLED = "#555"
_TEXT_SAVE_IDLE = "#888"
_TEXT_SAVE_IDLE_HOVER = "#aaa"
_TEXT_REVERT_INACTIVE = "#666"
_TEXT_REVERT_INACTIVE_HOVER = "#888"

# ── border ───────────────────────────────────────────────────────────────────

_BORDER_SUBTLE = "#555"
_BORDER_READONLY = "#444"
_BORDER_GHOST = "#444"
_BORDER_BTN_SECONDARY_HOVER = "#666"
_BORDER_BTN_SECONDARY_DISABLED = "#444"
_BORDER_DANGER = "#553030"
_BORDER_DANGER_HOVER = "#ff3b30"
_BORDER_DANGER_MUTED = "#444"

# ── accent ───────────────────────────────────────────────────────────────────

_ACCENT_FOCUS = "#007aff"
_ACCENT_FOCUS_HOVER = "#0066dd"
_ACCENT_LINK_HOVER = "#339aff"
_ACCENT_SUCCESS = "#34c759"
_ACCENT_SUCCESS_BG = "#1a3a1a"
_ACCENT_DANGER = "#ff3b30"
_ACCENT_DANGER_BG = "#3a1a1a"
_ACCENT_DANGER_BTN = "#8b1a1a"
_ACCENT_DANGER_BTN_BORDER = "#a02020"
_ACCENT_DANGER_BTN_HOVER = "#a02020"
_ACCENT_DANGER_BTN_BORDER_HOVER = "#c03030"
_ACCENT_DANGER_TEXT = "#ff6b60"
_ACCENT_DANGER_TEXT_DISABLED = "#553030"
_ACCENT_WARNING = "#ff9f0a"
_ACCENT_ACTIVATE_BG = "#0a5c2a"
_ACCENT_ACTIVATE_TEXT = "#4cdf90"
_ACCENT_ACTIVATE_BORDER = "#1a8040"

# ── scrollbar / splitter ─────────────────────────────────────────────────────

_SCROLLBAR_HANDLE = _BORDER_SUBTLE
_SCROLLBAR_HANDLE_HOVER = _BORDER_BTN_SECONDARY_HOVER
_SPLITTER_HANDLE = _BORDER_READONLY

# ── spacing / radius / typography ────────────────────────────────────────────

_RADIUS_PANEL = "0px"
_RADIUS_CARD = "6px"
_RADIUS_TOOLTIP = "4px"
_RADIUS_HOTKEY_CAPTURE = "8px"
_RADIUS_LIST_ITEM = "4px"

_SPACING_TOOLTIP_X = "9px"
_SPACING_TOOLTIP_Y = "6px"
_SPACING_INPUT = "8px"
_SPACING_LIST = "4px"
_SPACING_LIST_ITEM_Y = "8px"
_SPACING_LIST_ITEM_X = "10px"
_SPACING_LIST_ITEM_MARGIN = "2px 0"

_FONT_BODY = "13px"
_FONT_CAPTION = "12px"
_FONT_TITLE = "18px"
_FONT_SUBTITLE = "14px"
_FONT_HOTKEY_CAPTURE = "18px"

_BTN_METRICS = (
    f"border-radius: {_RADIUS_CARD}; padding: 4px 14px; "
    f"font-size: {_FONT_BODY}; min-height: 26px;"
)

# QColor-friendly name for delegates
COLOR_TEXT_PRIMARY = _TEXT_PRIMARY
