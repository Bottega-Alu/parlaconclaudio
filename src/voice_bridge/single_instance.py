"""Single instance guard: a second launch exits, the running bridge is untouched.

Decision tree (ensure_single_instance):
  1. named mutex Local\\parlaconclaudio-voice-bridge already exists
       -> another v1.0.7+ bridge owns this session: exit
  2. we now own the mutex; a pre-1.0.7 bridge (no mutex) may still be running:
       a python(w).exe started BEFORE us whose command line runs
       `-m src.voice_bridge`, other than us and our parent -> exit
  3. otherwise -> we are the instance; the mutex lives as long as the process
     (the OS releases it on exit or crash, so there is no stale lock to clean)

Replaces the old policy "the new run kills the previous one" (taskkill of the
PID lockfile and of every python.exe matching voice_bridge): with autostart at
logon plus a Start-menu launch, killing would restart the bridge mid-dictation.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
from ctypes import wintypes
from typing import Callable

logger = logging.getLogger(__name__)

MUTEX_NAME = "Local\\parlaconclaudio-voice-bridge"
ERROR_ALREADY_EXISTS = 183
CREATE_NO_WINDOW = 0x08000000

# Kept referenced for the whole process lifetime: closing it releases the lock.
_HANDLE = None


def acquire_mutex(name: str = MUTEX_NAME) -> tuple[object | None, bool]:
    """(handle, already_existed). Handle None means the API call failed."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, name)
    already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    return (handle or None), already


def release_mutex(handle) -> None:
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)


def legacy_query(my_pid: int) -> str:
    """Bridge processes STARTED BEFORE us. Only older ones count: two launches
    racing at logon must not both see each other and both exit."""
    return (
        f"$me = Get-CimInstance Win32_Process -Filter 'ProcessId={int(my_pid)}'; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
        "| Where-Object { $_.CommandLine -match '-m\\s+src\\.voice_bridge(\\s|$)' "
        "-and $_.CreationDate -lt $me.CreationDate } "
        "| Select-Object -ExpandProperty ProcessId"
    )


def find_legacy_instances(
    exclude: set[int],
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[int]:
    """PIDs of other bridge processes (any version). Empty on any failure."""
    try:
        out = run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", legacy_query(os.getpid())],
            capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"instance scan failed, assuming none: {exc}")
        return []
    pids = []
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit() and int(line) not in exclude:
            pids.append(int(line))
    return pids


def ensure_single_instance(
    acquire: Callable[[], tuple[object | None, bool]] = acquire_mutex,
    scan: Callable[[set[int]], list[int]] = find_legacy_instances,
) -> tuple[bool, str]:
    """(may_run, reason). Keeps the mutex handle alive when may_run is True."""
    global _HANDLE
    handle, already = acquire()
    if handle is not None and already:
        release_mutex(handle)
        return False, f"another instance holds {MUTEX_NAME}"
    if handle is None:
        logger.warning("CreateMutexW failed: single-instance guard degraded to process scan")
    others = scan({os.getpid(), os.getppid()})
    if others:
        release_mutex(handle)
        return False, f"bridge already running (PID {', '.join(map(str, others))})"
    _HANDLE = handle
    return True, "first instance"
