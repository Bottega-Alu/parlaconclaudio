#!/usr/bin/env python3
"""
Generate manifest.json for each sound pack.

Scans all MP3 files, extracts duration via ffprobe, and creates a
self-describing manifest.json per pack. Also reads _chime_mapping.json
if present, or falls back to hardcoded R2D2 mapping for legacy packs.

Usage:
    python scripts/generate_manifests.py [--pack PACK_NAME]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

CACHE_DIR = Path.home() / ".claude" / "cache" / "tts"
SOUNDS_DIR = CACHE_DIR / "sounds"

# Legacy R2D2 mapping (the only pack without _chime_mapping.json)
R2D2_LEGACY_CHIMES = {
    "task_done": ["acknowledged.mp3", "acknowledged-2.mp3"],
    "stop": ["excited.mp3", "excited-2.mp3"],
    "permission": ["worried.mp3", "8.mp3"],
    "question": ["chat.mp3", "3.mp3"],
    "idle": ["12.mp3", "19.mp3"],
    "auth": ["7.mp3", "18.mp3"],
    "default": ["13.mp3", "6.mp3"],
}

R2D2_LEGACY_META = {
    "acknowledged.mp3": {"label": "R2-D2 Acknowledged", "mood": "positive"},
    "acknowledged-2.mp3": {"label": "R2-D2 Acknowledged 2", "mood": "positive"},
    "excited.mp3": {"label": "R2-D2 Excited", "mood": "excited"},
    "excited-2.mp3": {"label": "R2-D2 Excited 2", "mood": "excited"},
    "worried.mp3": {"label": "R2-D2 Worried", "mood": "worried"},
    "chat.mp3": {"label": "R2-D2 Chat", "mood": "curious"},
    "shout.mp3": {"label": "R2-D2 Shout", "mood": "alert"},
    "1-screaming.mp3": {"label": "R2-D2 Screaming", "mood": "alert"},
    "r2d2-sing-sound-effect.mp3": {"label": "R2-D2 Singing", "mood": "happy"},
}


def get_duration_ms(filepath: Path) -> int:
    """Get audio duration in milliseconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(filepath)],
            capture_output=True, text=True, timeout=5,
        )
        seconds = float(result.stdout.strip())
        return int(seconds * 1000)
    except Exception:
        return 0


def build_event_index(chimes: dict) -> dict[str, str]:
    """Reverse mapping: filename -> event_type."""
    index = {}
    for event, files in chimes.items():
        for f in files:
            index[f] = event
    return index


def generate_manifest(pack_dir: Path) -> dict:
    """Generate manifest.json for a single pack."""
    pack_name = pack_dir.name

    # Load chime mapping
    mapping_file = pack_dir / "_chime_mapping.json"
    if mapping_file.is_file():
        chimes = json.loads(mapping_file.read_text())
    elif pack_name == "r2d2":
        chimes = R2D2_LEGACY_CHIMES
    else:
        # Auto-generate: all sounds go to "default"
        all_mp3s = [f.name for f in sorted(pack_dir.glob("*.mp3"))]
        chimes = {"default": all_mp3s}

    event_index = build_event_index(chimes)

    # Scan all MP3 files and extract metadata
    sounds = {}
    for mp3 in sorted(pack_dir.glob("*.mp3")):
        if mp3.name.startswith("_"):
            continue
        duration = get_duration_ms(mp3)
        event = event_index.get(mp3.name, "unmapped")

        meta = {"duration_ms": duration, "event": event}

        # Add legacy labels for R2D2
        if pack_name == "r2d2" and mp3.name in R2D2_LEGACY_META:
            meta.update(R2D2_LEGACY_META[mp3.name])

        sounds[mp3.name] = meta

    manifest = {
        "pack": pack_name,
        "version": "1.0",
        "description": _pack_description(pack_name),
        "sound_count": len(sounds),
        "chimes": chimes,
        "sounds": sounds,
    }

    return manifest


def _pack_description(name: str) -> str:
    descs = {
        "r2d2": "R2-D2 semantic chimes - robot beeps and boops",
        "south-park": "South Park character sounds - Cartman, Kenny, Butters & friends",
        "horror-zombie": "Horror/zombie themed sounds - grunts, stingers, groans",
    }
    return descs.get(name, f"Custom sound pack: {name}")


def main():
    parser = argparse.ArgumentParser(description="Generate manifest.json for sound packs")
    parser.add_argument("--pack", help="Generate for specific pack only")
    args = parser.parse_args()

    if not SOUNDS_DIR.is_dir():
        print(f"No sounds directory found at {SOUNDS_DIR}")
        sys.exit(1)

    packs = []
    if args.pack:
        pack_dir = SOUNDS_DIR / args.pack
        if pack_dir.is_dir():
            packs.append(pack_dir)
        else:
            print(f"Pack '{args.pack}' not found")
            sys.exit(1)
    else:
        packs = [d for d in sorted(SOUNDS_DIR.iterdir()) if d.is_dir()]

    for pack_dir in packs:
        print(f"\nGenerating manifest for: {pack_dir.name}")
        manifest = generate_manifest(pack_dir)

        manifest_path = pack_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

        print(f"  Sounds: {manifest['sound_count']}")
        print(f"  Events: {list(manifest['chimes'].keys())}")
        print(f"  Saved: {manifest_path}")

    print(f"\nDone! Generated {len(packs)} manifests.")


if __name__ == "__main__":
    main()
