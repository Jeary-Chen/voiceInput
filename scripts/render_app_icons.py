"""
Render VoiceInput app icon (PNG + multi-size ICO) for PyInstaller / installers.

Requires: pip install Pillow
Run from repo root: python _scripts/render_app_icons.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"


def _need_pillow() -> bool:
    try:
        from PIL import Image, ImageDraw  # noqa: F401

        return True
    except ImportError:
        return False


def draw_icon(size: int):
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    r_rect = int(size * 0.22)
    pad = max(1, size // 64)
    draw.rounded_rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        radius=r_rect,
        fill=(26, 26, 26, 255),
    )

    cx = size // 2
    arc_y = int(size * 0.22)
    arc_h = int(size * 0.28)
    arc_w = int(size * 0.52)
    x0 = cx - arc_w // 2
    lw = max(2, size // 32)
    draw.arc(
        [x0, arc_y, x0 + arc_w, arc_y + arc_h],
        start=200,
        end=340,
        fill=(0, 122, 255, 255),
        width=lw,
    )
    draw.arc(
        [x0, arc_y, x0 + arc_w, arc_y + arc_h],
        start=20,
        end=160,
        fill=(255, 59, 48, 255),
        width=lw,
    )

    mic_w = max(size // 8, 4)
    mic_h = int(size * 0.38)
    top = int(size * 0.18)
    body = [
        cx - mic_w // 2,
        top,
        cx + mic_w // 2,
        top + mic_h,
    ]
    draw.rounded_rectangle(body, radius=mic_w // 2, fill=(224, 224, 224, 255))

    stand_top = top + mic_h - max(1, size // 64)
    base_y = int(size * 0.78)
    line_w = max(2, size // 28)
    draw.line(
        [(cx, stand_top), (cx, base_y)],
        fill=(224, 224, 224, 255),
        width=line_w,
    )
    bw = int(size * 0.28)
    draw.line(
        [(cx - bw // 2, base_y), (cx + bw // 2, base_y)],
        fill=(224, 224, 224, 255),
        width=line_w,
    )

    return im


def main() -> None:
    if not _need_pillow():
        print("Installing Pillow…")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "Pillow", "-q"],
        )
        if not _need_pillow():
            sys.exit("Could not import Pillow after install.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUT_DIR / "app_icon.png"
    ico_path = OUT_DIR / "app_icon.ico"

    master = draw_icon(512)
    master.save(png_path, "PNG")
    print(f"[OK] {png_path}")

    master.save(
        ico_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"[OK] {ico_path}")


if __name__ == "__main__":
    main()
