"""'Ho bisogno di una tua scelta': PreToolUse/AskUserQuestion in notify-tts.py
and in voice_hooks (connect/disconnect on fake config dirs only)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from src.core import voice_hooks as vh

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("notify_tts_question_under_test",
                                                  REPO / "scripts" / "notify-tts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nt = _load()

ASK = {
    "session_id": "s1", "cwd": "C:/x/alpha", "hook_event_name": "PreToolUse",
    "tool_name": "AskUserQuestion", "tool_use_id": "toolu_1", "permission_mode": "default",
    "tool_input": {"questions": [{"question": "Quale?", "header": "Scelta",
                                  "options": [{"label": "A"}, {"label": "B"}], "multiSelect": False}]},
}
NOTIF = {"session_id": "s1", "cwd": "C:/x/alpha", "hook_event_name": "Notification",
         "notification_type": "permission_prompt", "message": "Claude needs your permission"}


@pytest.fixture()
def spoken(monkeypatch, tmp_path):
    said: list[str] = []
    monkeypatch.setattr(nt, "QUESTION_MARK_FILE", tmp_path / "notify_question.json")
    monkeypatch.setattr(nt, "burst_check_should_play", lambda wait_ms=0: True)
    monkeypatch.setattr(nt, "cleanup_tts_cache", lambda: None)
    monkeypatch.setattr(nt, "load_config", lambda: {"tts_mode": "full"})
    monkeypatch.setattr(nt, "play_chime", lambda key, progress=0.0: said.append(f"chime:{key}"))
    monkeypatch.setattr(nt, "resolve_audio", lambda text, profile: said.append(text) or None)
    monkeypatch.setattr(nt, "maybe_play_easter_egg", lambda mode: None)
    monkeypatch.setattr(nt, "extract_repo_name", lambda data: "alpha")
    return said


def _main(payload: dict) -> None:
    nt.main([], lambda: json.dumps(payload))


# --------------------------------------------------------------------------- notify-tts


def test_ask_user_question_says_needs_your_choice(spoken):
    _main(ASK)
    assert spoken == ["chime:question", "Progetto alpha. Ho bisogno di una tua scelta."]


def test_other_pretooluse_tools_are_silent(spoken):
    _main({**ASK, "tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert spoken == []


def test_notification_right_after_question_same_session_is_silent(spoken):
    _main(ASK)
    _main(NOTIF)
    assert spoken == ["chime:question", "Progetto alpha. Ho bisogno di una tua scelta."]


def test_notification_other_session_still_speaks(spoken):
    _main(ASK)
    _main({**NOTIF, "session_id": "s2"})
    assert spoken[-1].startswith("Progetto alpha. Ho bisogno del tuo permesso.")


def test_auth_success_is_never_suppressed(spoken):
    _main(ASK)
    _main({**NOTIF, "notification_type": "auth_success"})
    assert spoken[-1] == "Progetto alpha. Autenticazione completata."


def test_dedup_window_expires(spoken, monkeypatch):
    nt.mark_question("s1", now=1000.0)
    assert nt.question_just_announced("s1", now=1000.0 + nt.QUESTION_DEDUP_S - 1)
    assert not nt.question_just_announced("s1", now=1000.0 + nt.QUESTION_DEDUP_S + 1)
    assert not nt.question_just_announced(None, now=1000.0)


def test_corrupt_marker_does_not_suppress(spoken):
    nt.QUESTION_MARK_FILE.write_text("{not json", encoding="utf-8")
    _main(NOTIF)
    assert spoken[-1].startswith("Progetto alpha. Ho bisogno del tuo permesso.")


def test_question_hook_prints_nothing(spoken, capsys):
    """A PreToolUse hook's stdout JSON could be read as a permission decision."""
    _main(ASK)
    assert capsys.readouterr().out == ""


def test_script_exits_zero_on_question_payload(tmp_path):
    """Real process, stdin payload, unknown tool: exit 0, empty stdout (no audio path)."""
    import subprocess
    import sys

    proc = subprocess.run([sys.executable, str(REPO / "scripts" / "notify-tts.py")],
                          input=json.dumps({**ASK, "tool_name": "Bash"}), text=True,
                          capture_output=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout == ""


# --------------------------------------------------------------------------- voice_hooks

NOW = dt.datetime(2026, 10, 20, 9, 8, 7)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "claude").mkdir()
    (tmp_path / "codex").mkdir()
    return {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "CODEX_HOME": str(tmp_path / "codex")}


def _settings(env) -> Path:
    return Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"


GUARD = {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash guard.sh"}]}


def test_connect_adds_async_pretooluse_ask_user_question_group(env):
    _settings(env).write_text(json.dumps({"hooks": {"PreToolUse": [GUARD]}}), encoding="utf-8")
    assert vh.connect(vh.CLAUDE, env=env, now=NOW).ok
    groups = json.loads(_settings(env).read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    assert groups[0] == GUARD
    ours = [g for g in groups if g.get("matcher") == "AskUserQuestion"]
    assert len(ours) == 1
    handler = ours[0]["hooks"][0]
    assert vh.is_voice_handler(handler) and handler["async"] is True and handler["type"] == "command"


def test_voice_hook_under_another_matcher_does_not_count(env):
    doc = {"hooks": {ev: [{"hooks": [{"type": "command", "command": "python notify-tts.py"}]}]
                     for ev in ("Stop", "Notification", "TaskCompleted")}}
    doc["hooks"]["PreToolUse"] = [{"matcher": "Bash", "hooks": [{"type": "command",
                                                                 "command": "python notify-tts.py"}]}]
    _settings(env).write_text(json.dumps(doc), encoding="utf-8")
    assert vh.status(vh.CLAUDE, env) is False


def test_pre_1_0_8_connection_is_completed_by_reconnect(env):
    """A 1.0.7 install wired Stop/Notification/TaskCompleted only: shown as not
    connected, and connect adds just the question hook."""
    cmd = "python C:/u/.claude/scripts/notify-tts.py"
    doc = {"hooks": {ev: [{"hooks": [{"type": "command", "command": cmd}]}]
                     for ev in ("Stop", "Notification", "TaskCompleted")}}
    _settings(env).write_text(json.dumps(doc), encoding="utf-8")
    assert vh.status(vh.CLAUDE, env) is False
    res = vh.connect(vh.CLAUDE, env=env, now=NOW)
    out = json.loads(_settings(env).read_text(encoding="utf-8"))
    assert res.changed and {k: out["hooks"][k] for k in doc["hooks"]} == doc["hooks"]
    assert out["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == cmd
    assert vh.status(vh.CLAUDE, env) is True
    assert not vh.connect(vh.CLAUDE, env=env, now=NOW).changed  # idempotent


def test_disconnect_removes_question_group_keeps_guard(env):
    original = {"hooks": {"PreToolUse": [GUARD]}}
    _settings(env).write_text(json.dumps(original), encoding="utf-8")
    vh.connect(vh.CLAUDE, env=env, now=NOW)
    res = vh.disconnect(vh.CLAUDE, env=env, now=NOW)
    assert res.ok and res.backup is not None
    assert json.loads(_settings(env).read_text(encoding="utf-8")) == original


def test_codex_has_no_question_hook(env):
    vh.connect(vh.CODEX, env=env, now=NOW)
    hooks = json.loads((Path(env["CODEX_HOME"]) / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "PreToolUse" not in hooks
