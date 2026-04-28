"""Tests for gesture state machine logic (no actual OS hooks).

We test the state machine in isolation by injecting press/release
events with controlled timestamps.
"""
import time
import pytest
from src.voice_bridge.gesture_listener import (
    MouseGestureState,
    CapsLockGestureState,
)


# ─── Mouse gesture state machine ───

def test_mouse_long_press_fires_after_threshold():
    fired = []
    state = MouseGestureState(
        long_press_ms=480,
        double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
    )
    state.on_press(t=0.0)
    state.on_timer_fire(t=0.480)
    assert fired == ["toggle"]


def test_mouse_release_before_long_press_no_fire():
    fired = []
    state = MouseGestureState(
        long_press_ms=480, double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
    )
    state.on_press(t=0.0)
    state.on_release(t=0.200)  # short release, no double yet
    assert fired == []


def test_mouse_double_click_within_window_fires():
    fired = []
    state = MouseGestureState(
        long_press_ms=480, double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
    )
    # First click (short)
    state.on_press(t=0.0)
    state.on_release(t=0.100)
    # Second click within 350ms
    state.on_press(t=0.300)
    state.on_release(t=0.400)
    assert fired == ["toggle"]


def test_mouse_double_click_outside_window_no_fire():
    fired = []
    state = MouseGestureState(
        long_press_ms=480, double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
    )
    state.on_press(t=0.0)
    state.on_release(t=0.100)
    state.on_press(t=0.500)  # > 350ms after first release
    state.on_release(t=0.600)
    assert fired == []


def test_mouse_long_press_does_not_count_as_first_of_double():
    fired = []
    state = MouseGestureState(
        long_press_ms=480, double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
    )
    state.on_press(t=0.0)
    state.on_timer_fire(t=0.480)  # long-press fires
    state.on_release(t=0.500)
    state.on_press(t=0.600)  # quick second click
    state.on_release(t=0.650)
    # Only the long-press fired; the next click is just one click, not double
    assert fired == ["toggle"]


# ─── CapsLock gesture state machine ───

def test_caps_long_press_fires_after_threshold():
    fired = []
    compensated = []
    state = CapsLockGestureState(
        long_press_ms=660,
        on_toggle=lambda: fired.append("toggle"),
        on_compensate=lambda: compensated.append("comp"),
    )
    state.on_press(t=0.0, caps_state_before=False)
    state.on_timer_fire(t=0.660, caps_state_now=True)
    assert fired == ["toggle"]
    assert compensated == ["comp"]


def test_caps_short_press_no_fire_no_compensate():
    fired = []
    compensated = []
    state = CapsLockGestureState(
        long_press_ms=660,
        on_toggle=lambda: fired.append("toggle"),
        on_compensate=lambda: compensated.append("comp"),
    )
    state.on_press(t=0.0, caps_state_before=False)
    state.on_release(t=0.300)
    assert fired == []
    assert compensated == []


def test_caps_compensate_skipped_when_state_unchanged():
    """Edge: if for some reason Caps state didn't toggle, don't double-tap."""
    fired = []
    compensated = []
    state = CapsLockGestureState(
        long_press_ms=660,
        on_toggle=lambda: fired.append("toggle"),
        on_compensate=lambda: compensated.append("comp"),
    )
    state.on_press(t=0.0, caps_state_before=True)
    state.on_timer_fire(t=0.660, caps_state_now=True)  # same state
    assert fired == ["toggle"]
    assert compensated == []


def test_caps_release_after_long_press_is_noop():
    fired = []
    compensated = []
    state = CapsLockGestureState(
        long_press_ms=660,
        on_toggle=lambda: fired.append("toggle"),
        on_compensate=lambda: compensated.append("comp"),
    )
    state.on_press(t=0.0, caps_state_before=False)
    state.on_timer_fire(t=0.660, caps_state_now=True)
    state.on_release(t=1.000)
    # No additional fires
    assert fired == ["toggle"]


def test_gesture_listener_imports_and_constructs():
    """Smoke test: GestureListener can be constructed without starting hooks."""
    from src.voice_bridge.gesture_listener import GestureListener
    fired = []
    gl = GestureListener(
        on_toggle_rec=lambda: fired.append("t"),
        mouse_long_press_ms=100,
        caps_long_press_ms=200,
        double_click_ms=50,
    )
    assert gl is not None
    # Don't call start() — would install global OS hooks during test run.
