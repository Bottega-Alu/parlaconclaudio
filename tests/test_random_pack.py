"""Tests for sound-pack resolution in scripts/notify-tts.py.

We import the script as a module to test the pure resolver functions.
"""
import importlib.util
import json
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


# ─── get_sound_pack: now a thin pass-through ───

def test_get_sound_pack_returns_configured_value(notify_tts, monkeypatch):
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "r2d2"})
    assert notify_tts.get_sound_pack() == "r2d2"


def test_get_sound_pack_returns_random_literal(notify_tts, monkeypatch):
    """sound_pack='random' is no longer resolved here — play_chime handles it."""
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "random"})
    assert notify_tts.get_sound_pack() == "random"


# ─── _pick_cross_pack_chime: cross-pack random sample ───

def _make_pack(parent: Path, name: str, chime_files: dict[str, list[str]]) -> Path:
    """Create a fake pack dir with manifest.json + given mp3 stubs."""
    pack = parent / name
    pack.mkdir()
    manifest = {"chimes": chime_files, "sounds": {}}
    (pack / "manifest.json").write_text(json.dumps(manifest))
    for files in chime_files.values():
        for f in files:
            (pack / f).write_bytes(b"\x00" * 100)  # stub mp3 content
    return pack


def test_cross_pack_pick_aggregates_across_packs(notify_tts, monkeypatch, tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    _make_pack(sounds_dir, "alpha", {"task_done": ["a1.mp3", "a2.mp3"]})
    _make_pack(sounds_dir, "beta", {"task_done": ["b1.mp3"]})
    _make_pack(sounds_dir, "gamma", {"task_done": ["g1.mp3", "g2.mp3", "g3.mp3"]})
    # Hidden pack should be ignored
    _make_pack(sounds_dir, "_hidden", {"task_done": ["x1.mp3"]})

    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", sounds_dir)

    seen_packs = set()
    seen_files = set()
    for _ in range(60):
        chosen = notify_tts._pick_cross_pack_chime("task_done")
        assert chosen is not None
        seen_packs.add(chosen.parent.name)
        seen_files.add(chosen.name)

    # Hidden pack must never appear
    assert "_hidden" not in seen_packs
    # All 3 visible packs should be statistically reachable across 60 picks
    assert seen_packs == {"alpha", "beta", "gamma"}
    # And we should have hit a variety of files (not stuck on one)
    assert len(seen_files) >= 4


def test_cross_pack_pick_returns_none_when_chime_key_missing(notify_tts, monkeypatch, tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    _make_pack(sounds_dir, "alpha", {"task_done": ["a1.mp3"]})

    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", sounds_dir)

    # No pack has a "stop" chime → no candidates
    assert notify_tts._pick_cross_pack_chime("stop") is None


def test_cross_pack_pick_falls_back_to_default_chime_pool(notify_tts, monkeypatch, tmp_path):
    sounds_dir = tmp_path / "sounds"
    sounds_dir.mkdir()
    # Pack only defines "default"; specific chime keys should fall through to it
    _make_pack(sounds_dir, "alpha", {"default": ["d1.mp3", "d2.mp3"]})

    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", sounds_dir)

    chosen = notify_tts._pick_cross_pack_chime("task_done")
    assert chosen is not None
    assert chosen.name in ("d1.mp3", "d2.mp3")


def test_cross_pack_pick_returns_none_when_no_packs_installed(notify_tts, monkeypatch, tmp_path):
    sounds_dir = tmp_path / "empty"
    sounds_dir.mkdir()
    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", sounds_dir)
    assert notify_tts._pick_cross_pack_chime("task_done") is None
