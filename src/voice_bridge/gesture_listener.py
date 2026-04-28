"""Global gesture detection: mouse on tray + CapsLock long-press.

This module exposes:
- MouseGestureState / CapsLockGestureState — pure state machines
  (testable in isolation, no OS dependency)
- GestureListener — wraps the state machines with pynput global
  hooks, threading.Timer for thresholds, and a Win32 helper to
  identify clicks on the tray icon. Added in Task A5.
"""
from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


class MouseGestureState:
    """State machine for mouse gestures on the tray icon.

    Recognizes:
    - Long-press: press held >= long_press_ms
    - Double-click: two quick presses, second starts within
      double_click_ms of the first release

    Both gestures invoke on_toggle() exactly once per recognition.
    """

    def __init__(self, long_press_ms: int, double_click_ms: int,
                 on_toggle: Callable[[], None]):
        self._long_ms = long_press_ms
        self._double_ms = double_click_ms
        self._on_toggle = on_toggle
        # Press tracking
        self._press_ts: float | None = None
        self._long_press_fired = False
        # Double-click tracking: timestamp of last *short* release
        self._last_short_release_ts: float | None = None

    def on_press(self, t: float) -> None:
        self._press_ts = t
        self._long_press_fired = False

    def on_release(self, t: float) -> None:
        if self._press_ts is None:
            return
        held_s = t - self._press_ts
        self._press_ts = None

        if self._long_press_fired:
            # Long-press already handled; this release closes the gesture.
            self._long_press_fired = False
            self._last_short_release_ts = None  # don't pair with future click
            return

        # Short release — check for double-click pairing
        if held_s * 1000 < self._long_ms:
            now_ms_release = t
            if self._last_short_release_ts is not None:
                gap_ms = (now_ms_release - self._last_short_release_ts) * 1000
                # Note: we measure release-to-press in on_press, but for
                # simplicity we treat release-to-release < double_click_ms
                # as the double-click window. Acceptable approximation.
                if gap_ms <= self._double_ms:
                    self._on_toggle()
                    self._last_short_release_ts = None
                    return
            self._last_short_release_ts = now_ms_release

    def on_timer_fire(self, t: float) -> None:
        """Called by an external Timer at press_ts + long_press_ms.

        If still pressed, fires long-press toggle.
        """
        if self._press_ts is None:
            return
        held_s = t - self._press_ts
        if held_s * 1000 + 1 >= self._long_ms:  # +1 ms tolerance
            self._on_toggle()
            self._long_press_fired = True
            self._last_short_release_ts = None  # don't chain into double


class CapsLockGestureState:
    """State machine for Caps Lock long-press with OS toggle compensation.

    Recognizes long-press of Caps Lock (held >= long_press_ms).
    On fire, invokes on_toggle() AND on_compensate() if the OS Caps
    state has changed since press (to undo the unwanted toggle).
    Short presses are pass-through (Caps Lock toggle works normally).
    """

    def __init__(self, long_press_ms: int,
                 on_toggle: Callable[[], None],
                 on_compensate: Callable[[], None]):
        self._long_ms = long_press_ms
        self._on_toggle = on_toggle
        self._on_compensate = on_compensate
        self._press_ts: float | None = None
        self._caps_before: bool | None = None
        self._long_press_fired = False

    def on_press(self, t: float, caps_state_before: bool) -> None:
        if self._press_ts is not None:
            # Already pressed (key autorepeat) — ignore subsequent down events
            return
        self._press_ts = t
        self._caps_before = caps_state_before
        self._long_press_fired = False

    def on_release(self, t: float) -> None:
        self._press_ts = None
        self._caps_before = None
        # If long-press already fired, the release is a no-op
        # (state already cleared above).
        self._long_press_fired = False

    def on_timer_fire(self, t: float, caps_state_now: bool) -> None:
        """Called by an external Timer at press_ts + long_press_ms.

        If still pressed, fires toggle and compensates Caps state.
        """
        if self._press_ts is None or self._long_press_fired:
            return
        held_s = t - self._press_ts
        if held_s * 1000 + 1 < self._long_ms:
            return
        self._on_toggle()
        if caps_state_now != self._caps_before:
            self._on_compensate()
        self._long_press_fired = True
