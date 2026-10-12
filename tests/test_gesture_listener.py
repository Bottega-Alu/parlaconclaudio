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


# ─── Regression: click+drag must never be recognized as a gesture ───
# Bug 2026-10-12: a normal click+drag (text selection, window move) made
# the bridge beep. A drag lasts > long_press_ms, so the long-press timer
# fired and toggled REC. Movement beyond the drag threshold while the
# button is held now cancels the gesture.

def _mouse_state(fired):
    return MouseGestureState(
        long_press_ms=480, double_click_ms=350,
        on_toggle=lambda: fired.append("toggle"),
        drag_threshold_px=10,
    )


def test_mouse_drag_then_hold_does_not_fire_long_press():
    fired = []
    state = _mouse_state(fired)
    state.on_press(t=0.0, x=100, y=100)
    state.on_move(x=180, y=140)      # drag well past threshold
    state.on_timer_fire(t=0.480)     # drag still held at threshold
    state.on_release(t=0.900)
    assert fired == []


def test_mouse_drag_release_does_not_pair_into_double_click():
    fired = []
    state = _mouse_state(fired)
    # Quick drag (short in time), then a click right after
    state.on_press(t=0.0, x=100, y=100)
    state.on_move(x=200, y=100)
    state.on_release(t=0.100)
    state.on_press(t=0.200, x=200, y=100)
    state.on_release(t=0.250)
    assert fired == []


def test_mouse_jitter_below_threshold_still_long_press():
    fired = []
    state = _mouse_state(fired)
    state.on_press(t=0.0, x=100, y=100)
    state.on_move(x=103, y=102)      # hand jitter, not a drag
    state.on_timer_fire(t=0.480)
    assert fired == ["toggle"]


def test_mouse_move_without_press_is_ignored():
    fired = []
    state = _mouse_state(fired)
    state.on_move(x=500, y=500)      # plain hover, no button held
    state.on_press(t=1.0, x=500, y=500)
    state.on_timer_fire(t=1.480)
    assert fired == ["toggle"]


def test_mouse_new_press_after_drag_resets_drag_flag():
    fired = []
    state = _mouse_state(fired)
    state.on_press(t=0.0, x=100, y=100)
    state.on_move(x=300, y=300)
    state.on_release(t=0.600)
    state.on_press(t=2.0, x=300, y=300)  # fresh press, no movement
    state.on_timer_fire(t=2.480)
    assert fired == ["toggle"]


# ─── Regression: tray rect plausibility (root cause of the false "on tray") ───
# In a DPI-unaware process on a 300% display, ClientToScreen mixed logical
# and physical coordinates and the "tray icon" rect came back as
# (1061, 840, 3400, 2883): ~2339x2043 px, i.e. most of the screen.

def test_tray_rect_huge_is_rejected():
    from src.voice_bridge import _win32_tray
    assert _win32_tray._is_plausible_icon_rect((1061, 840, 3400, 2883)) is False


def test_tray_rect_icon_sized_is_accepted():
    from src.voice_bridge import _win32_tray
    assert _win32_tray._is_plausible_icon_rect((3184, 2278, 3256, 2399)) is True


def test_tray_rect_degenerate_is_rejected():
    from src.voice_bridge import _win32_tray
    assert _win32_tray._is_plausible_icon_rect((10, 10, 10, 40)) is False
