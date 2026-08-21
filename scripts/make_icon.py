"""Regenerate assets/voicebridge.ico from the tray's marble-sphere renderer.

Keeps the exe icon visually identical to the system-tray icon (same generator,
frozen at a fixed hue/time so the file is deterministic).

    .\venv\Scripts\python.exe scripts\make_icon.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.voice_bridge.tray_icon import _generate_marble_sphere  # noqa: E402

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
OUT = ROOT / "assets" / "voicebridge.ico"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sphere = _generate_marble_sphere(
        256, hue_offset=0.45, hue_range=0.30, saturation=0.80, brightness=0.98, time_val=0.0
    )
    sphere.save(OUT, format="ICO", sizes=ICO_SIZES)
    print(f"{OUT}  ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
