"""Feature B: connect / disconnect the notify-tts voice hooks for Claude Code
and Codex, on fake config dirs only (CLAUDE_CONFIG_DIR / CODEX_HOME in an env
dict - the real ~/.claude and ~/.codex are never touched)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from src.core import voice_hooks as vh

NOW = dt.datetime(2026, 10, 20, 9, 8, 7)
VOICE = "python C:/Users/x/.claude/scripts/notify-tts.py"


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "claude").mkdir()
    (tmp_path / "codex").mkdir()
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "CODEX_HOME": str(tmp_path / "codex")}


def _claude(env) -> Path:
    return Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"


def _codex(env) -> Path:
    return Path(env["CODEX_HOME"]) / "hooks.json"


def _read(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


OTHER_SETTINGS = {
    "model": "opus",
    "permissions": {"allow": ["Bash(git status)"]},
    "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "bash guard.sh", "timeout": 15}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "echo other-stop"}]}],
    },
}


# --------------------------------------------------------------------------- locations


def test_paths_honour_env(env):
    assert vh.config_path(vh.CLAUDE, env) == _claude(env)
    assert vh.config_path(vh.CODEX, env) == _codex(env)


def test_default_paths_are_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert vh.config_path(vh.CLAUDE, {}) == tmp_path / ".claude" / "settings.json"
    assert vh.config_path(vh.CODEX, {}) == tmp_path / ".codex" / "hooks.json"


# --------------------------------------------------------------------------- Claude


def test_claude_connect_preserves_everything_else(env):
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    res = vh.connect(vh.CLAUDE, env=env, now=NOW)
    assert res.ok and res.changed
    doc = _read(_claude(env))
    assert doc["model"] == "opus" and doc["permissions"] == OTHER_SETTINGS["permissions"]
    assert doc["hooks"]["PreToolUse"][0] == OTHER_SETTINGS["hooks"]["PreToolUse"][0]  # untouched
    assert doc["hooks"]["PreToolUse"][1]["matcher"] == "AskUserQuestion"  # ours appended
    assert doc["hooks"]["Stop"][0] == OTHER_SETTINGS["hooks"]["Stop"][0]  # untouched, ours appended
    for ev in ("Stop", "Notification", "TaskCompleted"):
        handlers = [h for g in doc["hooks"][ev] for h in g["hooks"] if vh.is_voice_handler(h)]
        assert len(handlers) == 1 and handlers[0]["async"] is True and handlers[0]["timeout"] == 10
    assert vh.status(vh.CLAUDE, env) is True
    assert res.backup is not None and res.backup.name == "settings.json.bak-20261020-090807"
    assert _read(res.backup) == OTHER_SETTINGS


def test_claude_connect_is_idempotent(env):
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    vh.connect(vh.CLAUDE, env=env, now=NOW)
    before = _claude(env).read_bytes()
    res = vh.connect(vh.CLAUDE, env=env, now=NOW)
    assert res.ok and not res.changed and res.backup is None
    assert _claude(env).read_bytes() == before
    assert "gia'" in res.summary()


def test_claude_connect_partial_adds_only_missing(env):
    doc = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": VOICE}]}]}}
    _claude(env).write_text(json.dumps(doc), encoding="utf-8")
    assert vh.status(vh.CLAUDE, env) is False  # not every event wired
    vh.connect(vh.CLAUDE, env=env, now=NOW)
    out = _read(_claude(env))
    assert len(out["hooks"]["Stop"]) == 1  # existing voice hook kept, no duplicate
    assert out["hooks"]["Notification"][0]["hooks"][0]["command"] == VOICE  # reused command


def test_claude_disconnect_removes_only_voice_entries(env):
    doc = json.loads(json.dumps(OTHER_SETTINGS))
    doc["hooks"]["Stop"][0]["hooks"].append({"type": "command", "command": VOICE})  # shared group
    doc["hooks"]["Notification"] = [{"hooks": [{"type": "command", "command": VOICE}]}]
    _claude(env).write_text(json.dumps(doc), encoding="utf-8")
    res = vh.disconnect(vh.CLAUDE, env=env, now=NOW)
    assert res.ok and res.changed
    assert _read(_claude(env)) == OTHER_SETTINGS  # emptied event pruned, shared group kept
    assert vh.status(vh.CLAUDE, env) is False


def test_disconnect_roundtrip_restores_original(env):
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    vh.connect(vh.CLAUDE, env=env, now=NOW)
    vh.disconnect(vh.CLAUDE, env=env, now=NOW)
    assert _read(_claude(env)) == OTHER_SETTINGS


def test_disconnect_idempotent_and_missing_file(env):
    res = vh.disconnect(vh.CLAUDE, env=env)
    assert res.ok and not res.changed and not _claude(env).exists()
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    res = vh.disconnect(vh.CLAUDE, env=env)
    assert res.ok and not res.changed and res.backup is None


def test_connect_missing_file_creates_it_without_backup(env, tmp_path):
    res = vh.connect(vh.CLAUDE, root=tmp_path / "app", env=env, now=NOW)
    assert res.ok and res.changed and res.backup is None
    assert vh.status(vh.CLAUDE, env) is True


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
@pytest.mark.parametrize("op", ["connect", "disconnect"])
def test_malformed_file_is_structured_error_and_never_written(env, bad, op):
    _claude(env).write_text(bad, encoding="utf-8")
    res = vh.connect(vh.CLAUDE, env=env) if op == "connect" else vh.disconnect(vh.CLAUDE, env=env)
    assert not res.ok and res.error_kind is vh.HookErrorKind.MALFORMED
    assert _claude(env).read_text(encoding="utf-8") == bad
    assert list(_claude(env).parent.glob("*.bak-*")) == []
    assert vh.status(vh.CLAUDE, env) is None
    assert "errore (malformed)" in res.summary()


def test_write_failure_leaves_original_intact(env, monkeypatch):
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    original = _claude(env).read_bytes()

    def boom(src, dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(vh.os, "replace", boom)
    res = vh.connect(vh.CLAUDE, env=env, now=NOW)
    assert not res.ok and res.error_kind is vh.HookErrorKind.WRITE_FAILED
    assert _claude(env).read_bytes() == original
    assert [p.name for p in _claude(env).parent.iterdir() if p.name.endswith(".tmp")] == []


def test_backups_do_not_collide_within_a_second(env):
    _claude(env).write_text(json.dumps(OTHER_SETTINGS), encoding="utf-8")
    b1 = vh.connect(vh.CLAUDE, env=env, now=NOW).backup
    b2 = vh.disconnect(vh.CLAUDE, env=env, now=NOW).backup
    assert b1 != b2 and b1.exists() and b2.exists()


def test_utf8_bom_settings_are_read(env):
    _claude(env).write_bytes(b"\xef\xbb\xbf" + json.dumps(OTHER_SETTINGS).encode())
    assert vh.connect(vh.CLAUDE, env=env, now=NOW).ok


# --------------------------------------------------------------------------- Codex


def test_codex_connect_reuses_claude_command_with_source_flag(env):
    _claude(env).write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": VOICE}]}]}}),
                            encoding="utf-8")
    res = vh.connect(vh.CODEX, env=env, now=NOW)
    assert res.ok and res.changed and "/hooks" in res.message
    doc = _read(_codex(env))
    assert set(doc["hooks"]) == {"Stop", "PermissionRequest"}
    h = doc["hooks"]["Stop"][0]["hooks"][0]
    assert h == {"type": "command", "command": VOICE + " --source codex", "timeout": 30, "async": True}
    assert vh.status(vh.CODEX, env) is True


def test_codex_disconnect_keeps_other_codex_hooks(env):
    other = {"description": "mine", "hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "graphify hook-check"}]}]}}
    _codex(env).write_text(json.dumps(other), encoding="utf-8")
    vh.connect(vh.CODEX, env=env, now=NOW)
    assert vh.status(vh.CODEX, env) is True
    vh.disconnect(vh.CODEX, env=env, now=NOW)
    assert _read(_codex(env)) == other


def test_claude_reconnect_reuses_codex_command_without_source_flag(env):
    _codex(env).write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": VOICE + " --source codex"}]}]}}), encoding="utf-8")
    vh.connect(vh.CLAUDE, env=env, now=NOW)
    assert _read(_claude(env))["hooks"]["Stop"][0]["hooks"][0]["command"] == VOICE


def test_codex_and_claude_are_independent(env):
    vh.connect(vh.CODEX, env=env, now=NOW)
    assert vh.status(vh.CODEX, env) is True and vh.status(vh.CLAUDE, env) is False
    assert not _claude(env).exists()


# --------------------------------------------------------------------------- command


def test_default_command_is_app_venv(env, tmp_path):
    base, origin = vh.base_hook_command(tmp_path / "app", env)
    assert origin == "app venv"
    root = str(tmp_path / "app").replace("\\", "/")
    assert f"{root}/venv/Scripts/python.exe" in base and f"{root}/scripts/notify-tts.py" in base


def test_paths_with_spaces_are_quoted(env, tmp_path):
    base, _ = vh.base_hook_command(tmp_path / "Program Files" / "VB", env)
    target = vh.parse_hook_command(base)
    assert base.startswith('"') and target is not None
    assert target.script.name == "notify-tts.py"


def test_codex_command_parses_back_to_script():
    t = vh.parse_hook_command(vh.agent_command(vh.CODEX, VOICE))
    assert t.interpreter == ("python",) and t.script.name == "notify-tts.py"


def test_bootstrap_reuses_the_shared_parser():
    import importlib.util
    import sys

    repo = Path(vh.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("vb_boot_shared", repo / "installer" / "vb_bootstrap.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vb_boot_shared"] = mod
    spec.loader.exec_module(mod)
    assert mod.parse_hook_command is vh.parse_hook_command
    assert mod.find_hook_commands is vh.find_hook_commands
