"""The local engine follows the hardware: no NVIDIA card still dictates (Whisper on CPU),
and a small GPU gets the quantized/smaller model the installer chose, not large-v3 fp16."""
import pytest

from src.core import gpu_probe
from src.core.gpu_probe import GpuInfo, select_profile
from src.voice_bridge import transcriber as tr

NO_GPU = GpuInfo(present=False, name="", vram_mb=0, driver_version="", cuda_usable=False, confirmed_by_ct2=False)
SMALL_GPU = GpuInfo(present=True, name="GTX 1650", vram_mb=4096, driver_version="560.0", cuda_usable=True,
                    confirmed_by_ct2=True)
BIG_GPU = GpuInfo(present=True, name="RTX 5080", vram_mb=16303, driver_version="580.0", cuda_usable=True,
                  confirmed_by_ct2=True)


class FakeEngine:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name = f"whisper-{kwargs['device']}"
        self.beam_size = kwargs.get("beam_size", 5)


@pytest.fixture
def made(monkeypatch):
    import src.core.stt_engine.whisper_rtx as whisper_rtx
    engines = []

    def factory(**kwargs):
        engines.append(FakeEngine(**kwargs))
        return engines[-1]

    monkeypatch.setattr(whisper_rtx, "WhisperRTXEngine", factory)
    from src.core.stt_engine.key_manager import KeyManager
    monkeypatch.setattr(KeyManager, "get_key", staticmethod(lambda provider: None))
    return engines


def transcriber_on(monkeypatch, gpu):
    t = tr.Transcriber()
    monkeypatch.setattr(gpu_probe, "probe_gpu", lambda: gpu)
    return t


def test_cpu_profile_is_small_int8_greedy():
    profile = select_profile(NO_GPU)
    assert (profile.device, profile.model, profile.compute_type, profile.beam_size) == ("cpu", "small", "int8", 1)
    assert select_profile(BIG_GPU).beam_size == 5


def test_no_gpu_and_no_cloud_key_still_dictates_on_cpu(monkeypatch, made):
    t = transcriber_on(monkeypatch, NO_GPU)
    t._ensure_engine()
    assert made and made[0].kwargs["device"] == "cpu" and made[0].kwargs["model_name"] == "small"


def test_local_mode_without_gpu_stays_local(monkeypatch, made, tmp_path):
    import json
    tr.TTS_CONFIG.write_text(json.dumps({"stt_mode": "local"}), encoding="utf-8")
    t = transcriber_on(monkeypatch, NO_GPU)
    t._ensure_engine()
    assert made[0].kwargs["device"] == "cpu"


def test_small_gpu_gets_the_profile_not_large_fp16(monkeypatch, made):
    t = transcriber_on(monkeypatch, SMALL_GPU)
    t._ensure_engine()
    expected = select_profile(SMALL_GPU)
    assert (made[0].kwargs["model_name"], made[0].kwargs["compute_type"]) == (expected.model, expected.compute_type)
    assert (made[0].kwargs["model_name"], made[0].kwargs["compute_type"]) != ("large-v3", "float16")


def test_big_gpu_keeps_large_v3_fp16(monkeypatch, made):
    t = transcriber_on(monkeypatch, BIG_GPU)
    t._ensure_engine()
    assert (made[0].kwargs["device"], made[0].kwargs["model_name"], made[0].kwargs["compute_type"]) == \
        ("cuda", "large-v3", "float16")


def test_live_dictation_skips_timestamps_and_uses_the_profile_beam(monkeypatch, made):
    import numpy as np
    calls = []
    t = transcriber_on(monkeypatch, NO_GPU)
    t._ensure_engine()

    class Result:
        text, error, language = "ciao", None, "it"

    made[0].transcribe = lambda audio, **kwargs: calls.append(kwargs) or Result()
    made[0].is_local = True
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert calls and calls[0]["without_timestamps"] is True and calls[0]["beam_size"] == 1


# --------------------------------------------------------------------------- CPU: fast or accurate


def test_accurate_cpu_profile_is_turbo_beam5_one_thread_per_core(monkeypatch):
    monkeypatch.setattr(gpu_probe.os, "cpu_count", lambda: 32)
    profile = select_profile(NO_GPU, cpu_quality="accurate")
    assert (profile.device, profile.model, profile.compute_type, profile.beam_size, profile.cpu_threads) == \
        ("cpu", "large-v3-turbo", "int8", 5, 16)


@pytest.mark.parametrize("quality", ["fast", "garbage", ""])
def test_anything_but_accurate_keeps_small(quality):
    assert select_profile(NO_GPU, cpu_quality=quality).model == "small"


def test_gpu_host_ignores_the_cpu_choice():
    assert select_profile(BIG_GPU, cpu_quality="accurate") == select_profile(BIG_GPU)


@pytest.mark.parametrize("logical,threads", [(32, 16), (64, 16), (16, 8), (8, 4), (4, 4), (2, 4)])
def test_cpu_threads_one_per_physical_core_capped(logical, threads):
    assert gpu_probe.cpu_thread_count(logical) == threads


def test_cpu_threads_when_windows_cannot_count(monkeypatch):
    monkeypatch.setattr(gpu_probe.os, "cpu_count", lambda: None)
    assert gpu_probe.cpu_thread_count() == 4


def test_transcriber_reads_the_cpu_choice(monkeypatch, made):
    import json
    tr.TTS_CONFIG.write_text(json.dumps({"stt_cpu_quality": "accurate"}), encoding="utf-8")
    t = transcriber_on(monkeypatch, NO_GPU)
    t._ensure_engine()
    kw = made[0].kwargs
    assert (kw["model_name"], kw["beam_size"]) == ("large-v3-turbo", 5) and kw["cpu_threads"] >= 4


def test_set_cpu_quality_reloads_engine_then_saves(monkeypatch, tmp_path):
    import json
    from src.voice_bridge import tray_icon as ti
    monkeypatch.setattr(ti, "TTS_CONFIG", tmp_path / "tts_config.json")
    ti.TTS_CONFIG.write_text(json.dumps({"stt_mode": "local"}), encoding="utf-8")
    switched, notes = [], []
    tray = ti.TrayIcon.__new__(ti.TrayIcon)
    tray._on_stt_mode_changed = lambda mode: switched.append(mode) or True
    tray._notify = notes.append
    assert tray._set_cpu_quality("accurate") is True
    assert switched == ["local"] and json.loads(ti.TTS_CONFIG.read_text())["stt_cpu_quality"] == "accurate"
    assert tray._set_cpu_quality("bogus") is False


def test_set_cpu_quality_refused_while_busy_keeps_config(monkeypatch, tmp_path):
    import json
    from src.voice_bridge import tray_icon as ti
    monkeypatch.setattr(ti, "TTS_CONFIG", tmp_path / "tts_config.json")
    ti.TTS_CONFIG.write_text(json.dumps({"stt_cpu_quality": "fast"}), encoding="utf-8")
    notes = []
    tray = ti.TrayIcon.__new__(ti.TrayIcon)
    tray._on_stt_mode_changed = lambda mode: False
    tray._notify = notes.append
    assert tray._set_cpu_quality("accurate") is False
    assert json.loads(ti.TTS_CONFIG.read_text())["stt_cpu_quality"] == "fast" and notes
