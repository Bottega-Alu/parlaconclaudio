"""Sound packs as add-ons, downloaded on demand and updated without reinstalling the app.

Where they come from: the GitHub release tagged "addons" of this repository holds one
zip per pack plus manifest.json (built by scripts/build_addons.py):
    {"format": 1, "packs": [{"name": "dune", "file": "dune.zip", "sha256": "<64 hex>",
                             "size": 123456, "sounds": 34}, ...]}

Decision tree for one pack:
    catalogue entry malformed  -> skipped (never trusted)
    download fails             -> AddonError("network"), nothing on disk changes
    size or SHA256 differ      -> AddonError("integrity"), nothing changes
    zip entry not a flat audio/json file -> AddonError("unsafe"), nothing changes
    otherwise                  -> extracted to _partial-<name>, then swapped in place

Installed add-ons carry addon.json (their SHA256) in the pack folder. Packs copied from
the installer have none: they are never auto-updated or removed by update_installed().
update_if_due() checks at most once a day and updates only packs already installed.
"""

import hashlib
import io
import json
import logging
import os
import re
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RELEASE_URL = "https://github.com/Bottega-Alu/parlaconclaudio/releases/download/addons/"
MANIFEST_URL = RELEASE_URL + "manifest.json"
SOUNDS_DIR = Path.home() / ".claude" / "cache" / "tts" / "sounds"
STATE_FILE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / \
    "ParlaConClaudio" / "addons-state.json"
MARKER = "addon.json"
CHECK_EVERY_S = 24 * 3600
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PACK_BYTES = 64 * 1024 * 1024
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MEMBER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._ -]{0,120}\.(mp3|wav|json|txt)$")


class AddonError(Exception):
    """kind: network | integrity | unsafe | invalid | io"""

    def __init__(self, kind: str, detail: str):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


@dataclass(frozen=True)
class Pack:
    name: str
    file: str
    sha256: str
    size: int
    sounds: int = 0


def _urlopen_fetch(url: str, limit: int) -> bytes:
    """HTTPS GET (GitHub release assets redirect to their CDN), at most `limit` bytes."""
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "ParlaConClaudio-addons"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise AddonError("integrity", f"{url} is larger than {limit} bytes")
    return data


def _get(url: str, limit: int, fetch) -> bytes:
    try:
        return (fetch or _urlopen_fetch)(url, limit)
    except AddonError:
        raise
    except Exception as e:
        raise AddonError("network", f"{url}: {type(e).__name__}: {e}") from e


def fetch_catalog(fetch=None) -> list[Pack]:
    """The packs the add-on release offers; malformed entries are dropped."""
    raw = _get(MANIFEST_URL, MAX_MANIFEST_BYTES, fetch)
    try:
        doc = json.loads(raw)
        entries = doc["packs"] if isinstance(doc, dict) and doc.get("format") == 1 else None
    except (ValueError, KeyError) as e:
        raise AddonError("invalid", f"manifest.json: {e}") from e
    if not isinstance(entries, list):
        raise AddonError("invalid", "manifest.json: unknown format")
    packs = []
    for item in entries:
        try:
            pack = Pack(str(item["name"]), str(item["file"]), str(item["sha256"]), int(item["size"]),
                        int(item.get("sounds", 0)))
        except (KeyError, TypeError, ValueError):
            continue
        if (_NAME.match(pack.name) and pack.file == f"{pack.name}.zip" and _SHA256.match(pack.sha256)
                and 0 < pack.size <= MAX_PACK_BYTES):
            packs.append(pack)
        else:
            logger.warning(f"Add-on catalogue entry skipped: {pack.name!r}")
    return packs


def installed() -> dict[str, str]:
    """Installed add-on packs -> their SHA256 (packs from the installer are not listed)."""
    found = {}
    if SOUNDS_DIR.is_dir():
        for marker in SOUNDS_DIR.glob(f"*/{MARKER}"):
            if marker.parent.name.startswith("_"):
                continue
            try:
                found[marker.parent.name] = json.loads(marker.read_text(encoding="utf-8"))["sha256"]
            except (OSError, ValueError, KeyError):
                continue
    return found


def install(pack: Pack, fetch=None) -> None:
    """Download, verify and swap in one pack; on any error the old copy stays as it was."""
    blob = _get(RELEASE_URL + pack.file, pack.size, fetch)
    if len(blob) != pack.size or hashlib.sha256(blob).hexdigest() != pack.sha256:
        raise AddonError("integrity", f"{pack.file}: size or SHA256 differ from the catalogue")
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        members = archive.infolist()
    except zipfile.BadZipFile as e:
        raise AddonError("integrity", f"{pack.file}: {e}") from e
    for member in members:
        if member.is_dir() or not _MEMBER.match(member.filename):
            raise AddonError("unsafe", f"{pack.file}: refused entry {member.filename!r}")
    staging = SOUNDS_DIR / f"_partial-{pack.name}"
    old = SOUNDS_DIR / f"_old-{pack.name}"
    target = SOUNDS_DIR / pack.name
    try:
        for leftover in (staging, old):
            shutil.rmtree(leftover, ignore_errors=True)
        staging.mkdir(parents=True)
        for member in members:  # flat names only (checked above): no path can leave staging
            (staging / member.filename).write_bytes(archive.read(member))
        (staging / MARKER).write_text(json.dumps({"sha256": pack.sha256}), encoding="utf-8")
        if target.exists():
            target.rename(old)
        staging.rename(target)
        shutil.rmtree(old, ignore_errors=True)
    except OSError as e:
        if not target.exists() and old.exists():
            old.rename(target)  # put the previous copy back
        shutil.rmtree(staging, ignore_errors=True)
        raise AddonError("io", f"{pack.name}: {e}") from e
    logger.info(f"Sound pack add-on installed: {pack.name} ({pack.size} bytes)")


def remove(name: str) -> None:
    if _NAME.match(name):
        shutil.rmtree(SOUNDS_DIR / name, ignore_errors=True)


def update_installed(fetch=None) -> list[str]:
    """Bring installed add-ons to the catalogue version; returns the packs updated."""
    current = installed()
    updated = []
    for pack in fetch_catalog(fetch):
        if pack.name in current and current[pack.name] != pack.sha256:
            try:
                install(pack, fetch)
                updated.append(pack.name)
            except AddonError as e:
                logger.warning(f"Sound pack update failed: {e}")
    return updated


def update_if_due(fetch=None, now: float | None = None) -> list[str] | None:
    """Daily OTA check (None = not due yet). Never raises: it runs in the background."""
    now = time.time() if now is None else now
    try:
        last = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("checked", 0)
    except (OSError, ValueError, AttributeError):
        last = 0
    if now - float(last) < CHECK_EVERY_S:
        return None
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"checked": now}), encoding="utf-8")
    except OSError:
        pass
    try:
        return update_installed(fetch)
    except AddonError as e:
        logger.info(f"Sound pack update check skipped: {e}")
        return []
