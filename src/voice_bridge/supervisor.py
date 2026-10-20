"""Supervisor: run the bridge as a child process and relaunch it after a crash.

Why: the logon task's RestartOnFailure only covers a task that fails to START;
a bridge that exits with an error afterwards stays dead until the next sign-in.

Every launch path ends in `python(w) -m src.voice_bridge` (logon task, Run-key
fallback, VoiceBridge.exe stub, VoiceBridge.bat), so __main__ is the single
place where the supervisor lives:

  supervisor  holds the single-instance mutex for its whole life
     |        (a second launch sees the mutex and exits; a dead child never
     |         held it, so a relaunch is never blocked by a stale lock)
     +-- child  <same interpreter> -m src.voice_bridge --child [--append-log]
                runs bridge.main(); never touches the mutex

Decision tree on child exit (supervise):
  exit 0                                   -> voluntary (tray Exit): stop, return 0
  exit != 0, < MAX_RELAUNCHES in WINDOW_S  -> log, short backoff, relaunch
  exit != 0, budget used up                -> structured error in the log, stop,
                                              return EXIT_GAVE_UP
  child cannot be started                  -> structured error, return EXIT_SPAWN_FAILED

Relaunched children get --append-log so bridge.py does not truncate the log
holding the crash traceback. Supervisor lines go to voicebridge.log in append
mode and are written only while no child is alive.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

APP_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = APP_ROOT / "voicebridge.log"

CHILD_FLAG = "--child"
APPEND_LOG_FLAG = "--append-log"

MAX_RELAUNCHES = 3
WINDOW_S = 300.0
BACKOFF_S = 2.0  # x attempt number in the current window: 2, 4, 6 s

EXIT_GAVE_UP = 3
EXIT_SPAWN_FAILED = 4


class ChildProcess(Protocol):
    pid: int

    def wait(self) -> int: ...


Spawner = Callable[[bool], ChildProcess]  # (append_log) -> started child


def child_command(append_log: bool, executable: str | None = None) -> list[str]:
    cmd = [executable or sys.executable, "-m", "src.voice_bridge", CHILD_FLAG]
    if append_log:
        cmd.append(APPEND_LOG_FLAG)
    return cmd


def spawn_child(append_log: bool) -> subprocess.Popen:
    """Same interpreter as the supervisor: pythonw stays console-less, a console
    python run keeps sharing its console (live log output, Ctrl+C reaches both)."""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.Popen(child_command(append_log), cwd=str(APP_ROOT), env=env,
                            stdin=subprocess.DEVNULL)


def log_line(level: str, event: str, **fields) -> str:
    """One structured line: '<time> [voice_bridge.supervisor] LEVEL: event {json}'."""
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True)
    return f"{time.strftime('%H:%M:%S')} [voice_bridge.supervisor] {level}: {event} {payload}"


def append_log(line: str, path: Path = LOG_PATH) -> None:
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


@dataclass
class SupervisorOutcome:
    exit_code: int
    reason: str  # voluntary_exit | crash_loop | spawn_failed | interrupted
    exit_codes: list[int] = field(default_factory=list)
    relaunches: int = 0


def supervise(
    spawn: Spawner = spawn_child,
    log: Callable[[str], None] = append_log,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    max_relaunches: int = MAX_RELAUNCHES,
    window_s: float = WINDOW_S,
    backoff_s: float = BACKOFF_S,
) -> SupervisorOutcome:
    exit_codes: list[int] = []
    relaunch_times: list[float] = []
    append = False
    while True:
        try:
            child = spawn(append)
        except OSError as exc:
            log(log_line("ERROR", "supervisor_spawn_failed", kind="spawn_failed",
                         error=str(exc), command=child_command(append)))
            return SupervisorOutcome(EXIT_SPAWN_FAILED, "spawn_failed", exit_codes, len(relaunch_times))
        started = clock()
        try:
            code = child.wait()
        except KeyboardInterrupt:  # console run: Ctrl+C also reached the child
            try:
                code = child.wait()
            except KeyboardInterrupt:
                code = 0
            return SupervisorOutcome(0, "interrupted", exit_codes + [code], len(relaunch_times))
        exit_codes.append(code)
        now = clock()
        if code == 0:
            if relaunch_times:
                log(log_line("INFO", "supervisor_child_exited", pid=child.pid, exit_code=0,
                             reason="voluntary"))
            return SupervisorOutcome(0, "voluntary_exit", exit_codes, len(relaunch_times))

        relaunch_times = [t for t in relaunch_times if now - t < window_s]
        if len(relaunch_times) >= max_relaunches:
            log(log_line("ERROR", "supervisor_gave_up", kind="crash_loop",
                         pid=child.pid, exit_code=code, uptime_s=round(now - started, 1),
                         relaunches_in_window=len(relaunch_times), max_relaunches=max_relaunches,
                         window_s=window_s, exit_codes=exit_codes[-(max_relaunches + 1):],
                         action="bridge stopped; start it again from the Start menu"))
            return SupervisorOutcome(EXIT_GAVE_UP, "crash_loop", exit_codes, len(relaunch_times))

        attempt = len(relaunch_times) + 1
        delay = backoff_s * attempt
        log(log_line("WARNING", "supervisor_child_crashed", pid=child.pid, exit_code=code,
                     uptime_s=round(now - started, 1), relaunch=attempt,
                     max_relaunches=max_relaunches, window_s=window_s, backoff_s=delay))
        sleep(delay)
        relaunch_times.append(clock())
        append = True
