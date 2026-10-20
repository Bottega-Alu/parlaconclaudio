"""Console-less interpreter for the venv, without trusting venv\\Scripts\\pythonw.exe.

Why: a venv made by `uv venv` (the bootstrap's) gets the SAME console-subsystem
trampoline as both venv\\Scripts\\python.exe and pythonw.exe (byte-identical,
PE subsystem 3). That trampoline runs the base python.exe - console subsystem -
so whenever it is started without a console to inherit (logon task, Run value,
VoiceBridge.exe with DETACHED_PROCESS) Windows opens a new console window.

Fix: start the BASE interpreter's pythonw.exe (GUI subsystem, never a console)
and point it at the venv with __PYVENV_LAUNCHER__ - the same variable the
CPython/uv venv launchers set: CPython then takes the venv launcher path as
sys.executable, reads venv\\pyvenv.cfg and uses the venv as sys.prefix (only
the venv's site-packages, include-system-site-packages honoured). One process
per level, no trampoline in between.

Decision tree (resolve):
  1. venv\\pyvenv.cfg has `home` and <home>\\pythonw.exe exists
       -> base pythonw.exe + __PYVENV_LAUNCHER__=<venv>\\Scripts\\pythonw.exe
          (via "base_pythonw")
  2. otherwise (no cfg, no home, base without pythonw.exe)
       -> <venv>\\Scripts\\pythonw.exe, no env (via "venv_launcher"); callers
          that can, add CREATE_NO_WINDOW so a console trampoline stays hidden

Stdlib only: imported by the supervisor (which may run outside the venv, see
autostart.py) and compiled into the VoiceBridge.exe launcher stub.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PYVENV_LAUNCHER_ENV = "__PYVENV_LAUNCHER__"
VIA_BASE = "base_pythonw"
VIA_VENV = "venv_launcher"


@dataclass(frozen=True)
class GuiPython:
    exe: Path
    env: dict[str, str] = field(default_factory=dict)  # overrides for the child env
    via: str = VIA_VENV


def venv_launcher(venv: Path) -> Path:
    return venv / "Scripts" / "pythonw.exe"


def read_pyvenv_cfg(venv: Path) -> dict[str, str]:
    """pyvenv.cfg is `key = value` lines (same parsing as CPython's site.py).
    Empty dict when missing or unreadable."""
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return {}
    cfg: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            cfg[key.strip().lower()] = value.strip()
    return cfg


def base_pythonw(venv: Path) -> Path | None:
    home = read_pyvenv_cfg(venv).get("home")
    if not home:
        return None
    exe = Path(home) / "pythonw.exe"
    return exe if exe.is_file() else None


def resolve(venv: Path) -> GuiPython:
    base = base_pythonw(venv)
    if base is not None:
        return GuiPython(base, {PYVENV_LAUNCHER_ENV: str(venv_launcher(venv))}, VIA_BASE)
    return GuiPython(venv_launcher(venv), {}, VIA_VENV)
