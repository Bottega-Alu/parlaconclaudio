"""Supervisor: run the bridge as a child process and relaunch it after a crash.

Why: the logon task's RestartOnFailure only covers a task that fails to START;
a bridge that exits with an error afterwards stays dead until the next sign-in.

Every launch path ends in `python(w) -m src.voice_bridge` (logon task, Run-key
fallback, VoiceBridge.exe stub, VoiceBridge.bat), so __main__ is the single
place where the supervisor lives:

  supervisor  holds the single-instance mutex for its whole life
     |        (a second launch sees the mutex and exits; a dead child never
     |         held it, so a relaunch is never blocked by a stale lock)
     +-- child  -m src.voice_bridge --child [--append-log]
                runs bridge.main(); never touches the mutex

Child interpreter (child_launch), so no level of the chain ever owns a console:
  supervisor runs as python.exe (console run from a terminal)
      -> same interpreter, shares that console (live log, Ctrl+C reaches both)
  anything else (pythonw / base pythonw / a uv trampoline named pythonw.exe)
      -> gui_python.resolve(venv): the base pythonw.exe + __PYVENV_LAUNCHER__
         (one process, GUI subsystem), started with CREATE_NO_WINDOW and all
         std streams on DEVNULL; CREATE_NO_WINDOW also keeps a console
         trampoline fallback hidden. child.pid is the bridge itself, not a
         trampoline, so waiting on / terminating it covers the whole child tree.

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

from . import gui_python

APP_ROOT = Path(__file__).resolve().parents[2]
LOG_PATH = APP_ROOT / "voicebridge.log"
VENV_DIR = APP_ROOT / "venv"
CREATE_NO_WINDOW = 0x08000000

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


@dataclass(frozen=True)
class ChildLaunch:
    cmd: list[str]
    env_overrides: dict[str, str]
    creationflags: int
    console: bool  # True: shares the supervisor's console, std streams inherited
    via: str


def is_console_run(executable: str) -> bool:
    """Only an explicit python.exe run keeps a shared console. Under a uv venv
    sys.executable is the launcher NAME (pythonw.exe) even though the process
    is the base python.exe: the name states the intent, the process does not."""
    return Path(executable).name.lower() == "python.exe"


def child_launch(append_log: bool, executable: str | None = None,
                 venv: Path | None = None) -> ChildLaunch:
    """Pure: which interpreter, env and flags the child gets (see module doc)."""
    executable = executable or sys.executable
    if is_console_run(executable):
        return ChildLaunch(child_command(append_log, executable), {}, 0, True, "console")
    gui = gui_python.resolve(venv or VENV_DIR)
    return ChildLaunch(child_command(append_log, str(gui.exe)), dict(gui.env),
                       CREATE_NO_WINDOW, False, gui.via)


def spawn_child(append_log: bool) -> subprocess.Popen:
    launch = child_launch(append_log)
    env = os.environ.copy()
    env.pop(gui_python.PYVENV_LAUNCHER_ENV, None)  # never leak the supervisor's own
    env.update(launch.env_overrides)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    streams = {} if launch.console else {"stdout": subprocess.DEVNULL,
                                         "stderr": subprocess.DEVNULL}
    return subprocess.Popen(launch.cmd, cwd=str(APP_ROOT), env=env,
                            stdin=subprocess.DEVNULL, creationflags=launch.creationflags,
                            **streams)


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
                         error=str(exc), command=child_launch(append).cmd))
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
