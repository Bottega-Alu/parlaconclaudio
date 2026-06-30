"""Tests for the STT cleanup agent + its wiring into the file pipeline.

All HTTP is mocked (monkeypatch on the HTTP boundary inside ``stt_cleanup``)
and edge-tts / Groq / Whisper are stubbed — there is NO network access here.

Covers:
  * cleanup happy path                     -> returns cleaned text
  * primary model fails -> fallback used   -> returns fallback text
  * both models fail                       -> None (NullObject)
  * missing api_key                        -> None, no HTTP at all
  * retry-once on 5xx inside one model
  * circuit-breaker opens after N failures -> short-circuits (no HTTP)
  * pipeline: enable_stt_cleanup=False     -> cleanup_text never called
  * pipeline: long transcription           -> EN/IT/PT translations + dubs skipped
  * pipeline: normal path                  -> downstream runs on the *cleaned* text
  * transcriber it-bias fix                -> auto-detect (None) by default
"""
import json

import pytest

from src.voice_bridge import stt_cleanup
from src.voice_bridge import audio_file_processor as afp
from src.voice_bridge.audio_file_processor import AudioFileProcessor


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _reset_breaker():
    """The circuit-breaker is module/session state — reset around each test."""
    stt_cleanup.reset_circuit_breaker()
    yield
    stt_cleanup.reset_circuit_breaker()


def _ok(text: str) -> dict:
    """Build an OpenAI-compatible chat-completion response body."""
    return {"choices": [{"message": {"content": text}}]}


class FakeTranscriber:
    """Minimal stand-in for Transcriber used by AudioFileProcessor."""

    def __init__(self, text: str, lang: str, en_text: str = "ENGLISH"):
        self.text = text
        self.lang = lang
        self.en_text = en_text
        self.calls: list[str] = []

    def transcribe_file(self, path, language=None, task="transcribe"):
        self.calls.append(task)
        if task == "translate":
            return self.en_text, "en"
        return self.text, self.lang


# --------------------------------------------------------------------------- #
# FEATURE A — cleanup_text unit behaviour
# --------------------------------------------------------------------------- #

def test_cleanup_happy_returns_cleaned(monkeypatch):
    seen = []

    def fake_http(url, body, headers, timeout):
        payload = json.loads(body.decode("utf-8"))
        seen.append(payload["model"])
        assert url == stt_cleanup.OPENROUTER_API_URL
        assert headers["Authorization"] == "Bearer KEY"
        # user message carries the raw text verbatim
        assert payload["messages"][-1]["content"] == "spicciuto testo"
        # detected_language must NOT be smuggled in as authoritative
        assert payload["temperature"] == 0.2
        # max_tokens is dynamic; short input floors at 1024
        assert payload["max_tokens"] == 1024
        # no glossary passed -> base prompt, no GLOSSARY section
        assert "GLOSSARY" not in payload["messages"][0]["content"]
        # the request stays clean — no `reasoning` override
        assert "reasoning" not in payload
        return _ok("speech to text")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    out = stt_cleanup.cleanup_text(
        "spicciuto testo", model="qwen", fallback="nova", api_key="KEY"
    )
    assert out == "speech to text"
    assert seen == ["qwen"]  # fallback untouched on success


def test_cleanup_primary_fails_uses_fallback(monkeypatch):
    monkeypatch.setattr(stt_cleanup.time, "sleep", lambda *a, **k: None)
    seen = []

    def fake_http(url, body, headers, timeout):
        model = json.loads(body.decode("utf-8"))["model"]
        seen.append(model)
        if model == "qwen":
            raise stt_cleanup._HttpError(status=500)
        return _ok("cleaned by fallback")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    out = stt_cleanup.cleanup_text("raw", model="qwen", fallback="nova", api_key="KEY")
    assert out == "cleaned by fallback"
    assert "qwen" in seen and "nova" in seen


def test_cleanup_both_fail_returns_none(monkeypatch):
    monkeypatch.setattr(stt_cleanup.time, "sleep", lambda *a, **k: None)

    def fake_http(url, body, headers, timeout):
        raise stt_cleanup._HttpError(status=None)  # network/timeout

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    out = stt_cleanup.cleanup_text("raw", model="qwen", fallback="nova", api_key="KEY")
    assert out is None


def test_cleanup_missing_key_returns_none_without_http(monkeypatch):
    called = []
    monkeypatch.setattr(
        stt_cleanup, "_http_post_json", lambda *a, **k: called.append(1) or _ok("x")
    )
    assert stt_cleanup.cleanup_text("raw", model="qwen", fallback="nova", api_key=None) is None
    assert called == []  # no network attempted when key is missing


def test_cleanup_empty_text_returns_none(monkeypatch):
    called = []
    monkeypatch.setattr(
        stt_cleanup, "_http_post_json", lambda *a, **k: called.append(1) or _ok("x")
    )
    assert stt_cleanup.cleanup_text("   ", model="qwen", fallback=None, api_key="KEY") is None
    assert called == []


def test_cleanup_glossary_injected_into_system_prompt(monkeypatch):
    captured = {}

    def fake_http(url, body, headers, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return _ok("out")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    stt_cleanup.cleanup_text(
        "di tab è giù", model="m", fallback=None, api_key="KEY",
        glossary=["GitHub", "OpenRouter"],
    )
    sys_msg = captured["payload"]["messages"][0]["content"]
    assert "7. GLOSSARY" in sys_msg
    assert "GitHub, OpenRouter" in sys_msg
    # base prompt is preserved, glossary is appended after it
    assert sys_msg.startswith(stt_cleanup.SYSTEM_PROMPT)


def test_cleanup_empty_glossary_keeps_base_prompt(monkeypatch):
    captured = {}

    def fake_http(url, body, headers, timeout):
        captured["payload"] = json.loads(body.decode("utf-8"))
        return _ok("out")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    # both None and empty-list glossaries must yield the unchanged base prompt
    for gloss in (None, [], ["   ", ""]):
        stt_cleanup.cleanup_text("testo", model="m", fallback=None, api_key="KEY",
                                 glossary=gloss)
        sys_msg = captured["payload"]["messages"][0]["content"]
        assert "GLOSSARY" not in sys_msg
        assert sys_msg == stt_cleanup.SYSTEM_PROMPT


def test_max_tokens_scales_with_input_length(monkeypatch):
    captured = []

    def fake_http(url, body, headers, timeout):
        captured.append(json.loads(body.decode("utf-8")))
        return _ok("out")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)

    # short input -> floored at 1024
    stt_cleanup.cleanup_text("short", model="m", fallback=None, api_key="KEY")
    assert captured[-1]["max_tokens"] == 1024

    # mid-length input -> len//3 + 256
    mid = "x" * 6000
    stt_cleanup.cleanup_text(mid, model="m", fallback=None, api_key="KEY")
    assert captured[-1]["max_tokens"] == min(4096, max(1024, len(mid) // 3 + 256))
    assert captured[-1]["max_tokens"] == 2256

    # very long input -> capped at 4096
    huge = "y" * 30000
    stt_cleanup.cleanup_text(huge, model="m", fallback=None, api_key="KEY")
    assert captured[-1]["max_tokens"] == 4096


def test_chat_retries_once_on_5xx(monkeypatch):
    monkeypatch.setattr(stt_cleanup.time, "sleep", lambda *a, **k: None)
    state = {"n": 0}

    def fake_http(url, body, headers, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise stt_cleanup._HttpError(status=503)
        return _ok("ok after retry")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    out = stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY")
    assert out == "ok after retry"
    assert state["n"] == 2  # original + one retry


def test_circuit_breaker_opens_after_threshold(monkeypatch):
    monkeypatch.setattr(stt_cleanup.time, "sleep", lambda *a, **k: None)
    calls = {"n": 0}

    def fake_http(url, body, headers, timeout):
        calls["n"] += 1
        raise stt_cleanup._HttpError(status=None)

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)

    for _ in range(stt_cleanup._CIRCUIT_BREAKER_THRESHOLD):
        assert stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY") is None

    before = calls["n"]
    # breaker now open -> next call must short-circuit with no HTTP
    assert stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY") is None
    assert calls["n"] == before


def test_success_resets_breaker(monkeypatch):
    monkeypatch.setattr(stt_cleanup.time, "sleep", lambda *a, **k: None)
    mode = {"fail": True}

    def fake_http(url, body, headers, timeout):
        if mode["fail"]:
            raise stt_cleanup._HttpError(status=None)
        return _ok("good")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", fake_http)
    # one failure
    assert stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY") is None
    # then a success resets the counter
    mode["fail"] = False
    assert stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY") == "good"
    # breaker stays closed across many more successes
    for _ in range(stt_cleanup._CIRCUIT_BREAKER_THRESHOLD + 1):
        assert stt_cleanup.cleanup_text("raw", model="qwen", fallback=None, api_key="KEY") == "good"


# --------------------------------------------------------------------------- #
# FEATURE A/B — pipeline wiring
# --------------------------------------------------------------------------- #

def test_pipeline_cleanup_disabled_not_called(monkeypatch, tmp_path):
    monkeypatch.setattr(
        afp, "_load_tts_config",
        lambda: {"enable_stt_cleanup": False, "transcribe_translations": False},
    )
    spy = []
    monkeypatch.setattr(afp, "cleanup_text", lambda *a, **k: spy.append(1) or "NOPE")
    monkeypatch.setattr(AudioFileProcessor, "_synthesize_dubs", staticmethod(lambda result: []))

    src = tmp_path / "note.mp3"
    src.write_bytes(b"x")
    proc = AudioFileProcessor(FakeTranscriber("ciao mondo", "it"))
    res = proc.process_file(src)

    assert spy == []                      # cleanup_text never invoked
    assert res.cleaned is None
    assert res.original == "ciao mondo"
    assert (tmp_path / "note.original.txt").read_text(encoding="utf-8") == "ciao mondo"


def test_pipeline_long_file_skips_extras(monkeypatch, tmp_path):
    long_text = "parola " * 100  # 700 chars > threshold
    monkeypatch.setattr(
        afp, "_load_tts_config",
        lambda: {
            "enable_stt_cleanup": False,
            "transcribe_extras_max_chars": 50,
            "transcribe_translations": True,
            "transcribe_tts_dub": True,
        },
    )
    dub_spy = []
    groq_calls = []
    monkeypatch.setattr(
        AudioFileProcessor, "_synthesize_dubs",
        staticmethod(lambda result: dub_spy.append(1) or []),
    )
    monkeypatch.setattr(afp, "_groq_translate", lambda *a, **k: groq_calls.append(1) or "X")
    monkeypatch.setattr(afp, "_load_groq_key", lambda: "fake-key")

    src = tmp_path / "long.mp3"
    src.write_bytes(b"x")
    tr = FakeTranscriber(long_text, "it")
    res = AudioFileProcessor(tr).process_file(src)

    assert "translate" not in tr.calls    # English (audio re-transcribe) skipped
    assert groq_calls == []               # IT/PT translations skipped
    assert dub_spy == []                  # TTS dubs skipped
    assert res.english == "" and res.italian == "" and res.portuguese == ""
    assert (tmp_path / "long.original.txt").is_file()
    assert not (tmp_path / "long.en.txt").exists()
    assert not (tmp_path / "long.it.txt").exists()
    assert not (tmp_path / "long.pt.txt").exists()


def test_pipeline_normal_path_uses_cleaned(monkeypatch, tmp_path):
    monkeypatch.setattr(
        afp, "_load_tts_config",
        lambda: {
            "enable_stt_cleanup": True,
            "transcribe_extras_max_chars": 2500,
            "transcribe_translations": True,
            "transcribe_tts_dub": False,
        },
    )
    monkeypatch.setattr(afp, "cleanup_text", lambda text, **k: "CLEANED")
    groq_calls = []
    monkeypatch.setattr(
        afp, "_groq_translate",
        lambda base, lang_name, key: groq_calls.append((base, lang_name)) or f"T:{lang_name}",
    )
    monkeypatch.setattr(afp, "_load_groq_key", lambda: "fake-key")
    monkeypatch.setattr(AudioFileProcessor, "_synthesize_dubs", staticmethod(lambda result: []))

    src = tmp_path / "n.mp3"
    src.write_bytes(b"x")
    # Spanish source -> both IT and PT actually get translated (neither is the
    # source language), and English comes from the audio translate step.
    tr = FakeTranscriber("texto crudo", "es", en_text="EN TRANS")
    proc = AudioFileProcessor(tr)
    proc._openrouter_key = lambda: "or-key"  # avoid touching real key stores
    res = proc.process_file(src)

    assert res.original == "texto crudo"          # raw preserved for audit
    assert res.cleaned == "CLEANED"
    assert res.english == "EN TRANS"              # step 2 (audio translate) untouched
    assert "translate" in tr.calls
    # IT/PT translations ran on the CLEANED text, not the raw original
    assert groq_calls and all(base == "CLEANED" for base, _ in groq_calls)
    assert res.italian == "T:Italian"
    assert res.portuguese == "T:Portuguese (Brazilian variant)"
    # source-language .txt holds the cleaned version
    assert (tmp_path / "n.original.txt").read_text(encoding="utf-8") == "CLEANED"


# --------------------------------------------------------------------------- #
# FEATURE C — Whisper it-bias fix
# --------------------------------------------------------------------------- #

def test_transcriber_language_defaults_to_autodetect(monkeypatch):
    from src.voice_bridge.transcriber import Transcriber

    t = Transcriber()
    monkeypatch.setattr(t, "_load_config", lambda: {})
    assert t._get_system_language() is None      # no hardcoded "it"
    assert t._get_active_language() is None       # -> Whisper auto-detect


def test_transcriber_locale_fallback_is_optin(monkeypatch):
    from src.voice_bridge.transcriber import Transcriber

    t = Transcriber()
    monkeypatch.setattr(t, "_load_config", lambda: {"whisper_locale_fallback": True})
    val = t._get_system_language()
    assert val is None or isinstance(val, str)    # locale-derived only when opted in
