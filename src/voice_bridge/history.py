"""Dictation history: every transcription the bridge produced, newest last.

One JSON object per line in %LOCALAPPDATA%\\ParlaConClaudio\\history.jsonl:
    {"ts": "2026-10-28T09:12:03", "text": "...", "outcome": "pasted" | "clipboard" | "failed"}
Only the newest MAX_ENTRIES are kept. Text stays on this PC; the app window
shows it on the Home page and can clear it.
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ENTRIES = 100
OUTCOMES = ("pasted", "clipboard", "failed")
_lock = threading.Lock()


def history_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ParlaConClaudio" / "history.jsonl"


def entries(path: Path | None = None) -> list[dict]:
    """Saved transcriptions, oldest first; unreadable lines are skipped."""
    path = path or history_file()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            found.append(item)
    return found


def add(text: str, outcome: str, path: Path | None = None) -> None:
    """Record one transcription. Never raises: history must not break dictation."""
    if not text.strip():
        return
    path = path or history_file()
    item = {"ts": datetime.now().isoformat(timespec="seconds"), "text": text,
            "outcome": outcome if outcome in OUTCOMES else "failed"}
    try:
        with _lock:
            kept = (entries(path) + [item])[-MAX_ENTRIES:]
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept), encoding="utf-8")
            os.replace(tmp, path)
    except OSError:
        logger.exception("Dictation history not saved")


def clear(path: Path | None = None) -> None:
    path = path or history_file()
    with _lock:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Dictation history not cleared")
