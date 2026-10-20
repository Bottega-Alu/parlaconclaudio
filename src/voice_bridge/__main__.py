"""Entry point for: python(w) -m src.voice_bridge [--child [--append-log]]

Every launch path (logon task, Run-key fallback, VoiceBridge.exe, VoiceBridge.bat)
lands here, so this is the one place that decides what a process becomes:

  no flag   -> SUPERVISOR: single-instance check, then supervisor.supervise()
               runs the bridge as a child and relaunches it after a crash
  --child   -> BRIDGE: started by the supervisor; runs bridge.main(). It does not
               touch the mutex: the supervisor holds it for both.

Order matters:
  1. console-less streams: under pythonw sys.stdout/sys.stderr are None and any
     library that writes to them (tqdm progress, warnings) would raise; route
     them to os.devnull - the real log is voicebridge.log.
  2. single instance BEFORE anything imports bridge: bridge.py opens
     voicebridge.log in "w" mode at import, so a second launch would truncate
     the live log. The supervisor never imports bridge at all.
"""
import os
import sys
import time
from pathlib import Path

for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))

from .single_instance import ensure_single_instance  # noqa: E402
from .supervisor import CHILD_FLAG, supervise  # noqa: E402

_LOG = Path(__file__).resolve().parent.parent.parent / "voicebridge.log"


def _note(line: str) -> None:
    """Append-only note for a launch that does not become the bridge."""
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} [voice_bridge.launch] INFO: {line}\n")
    except OSError:
        pass


def run_supervisor() -> int:
    may_run, reason = ensure_single_instance()
    if not may_run:
        _note(f"second launch (PID {os.getpid()}) exits: {reason}")
        return 0  # not a failure: the scheduled task must not retry it
    return supervise().exit_code


def run_child() -> int:
    import logging

    from .bridge import main

    logging.getLogger("voice_bridge.launch").info(
        f"bridge PID {os.getpid()} supervised by PID {os.getppid()}")
    main()
    return 0


def run(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return run_child() if CHILD_FLAG in argv else run_supervisor()


if __name__ == "__main__":
    sys.exit(run())
