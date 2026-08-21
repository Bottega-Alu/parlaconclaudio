"""Console-less launcher stub for Voice Bridge.

Built with PyInstaller (--noconsole) into VoiceBridge.exe. It does NOT bundle the
application: torch/CUDA + faster-whisper would blow the exe up to several GB and
make the CUDA runtime path fragile. Instead it resolves the project root, then
starts the existing venv interpreter (pythonw.exe -> no console window) detached
and exits immediately, leaving zero residual processes.

Single-instance handling is NOT done here on purpose: bridge.py already owns a
PID lockfile guard that kills a stale previous run.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

APP_TITLE = "Voice Bridge"

# Last-resort root, used only if the exe was copied somewhere detached from the repo.
FALLBACK_ROOT = Path(r"C:\PROJECTS\parlaconclaudio")

# Win32 process creation flags (avoid importing the whole subprocess constant set).
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _error(message: str) -> None:
    """Surface a real error dialog instead of dying mute behind --noconsole."""
    ctypes.windll.user32.MessageBoxW(None, message, f"{APP_TITLE} - launch failed", 0x10)


def _candidate_roots() -> list[Path]:
    """Ordered guesses for the project root, most specific first."""
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        roots += [here, here.parent]          # exe in repo root, or in dist/
    else:
        roots.append(Path(__file__).resolve().parent.parent)
    roots.append(FALLBACK_ROOT)

    seen, unique = set(), []
    for root in roots:
        if root not in seen:
            seen.add(root)
            unique.append(root)
    return unique


def _resolve_root() -> Path | None:
    """A root is valid only if it carries BOTH the venv and the package."""
    for root in _candidate_roots():
        if (root / "venv" / "Scripts" / "pythonw.exe").is_file() and (
            root / "src" / "voice_bridge" / "__main__.py"
        ).is_file():
            return root
    return None


def main() -> int:
    root = _resolve_root()
    if root is None:
        _error(
            "Could not locate the Voice Bridge installation.\n\n"
            "Looked for 'venv\Scripts\pythonw.exe' + 'src\voice_bridge' in:\n"
            + "\n".join(f"  - {p}" for p in _candidate_roots())
            + "\n\nKeep VoiceBridge.exe inside the project folder, or edit "
            "FALLBACK_ROOT in scripts/vb_launcher.py and rebuild."
        )
        return 2

    pythonw = root / "venv" / "Scripts" / "pythonw.exe"

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"        # logs contain non-ASCII (IT/PT transcripts)

    try:
        subprocess.Popen(
            [str(pythonw), "-m", "src.voice_bridge"],
            cwd=str(root),
            env=env,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        _error(f"Failed to start:\n  {pythonw} -m src.voice_bridge\n\n{exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
