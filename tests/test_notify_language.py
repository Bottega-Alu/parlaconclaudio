"""Announcements speak the interface language, with a voice that can speak it."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("notify_tts_language_under_test", REPO / "scripts" / "notify-tts.py")
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)


@pytest.fixture()
def spoken(monkeypatch, tmp_path):
    said = []
    config = {"tts_mode": "full"}
    monkeypatch.setattr(nt, "QUESTION_MARK_FILE", tmp_path / "q.json")
    monkeypatch.setattr(nt, "burst_check_should_play", lambda wait_ms=0: True)
    monkeypatch.setattr(nt, "cleanup_tts_cache", lambda: None)
    monkeypatch.setattr(nt, "load_config", lambda: dict(config))
    monkeypatch.setattr(nt, "play_chime", lambda key, progress=0.0: None)
    monkeypatch.setattr(nt, "resolve_audio", lambda text, profile: said.append((text, profile["voice"])) or None)
    monkeypatch.setattr(nt, "maybe_play_easter_egg", lambda mode: None)
    monkeypatch.setattr(nt, "extract_repo_name", lambda data: "alpha")
    return said, config


@pytest.mark.parametrize("lang,stop,permission,voice", [
    ("en", "Project alpha, Claudio has finished.", "Project alpha, Claudio needs your permission. use Bash",
     "en-US-EmmaMultilingualNeural"),
    ("pt-BR", "Projeto alpha, Claudio terminou.", "Projeto alpha, Claudio precisa da sua permissão. use Bash",
     "pt-BR-ThalitaMultilingualNeural"),
])
def test_announcements_follow_the_interface_language(spoken, lang, stop, permission, voice):
    said, config = spoken
    config.update(ui_language=lang, voice={"voice": "it-IT-IsabellaNeural"})
    nt.main([], lambda: json.dumps({"hook_event_name": "Stop"}))
    nt.main([], lambda: json.dumps({"hook_event_name": "Notification",
                                    "notification_type": "permission_prompt", "message": "use Bash"}))
    assert said == [(stop, voice), (permission, voice)]  # Italian-only voice replaced


def test_multilingual_voice_is_kept_and_never_set_means_italian(spoken):
    said, config = spoken
    config.update(ui_language="en", voice={"voice": "en-US-BrianMultilingualNeural"})
    nt.main([], lambda: json.dumps({"hook_event_name": "Stop"}))
    config.pop("ui_language")
    config["voice"] = {"voice": "it-IT-DiegoNeural"}
    nt.main([], lambda: json.dumps({"hook_event_name": "Stop"}))
    assert said == [("Project alpha, Claudio has finished.", "en-US-BrianMultilingualNeural"),
                    ("Progetto alpha, Claudio ha finito.", "it-IT-DiegoNeural")]


def test_system_follows_windows(monkeypatch):
    monkeypatch.setattr(nt, "windows_language", lambda: "pt-BR")
    assert nt.announce_language({"ui_language": "system"}) == "pt-BR"
    monkeypatch.setattr(nt, "windows_language", lambda: None)
    assert nt.announce_language({"ui_language": "system"}) == "it"
    assert nt.announce_language({"ui_language": "xx"}) == "it"


def test_every_language_has_every_phrase():
    reference = nt.PHRASES["it"]
    for lang in nt.ANNOUNCE_LANGUAGES:
        phrases = nt.PHRASES[lang]
        assert set(phrases) == set(reference)
        assert set(phrases["notif"]) == set(reference["notif"])
        assert set(phrases["stop_labels"]) == set(reference["stop_labels"])
        assert lang in nt.LANGUAGE_VOICES and lang in nt.VOICE_LOCALE_PREFIX
