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
