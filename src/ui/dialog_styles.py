"""Dialog styling facade — tokens, components, and apply helpers.

Import from this module in feature code. Internal split:

  dialog_tokens     raw hex / spacing / typography
  dialog_components QSS built from tokens
  dialog_styles     re-exports + apply_dialog_chrome()
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ui import dialog_components as _components
from ui import dialog_tokens as _tokens

# Star-import skips underscore names; re-export the dialog API explicitly.
for _mod in (_tokens, _components):
    for _name in dir(_mod):
        if _name.startswith("_DIALOG") or _name == "COLOR_TEXT_PRIMARY":
            globals()[_name] = getattr(_mod, _name)

del _mod, _name, _tokens, _components


def apply_dialog_chrome(widget: QWidget) -> None:
    """Apply shared dialog shell (background, labels, tooltips)."""
    widget.setStyleSheet(_DIALOG_CHROME_QSS)
