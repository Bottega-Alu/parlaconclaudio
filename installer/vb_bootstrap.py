"""Post-install bootstrapper: pick a GPU profile, then build the runtime for it.

Run by the Inno Setup installer after the payload is copied, and re-runnable by
hand for repair. Everything heavy is downloaded HERE, on the target host, so the
installer itself stays small and never ships CUDA kernels a machine cannot use.

    vb_bootstrap.exe --root "C:\\Program Files\\VoiceBridge"
    vb_bootstrap.exe --root . --profile cloud     # force, skip detection
    vb_bootstrap.exe --root . --dry-run           # print the plan, install nothing
    vb_bootstrap.exe --root . --no-pause          # never wait for Enter (silent setup, CI)
    vb_bootstrap.exe --root . --skip-hook         # leave the Claude Code / Codex TTS hooks alone

Every run is appended to <root>\\bootstrap.log (header with timestamp + argv),
with the uv/pip child output teed into it as well: the console window closes
when the process exits, so the log is what is left to read after a failure.
The exit code tells the installer which kind of failure it was (EXIT_* below).
"""

from __future__ import annotations

import argparse
import codecs
import datetime as _dt
import enum
import errno
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable

# Reading the voice hooks is shared with the app (tray connect/disconnect):
# one implementation in src/core/voice_hooks.py (stdlib only). Compiled into
# vb_bootstrap.exe (--hidden-import); from a source checkout the repo root is
# put on sys.path first.
try:
    from src.core import voice_hooks
except ImportError:  # python installer/vb_bootstrap.py from a checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.core import voice_hooks

from src.voice_bridge.build_info import IS_TEST_BUILD

HOOK_SCRIPT_NAME = voice_hooks.HOOK_SCRIPT_NAME
HookTarget = voice_hooks.HookTarget
claude_config_dir = voice_hooks.claude_config_dir
codex_home = voice_hooks.codex_home
find_hook_commands = voice_hooks.find_hook_commands
parse_hook_command = voice_hooks.parse_hook_command

PYTHON_VERSION = "3.11"
UV_INSTALL_URL = "https://astral.sh/uv/install.ps1"
STATE_FILE = "install-state.json"
LOG_FILE = "bootstrap.log"
FALLBACK_LOG_FILE = "vb_bootstrap.log"  # in %TEMP%, used when <root> is not writable

# Exit codes - VoiceBridge.iss reads them to pick the message it shows.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_PAYLOAD = 2
EXIT_PERMISSION = 3
EXIT_BLOCKED = 4

# Windows error codes meaning "the OS refused to run this file", as opposed to
# "the file ran and failed". 4550-4560 is the ERROR_SYSTEM_INTEGRITY_* family
# raised by Code Integrity / App Control for Business / Smart App Control
# (4551 = policy violation, 4556+ = reputation verdicts); 1260 = blocked by
# group policy (SRP/AppLocker); 225 = ERROR_VIRUS_INFECTED (Defender).
BLOCKED_WINERRORS = frozenset(range(4550, 4561)) | {1260, 225}
ACCESS_DENIED_WINERRORS = frozenset({5, 32})  # ACCESS_DENIED, SHARING_VIOLATION
ACCESS_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM})

# Fixed-format error-code markers printed by child processes: Rust (uv) prints
# "(os error 4551)", Python prints "[WinError 4551]". We match codes, never prose.
_CHILD_ERRCODE = re.compile(r"(?:os error|WinError)\s+(\d+)", re.IGNORECASE)

TAIL_CHARS = 4000  # child output kept in memory for classification

# edge-tts floor. 7.2.x sends the old Sec-MS-GEC token and gets HTTP 403 from the
# Microsoft service: every TTS line goes silent. It is a FLOOR on purpose:
# `uv pip install` leaves an already-satisfied requirement untouched, so on an
# upgrade over an existing venv only a raised floor makes it fetch the new one.
EDGE_TTS_MIN = "7.3.1"
EDGE_TTS_REQ = f"edge-tts>={EDGE_TTS_MIN}"

# Base runtime: everything that works with or without a GPU.
BASE_DEPS = [
    "numpy>=1.26,<2",
    "pyaudio>=0.2.14",
    "pynput>=1.7.6",
    "pyperclip>=1.8.2",
    "pystray>=0.19.5",
    "Pillow>=10",
    "faster-whisper>=1.2",
    "ctranslate2>=4.0,<5",
    "pywin32>=306",
    "keyring>=25",  # API keys in the Windows Credential Manager, not in plaintext JSON
    EDGE_TTS_REQ,
]

# CUDA kernels. ctranslate2 does NOT declare these but cannot run on GPU without
# them: the cudnn64_9.dll inside its wheel is only a dispatcher stub.
GPU_DEPS = [
    "nvidia-cublas-cu12>=12.9",
    "nvidia-cudnn-cu12>=9.20",
]


# --------------------------------------------------------------------------- #
# Run log
# --------------------------------------------------------------------------- #


class Tee:
    """File-like writing to the console stream AND the run log.

    The console side never raises: a cp1252/cp437 console that cannot render a
    character from a pip log gets a replacement char, the log keeps UTF-8.
    """

    def __init__(self, primary: IO[str] | None, log: IO[str]) -> None:
        self.primary = primary
        self.log = log

    def write(self, text: str) -> int:
        if self.primary is not None:
            try:
                self.primary.write(text)
            except UnicodeEncodeError:
                enc = getattr(self.primary, "encoding", None) or "ascii"
                self.primary.write(text.encode(enc, "replace").decode(enc))
        self.log.write(text)
        return len(text)

    def flush(self) -> None:
        if self.primary is not None:
            self.primary.flush()
        self.log.flush()

    def isatty(self) -> bool:
        return bool(self.primary is not None and self.primary.isatty())

    @property
    def encoding(self) -> str:
        return getattr(self.primary, "encoding", None) or "utf-8"


class NullLog:
    """NullObject log: used when neither <root> nor %TEMP% is writable."""

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


def log_header(argv: list[str], now: _dt.datetime | None = None) -> str:
    """One header per run, so appended runs stay distinguishable."""
    stamp = (now or _dt.datetime.now()).isoformat(timespec="seconds")
    return f"\n===== vb_bootstrap run {stamp} pid={os.getpid()} argv={argv!r} =====\n"


def open_run_log(root: Path, argv: list[str], temp_dir: Path | None = None):
    """Decision tree: <root>/bootstrap.log -> %TEMP%/vb_bootstrap.log -> NullLog.

    Returns (handle, path or None). Append mode: a repair run must not erase
    the evidence left by the failed run before it.
    """
    temp = temp_dir if temp_dir is not None else Path(tempfile.gettempdir())
    for path in (root / LOG_FILE, temp / FALLBACK_LOG_FILE):
        try:
            handle = open(path, "a", encoding="utf-8", errors="replace")
        except OSError:
            continue
        handle.write(log_header(argv))
        handle.flush()
        return handle, path
    return NullLog(), None


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #


class ErrorKind(enum.Enum):
    BLOCKED = "blocked"  # OS policy refused to run a binary (Smart App Control & co.)
    PERMISSION = "permission"  # access denied / file in use
    FAILED = "failed"  # anything else (network, wheel build, ...)


EXIT_BY_KIND = {
    ErrorKind.BLOCKED: EXIT_BLOCKED,
    ErrorKind.PERMISSION: EXIT_PERMISSION,
    ErrorKind.FAILED: EXIT_FAILED,
}

HINT_BY_KIND = {
    ErrorKind.BLOCKED: (
        "Windows blocked a program from running (Smart App Control / Application "
        "Control / antivirus). uv.exe and python.exe are unsigned downloads. If "
        "Smart App Control is on, turn it off (Windows Security > App & browser "
        "control) or allow the file, then run Repair runtime."
    ),
    ErrorKind.PERMISSION: (
        "Access denied. Quit any running VoiceBridge (tray icon > Exit), check the "
        "install folder is not read-only or locked by sync/antivirus, then run "
        "Repair runtime. No administrator rights are needed: this is a per-user install."
    ),
    ErrorKind.FAILED: (
        "Check the network connection and the output above, then run Repair runtime."
    ),
}


class CommandFailed(RuntimeError):
    """A child process exited non-zero; carries the tail of its output."""

    def __init__(self, cmd: list[str], returncode: int, tail: str) -> None:
        super().__init__(f"command failed (exit {returncode}): {' '.join(cmd)}")
        self.cmd = cmd
        self.returncode = returncode
        self.tail = tail


def _kind_from_code(code: int | None) -> ErrorKind | None:
    if code is None:
        return None
    if code in BLOCKED_WINERRORS:
        return ErrorKind.BLOCKED
    if code in ACCESS_DENIED_WINERRORS:
        return ErrorKind.PERMISSION
    return None


def classify_error(exc: BaseException) -> ErrorKind:
    """Explicit decision tree on exception type and OS error code.

    1. OSError carrying a Windows code      -> blocked / permission by code
    2. PermissionError, or errno EACCES/EPERM -> permission
    3. other OSError                        -> failed
    4. CommandFailed                        -> codes the child printed in its
       output tail; the last recognised one wins (closest to the failure)
    5. anything else                        -> failed
    """
    if isinstance(exc, OSError):
        kind = _kind_from_code(getattr(exc, "winerror", None))
        if kind is not None:
            return kind
        if isinstance(exc, PermissionError) or exc.errno in ACCESS_DENIED_ERRNOS:
            return ErrorKind.PERMISSION
        return ErrorKind.FAILED
    if isinstance(exc, CommandFailed):
        for code in reversed(_CHILD_ERRCODE.findall(exc.tail)):
            kind = _kind_from_code(int(code))
            if kind is not None:
                return kind
        return ErrorKind.FAILED
    return ErrorKind.FAILED


@dataclass(frozen=True)
class Failure:
    kind: ErrorKind
    exit_code: int
    reason: str
    hint: str


def describe_failure(exc: BaseException) -> Failure:
    kind = classify_error(exc)
    return Failure(kind, EXIT_BY_KIND[kind], f"{type(exc).__name__}: {exc}", HINT_BY_KIND[kind])


def should_pause(no_pause: bool, stdin: IO[str] | None) -> bool:
    """Wait for Enter only when a human can actually press it."""
    if no_pause or stdin is None:
        return False
    try:
        return bool(stdin.isatty())
    except (ValueError, OSError):  # closed or detached stdin
        return False


def pause_for_user(
    no_pause: bool,
    stdin: IO[str] | None = None,
    prompt: Callable[[str], str] = input,
) -> bool:
    """Returns True if it paused."""
    if not should_pause(no_pause, sys.stdin if stdin is None else stdin):
        return False
    try:
        prompt("\nPremi Invio per chiudere / Press Enter to close... ")
    except (EOFError, KeyboardInterrupt):
        pass
    return True


# --------------------------------------------------------------------------- #
# Setup steps
# --------------------------------------------------------------------------- #


class Reporter:
    """Plain sequential output - the installer shows this console to the user."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def step(self, msg: str) -> None:
        print(f"\n[{time.perf_counter() - self.t0:6.1f}s] === {msg}", flush=True)

    def info(self, msg: str) -> None:
        print(f"          {msg}", flush=True)

    def fail(self, msg: str) -> None:
        print(f"\n*** {msg}", file=sys.stderr, flush=True)


def find_uv(root: Path, rep: Reporter) -> str:
    """Prefer the uv shipped next to the installer, else PATH, else fetch it."""
    bundled = root / "bin" / "uv.exe"
    if bundled.is_file():
        rep.info(f"uv: bundled ({bundled})")
        return str(bundled)

    on_path = shutil.which("uv")
    if on_path:
        rep.info(f"uv: found on PATH ({on_path})")
        return on_path

    rep.info("uv: not found, downloading from astral.sh ...")
    run_streamed(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"irm {UV_INSTALL_URL} | iex",
        ],
        rep,
    )
    fetched = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    if not Path(fetched).is_file():
        raise RuntimeError("uv install completed but uv.exe was not found")
    return fetched


def _load_probe():
    """Import gpu_probe from the frozen bundle first, from the payload second.

    --probe-json runs during the wizard, BEFORE the payload is extracted, so the
    copy compiled into this exe is the only one available then. After install the
    on-disk payload is authoritative, which keeps repair runs honest if the app
    was updated without rebuilding this bootstrapper.
    """
    try:
        from src.core.gpu_probe import probe_gpu, select_profile

        return probe_gpu, select_profile
    except ImportError:
        return None, None


def detect_profile(root: Path, rep: Reporter):
    """Ask the shared probe. Works with zero dependencies installed."""
    probe_gpu, select_profile = _load_probe()
    if probe_gpu is None:
        sys.path.insert(0, str(root))
        from src.core.gpu_probe import probe_gpu, select_profile  # noqa: F811

    gpu = probe_gpu()
    profile = select_profile(gpu)
    rep.info(f"GPU     : {gpu.name}")
    rep.info(f"VRAM    : {gpu.vram_mb} MB")
    rep.info(f"Driver  : {gpu.driver_version or 'n/a'}")
    rep.info(f"Decision: {profile.reason}")
    rep.info(
        f"Runtime : device={profile.device} model={profile.model} "
        f"compute={profile.compute_type}"
    )
    return gpu, profile


def run_streamed(
    cmd: list[str],
    rep: Reporter,
    cwd: Path | None = None,
    out: IO[str] | None = None,
) -> None:
    """Run a child, relaying its merged stdout/stderr to `out` (console + log tee).

    Reads chunks, not lines: uv/pip redraw progress with carriage returns and a
    line reader would hold it back. Launch errors (OSError, e.g. WinError 4551
    when Smart App Control blocks uv.exe) propagate untouched so classify_error
    sees the code; a non-zero exit raises CommandFailed with the output tail.
    """
    sink = out if out is not None else sys.stdout
    rep.info(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    tail = ""
    while True:
        chunk = proc.stdout.read1(4096)
        text = decoder.decode(chunk, final=not chunk)
        if text:
            sink.write(text)
            sink.flush()
            tail = (tail + text)[-TAIL_CHARS:]
        if not chunk:
            break
    returncode = proc.wait()
    if returncode != 0:
        raise CommandFailed(cmd, returncode, tail)


# --------------------------------------------------------------------------- #
# Voice (TTS) hooks: Claude Code + Codex
# --------------------------------------------------------------------------- #
#
# The hook is NOT run by the venv: Claude Code / Codex spawn whatever command the
# config names (README: "python <...>/scripts/notify-tts.py"), so it imports
# edge_tts from THAT interpreter and reads THAT copy of the script. Upgrading the
# venv alone would leave the hook on the broken edge-tts. This step reads
# Claude's settings.json and Codex's hooks.json (never writes either), and for
# every notify-tts.py hook:
#   1. interpreter: edge-tts missing or < EDGE_TTS_MIN -> pip upgrade
#      (retried with --user when the interpreter's site-packages is not writable)
#   2. script: inside <root>  -> already replaced by the installer, nothing to do
#              elsewhere, same -> nothing to do
#              elsewhere, diff -> timestamped .bak, then copy <root>/scripts/notify-tts.py
# Best effort: any failure here is reported and logged, never fails the setup.


def version_tuple(text: str) -> tuple[int, ...]:
    """Release segment only: '7.3.1' -> (7, 3, 1), '7.3.1rc1' -> (7, 3, 1)."""
    m = re.match(r"\s*(\d+(?:\.\d+)*)", text)
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def edge_tts_outdated(installed: str | None) -> bool:
    return installed is None or version_tuple(installed) < version_tuple(EDGE_TTS_MIN)


def resolve_interpreter(tokens: tuple[str, ...]) -> list[str] | None:
    """First token through PATH (or as a file path); the rest passed through."""
    exe = shutil.which(tokens[0]) or (tokens[0] if Path(tokens[0]).is_file() else None)
    return [exe, *tokens[1:]] if exe else None


_EDGE_TTS_PROBE = (
    "import importlib.metadata as m\n"
    "try:\n"
    "    print(m.version('edge-tts'))\n"
    "except m.PackageNotFoundError:\n"
    "    print('')\n"
)


def installed_edge_tts(interp: list[str]) -> str | None:
    out = subprocess.run(
        [*interp, "-c", _EDGE_TTS_PROBE],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"interpreter probe failed (exit {out.returncode}): {out.stderr.strip()[-300:]}"
        )
    return out.stdout.strip() or None


def upgrade_edge_tts(interp: list[str], rep: Reporter) -> None:
    cmd = [*interp, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", EDGE_TTS_REQ]
    try:
        run_streamed(cmd, rep)
    except CommandFailed as exc:
        if classify_error(exc) is ErrorKind.BLOCKED:
            raise
        rep.info("retrying with --user (interpreter site-packages not writable?)")
        run_streamed([*cmd, "--user"], rep)


def sync_hook_script(
    src: Path, target: Path, root: Path, rep: Reporter, now: _dt.datetime | None = None
) -> str:
    """Returns 'in-root' | 'missing' | 'same' | 'updated'."""
    try:
        target.resolve().relative_to(root.resolve())
        return "in-root"
    except ValueError:
        pass
    if not target.is_file():
        rep.info(f"hook script not found, left alone: {target}")
        return "missing"
    if target.read_bytes() == src.read_bytes():
        return "same"
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.name}.bak-{stamp}")
    shutil.copy2(target, backup)
    shutil.copy2(src, target)
    rep.info(f"hook script updated: {target} (previous copy: {backup.name})")
    return "updated"


def _hook_commands_in(path: Path, rep: Reporter, results: list[str]) -> list[str]:
    """notify-tts commands in one config file; [] when absent or unreadable
    (an unreadable one also adds "error <ExcType>" to results)."""
    if not path.is_file():
        rep.info(f"no hook config at {path}: skipped")
        return []
    try:
        return find_hook_commands(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        results.append(f"error {type(exc).__name__}")
        rep.info(f"WARNING TTS hook check skipped for {path}: {type(exc).__name__}: {exc}")
        return []


def sync_tts_hook(
    root: Path,
    rep: Reporter,
    settings_file: Path | None = None,
    codex_hooks_file: Path | None = None,
) -> list[str]:
    """Bring the hooks' interpreters and script copies up to date. Never raises.

    Explicit files (tests) are the only ones read; with none given, the real
    Claude settings.json and Codex hooks.json (CLAUDE_CONFIG_DIR / CODEX_HOME).
    Returns one status line per action (logged, and asserted by the tests).
    """
    results: list[str] = []
    if settings_file is None and codex_hooks_file is None:
        settings_file = claude_config_dir() / "settings.json"
        codex_hooks_file = codex_home() / "hooks.json"
    src = root / "scripts" / HOOK_SCRIPT_NAME
    try:
        commands: list[str] = []
        for path in (settings_file, codex_hooks_file):
            if path is not None:
                commands += [c for c in _hook_commands_in(path, rep, results) if c not in commands]
        targets = [t for t in map(parse_hook_command, commands) if t]
        if not targets:
            rep.info("no notify-tts.py hook in Claude Code / Codex config: skipped")
            return results
        seen_interp: set[tuple[str, ...]] = set()
        seen_script: set[str] = set()
        for t in targets:
            rep.info(f"hook: {t.command}")
            interp = resolve_interpreter(t.interpreter)
            if interp is None:
                results.append(f"interpreter-not-found {' '.join(t.interpreter)}")
                rep.info(f"interpreter not found: {' '.join(t.interpreter)}")
            elif tuple(interp) not in seen_interp:
                seen_interp.add(tuple(interp))
                try:
                    before = installed_edge_tts(interp)
                    if edge_tts_outdated(before):
                        rep.info(f"edge-tts {before or 'missing'} < {EDGE_TTS_MIN}: upgrading")
                        upgrade_edge_tts(interp, rep)
                        after = installed_edge_tts(interp)
                        status = "ineffective" if edge_tts_outdated(after) else "upgraded"
                        results.append(f"{status} {interp[0]} edge-tts {before} -> {after}")
                        rep.info(f"edge-tts now {after}")
                    else:
                        results.append(f"ok {interp[0]} edge-tts {before}")
                        rep.info(f"edge-tts {before}: ok")
                except Exception as exc:  # one bad interpreter must not stop the rest
                    results.append(f"error {interp[0]} {type(exc).__name__}")
                    rep.info(f"WARNING edge-tts upgrade failed for {interp[0]}: {exc}")
            key = os.path.normcase(str(t.script))
            if key not in seen_script and src.is_file():
                seen_script.add(key)
                try:
                    results.append(f"script-{sync_hook_script(src, t.script, root, rep)} {t.script}")
                except OSError as exc:
                    results.append(f"script-error {t.script}")
                    rep.info(f"WARNING could not update {t.script}: {exc}")
    except Exception as exc:
        results.append(f"error {type(exc).__name__}")
        rep.info(f"WARNING TTS hook check skipped: {type(exc).__name__}: {exc}")
    return results


def resolve_hook_choices(args: argparse.Namespace, root: Path) -> dict[str, bool | None]:
    """Explicit wizard choices win; repair reuses the last successful install.

    None preserves the legacy repair behavior (sync existing hooks only), never
    silently opts an old installation into a new integration.
    """
    try:
        state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
        saved = state.get("voice_hooks", {}) if isinstance(state, dict) else {}
        if not isinstance(saved, dict):
            saved = {}
    except (OSError, ValueError):
        saved = {}
    choices = {}
    for agent in voice_hooks.AGENTS:
        value = getattr(args, f"{agent}_hooks", None)
        previous = saved.get(agent)
        choices[agent] = (value == "yes") if value is not None else (
            previous if isinstance(previous, bool) else None
        )
    return choices


def configure_voice_hooks(root: Path, rep: Reporter, choices: dict[str, bool | None],
                          maintain_existing: bool = True) -> list[str]:
    """Connect selected integrations and sync only selected/legacy configs.

    Deselection leaves existing agent configuration untouched. The shared writer
    handles backups, atomic replacement and idempotency; no CLI is installed and
    Codex hook trust always remains a manual /hooks action.
    """
    results = []
    paths: dict[str, Path] = {}
    for agent, selected in choices.items():
        if selected is False:
            rep.info(f"{voice_hooks.AGENT_LABEL[agent]} hooks: not selected; left untouched")
            continue
        paths[agent] = voice_hooks.config_path(agent)
        if selected is True:
            result = voice_hooks.connect(agent, root=root)
            results.append(result.summary())
            rep.info(result.summary())
            if agent == voice_hooks.CODEX and result.ok:
                rep.info("Codex: open /hooks and manually review/trust the voice hooks before use.")
    # Passing no paths invokes legacy auto-discovery in sync_tts_hook: do not
    # call it at all when both integrations were explicitly deselected.
    if paths and maintain_existing:
        results.extend(sync_tts_hook(root, rep, paths.get(voice_hooks.CLAUDE),
                                     paths.get(voice_hooks.CODEX)))
    elif paths:
        rep.info("Test build: existing hook interpreters and script copies left untouched.")
    return results


# --------------------------------------------------------------------------- #
# Virtual environment: create / reuse / recreate
# --------------------------------------------------------------------------- #
#
# Installing over an existing version runs this bootstrap again. uv >= 0.8
# refuses `uv venv` on an existing venv ("Use --clear to replace it", exit 2),
# older uv replaced it silently - and the uv we get is whatever is on PATH or
# freshly downloaded. Never ask uv: decide here, the same way for every uv.
#   no venv dir                      -> create
#   venv python runs                 -> reuse (uv pip install upgrades what the
#                                       new requirement floors no longer satisfy)
#   dir present, python missing/dead -> recreate (rmtree, then create)

VENV_CREATE = "create"
VENV_REUSE = "reuse"
VENV_RECREATE = "recreate"


def venv_python_runs(python: Path) -> bool:
    if not python.is_file():
        return False
    try:
        out = subprocess.run(
            [str(python), "-c", "import sys"],
            capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def venv_action(venv: Path, runs: Callable[[Path], bool] = venv_python_runs) -> str:
    if not venv.exists():
        return VENV_CREATE
    if runs(venv / "Scripts" / "python.exe"):
        return VENV_REUSE
    return VENV_RECREATE


def build_model_prefetch_code(root: Path, profile) -> str:
    """Code run inside the fresh venv to pull the model into the HF cache."""
    return "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(root)!r})",
            "from src.core.gpu_probe import ensure_cuda_dll_path",
            "ensure_cuda_dll_path()",
            "from faster_whisper import WhisperModel",
            f"WhisperModel({profile.model!r}, device={profile.device!r}, "
            f"compute_type={profile.compute_type!r})",
            "print('model cached')",
        ]
    )


def report_failure(root: Path, log_path: Path | None, reason: str, hint: str) -> None:
    """Structured failure block: reason, hint, log path, how to retry."""
    err = sys.stderr
    print(f"\n*** setup failed: {reason}", file=err)
    print(f"\n  Hint: {hint}", file=err)
    if log_path is not None:
        print(f"\n  Full log: {log_path}", file=err)
    else:
        print("\n  (no log written: neither the install folder nor %TEMP% is writable)", file=err)
    print(
        "\nThe installation is incomplete. Retry from the Start menu:\n"
        "  parlaconclaudio - voice-bridge > Repair runtime\n"
        "or from a terminal:",
        file=err,
    )
    print(f'  vb_bootstrap.exe --root "{root}"', file=err, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build the VoiceBridge runtime for this host"
    )
    ap.add_argument("--root", required=True, type=Path, help="installation directory")
    ap.add_argument(
        "--profile", choices=["auto", "gpu", "cpu", "cloud"], default="auto"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan, change nothing"
    )
    ap.add_argument(
        "--skip-model", action="store_true", help="do not pre-fetch the model"
    )
    ap.add_argument(
        "--probe-json",
        type=Path,
        metavar="FILE",
        help="write the detected profile as JSON and exit (used by the installer wizard)",
    )
    ap.add_argument(
        "--skip-hook",
        action="store_true",
        help="leave the voice hooks alone (their interpreters' edge-tts, their script copies)",
    )
    for agent in voice_hooks.AGENTS:
        ap.add_argument(f"--{agent}-hooks", choices=["yes", "no"], default=None,
                        help="connect voice hooks when selected; no leaves existing config untouched")
    ap.add_argument(
        "--no-pause",
        action="store_true",
        help="on failure exit at once instead of waiting for Enter",
    )
    ap.add_argument(
        "--disconnect-hooks",
        action="store_true",
        help="uninstall: remove the Claude Code / Codex voice hooks that run from --root, then exit",
    )
    raw_argv = sys.argv[1:] if argv is None else argv
    args = ap.parse_args(raw_argv)

    if args.disconnect_hooks:
        # Run by the uninstaller before {app} is deleted: hooks left pointing at it
        # would fail on every agent event. No run log: the folder is going away.
        for agent in voice_hooks.AGENTS:
            res = voice_hooks.disconnect(agent, under=args.root.resolve())
            print(f"{agent}: ok={res.ok} changed={res.changed} {res.message}".rstrip())
        return EXIT_OK  # never block the uninstall

    if args.probe_json is not None:
        # Silent mode for the Inno Setup wizard: no banner, no log, machine-readable only.
        quiet = Reporter()
        quiet.info = lambda _msg: None  # type: ignore[method-assign]
        try:
            gpu, profile = detect_profile(args.root.resolve(), quiet)
            payload = {
                "ok": True,
                "recommended": "gpu" if profile.device == "cuda" else "cpu",
                "gpu_name": gpu.name,
                "vram_mb": gpu.vram_mb,
                "driver_version": gpu.driver_version,
                "model": profile.model,
                "compute_type": profile.compute_type,
                "reason": profile.reason,
            }
        except Exception as exc:
            payload = {"ok": False, "recommended": "cpu", "reason": f"probe failed: {exc}"}
        args.probe_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload))
        return EXIT_OK

    root: Path = args.root.resolve()
    log, log_path = open_run_log(root, [sys.argv[0], *raw_argv])
    saved = sys.stdout, sys.stderr
    sys.stdout = Tee(saved[0], log)  # type: ignore[assignment]
    sys.stderr = Tee(saved[1], log)  # type: ignore[assignment]
    code: int | str = "exception"
    try:
        code = setup(args, root, log_path)
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout, sys.stderr = saved
        log.write(f"===== exit {code} =====\n")
        log.close()
    if code != EXIT_OK:
        pause_for_user(args.no_pause)
    return code  # type: ignore[return-value]


def setup(args: argparse.Namespace, root: Path, log_path: Path | None) -> int:
    """The install itself. Every failure returns an EXIT_* code, never raises."""
    rep = Reporter()
    venv = root / "venv"

    print("=" * 68)
    print(" VoiceBridge - runtime setup")
    print("=" * 68)
    rep.info(f"target: {root}")
    rep.info(f"log   : {log_path or 'unavailable'}")

    if not (root / "src" / "voice_bridge" / "__main__.py").is_file():
        report_failure(
            root,
            log_path,
            f"payload not found under {root} (expected src/voice_bridge/)",
            "The application files are missing: re-run the installer.",
        )
        return EXIT_NO_PAYLOAD

    try:
        rep.step("Detecting hardware")
        gpu, profile = detect_profile(root, rep)

        if args.profile == "gpu":
            deps, kind = BASE_DEPS + GPU_DEPS, "gpu"
        elif args.profile in ("cpu", "cloud"):
            deps, kind = BASE_DEPS, args.profile
        else:
            wants_gpu = profile.device == "cuda"
            deps = BASE_DEPS + GPU_DEPS if wants_gpu else BASE_DEPS
            kind = "gpu" if wants_gpu else "cpu"

        rep.info(f"profile selected: {kind} ({len(deps)} packages)")
        if kind == "gpu":
            rep.info("includes ~1.6 GB of CUDA kernels (cuBLAS + cuDNN)")

        if args.dry_run:
            rep.step("DRY RUN - nothing will be installed")
            for dep in deps:
                print(f"          - {dep}")
            print(f"\n          venv    : {venv}")
            print(f"          model   : {profile.model} ({profile.compute_type})")
            return EXIT_OK

        rep.step("Locating uv")
        uv = find_uv(root, rep)

        rep.step(f"Installing Python {PYTHON_VERSION}")
        run_streamed([uv, "python", "install", PYTHON_VERSION], rep)

        action = venv_action(venv)
        rep.step(f"Virtual environment ({action})")
        if action == VENV_REUSE:
            rep.info(f"keeping {venv}: packages below are upgraded in place")
        else:
            if action == VENV_RECREATE:
                rep.info(f"{venv} exists but its interpreter does not run: rebuilding")
                shutil.rmtree(venv)
            run_streamed([uv, "venv", str(venv), "--python", PYTHON_VERSION], rep)

        rep.step(f"Installing {len(deps)} packages")
        env_python = venv / "Scripts" / "python.exe"
        run_streamed([uv, "pip", "install", "--python", str(env_python), *deps], rep)

        # The app currently supports local CUDA transcription only. CPU/base
        # installs need a configured cloud provider, so downloading a CPU model
        # here wastes disk/network without enabling dictation.
        if not args.skip_model and kind == "gpu":
            rep.step(f"Pre-fetching Whisper model '{profile.model}'")
            rep.info("first download only; later starts read it from cache")
            run_streamed(
                [str(env_python), "-c", build_model_prefetch_code(root, profile)],
                rep,
                cwd=root,
            )

        hook_choices = resolve_hook_choices(args, root)
        if not args.skip_hook:
            rep.step("Voice hooks: Claude Code + Codex (edge-tts, notify-tts.py)")
            configure_voice_hooks(root, rep, hook_choices, maintain_existing=not IS_TEST_BUILD)

        rep.step("Writing install state")
        state = {
            "profile": kind,
            "device": profile.device,
            "model": profile.model,
            "compute_type": profile.compute_type,
            "reason": profile.reason,
            "gpu_name": gpu.name,
            "vram_mb": gpu.vram_mb,
            "driver_version": gpu.driver_version,
            "python_version": PYTHON_VERSION,
            "voice_hooks": hook_choices,
        }
        (root / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
        rep.info(str(root / STATE_FILE))

        rep.step("Done")
        print(f"\n  VoiceBridge is ready ({kind} profile).")
        print(f"  Launch it with {root / 'VoiceBridge.exe'}\n")
        return EXIT_OK

    except Exception as exc:
        failure = describe_failure(exc)
        report_failure(root, log_path, failure.reason, failure.hint)
        rep.info(f"error class: {failure.kind.value} -> exit {failure.exit_code}")
        return failure.exit_code


if __name__ == "__main__":
    sys.exit(main())
