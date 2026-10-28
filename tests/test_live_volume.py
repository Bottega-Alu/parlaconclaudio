"""The detached player follows the volume sliders while the clip is playing."""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np


def load_hook():
    spec = importlib.util.spec_from_file_location(
        "notify_live_volume", Path(__file__).resolve().parents[1] / "scripts" / "notify-tts.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    return hook


class FakeStream:
    def __init__(self, on_write):
        self.blocks, self.on_write = [], on_write

    def write(self, data):
        self.blocks.append(np.frombuffer(data, dtype=np.int16).reshape(-1, 2))
        self.on_write(len(self.blocks))

    def stop_stream(self):
        pass

    def close(self):
        pass


def fake_audio(stream):
    audio = SimpleNamespace(open=lambda **kw: stream, terminate=lambda: None)
    return SimpleNamespace(paInt16=8, PyAudio=lambda: audio)


def fake_decoder(blocks, frames):
    pcm = np.full((blocks * frames, 2), 1000, dtype=np.int16).tobytes()
    return SimpleNamespace(stdout=io.BytesIO(pcm), poll=lambda: 0, kill=lambda: None)


def test_volume_change_reaches_the_clip_already_playing(monkeypatch, tmp_path):
    hook = load_hook()
    config = tmp_path / "tts_config.json"
    config.write_text(json.dumps({"volume": 100, "voice_volume": 100}))
    monkeypatch.setattr(hook, "TTS_CONFIG", config)

    def slider_moves(written):
        if written == 2:  # the user drags the voices slider mid-sentence
            config.write_text(json.dumps({"volume": 100, "voice_volume": 125}))
            stat = config.stat()
            os.utime(config, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

    stream = FakeStream(slider_moves)
    monkeypatch.setitem(sys.modules, "pyaudio", fake_audio(stream))
    frames = hook.LIVE_BLOCK_FRAMES
    monkeypatch.setattr(hook.subprocess, "Popen", lambda *a, **kw: fake_decoder(4, frames))

    assert hook.play_live("clip.mp3", voice=True) is True
    first, _, ramp, after = stream.blocks
    assert (first == 1000).all()
    assert ramp[0, 0] == 1000 and ramp[-1, 0] == 1250  # ramped, no click
    assert (after == 1250).all()


def test_mute_and_clipping(monkeypatch, tmp_path):
    hook = load_hook()
    config = tmp_path / "tts_config.json"
    config.write_text(json.dumps({"volume": 300}))  # saved by the old 0..300 range
    monkeypatch.setattr(hook, "TTS_CONFIG", config)
    level = hook._LiveLevel(voice=False)
    assert level.read() == 1.25
    config.write_text(json.dumps({"volume": 300, "muted": True}))
    os.utime(config, ns=(0, config.stat().st_mtime_ns + 10**9))
    assert level.read() == 0.0


def test_player_falls_back_to_ffplay_without_live_audio(monkeypatch):
    hook = load_hook()
    monkeypatch.setattr(hook, "load_config", lambda: {"volume": 40, "voice_volume": 120})
    monkeypatch.setattr(hook, "play_live", lambda path, voice: False)
    calls = []
    monkeypatch.setattr(hook.subprocess, "Popen", lambda args, **kw: calls.append(args))
    hook.main(["--play", "voice", "clip.mp3"])
    assert calls and calls[0][calls[0].index("-af") + 1] == "volume=120/100"
