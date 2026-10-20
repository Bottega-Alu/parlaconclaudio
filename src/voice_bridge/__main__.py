"""Entry point for: python(w) -m src.voice_bridge

Order matters:
  1. console-less streams: under pythonw sys.stdout/sys.stderr are None and any
     library that writes to them (tqdm progress, warnings) would raise; route
     them to os.devnull - the real log is voicebridge.log.
  2. single instance BEFORE importing bridge: bridge.py opens voicebridge.log in
     "w" mode at import, so a second launch would truncate the live log.
"""
import os
import sys
import time
from pathlib import Path

for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

from .single_instance import ensure_single_instance  # noqa: E402

_LOG = Path(__file__).resolve().parent.parent.parent / "voicebridge.log"


def _note(line: str) -> None:
    """Append-only note for a launch that does not become the bridge."""
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} [voice_bridge.launch] INFO: {line}\n")
    except OSError:
        pass


def run() -> int:
    may_run, reason = ensure_single_instance()
    if not may_run:
        _note(f"second launch (PID {os.getpid()}) exits: {reason}")
        return 0  # not a failure: the scheduled task must not retry it
    from .bridge import main

    main()
    return 0


if __name__ == "__main__":
    sys.exit(run())
