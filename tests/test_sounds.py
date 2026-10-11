"""Tests for the timeout glissando (decision B, 2026-10-11).

beep_timeout() must be audibly distinct from beep_stop(): longer, and a
descending glissando ("svirgolato in basso", ~900Hz -> ~250Hz over ~700ms).
No audio is ever actually played in these tests: winsound is mocked.
"""
import io
import struct
import sys
import wave

import pytest

from src.voice_bridge import sounds


# ─── make_chirp_wav: pure synthesis function ───

def _read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as wf:
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    samples = list(struct.unpack(f"<{n_frames}h", raw))
    return n_channels, sample_width, framerate, samples


def test_make_chirp_wav_is_a_valid_mono_16bit_wav():
    data = sounds.make_chirp_wav(900, 250, 700)
    n_channels, sample_width, framerate, samples = _read_wav(data)
    assert n_channels == 1
    assert sample_width == 2
    assert len(samples) > 0


def test_make_chirp_wav_duration_within_5_percent():
    duration_ms = 700
    data = sounds.make_chirp_wav(900, 250, duration_ms)
    _, _, framerate, samples = _read_wav(data)
    actual_ms = 1000 * len(samples) / framerate
    assert abs(actual_ms - duration_ms) <= duration_ms * 0.05


def test_make_chirp_wav_amplitude_capped_at_half_full_scale():
    data = sounds.make_chirp_wav(900, 250, 700, amplitude=0.5)
    _, _, _, samples = _read_wav(data)
    peak = max(abs(s) for s in samples)
    # full scale for int16 is 32767; allow 1-sample rounding slack
    assert peak <= 0.5 * 32767 + 1


def _zero_crossings(samples):
    crossings = 0
    for a, b in zip(samples, samples[1:]):
        if (a >= 0) != (b >= 0):
            crossings += 1
    return crossings


def test_make_chirp_wav_descends_in_frequency():
    """First decile must oscillate faster than the last decile -- the sweep
    goes from f_start (high) down to f_end (low), not the other way round.

    This ordering check alone is weak (S6): it also passes for the WRONG
    phase formula sin(2*pi*f(t)*t) -- see the stronger magnitude-pinning
    test below for the real regression guard on the formula itself.
    """
    data = sounds.make_chirp_wav(900, 250, 700)
    _, _, _, samples = _read_wav(data)
    decile = max(1, len(samples) // 10)
    first_decile_crossings = _zero_crossings(samples[:decile])
    last_decile_crossings = _zero_crossings(samples[-decile:])
    assert first_decile_crossings > last_decile_crossings


def _estimate_instantaneous_freq_hz(samples, sample_rate):
    """Zero-crossing-rate frequency estimate over a (short) sample window:
    each full cycle of a sinusoid produces 2 zero crossings."""
    crossings = _zero_crossings(samples)
    window_s = len(samples) / sample_rate
    return crossings / (2 * window_s)


def test_make_chirp_wav_instantaneous_frequency_matches_linear_sweep_formula():
    """S6: pin the actual linear-chirp phase integral, not just direction.

    sin(2*pi*f(t)*t) (a common but WRONG shortcut -- it is not the
    integral of the instantaneous frequency) also sweeps high-to-low and
    would pass test_make_chirp_wav_descends_in_frequency above. This test
    checks the measured instantaneous frequency (via zero-crossing rate)
    against the value the correct linear sweep predicts at the midpoint of
    the first and last decile windows -- computed from the formula here,
    not hardcoded.
    """
    f_start, f_end, duration_ms, sample_rate = 900, 250, 700, 22050
    duration_s = duration_ms / 1000.0
    data = sounds.make_chirp_wav(f_start, f_end, duration_ms, sample_rate=sample_rate)
    _, _, framerate, samples = _read_wav(data)
    assert framerate == sample_rate

    n = len(samples)
    decile = max(1, n // 10)

    def expected_freq_at_sample(sample_index):
        t = sample_index / sample_rate
        return f_start + (f_end - f_start) * t / duration_s

    first_window = samples[:decile]
    last_window = samples[-decile:]
    expected_first = expected_freq_at_sample(decile / 2)
    expected_last = expected_freq_at_sample(n - decile / 2)

    measured_first = _estimate_instantaneous_freq_hz(first_window, sample_rate)
    measured_last = _estimate_instantaneous_freq_hz(last_window, sample_rate)

    assert measured_first == pytest.approx(expected_first, rel=0.10)
    assert measured_last == pytest.approx(expected_last, rel=0.10)


# ─── beep_timeout: playback wiring (winsound mocked, nothing is actually played) ───
#
# _FakeWinsoundOk is "faithful" to CPython's real winsound.PlaySound, which
# raises RuntimeError("Cannot play asynchronously from memory") when BOTH
# SND_MEMORY and SND_ASYNC are passed together (S1/S2 regression: the old,
# non-faithful fake accepted that combination silently, so the suite never
# caught that beep_timeout() never actually played in production -- it
# always fell through to the Beep fallback).

class _FakeWinsoundOk:
    SND_MEMORY = 4
    SND_ASYNC = 1

    def __init__(self):
        self.play_calls = []
        self.beep_calls = []

    def PlaySound(self, data, flags):
        if (flags & self.SND_MEMORY) and (flags & self.SND_ASYNC):
            raise RuntimeError("Cannot play asynchronously from memory")
        self.play_calls.append((data, flags))

    def Beep(self, freq, duration_ms):
        self.beep_calls.append((freq, duration_ms))


class _FakeWinsoundPlaySoundFails(_FakeWinsoundOk):
    def PlaySound(self, data, flags):
        raise OSError("no audio device")


def test_play_wav_bytes_uses_playsound_memory_sync_not_async(monkeypatch):
    """SND_ASYNC together with SND_MEMORY is rejected by real CPython
    (RuntimeError) -- _play_wav_bytes must pass SND_MEMORY alone."""
    fake = _FakeWinsoundOk()
    monkeypatch.setitem(sys.modules, "winsound", fake)

    data = sounds.make_chirp_wav(900, 250, 700)
    sounds._play_wav_bytes(data)

    assert len(fake.play_calls) == 1
    played_data, flags = fake.play_calls[0]
    assert played_data == data
    assert flags == fake.SND_MEMORY
    assert not (flags & fake.SND_ASYNC)
    assert fake.beep_calls == []  # real playback succeeded -- no fallback needed


def test_play_wav_bytes_takes_playsound_path_with_faithful_fake_no_fallback(monkeypatch):
    """With a fake that behaves like real winsound (rejects MEMORY|ASYNC),
    the fixed _play_wav_bytes must still reach PlaySound and must NOT fall
    back to Beep -- pins S1 end-to-end against the faithful fake."""
    fake = _FakeWinsoundOk()
    monkeypatch.setitem(sys.modules, "winsound", fake)

    sounds._play_wav_bytes(sounds.make_chirp_wav(900, 250, 700))

    assert len(fake.play_calls) == 1
    assert fake.beep_calls == []


def test_play_wav_bytes_falls_back_to_descending_beeps_on_playsound_failure(monkeypatch):
    fake = _FakeWinsoundPlaySoundFails()
    monkeypatch.setitem(sys.modules, "winsound", fake)

    data = sounds.make_chirp_wav(900, 250, 700)
    sounds._play_wav_bytes(data)

    assert len(fake.beep_calls) >= 2
    freqs = [f for f, _d in fake.beep_calls]
    assert freqs == sorted(freqs, reverse=True)  # descending, like the glissando


def test_play_wav_bytes_logs_a_warning_on_playsound_failure_not_a_silent_pass(monkeypatch, caplog):
    fake = _FakeWinsoundPlaySoundFails()
    monkeypatch.setitem(sys.modules, "winsound", fake)

    with caplog.at_level("WARNING", logger="src.voice_bridge.sounds"):
        sounds._play_wav_bytes(sounds.make_chirp_wav(900, 250, 700))

    assert any("PlaySound" in r.message for r in caplog.records)
    assert any("no audio device" in r.message for r in caplog.records)  # the actual exception, not swallowed


def test_play_wav_bytes_never_raises_even_if_winsound_totally_unavailable(monkeypatch):
    class _TotallyBroken:
        SND_MEMORY = 4
        SND_ASYNC = 1

        def PlaySound(self, *a, **k):
            raise OSError("boom")

        def Beep(self, *a, **k):
            raise OSError("boom too")

    monkeypatch.setitem(sys.modules, "winsound", _TotallyBroken())
    sounds._play_wav_bytes(sounds.make_chirp_wav(900, 250, 700))  # must not raise


def test_beep_timeout_spawns_playback_of_a_generated_chirp(monkeypatch):
    """beep_timeout() must fire-and-forget like beep_start/beep_stop/beep_output
    (background thread), and hand _play_wav_bytes real chirp bytes."""
    captured = {}

    class _ImmediateThread:
        """Runs the target synchronously so the test is deterministic --
        mirrors the daemon-thread fire-and-forget contract without
        depending on real thread scheduling."""

        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(sounds.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(sounds, "_play_wav_bytes", lambda data: captured.setdefault("data", data))

    sounds.beep_timeout()

    assert isinstance(captured.get("data"), bytes)
    assert len(captured["data"]) > 44  # more than just a bare WAV header


def test_beep_timeout_default_sweep_is_descending_and_longer_than_beep_stop():
    """Defaults per spec: ~900Hz -> ~250Hz over ~700ms, i.e. longer than the
    150ms beep_stop default."""
    import inspect
    sig = inspect.signature(sounds.beep_timeout)
    assert sig.parameters["f_start"].default > sig.parameters["f_end"].default
    assert sig.parameters["duration_ms"].default > 150
