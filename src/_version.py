import sys
from pathlib import Path

def _read_version() -> str:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS))
    here = Path(__file__).resolve().parent
    candidates.extend([here.parent, here])
    for base in candidates:
        p = base / "VERSION"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    return "dev"

VERSION = _read_version()
