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


# ──────────────────────────────────────────────────
# OS integration
# ──────────────────────────────────────────────────

import ctypes
import threading
import time

from pynput import keyboard, mouse

from . import _win32_tray


_VK_CAPITAL = 0x14


def _get_caps_state() -> bool:
    """Return True if Caps Lock is currently ON (toggle state, not held)."""
    try:
        return bool(ctypes.windll.user32.GetKeyState(_VK_CAPITAL) & 0x0001)
    except Exception:
        return False


class GestureListener:
    """Coordinates global mouse and keyboard hooks for REC toggle gestures.

    Threading model:
    - pynput Listeners run on their own daemon threads.
    - Each press arms a threading.Timer (long-press threshold).
    - State machine callbacks (on_toggle / on_compensate) may run on
      either listener thread or timer thread; they must be thread-safe.
    """

    def __init__(self, on_toggle_rec: Callable[[], None],
                 mouse_long_press_ms: int = 480,
                 caps_long_press_ms: int = 660,
                 double_click_ms: int = 350):
        self._on_toggle = on_toggle_rec
        self._mouse_state = MouseGestureState(
            long_press_ms=mouse_long_press_ms,
            double_click_ms=double_click_ms,
            on_toggle=self._safe_toggle,
        )
        self._caps_state = CapsLockGestureState(
            long_press_ms=caps_long_press_ms,
            on_toggle=self._safe_toggle,
            on_compensate=self._compensate_caps,
        )
        self._mouse_listener: mouse.Listener | None = None
        self._kbd_listener: keyboard.Listener | None = None
        self._mouse_timer: threading.Timer | None = None
        self._caps_timer: threading.Timer | None = None
        self._kbd_controller = keyboard.Controller()
        self._tray_warning_logged = False

    # ─── public lifecycle ───

    def start(self) -> None:
        self._mouse_listener = mouse.Listener(
            on_click=self._on_mouse_click,
        )
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

        self._kbd_listener = keyboard.Listener(
            on_press=self._on_kbd_press,
            on_release=self._on_kbd_release,
        )
        self._kbd_listener.daemon = True
        self._kbd_listener.start()

        logger.info(
            f"GestureListener started "
            f"(mouse_long={self._mouse_state._long_ms}ms, "
            f"caps_long={self._caps_state._long_ms}ms, "
            f"double_click={self._mouse_state._double_ms}ms)"
        )

    def stop(self) -> None:
        if self._mouse_timer:
            self._mouse_timer.cancel()
        if self._caps_timer:
            self._caps_timer.cancel()
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._kbd_listener:
            self._kbd_listener.stop()
            self._kbd_listener = None
        logger.info("GestureListener stopped")

    # ─── safe callback wrappers ───

    def _safe_toggle(self) -> None:
        try:
            self._on_toggle()
        except Exception as e:
            logger.error(f"Toggle callback raised: {e}")

    def _compensate_caps(self) -> None:
        try:
            self._kbd_controller.tap(keyboard.Key.caps_lock)
            logger.debug("Caps Lock state compensated")
        except Exception as e:
            logger.warning(f"Caps Lock compensation failed: {e}")

    # ─── mouse callback ───

    def _on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        if button != mouse.Button.left:
            return
        if pressed:
            if not _win32_tray.is_inside_tray_icon(x, y):
                if not self._tray_warning_logged and \
                        _win32_tray._get_cached_rect(_win32_tray.TOOLTIP_PREFIX) is None:
                    logger.warning(
                        "Tray icon not found in notification area — "
                        "mouse long-press / double-click disabled. "
                        "Tip: pin the parlaconclaudio icon out of the overflow popup."
                    )
                    self._tray_warning_logged = True
                return
            now = time.monotonic()
            self._mouse_state.on_press(now)
            # Arm long-press timer
            if self._mouse_timer:
                self._mouse_timer.cancel()
            delay_s = self._mouse_state._long_ms / 1000
            self._mouse_timer = threading.Timer(
                delay_s,
                lambda: self._mouse_state.on_timer_fire(time.monotonic()),
            )
            self._mouse_timer.daemon = True
            self._mouse_timer.start()
        else:
            now = time.monotonic()
            self._mouse_state.on_release(now)
            if self._mouse_timer:
                self._mouse_timer.cancel()
                self._mouse_timer = None

    # ─── keyboard callbacks ───

    def _on_kbd_press(self, key) -> None:
        if key != keyboard.Key.caps_lock:
            return
        now = time.monotonic()
        self._caps_state.on_press(now, caps_state_before=_get_caps_state())
        if self._caps_timer:
            self._caps_timer.cancel()
        delay_s = self._caps_state._long_ms / 1000
        self._caps_timer = threading.Timer(
            delay_s,
            lambda: self._caps_state.on_timer_fire(
                time.monotonic(), caps_state_now=_get_caps_state()
            ),
        )
        self._caps_timer.daemon = True
        self._caps_timer.start()

    def _on_kbd_release(self, key) -> None:
        if key != keyboard.Key.caps_lock:
            return
        now = time.monotonic()
        self._caps_state.on_release(now)
        if self._caps_timer:
            self._caps_timer.cancel()
            self._caps_timer = None
