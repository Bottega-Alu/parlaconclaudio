"""Every announcement says WHO is speaking, as one flowing sentence:
"Progetto <repo>, Claudio|Codex <phrase>"; no project prefix when the project
is unknown or named like an agent."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("notify_tts_speaker_under_test",
                                                  REPO / "scripts" / "notify-tts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nt = _load()


@pytest.fixture()
def spoken(monkeypatch, tmp_path):
    said: list[str] = []
    monkeypatch.setattr(nt, "QUESTION_MARK_FILE", tmp_path / "notify_question.json")
    monkeypatch.setattr(nt, "burst_check_should_play", lambda wait_ms=0: True)
    monkeypatch.setattr(nt, "cleanup_tts_cache", lambda: None)
    monkeypatch.setattr(nt, "load_config", lambda: {"tts_mode": "full"})
    monkeypatch.setattr(nt, "play_chime", lambda key, progress=0.0: None)
    monkeypatch.setattr(nt, "resolve_audio", lambda text, profile: said.append(text) or None)
    monkeypatch.setattr(nt, "maybe_play_easter_egg", lambda mode: None)
    return said


def _say(spoken, monkeypatch, payload, argv=(), repo="alpha"):
    monkeypatch.setattr(nt, "extract_repo_name", lambda data: repo)
    nt.main(list(argv), lambda: json.dumps(payload))
    return spoken[-1]


CLAUDE_CASES = [
    ({"hook_event_name": "Stop"}, "Progetto alpha, Claudio ha finito."),
    ({"hook_event_name": "Stop", "stop_hook_active": True}, "Progetto alpha, Claudio ha finito un ciclo."),
    ({"hook_event_name": "Notification", "notification_type": "permission_prompt"},
     "Progetto alpha, Claudio ha bisogno del tuo permesso."),
    ({"hook_event_name": "Notification", "notification_type": "idle_prompt", "message": "waiting"},
     "Progetto alpha, Claudio ha bisogno del tuo aiuto."),
    ({"hook_event_name": "Notification", "notification_type": "elicitation_dialog"},
     "Progetto alpha, Claudio ha bisogno di una tua scelta."),
    ({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s9"},
     "Progetto alpha, Claudio ha bisogno di una tua scelta."),
]


@pytest.mark.parametrize("payload,expected", CLAUDE_CASES)
def test_claude_hooks_speak_as_claudio(spoken, monkeypatch, payload, expected):
    assert _say(spoken, monkeypatch, payload) == expected


@pytest.mark.parametrize("payload,expected", [
    ({"hook_event_name": "Stop"}, "Progetto alpha, Codex ha finito."),
    ({"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}},
     "Progetto alpha, Codex ha bisogno del tuo permesso. Bash"),
])
def test_codex_hooks_speak_as_codex(spoken, monkeypatch, payload, expected):
    assert _say(spoken, monkeypatch, payload, argv=["--source", "codex", nt.WORKER_FLAG]) == expected


def test_stop_keeps_task_detail(spoken, monkeypatch):
    text = _say(spoken, monkeypatch, {"hook_event_name": "Stop", "task_subject": "fix login bug",
                                      "plan_title": "Auth"})
    assert text == "Progetto alpha, Claudio ha finito. Bug risolto. fix login bug Piano: Auth."


def test_permission_keeps_detail(spoken, monkeypatch):
    text = _say(spoken, monkeypatch, {"hook_event_name": "Notification",
                                      "notification_type": "permission_prompt", "message": "usa Bash"})
    assert text == "Progetto alpha, Claudio ha bisogno del tuo permesso. usa Bash"


@pytest.mark.parametrize("repo", ["sconosciuto", "", "codex", "Codex", "claudio", "claude", ".codex"])
def test_no_project_prefix_when_unknown_or_agent_name(spoken, monkeypatch, repo):
    assert _say(spoken, monkeypatch, {"hook_event_name": "Stop"}, argv=["--source", "codex", nt.WORKER_FLAG],
                repo=repo) == "Codex ha finito."
    assert _say(spoken, monkeypatch, {"hook_event_name": "Stop"}, repo=repo) == "Claudio ha finito."


def test_one_flowing_sentence_no_repeated_subject():
    for who in (nt.SPEAKER_CLAUDE, nt.SPEAKER_CODEX):
        text = nt.announce("alpha", who, nt.STOP_PHRASE)
        assert text == f"Progetto alpha, {who} ha finito."
        assert text.count(who) == 1 and "alpha." not in text
