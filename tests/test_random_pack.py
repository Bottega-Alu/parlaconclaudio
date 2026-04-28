"""Tests for random pack resolver in scripts/notify-tts.py.

We import the script as a module to test the pure resolver function.
"""
import importlib.util
import sys
from pathlib import Path
import pytest


@pytest.fixture(scope="module")
def notify_tts():
    """Load scripts/notify-tts.py as an importable module."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "notify-tts.py"
    spec = importlib.util.spec_from_file_location("notify_tts_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_pack_returns_configured_pack_when_not_random(notify_tts, monkeypatch):
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "r2d2"})
    assert notify_tts.get_sound_pack() == "r2d2"


def test_resolve_pack_picks_random_when_random_configured(notify_tts, monkeypatch, tmp_path):
    # Create fake sounds dir with three pack subdirs
    fake_sounds = tmp_path / "sounds"
    fake_sounds.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (fake_sounds / name).mkdir()
    # Also a non-dir entry (should be ignored) and a _hidden dir (should be ignored)
    (fake_sounds / "readme.txt").write_text("ignore")
    (fake_sounds / "_disabled").mkdir()

    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "random"})
    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", fake_sounds)

    seen = set()
    for _ in range(50):
        seen.add(notify_tts.get_sound_pack())
    assert seen.issubset({"alpha", "beta", "gamma"})
    assert "_disabled" not in seen
    assert "readme.txt" not in seen
    # Statistically very likely to see all 3 in 50 picks
    assert len(seen) >= 2


def test_resolve_pack_random_with_no_packs_falls_back(notify_tts, monkeypatch, tmp_path):
    fake_sounds = tmp_path / "empty_sounds"
    fake_sounds.mkdir()
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "random"})
    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", fake_sounds)
    assert notify_tts.get_sound_pack() == "r2d2"
