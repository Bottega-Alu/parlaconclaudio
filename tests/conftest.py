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
