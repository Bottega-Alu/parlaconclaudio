"""notify-tts.py input: Codex hooks (stdin + --source codex), legacy Codex
notify (JSON in argv) and the unchanged Claude path. Audio is stubbed: the
test captures the sentence that would be synthesized."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "notify_tts_codex_under_test", REPO / "scripts" / "notify-tts.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nt = _load()

CODEX_STOP = {
    "session_id": "s1", "transcript_path": None, "cwd": "C:/x/alpha", "hook_event_name": "Stop",
    "model": "gpt-5", "turn_id": "t1", "stop_hook_active": False,
    "last_assistant_message": "Fatto: ho aggiornato i test.", "permission_mode": "default",
}
CODEX_PERM = {
    "session_id": "s1", "cwd": "C:/x/alpha", "hook_event_name": "PermissionRequest", "model": "gpt-5",
    "turn_id": "t2", "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "Pulisci la build"},
}
LEGACY_NOTIFY = {
    "type": "agent-turn-complete", "thread-id": "th", "turn-id": "t", "cwd": "C:/x/alpha",
    "input-messages": ["fai x"], "last-assistant-message": "ok",
}


# --------------------------------------------------------------------------- read_event


def test_codex_stop_from_stdin():
    src, ev = nt.read_event(["--source", "codex"], lambda: json.dumps(CODEX_STOP))
    assert src == "codex"
    assert ev["hook_event_name"] == "Stop" and ev["agent"] == "codex" and ev["cwd"] == "C:/x/alpha"


def test_codex_permission_request_maps_to_permission_prompt():
    src, ev = nt.read_event(["--source=codex"], lambda: json.dumps(CODEX_PERM))
    assert ev["hook_event_name"] == "Notification"
    assert ev["notification_type"] == "permission_prompt"
    assert ev["message"] == "Pulisci la build"


def test_codex_permission_without_description_uses_tool_name():
    payload = dict(CODEX_PERM, tool_input={"command": "ls"})
    _, ev = nt.read_event(["--source", "codex"], lambda: json.dumps(payload))
    assert ev["message"] == "Bash"


def test_legacy_notify_json_in_argv_ignores_stdin():
    def no_stdin():
        raise AssertionError("stdin must not be read for legacy notify")

    src, ev = nt.read_event([json.dumps(LEGACY_NOTIFY)], no_stdin)
    assert src == "codex" and ev["hook_event_name"] == "Stop" and ev["cwd"] == "C:/x/alpha"


@pytest.mark.parametrize("event", ["SessionStart", "PreToolUse", "UserPromptSubmit"])
def test_other_codex_events_are_silent(event):
    assert nt.read_event(["--source", "codex"], lambda: json.dumps({"hook_event_name": event})) is None


def test_claude_path_unchanged_without_flag():
    payload = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "cwd": "C:/x/alpha"}
    src, ev = nt.read_event([], lambda: json.dumps(payload))
    assert src == "claude" and ev == payload


@pytest.mark.parametrize("raw", ["", "   ", "{bad", "[1]"])
def test_garbage_input_is_silent(raw):
    assert nt.read_event(["--source", "codex"], lambda: raw) is None
    assert nt.read_event([], lambda: raw) is None


# --------------------------------------------------------------------------- spoken sentence


@pytest.fixture()
def spoken(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(nt, "burst_check_should_play", lambda wait_ms=0: True)
    monkeypatch.setattr(nt, "cleanup_tts_cache", lambda: None)
    monkeypatch.setattr(nt, "load_config", lambda: {"tts_mode": "full"})
    monkeypatch.setattr(nt, "play_chime", lambda key, progress=0.0: said.append(f"chime:{key}"))
    monkeypatch.setattr(nt, "resolve_audio", lambda text, profile: said.append(text) or None)
    monkeypatch.setattr(nt, "maybe_play_easter_egg", lambda mode: None)
    monkeypatch.setattr(nt, "extract_repo_name", lambda data: "alpha")
    return said


def test_codex_stop_says_codex_finished(spoken):
    nt.main(["--source", "codex"], lambda: json.dumps(CODEX_STOP))
    assert spoken == ["chime:stop", "Progetto alpha, Codex ha finito."]


def test_codex_permission_says_needs_you(spoken):
    nt.main(["--source", "codex"], lambda: json.dumps(CODEX_PERM))
    assert spoken == ["chime:permission", "Progetto alpha, Codex ha bisogno del tuo permesso. Pulisci la build"]


def test_legacy_notify_says_codex_finished(spoken):
    nt.main([json.dumps(LEGACY_NOTIFY)], lambda: "")
    assert spoken[-1] == "Progetto alpha, Codex ha finito."


def test_claude_stop_says_claudio_finished(spoken):
    nt.main([], lambda: json.dumps({"hook_event_name": "Stop", "cwd": "C:/x/alpha"}))
    assert spoken == ["chime:stop", "Progetto alpha, Claudio ha finito."]


def test_nothing_printed_to_stdout(spoken, capsys):
    # A Codex Stop hook must print JSON or nothing.
    nt.main(["--source", "codex"], lambda: json.dumps(CODEX_STOP))
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- detached worker (Codex)


def test_codex_event_is_handed_to_detached_worker(spoken):
    calls = []
    nt.main(["--source", "codex"], lambda: json.dumps(CODEX_STOP),
            detach=lambda argv, raw: calls.append((argv, raw)) or True)
    assert calls == [(["--source", "codex"], json.dumps(CODEX_STOP))]
    assert spoken == []  # the hook process itself says nothing: the worker does


def test_worker_handles_event_inline(spoken):
    def never(argv, raw):
        raise AssertionError("a worker must not re-spawn")

    nt.main(["--source", "codex", nt.WORKER_FLAG], lambda: json.dumps(CODEX_STOP), detach=never)
    assert spoken == ["chime:stop", "Progetto alpha, Codex ha finito."]


def test_failed_spawn_falls_back_to_inline(spoken):
    nt.main(["--source", "codex"], lambda: json.dumps(CODEX_STOP), detach=lambda argv, raw: False)
    assert spoken[-1] == "Progetto alpha, Codex ha finito."


def test_claude_events_never_detach(spoken):
    def never(argv, raw):
        raise AssertionError("Claude Code hooks are async already")

    nt.main([], lambda: json.dumps({"hook_event_name": "Stop", "cwd": "C:/x/alpha"}), detach=never)
    assert spoken[0] == "chime:stop"


def test_silent_codex_events_do_not_spawn(spoken):
    def never(argv, raw):
        raise AssertionError("nothing to say -> no worker")

    nt.main(["--source", "codex"], lambda: json.dumps({"hook_event_name": "SessionStart"}), detach=never)
    assert spoken == []
