"""Regenerate assets/voicebridge.ico: the tray's default logo (wave) in white on a dark tile.

The tray draws the logo bare, tinted for the taskbar. Explorer, the Start menu and the
installer can sit on light or dark backgrounds, so the exe icon carries its own dark
rounded tile. Each size is rendered on its own (no downscaled 256 px blur at 16 px),
so the file is deterministic and crisp.

    .\venv\Scripts\python.exe scripts\make_icon.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.voice_bridge import tray_logo  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT = ROOT / "assets" / "voicebridge.ico"
TILE = (32, 32, 32, 255)
LOGO = (255, 255, 255)
SUPERSAMPLE = 4


def render(size):
    from PIL import Image, ImageDraw
    big = size * SUPERSAMPLE
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 0.22), fill=TILE)
    tile = tile.resize((size, size), Image.LANCZOS)
    inset = max(1, round(size * 0.10))
    logo = tray_logo.frame(tray_logo.DEFAULT, "idle", 0, size - 2 * inset, LOGO)
    tile.alpha_composite(logo, (inset, inset))
    return tile


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [render(size) for size in SIZES]
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"{OUT}  ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
