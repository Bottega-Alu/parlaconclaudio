"""Voice hooks (notify-tts.py) for Claude Code and Codex: read, connect, disconnect.

Single source of truth for "is the TTS hook wired into this agent's config?",
used by THREE callers, so it is stdlib-only:

  1. the tray menu          (checked state + connect / disconnect)
  2. installer/vb_bootstrap (which interpreters run the hook -> edge-tts floor)
  3. the tests

Where each agent keeps its hooks (same JSON schema: event -> matcher groups ->
handlers with "type": "command"):

  Claude Code  <CLAUDE_CONFIG_DIR or ~/.claude>/settings.json   "hooks" key
  Codex        <CODEX_HOME or ~/.codex>/hooks.json               "hooks" key
               (Codex >= 0.12x hooks engine; the legacy top-level `notify` key in
               config.toml holds ONE program and is often already taken - e.g. by
               the computer-use plugin - so it is never touched here.)

Events wired, per Frisco's two needs ("attivita' completata" / "ho bisogno di te"):

  Claude  Stop + TaskCompleted      -> done        Notification -> needs you
  Codex   Stop                      -> done        PermissionRequest -> needs you

Writes are conservative by construction:
  * parse first; a malformed file raises HookConfigError(MALFORMED) and is never written
  * connect adds only the missing entries; disconnect removes ONLY handlers whose
    command runs notify-tts.py, and prunes only the groups/events it emptied
  * a timestamped backup is taken before every write, the write is temp + os.replace,
    and the result is re-read and re-parsed before reporting success
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import os
import shlex
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

HOOK_SCRIPT_NAME = "notify-tts.py"
CODEX_SOURCE_ARGS = ("--source", "codex")

CLAUDE = "claude"
CODEX = "codex"
AGENTS = (CLAUDE, CODEX)
AGENT_LABEL = {CLAUDE: "Claude Code", CODEX: "Codex"}

# Events per agent. Order is the order they are added in.
AGENT_EVENTS: dict[str, tuple[str, ...]] = {
    CLAUDE: ("Stop", "Notification", "TaskCompleted"),
    CODEX: ("Stop", "PermissionRequest"),
}

# Handler options. async: the voice line must never delay the agent, and an
# async Codex hook cannot block/approve anything (it is fire-and-forget).
_HANDLER_OPTS: dict[str, dict] = {
    CLAUDE: {"timeout": 10, "async": True},
    CODEX: {"timeout": 30, "async": True},
}

# Repo / install root: src/core/voice_hooks.py -> parents[2]
APP_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #


def claude_config_dir(env: dict[str, str] | None = None) -> Path:
    """Claude Code honours CLAUDE_CONFIG_DIR; default ~/.claude."""
    env = os.environ if env is None else env
    base = env.get("CLAUDE_CONFIG_DIR")
    return Path(base) if base else Path.home() / ".claude"


def codex_home(env: dict[str, str] | None = None) -> Path:
    """Codex honours CODEX_HOME; default ~/.codex."""
    env = os.environ if env is None else env
    base = env.get("CODEX_HOME")
    return Path(base) if base else Path.home() / ".codex"


def config_path(agent: str, env: dict[str, str] | None = None) -> Path:
    if agent == CLAUDE:
        return claude_config_dir(env) / "settings.json"
    if agent == CODEX:
        return codex_home(env) / "hooks.json"
    raise ValueError(f"unknown agent {agent!r}")


# --------------------------------------------------------------------------- #
# Reading hook commands (also used by the bootstrap)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HookTarget:
    command: str  # raw command from the config
    interpreter: tuple[str, ...]  # tokens before the script: ("python",), ("py", "-3")
    script: Path


def _iter_handlers(doc: object):
    """Yield every handler dict of a hooks document (any event, any group)."""
    hooks = doc.get("hooks") if isinstance(doc, dict) else None
    if not isinstance(hooks, dict):
        return
    for groups in hooks.values():
        for group in groups if isinstance(groups, list) else []:
            entries = group.get("hooks") if isinstance(group, dict) else None
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict):
                    yield entry


def is_voice_handler(entry: object) -> bool:
    cmd = entry.get("command") if isinstance(entry, dict) else None
    return isinstance(cmd, str) and HOOK_SCRIPT_NAME in cmd.lower()


def find_hook_commands(settings: object) -> list[str]:
    """Distinct hook commands that run notify-tts.py, in document order."""
    found: list[str] = []
    for entry in _iter_handlers(settings):
        if is_voice_handler(entry) and entry["command"] not in found:
            found.append(entry["command"])
    return found


def _expand(token: str) -> str:
    return os.path.expanduser(os.path.expandvars(token))


def _is_hook_script(token: str) -> bool:
    t = token.replace("\\", "/").lower()
    return t == HOOK_SCRIPT_NAME or t.endswith("/" + HOOK_SCRIPT_NAME)


def parse_hook_command(command: str) -> HookTarget | None:
    """'<interpreter...> <path>/notify-tts.py [args]' -> HookTarget, else None.

    posix=False keeps Windows backslashes; quotes are stripped afterwards. A
    command starting with the script itself has no interpreter: None.
    """
    try:
        tokens = [t.strip('"').strip("'") for t in shlex.split(command, posix=False)]
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if _is_hook_script(tok):
            if i == 0:
                return None
            return HookTarget(command, tuple(_expand(t) for t in tokens[:i]), Path(_expand(tok)))
    return None


# --------------------------------------------------------------------------- #
# Structured errors / results
# --------------------------------------------------------------------------- #


class HookErrorKind(enum.Enum):
    MALFORMED = "malformed"  # not JSON, or JSON of the wrong shape: never written
    UNREADABLE = "unreadable"  # OSError reading
    WRITE_FAILED = "write_failed"  # backup / temp / replace failed (original intact)
    VERIFY_FAILED = "verify_failed"  # written file did not re-parse to what we wrote


class HookConfigError(Exception):
    def __init__(self, kind: HookErrorKind, path: Path, detail: str) -> None:
        super().__init__(f"{kind.value}: {path}: {detail}")
        self.kind = kind
        self.path = path
        self.detail = detail


@dataclass(frozen=True)
class HookResult:
    agent: str
    action: str  # "connect" | "disconnect"
    ok: bool
    changed: bool = False
    path: Path | None = None
    backup: Path | None = None
    error_kind: HookErrorKind | None = None
    message: str = ""

    def summary(self) -> str:
        """One line for the tray notification and the log."""
        who = AGENT_LABEL.get(self.agent, self.agent)
        verb = "collegati" if self.action == "connect" else "scollegati"
        if not self.ok:
            kind = self.error_kind.value if self.error_kind else "error"
            return f"Hook voce {who}: errore ({kind}) - {self.message}"
        if not self.changed:
            return f"Hook voce {who}: gia' {verb}, nessuna modifica"
        return f"Hook voce {who} {verb}. {self.message}".strip()


# --------------------------------------------------------------------------- #
# File I/O: load / atomic write with backup
# --------------------------------------------------------------------------- #


def load_doc(path: Path) -> dict:
    """Missing file -> {}. Malformed -> HookConfigError, never a silent {}."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise HookConfigError(HookErrorKind.UNREADABLE, path, str(exc)) from exc
    if not text.strip():
        return {}
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HookConfigError(HookErrorKind.MALFORMED, path, f"invalid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise HookConfigError(HookErrorKind.MALFORMED, path, "top level is not a JSON object")
    hooks = doc.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise HookConfigError(HookErrorKind.MALFORMED, path, '"hooks" is not an object')
    for event, groups in (hooks or {}).items():
        if not isinstance(groups, list):
            raise HookConfigError(HookErrorKind.MALFORMED, path, f'"hooks.{event}" is not a list')
    return doc


def _backup_path(path: Path, now: _dt.datetime) -> Path:
    stamp = now.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def write_doc_atomic(path: Path, doc: dict, now: _dt.datetime | None = None) -> Path | None:
    """Backup (if the file exists) -> temp file in the same dir -> os.replace -> verify.

    Returns the backup path (None for a new file). The original is untouched on
    any failure before os.replace.
    """
    backup: Path | None = None
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path, now or _dt.datetime.now())
            shutil.copy2(path, backup)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise HookConfigError(HookErrorKind.WRITE_FAILED, path, str(exc)) from exc
    try:
        reread = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HookConfigError(HookErrorKind.VERIFY_FAILED, path, str(exc)) from exc
    if reread != doc:
        raise HookConfigError(HookErrorKind.VERIFY_FAILED, path, "content differs after write")
    return backup


# --------------------------------------------------------------------------- #
# Pure document transforms
# --------------------------------------------------------------------------- #


def event_has_voice_hook(doc: dict, event: str) -> bool:
    groups = (doc.get("hooks") or {}).get(event)
    for group in groups if isinstance(groups, list) else []:
        entries = group.get("hooks") if isinstance(group, dict) else None
        if any(is_voice_handler(e) for e in (entries if isinstance(entries, list) else [])):
            return True
    return False


def is_connected(doc: dict, agent: str) -> bool:
    """Checked state: every event of the agent has a notify-tts handler."""
    return all(event_has_voice_hook(doc, ev) for ev in AGENT_EVENTS[agent])


def add_voice_hooks(doc: dict, agent: str, command: str) -> tuple[dict, bool]:
    """Append one matcher-less group per missing event. Never touches others."""
    out = json.loads(json.dumps(doc))  # deep copy, JSON-safe
    hooks = out.setdefault("hooks", {})
    changed = False
    for event in AGENT_EVENTS[agent]:
        if event_has_voice_hook(out, event):
            continue
        handler = {"type": "command", "command": command, **_HANDLER_OPTS[agent]}
        hooks.setdefault(event, []).append({"hooks": [handler]})
        changed = True
    return out, changed


def remove_voice_hooks(doc: dict) -> tuple[dict, bool]:
    """Drop ONLY notify-tts handlers; prune groups/events this emptied."""
    out = json.loads(json.dumps(doc))
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out, False
    changed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept_groups.append(group)
                continue
            kept = [e for e in entries if not is_voice_handler(e)]
            if len(kept) == len(entries):
                kept_groups.append(group)
                continue
            changed = True
            if kept:
                group["hooks"] = kept
                kept_groups.append(group)
        if len(kept_groups) != len(groups):
            if kept_groups:
                hooks[event] = kept_groups
            else:
                del hooks[event]
    return out, changed


# --------------------------------------------------------------------------- #
# Which command to write
# --------------------------------------------------------------------------- #


def _fmt_path(path: str | Path) -> str:
    """Forward slashes (valid for Windows, bash and cmd); quoted only if needed."""
    p = str(path).replace("\\", "/")
    return f'"{p}"' if any(c in p for c in " &()^%!;,'") else p


def _strip_codex_args(command: str) -> str:
    suffix = " " + " ".join(CODEX_SOURCE_ARGS)
    return command[: -len(suffix)].rstrip() if command.rstrip().endswith(suffix) else command.strip()


def base_hook_command(root: Path | None = None, env: dict[str, str] | None = None,
                      loader: Callable[[Path], dict] = load_doc) -> tuple[str, str]:
    """(command without agent-specific args, where it came from). Decision tree:

    1. a notify-tts hook already in Claude settings -> reuse it verbatim: its
       interpreter is the one the bootstrap keeps at edge-tts >= floor and its
       script copy is the one the bootstrap refreshes
    2. a notify-tts hook already in Codex hooks.json -> same, minus --source codex
    3. none -> <root>/venv/Scripts/python.exe <root>/scripts/notify-tts.py
       (the venv always carries edge-tts >= floor: bootstrap BASE_DEPS)
    Unreadable/malformed configs are skipped here; the write path reports them.
    """
    for agent in (CLAUDE, CODEX):
        try:
            doc = loader(config_path(agent, env))
        except HookConfigError:
            continue
        for cmd in find_hook_commands(doc):
            if parse_hook_command(cmd) is not None:
                return _strip_codex_args(cmd), f"reused from {AGENT_LABEL[agent]}"
    root = root or APP_ROOT
    python = root / "venv" / "Scripts" / "python.exe"
    script = root / "scripts" / HOOK_SCRIPT_NAME
    return f"{_fmt_path(python)} {_fmt_path(script)}", "app venv"


def agent_command(agent: str, base: str) -> str:
    return f"{base} {' '.join(CODEX_SOURCE_ARGS)}" if agent == CODEX else base


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #


def status(agent: str, env: dict[str, str] | None = None) -> bool | None:
    """True/False = connected or not; None = config unreadable/malformed."""
    try:
        return is_connected(load_doc(config_path(agent, env)), agent)
    except HookConfigError:
        return None


def connect(agent: str, root: Path | None = None, env: dict[str, str] | None = None,
            now: _dt.datetime | None = None) -> HookResult:
    path = config_path(agent, env)
    try:
        doc = load_doc(path)
        base, origin = base_hook_command(root, env)
        command = agent_command(agent, base)
        new_doc, changed = add_voice_hooks(doc, agent, command)
        backup = write_doc_atomic(path, new_doc, now) if changed else None
    except HookConfigError as exc:
        return HookResult(agent, "connect", False, path=path, error_kind=exc.kind, message=exc.detail)
    msg = f"Comando: {command} ({origin})." if changed else ""
    if changed and agent == CODEX:
        msg += " In Codex apri /hooks e approva i nuovi hook (finche' non sono trusted non partono)."
    return HookResult(agent, "connect", True, changed, path, backup, message=msg)


def disconnect(agent: str, env: dict[str, str] | None = None,
               now: _dt.datetime | None = None) -> HookResult:
    path = config_path(agent, env)
    try:
        if not path.exists():
            return HookResult(agent, "disconnect", True, False, path)
        doc = load_doc(path)
        new_doc, changed = remove_voice_hooks(doc)
        backup = write_doc_atomic(path, new_doc, now) if changed else None
    except HookConfigError as exc:
        return HookResult(agent, "disconnect", False, path=path, error_kind=exc.kind, message=exc.detail)
    return HookResult(agent, "disconnect", True, changed, path, backup,
                      message=f"Backup: {backup.name}." if backup else "")
