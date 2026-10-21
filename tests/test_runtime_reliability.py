"""Regression coverage for session ownership, bounded latency and engine safety."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from src.core.stt_engine.base import TranscriptionResult
from src.voice_bridge.audio_recorder import AudioRecorder
from src.voice_bridge.transcriber import Transcriber


@pytest.fixture
def bridge(monkeypatch):
    from src.voice_bridge import bridge as module
    monkeypatch.setattr(module.VoiceBridge, "_load_shared_config", lambda self: None)
    monkeypatch.setattr(module, "TrayIcon", Mock())
    instance = module.VoiceBridge(module.VoiceBridgeConfig())
    instance.config.muted = True
    return instance


def test_previous_watchdog_cannot_release_new_session(bridge, monkeypatch):
    from src.voice_bridge import bridge as module
    old_session = bridge._recording_cancel
    bridge._state = module.BridgeState.RECORDING
    bridge.config.recording_timeout = 2
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    def next_session(seconds):
        now[0] += seconds
        old_session.set()
        bridge._recording_cancel = threading.Event()

    monkeypatch.setattr(module.time, "sleep", next_session)
    bridge._on_hotkey_release = Mock()
    bridge._recording_watchdog(old_session)
    bridge._on_hotkey_release.assert_not_called()


def test_timeout_release_validates_session_inside_transition(bridge):
    from src.voice_bridge.bridge import BridgeState
    bridge._state = BridgeState.RECORDING
    bridge._on_hotkey_release(timed_out=True, session=threading.Event())
    assert bridge.state == BridgeState.RECORDING


def test_microphone_failure_recovers_bridge_for_retry(bridge):
    from src.voice_bridge.bridge import BridgeState
    bridge._recorder.start = Mock(side_effect=OSError("disconnected"))
    bridge._hotkey.reset_toggle = Mock()
    bridge._on_hotkey_press()
    assert bridge.state == BridgeState.IDLE
    assert bridge._recording_cancel.is_set()
    bridge._hotkey.reset_toggle.assert_called_once()
    bridge._tray._notify.assert_called_once()


def test_microphone_change_is_rejected_without_touching_device(bridge):
    from src.voice_bridge.bridge import BridgeState
    bridge._state = BridgeState.RECORDING
    bridge._recorder.set_device = Mock()
    assert bridge._on_mic_changed(7) is False
    bridge._recorder.set_device.assert_not_called()


def test_delivery_failure_does_not_sound_success(bridge, monkeypatch):
    from src.voice_bridge import bridge as module
    bridge.config.muted = False
    bridge.config.sound_on_output = True
    bridge._transcriber.transcribe = Mock(return_value="hello")
    bridge._apply_cleanup = lambda text: text
    bridge._output.deliver = Mock(return_value=False)
    success = Mock()
    monkeypatch.setattr(module, "beep_output", success)
    bridge._transcribe_and_output(np.ones(10))
    success.assert_not_called()
    bridge._tray._notify.assert_called_once()


def test_recorder_caps_actual_bytes_and_resets_counter():
    recorder = AudioRecorder(sample_rate=4, max_seconds=1)
    callbacks = []
    recorder._driver = SimpleNamespace(start=lambda callback: callbacks.append(callback), stop=lambda: None)
    recorder.start()
    callbacks[0](b"\x00" * 100)
    assert recorder._buffer_bytes == 8
    assert sum(map(len, recorder._chunks)) == 8
    assert not recorder._recording
    assert len(recorder.stop()) == 4
    assert recorder._buffer_bytes == 0


def test_recorder_start_failure_discards_driver_and_partial_audio():
    recorder = AudioRecorder()

    def fail(callback):
        callback(b"\x00\x00")
        raise OSError("device unavailable")

    recorder._driver = SimpleNamespace(start=fail, stop=lambda: None)
    with pytest.raises(OSError):
        recorder.start()
    assert recorder._driver is None
    assert not recorder._recording
    assert recorder._chunks == []
    assert recorder._buffer_bytes == 0


def make_transcriber(monkeypatch, error=None, text=""):
    transcriber = Transcriber()
    monkeypatch.setattr(transcriber, "_load_config", lambda: {"stt_auto_fallback": True})
    transcriber._engine = SimpleNamespace(
        name="groq", is_local=False, cleanup=Mock(),
        transcribe=Mock(return_value=TranscriptionResult(text, [], "en", error=error)),
    )
    return transcriber


def test_runtime_fallback_on_provider_error_and_cleanup(monkeypatch):
    from src.core.stt_engine.key_manager import KeyManager
    transcriber = make_transcriber(monkeypatch, error="network_error")
    monkeypatch.setattr(KeyManager, "get_key", lambda name: "test-key" if name == "deepgram" else None)
    fallback = SimpleNamespace(name="deepgram", is_local=False, cleanup=Mock(),
                               transcribe=Mock(return_value=TranscriptionResult(" recovered ", [], "en")))
    monkeypatch.setattr(transcriber, "_make_deepgram_engine", lambda key: fallback)
    assert transcriber.transcribe(np.ones(10)) == "recovered"
    assert transcriber.last_error is None
    fallback.cleanup.assert_called_once()


def test_runtime_fallback_respects_disabled_flag(monkeypatch):
    from src.core.stt_engine.key_manager import KeyManager
    transcriber = make_transcriber(monkeypatch, error="network_error")
    monkeypatch.setattr(transcriber, "_get_auto_fallback", lambda: False)
    lookup = Mock()
    monkeypatch.setattr(KeyManager, "get_key", lookup)
    assert transcriber.transcribe(np.ones(10)) == ""
    assert transcriber.last_error == "network_error"
    lookup.assert_not_called()


def test_valid_silence_never_uses_fallback(monkeypatch):
    transcriber = make_transcriber(monkeypatch)
    fallback = Mock()
    monkeypatch.setattr(transcriber, "_runtime_fallback", fallback)
    assert transcriber.transcribe(np.ones(10)) == ""
    fallback.assert_not_called()


def test_engine_cleanup_waits_for_inference(monkeypatch):
    transcriber = make_transcriber(monkeypatch)
    entered, finish, cleaned = threading.Event(), threading.Event(), threading.Event()
    engine = transcriber._engine

    def infer(*args, **kwargs):
        entered.set()
        assert finish.wait(2)
        return TranscriptionResult("ok", [], "en")

    engine.transcribe = infer
    engine.cleanup = cleaned.set
    infer_thread = threading.Thread(target=transcriber.transcribe, args=(np.ones(10),))
    cleanup_thread = threading.Thread(target=transcriber.cleanup)
    infer_thread.start()
    assert entered.wait(2)
    assert transcriber.busy
    cleanup_thread.start()
    assert not cleaned.wait(0.02)
    finish.set()
    infer_thread.join(2)
    cleanup_thread.join(2)
    assert cleaned.is_set()
    assert transcriber._engine is None
    assert not transcriber.busy


def test_unload_vram_releases_without_reload(monkeypatch):
    transcriber = make_transcriber(monkeypatch)
    engine = transcriber._engine
    engine.is_local = True
    transcriber.unload_vram()
    engine.cleanup.assert_called_once()
    assert transcriber.active_engine_name() == "not_loaded"


def test_live_cleanup_deadline_and_single_outstanding_worker(monkeypatch):
    from src.voice_bridge import stt_cleanup
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(2)
        return "cleaned"

    monkeypatch.setattr(stt_cleanup, "_cleanup_text_unbounded", slow)
    args = dict(model="primary", fallback="secondary", api_key="test", timeout_s=0.02)
    try:
        assert stt_cleanup.cleanup_text("raw", **args) is None
        assert started.is_set()
        assert stt_cleanup.cleanup_text("raw", **args) is None
        assert calls == [1]
    finally:
        release.set()
        # Acquire the worker's lock to ensure its daemon finished before patch teardown.
        assert stt_cleanup._live_worker_lock.acquire(timeout=2)
        stt_cleanup._live_worker_lock.release()


def test_retry_cannot_exceed_live_budget(monkeypatch):
    from src.voice_bridge import stt_cleanup
    stt_cleanup.reset_circuit_breaker()
    calls = []

    def failing(url, body, headers, timeout):
        calls.append(timeout)
        raise stt_cleanup._HttpError(429, "60")

    monkeypatch.setattr(stt_cleanup, "_http_post_json", failing)
    assert stt_cleanup.cleanup_text("raw", model="one", fallback="two", api_key="test", timeout_s=0.1) is None
    assert calls and all(0 < timeout <= 0.1 for timeout in calls)
    stt_cleanup.reset_circuit_breaker()
