"""Recognition text delivery preferences (config.output_mode).

Tray submenu 「有输入焦点时」 chooses the preference when typing is possible.
``TextInjector.deliver`` owns the full policy, including no-focus / type-failure
clipboard fallback — see ``core.injector``.

Modes:
  copy        — clipboard only, never type
  paste       — type into focus when possible; else clipboard
  paste_copy  — always clipboard, and type when focus can accept it
"""

from __future__ import annotations

OUTPUT_MODE_COPY = "copy"
OUTPUT_MODE_PASTE = "paste"
OUTPUT_MODE_PASTE_COPY = "paste_copy"

OUTPUT_MODES = frozenset({
    OUTPUT_MODE_COPY,
    OUTPUT_MODE_PASTE,
    OUTPUT_MODE_PASTE_COPY,
})

DEFAULT_OUTPUT_MODE = OUTPUT_MODE_PASTE_COPY

# Removed from Config; stripped from disk on load when still present.
LEGACY_OUTPUT_CONFIG_KEYS = frozenset({
    "paste_result",
    "restore_clipboard",
    "simulate_keypresses",
})

OUTPUT_MODE_LABELS = (
    (OUTPUT_MODE_COPY, "仅复制到剪贴板"),
    (OUTPUT_MODE_PASTE, "仅写入焦点处"),
    (OUTPUT_MODE_PASTE_COPY, "写入焦点并复制"),
)


DELIVER_COPIED = "copied"
DELIVER_PASTED = "pasted"
DELIVER_PASTED_COPIED = "pasted_copied"
DELIVER_FAILED = "failed"


def normalize_output_mode(value) -> str:
    if isinstance(value, str) and value in OUTPUT_MODES:
        return value
    return DEFAULT_OUTPUT_MODE


def output_mode_from_legacy(
    paste_result: bool = True,
    restore_clipboard: bool = False,
) -> str:
    """Map pre-output_mode boolean pair to the canonical mode."""
    if not paste_result:
        return OUTPUT_MODE_COPY
    if restore_clipboard:
        return OUTPUT_MODE_PASTE
    return OUTPUT_MODE_PASTE_COPY


def resolve_output_mode_from_raw(raw_data: dict) -> str:
    """Prefer explicit output_mode; else derive from legacy keys if present."""
    if "output_mode" in raw_data:
        return normalize_output_mode(raw_data.get("output_mode"))
    if LEGACY_OUTPUT_CONFIG_KEYS & raw_data.keys():
        return output_mode_from_legacy(
            bool(raw_data.get("paste_result", True)),
            bool(raw_data.get("restore_clipboard", False)),
        )
    return DEFAULT_OUTPUT_MODE
