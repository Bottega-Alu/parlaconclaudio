"""Build the sound pack add-ons: one deterministic zip per pack + manifest.json.

    venv\\Scripts\\python.exe scripts\\build_addons.py [out_dir]   (default installer/Output/addons)

Deterministic: same files -> same bytes -> same SHA256, so the app's daily update
(src/voice_bridge/addons.py) re-downloads a pack only when its content changed.
Publish by uploading every file in out_dir to the GitHub release tagged "addons"
(gh release upload addons <out_dir>/* --clobber).
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOUNDS = ROOT / "sounds"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def build_pack(pack_dir: Path, out_dir: Path) -> dict:
    target = out_dir / f"{pack_dir.name}.zip"
    files = sorted(p for p in pack_dir.iterdir() if p.is_file())
    with zipfile.ZipFile(target, "w") as archive:
        for path in files:
            info = zipfile.ZipInfo(path.name, date_time=FIXED_TIME)
            info.external_attr = 0o644 << 16
            # mp3 is already compressed: store it; deflate only the small json/txt.
            info.compress_type = zipfile.ZIP_STORED if path.suffix == ".mp3" else zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    blob = target.read_bytes()
    return {"name": pack_dir.name, "file": target.name, "sha256": hashlib.sha256(blob).hexdigest(),
            "size": len(blob), "sounds": sum(1 for p in files if p.suffix == ".mp3")}


def build(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    packs = [build_pack(d, out_dir) for d in sorted(SOUNDS.iterdir()) if d.is_dir() and not d.name.startswith("_")]
    manifest = {"format": 1, "packs": packs}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "installer" / "Output" / "addons"
    manifest = build(out)
    for pack in manifest["packs"]:
        print(f"{pack['file']:28} {pack['size'] / 1e6:6.2f} MB  {pack['sounds']:3} sounds  {pack['sha256'][:12]}")
    print(f"-> {out}")
