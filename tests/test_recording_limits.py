"""Tests for the single recording-length limit (decision A, 2026-10-11).

Before this fix, AudioRecorder.MAX_RECORDING_SECONDS (120s buffer cap) and
VoiceBridgeConfig.recording_timeout (180s watchdog) could diverge: audio
recorded between 120s and 180s was silently discarded (see
"Recording capped at 120s" / "Recording timeout (180s)" in last night's log,
21:38:00 / 21:39:01). Now there is exactly one source of truth —
config.DEFAULT_RECORDING_TIMEOUT_SECONDS — and the two can never disagree.
"""
import logging

import pytest

from src.voice_bridge.audio_recorder import AudioRecorder
from src.voice_bridge.config import DEFAULT_RECORDING_TIMEOUT_SECONDS, VoiceBridgeConfig


# ─── (1) AudioRecorder cap tracks config, single source of truth ───

def test_audio_recorder_default_cap_matches_config_default():
    cfg = VoiceBridgeConfig()
    rec = AudioRecorder()
    assert rec.max_seconds == cfg.recording_timeout == DEFAULT_RECORDING_TIMEOUT_SECONDS


def test_audio_recorder_cap_follows_explicit_value():
    rec = AudioRecorder(max_seconds=45)
    assert rec.max_seconds == 45


def test_config_recording_timeout_is_single_value_120():
    """A -- recording_timeout is now the ONE limit (120s), not 180s."""
    cfg = VoiceBridgeConfig()
    assert cfg.recording_timeout == 120
    assert DEFAULT_RECORDING_TIMEOUT_SECONDS == 120


def test_audio_recorder_cap_warning_uses_configured_seconds(monkeypatch, caplog):
    """The buffer-cap warning must reference whatever max_seconds was wired in,
    not a hardcoded module constant that could drift from it again."""
    rec = AudioRecorder(sample_rate=16000, channels=1, max_seconds=1)

    class _FakeDriver:
        def __init__(self):
            self._cb = None

        def start(self, callback):
            self._cb = callback

        def stop(self):
            pass

    fake_driver = _FakeDriver()
    monkeypatch.setattr(rec, "_ensure_driver", lambda: None)
    rec._driver = fake_driver

    caplog.set_level(logging.WARNING)
    rec.start()

    # Feed exactly max_bytes worth of silence in one chunk -> should cap at 1s.
    max_bytes = 16000 * 1 * 2 * 1  # sample_rate * channels * 2 bytes * max_seconds
    fake_driver._cb(b"\x00" * max_bytes)

    assert any("capped at 1s" in rec_.message for rec_ in caplog.records)
    assert rec._recording is False


# ─── bridge wiring: AudioRecorder gets config.recording_timeout, not a divergent default ───
#
# The voicebridge.log truncation guard lives in tests/conftest.py as an
# autouse fixture (_never_truncate_live_log) -- it applies to every test in
# the suite, not just the ones below, so no future test can truncate the
# live log just by being the first to import bridge.py.


@pytest.fixture
def bridge_module(monkeypatch, _never_truncate_live_log):
    """Import src.voice_bridge.bridge with _load_shared_config() neutralized.

    Without this, VoiceBridge.__init__ reads Frisco's real
    ~/.claude/cache/tts/tts_config.json (volume, muted, mic_device_id, ...)
    and overwrites the VoiceBridgeConfig the test just built -- e.g. a
    real {"muted": true} would silently make every beep-gate assertion
    below fail, for a reason that has nothing to do with the code under
    test. Tests must be isolated from that live, externally-mutable file.
    """
    import src.voice_bridge.bridge as bridge_module
    monkeypatch.setattr(bridge_module.VoiceBridge, "_load_shared_config", lambda self: None)
    return bridge_module


def _make_bridge(bridge_module):
    """A VoiceBridge with default config; construction is side-effect-light
    (no .start() called -> no hotkey/tray/gesture OS hooks registered)."""
    return bridge_module.VoiceBridge(bridge_module.VoiceBridgeConfig())


def test_bridge_wires_recorder_cap_to_config_recording_timeout(bridge_module):
    cfg = bridge_module.VoiceBridgeConfig(recording_timeout=37)
    bridge = bridge_module.VoiceBridge(cfg)
    assert bridge._recorder.max_seconds == 37


def test_bridge_recorder_cap_cannot_diverge_from_timeout(bridge_module):
    """Regression guard for the exact bug reported: cap and timeout must
    always be the same number, whatever the configured value is."""
    for value in (60, 120, 200):
        cfg = bridge_module.VoiceBridgeConfig(recording_timeout=value)
        bridge = bridge_module.VoiceBridge(cfg)
        assert bridge._recorder.max_seconds == bridge.config.recording_timeout == value


# ─── (3) timeout stop path sounds beep_timeout; manual stop path sounds beep_stop ───

def test_watchdog_timeout_plays_beep_timeout_not_beep_stop(bridge_module, monkeypatch):
    bridge = _make_bridge(bridge_module)
    # Explicit, not relying on dataclass defaults: the gate this exercises
    # is sound_on_stop and not muted and volume > 0.
    bridge.config.sound_on_stop = True
    bridge.config.muted = False
    bridge.config.volume = 200

    calls = {"beep_stop": 0, "beep_timeout": 0}
    monkeypatch.setattr(bridge_module, "beep_stop", lambda *a, **k: calls.__setitem__("beep_stop", calls["beep_stop"] + 1))
    monkeypatch.setattr(bridge_module, "beep_timeout", lambda *a, **k: calls.__setitem__("beep_timeout", calls["beep_timeout"] + 1))
    # Real _on_hotkey_release(timed_out=True) is exercised (that's where the
    # gate + sound choice live); only the background transcription is stubbed.
    monkeypatch.setattr(bridge, "_transcribe_and_output", lambda audio: None)
    monkeypatch.setattr(bridge.config, "recording_timeout", 0)

    bridge._state = bridge_module.BridgeState.RECORDING
    bridge._recording_watchdog()

    assert calls["beep_timeout"] == 1
    assert calls["beep_stop"] == 0


def test_manual_release_plays_beep_stop_not_beep_timeout(bridge_module, monkeypatch):
    bridge = _make_bridge(bridge_module)
    bridge.config.sound_on_stop = True
    bridge.config.muted = False
    bridge.config.volume = 200

    calls = {"beep_stop": 0, "beep_timeout": 0}
    monkeypatch.setattr(bridge_module, "beep_stop", lambda *a, **k: calls.__setitem__("beep_stop", calls["beep_stop"] + 1))
    monkeypatch.setattr(bridge_module, "beep_timeout", lambda *a, **k: calls.__setitem__("beep_timeout", calls["beep_timeout"] + 1))
    monkeypatch.setattr(bridge, "_transcribe_and_output", lambda audio: None)

    bridge._state = bridge_module.BridgeState.RECORDING
    bridge._on_hotkey_release()

    assert calls["beep_stop"] == 1
    assert calls["beep_timeout"] == 0


def test_watchdog_respects_mute_gate_for_timeout_beep(bridge_module, monkeypatch):
    """Same sound_on_stop/muted/volume gate as the manual path (bridge.py ~123/160/240) must
    also guard beep_timeout -- muting must not be bypassed on the timeout path."""
    bridge = _make_bridge(bridge_module)

    calls = {"beep_timeout": 0}
    monkeypatch.setattr(bridge_module, "beep_timeout", lambda *a, **k: calls.__setitem__("beep_timeout", calls["beep_timeout"] + 1))
    monkeypatch.setattr(bridge, "_transcribe_and_output", lambda audio: None)
    monkeypatch.setattr(bridge.config, "recording_timeout", 0)
    bridge.config.muted = True

    bridge._state = bridge_module.BridgeState.RECORDING
    bridge._recording_watchdog()

    assert calls["beep_timeout"] == 0


# ─── S3 regression: watchdog must stop at the configured timeout, not at
# ceil(timeout / fixed_poll) * fixed_poll. A fixed 5s poll with recording_
# timeout=37 used to force-stop at 40s -- 3s of audio discarded past the
# AudioRecorder cap, the exact same class of bug decision A eliminated.
# Virtual clock: no real sleeping, no flakiness, no 37s test runtime. ───

def test_watchdog_stops_at_configured_timeout_not_rounded_up_to_poll_multiple(bridge_module, monkeypatch):
    bridge = _make_bridge(bridge_module)
    timeout = 37  # deliberately not a multiple of the old fixed 5s poll
    monkeypatch.setattr(bridge.config, "recording_timeout", timeout)
    monkeypatch.setattr(bridge, "_transcribe_and_output", lambda audio: None)
    monkeypatch.setattr(bridge_module, "beep_timeout", lambda *a, **k: None)

    clock = {"now": 0.0}
    sleep_calls = []

    def fake_monotonic():
        return clock["now"]

    def fake_sleep(seconds):
        sleep_calls.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(bridge_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(bridge_module.time, "sleep", fake_sleep)

    bridge._state = bridge_module.BridgeState.RECORDING
    bridge._recording_watchdog()

    # Force-stop must have happened (state left RECORDING).
    assert bridge._state == bridge_module.BridgeState.TRANSCRIBING
    # Must land within one poll interval (<=1.0s per the fix) of the real
    # timeout -- NOT rounded up to the next multiple of a fixed 5s poll
    # (that would land at 40.0, which the assertion below rejects).
    assert timeout <= clock["now"] < timeout + 1.0
    assert all(s <= 1.0 for s in sleep_calls)
