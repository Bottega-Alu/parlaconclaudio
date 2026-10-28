"""Shared pytest fixtures for the voice_bridge test suite.

autouse log guard
------------------
The production VoiceBridge may be running concurrently (pythonw -m
src.voice_bridge) and actively writing to the repo-root voicebridge.log via
a module-level `logging.FileHandler(path, mode="w")` in bridge.py. The FIRST
test in the session that imports src.voice_bridge.bridge would otherwise
truncate that live log. This autouse fixture neutralizes logging.FileHandler
for the duration of every test, so no test file -- present or future -- can
truncate it, regardless of which one happens to import bridge.py first.
"""
import logging

import pytest


@pytest.fixture(autouse=True)
def _never_truncate_live_log(monkeypatch):
    class _NullHandler(logging.Handler):
        def emit(self, record):
            pass

    monkeypatch.setattr(logging, "FileHandler", lambda *a, **k: _NullHandler())
    yield


@pytest.fixture(autouse=True)
def _never_touch_the_real_config(monkeypatch, tmp_path):
    """Tests read and write a throwaway tts_config.json, never the user's settings."""
    from src.core.stt_engine import key_manager
    from src.voice_bridge import audio_file_processor, transcriber, tray_icon
    for module in (tray_icon, transcriber, audio_file_processor, key_manager):
        monkeypatch.setattr(module, "TTS_CONFIG", tmp_path / "tts_config.json")
    # Dictation history lives under %LOCALAPPDATA%: a throwaway one, never the user's.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    yield


@pytest.fixture(autouse=True)
def _never_touch_the_real_clipboard(monkeypatch):
    """The private clipboard path is unavailable in tests: copies go through pyperclip,
    which the delivery tests replace (a test that fakes win32clipboard overrides this)."""
    from src.voice_bridge import output_handler

    def unavailable():
        raise OSError("clipboard disabled in tests")

    monkeypatch.setattr(output_handler, "_win32clipboard", unavailable)
    yield


@pytest.fixture(autouse=True)
def _no_network_prewarm(monkeypatch):
    """Starting a recording opens a connection to OpenRouter: never from tests."""
    from src.voice_bridge import stt_cleanup
    monkeypatch.setattr(stt_cleanup, "prewarm", lambda *a, **k: None)
    yield


@pytest.fixture(autouse=True)
def _never_touch_the_real_credential_vault(monkeypatch):
    """No test reads or writes the Windows Credential Manager: keyring is absent unless a test fakes it."""
    import sys
    monkeypatch.setitem(sys.modules, "keyring", None)
    yield


@pytest.fixture(autouse=True)
def _italian_interface_default_theme():
    """Tests assert Italian labels: never the language or theme of the machine running them."""
    from src.voice_bridge import fluent, i18n
    i18n.set_language(i18n.SOURCE)
    fluent.set_theme("system")
    yield
    i18n.set_language(i18n.SOURCE)
    fluent.set_theme("system")


class FakeRunKey:
    """In-memory HKCU Run value (see src/voice_bridge/autostart.RunKey)."""

    def __init__(self, value=None, fail=None):
        self.value, self.fail = value, fail

    def get(self):
        if self.fail == "get":
            raise PermissionError("denied")
        return self.value

    def set(self, command):
        if self.fail == "set":
            raise PermissionError("[WinError 5] Accesso negato")
        self.value = command

    def delete(self):
        if self.fail == "delete":
            raise PermissionError("[WinError 5] Accesso negato")
        had, self.value = self.value is not None, None
        return had


@pytest.fixture(autouse=True)
def _never_touch_real_run_key(monkeypatch):
    """No test can read or write the real HKCU Run value: the default RunKey
    used by autostart when none is injected is an in-memory fake."""
    from src.voice_bridge import autostart

    fake = FakeRunKey()
    monkeypatch.setattr(autostart, "_default_run_key", lambda: fake)
    yield fake
