# Gesture Triggers + Random Sound Pack — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 3 alternative REC toggle triggers (mouse long-press on tray sphere, double-click sphere, Caps Lock long-press with OS toggle compensation) and a "random sound pack" mode that varies the pack at every event.

**Architecture:** Three independent work streams. Stream A creates a new `gesture_listener.py` module + `_win32_tray.py` Win32 helper using pynput global hooks. Stream B modifies `scripts/notify-tts.py` (random pack resolver) + `tray_icon.py` (menu entry). Stream C extends `VoiceBridgeConfig`. Final integration wires everything in `bridge.py` with a shared lock + 200 ms debounce so all trigger sources converge on a single `_toggle_recording()` callback.

**Tech Stack:** Python 3.11+, `pynput.mouse.Listener` and `pynput.keyboard.Listener` (already in deps), `ctypes` for Win32 API (`Shell_TrayWnd` enumeration + `GetKeyState(VK_CAPITAL)`), `pystray` for tray menu, `pytest` (new dev dep) for unit tests on pure logic.

**Spec:** `docs/superpowers/specs/2026-04-27-gesture-triggers-and-random-pack-design.md`

---

## Stream parallelization

- **Stream A** (Tasks A1–A8) — `_win32_tray.py` + `gesture_listener.py` + tests
- **Stream B** (Tasks B1–B4) — `notify-tts.py` random pack + `tray_icon.py` Random menu entry + tests
- **Stream C** (Tasks C1–C2) — `config.py` gesture defaults
- **Integration** (Tasks I1–I3) — `bridge.py` wiring, smoke test, final commit. Depends on A + B + C.

A, B, C are independent and can be worked on in parallel by separate agents/sessions. Integration runs after all three merge.

---

## Setup Task: Add pytest as dev dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pytest to pyproject**

Insert after the `[tool.poetry.dependencies]` block:

```toml
[tool.poetry.group.dev.dependencies]
pytest = "^8.0.0"
```

- [ ] **Step 2: Install**

Run: `poetry install` (or `pip install pytest>=8.0` inside the existing venv)
Expected: pytest installed, `python -m pytest --version` prints `pytest 8.x.x`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pytest as dev dependency"
```

---

## Stream A — Gesture detection

### Task A1: Win32 tray icon rect helper — failing test

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_win32_tray.py`

- [ ] **Step 1: Create empty tests package**

Create `tests/__init__.py` with empty content.

- [ ] **Step 2: Write failing test for `is_inside_tray_icon` signature**

Create `tests/test_win32_tray.py`:

```python
"""Smoke tests for _win32_tray helper.

Most of this module is Win32-specific and can't be unit-tested without
a real tray icon visible. We test the pure-Python parts: signature,
graceful fallback when icon not found, cache TTL behavior.
"""
import time
from src.voice_bridge import _win32_tray


def test_is_inside_tray_icon_returns_false_when_no_icon(monkeypatch):
    """When the tooltip target is missing, return False instead of raising."""
    # Force the rect lookup to return None (icon not found)
    monkeypatch.setattr(_win32_tray, "_find_tray_icon_rect", lambda tooltip: None)
    # Clear cache so monkeypatch takes effect
    _win32_tray._RECT_CACHE.clear()
    assert _win32_tray.is_inside_tray_icon(0, 0) is False
    assert _win32_tray.is_inside_tray_icon(500, 1000) is False


def test_is_inside_tray_icon_caches_rect(monkeypatch):
    """Rect lookup should be cached for ~2s to avoid hammering Win32."""
    calls = {"count": 0}

    def fake_find(tooltip):
        calls["count"] += 1
        return (100, 200, 120, 220)  # left, top, right, bottom

    monkeypatch.setattr(_win32_tray, "_find_tray_icon_rect", fake_find)
    _win32_tray._RECT_CACHE.clear()

    # First call -> miss, second call -> hit
    _win32_tray.is_inside_tray_icon(110, 210)
    _win32_tray.is_inside_tray_icon(110, 210)
    assert calls["count"] == 1


def test_is_inside_tray_icon_returns_true_when_inside(monkeypatch):
    monkeypatch.setattr(_win32_tray, "_find_tray_icon_rect",
                        lambda tooltip: (100, 200, 120, 220))
    _win32_tray._RECT_CACHE.clear()
    assert _win32_tray.is_inside_tray_icon(110, 210) is True
    assert _win32_tray.is_inside_tray_icon(99, 210) is False
    assert _win32_tray.is_inside_tray_icon(110, 199) is False
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_win32_tray.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.voice_bridge._win32_tray'`

### Task A2: Win32 tray icon rect helper — implementation

**Files:**
- Create: `src/voice_bridge/_win32_tray.py`

- [ ] **Step 1: Implement the helper**

Create `src/voice_bridge/_win32_tray.py`:

```python
"""Win32 helper to locate the parlaconclaudio tray icon rect.

Used by gesture_listener.MouseGestureDetector to decide whether a
left-click happened on our tray icon (vs anywhere else on screen).

Strategy: enumerate Shell_TrayWnd > TrayNotifyWnd > SysPager >
ToolbarWindow32, read each button's tooltip via TB_GETBUTTONTEXTW,
match against tooltip prefix, return TB_GETITEMRECT.

Fallback: if the icon is in the Windows overflow popup (hidden),
the lookup returns None and is_inside_tray_icon() returns False.
The caller logs a warning once and disables mouse long-press.

All Win32 calls are wrapped in try/except — failures degrade
gracefully (return None / False).
"""
import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

TOOLTIP_PREFIX = "parlaconclaudio"
_CACHE_TTL_S = 2.0
_RECT_CACHE: dict = {}  # tooltip_prefix -> (rect_or_None, expires_at)

# Win32 constants
_TB_BUTTONCOUNT = 0x418
_TB_GETBUTTON = 0x417
_TB_GETBUTTONTEXTW = 0x44B
_TB_GETITEMRECT = 0x41D
_PROCESS_VM_READ = 0x10
_PROCESS_QUERY_INFORMATION = 0x400


def is_inside_tray_icon(x: int, y: int) -> bool:
    """Return True if (x, y) is inside our tray icon rect."""
    rect = _get_cached_rect(TOOLTIP_PREFIX)
    if rect is None:
        return False
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def _get_cached_rect(tooltip_prefix: str):
    now = time.monotonic()
    cached = _RECT_CACHE.get(tooltip_prefix)
    if cached is not None and cached[1] > now:
        return cached[0]
    rect = _find_tray_icon_rect(tooltip_prefix)
    _RECT_CACHE[tooltip_prefix] = (rect, now + _CACHE_TTL_S)
    return rect


def _find_tray_icon_rect(tooltip_prefix: str):
    """Enumerate the system tray and return rect of icon matching tooltip prefix.

    Returns (left, top, right, bottom) in screen coordinates, or None.
    """
    try:
        user32 = ctypes.windll.user32
        find_window_ex = user32.FindWindowExW
        find_window_ex.argtypes = [wintypes.HWND, wintypes.HWND,
                                    wintypes.LPCWSTR, wintypes.LPCWSTR]
        find_window_ex.restype = wintypes.HWND

        tray = find_window_ex(None, None, "Shell_TrayWnd", None)
        if not tray:
            return None
        notify = find_window_ex(tray, None, "TrayNotifyWnd", None)
        if not notify:
            return None
        pager = find_window_ex(notify, None, "SysPager", None)
        toolbar = None
        if pager:
            toolbar = find_window_ex(pager, None, "ToolbarWindow32", None)
        # Fallback: some Win11 builds have the toolbar directly under TrayNotifyWnd
        if not toolbar:
            toolbar = find_window_ex(notify, None, "ToolbarWindow32", None)
        if not toolbar:
            return None

        return _scan_toolbar_for_tooltip(toolbar, tooltip_prefix)
    except Exception as e:
        logger.debug(f"Win32 tray rect lookup failed: {e}")
        return None


def _scan_toolbar_for_tooltip(toolbar_hwnd: int, prefix: str):
    """Iterate buttons in toolbar, match tooltip prefix, return rect."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    send_message = user32.SendMessageW
    send_message.argtypes = [wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM]
    send_message.restype = wintypes.LPARAM

    count = send_message(toolbar_hwnd, _TB_BUTTONCOUNT, 0, 0)
    if count <= 0:
        return None

    # Need to read memory in toolbar's process — use cross-process buffer trick
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(toolbar_hwnd, ctypes.byref(pid))
    h_proc = kernel32.OpenProcess(_PROCESS_VM_READ | _PROCESS_QUERY_INFORMATION,
                                    False, pid)
    if not h_proc:
        return None
    try:
        # Allocate buffer in remote process for tooltip text
        remote_buf = kernel32.VirtualAllocEx(h_proc, 0, 512, 0x1000, 0x40)
        if not remote_buf:
            return None
        try:
            for i in range(count):
                # Get tooltip text into remote buffer
                length = send_message(toolbar_hwnd, _TB_GETBUTTONTEXTW,
                                      i, remote_buf)
                if length <= 0:
                    continue
                local_buf = (ctypes.c_wchar * 256)()
                bytes_read = ctypes.c_size_t(0)
                kernel32.ReadProcessMemory(h_proc, remote_buf, local_buf,
                                           min(length * 2 + 2, 510),
                                           ctypes.byref(bytes_read))
                tooltip = local_buf.value
                if not tooltip.startswith(prefix):
                    continue
                # Found the icon — get its rect
                rect_remote = kernel32.VirtualAllocEx(h_proc, 0, 16, 0x1000, 0x40)
                if not rect_remote:
                    continue
                try:
                    ok = send_message(toolbar_hwnd, _TB_GETITEMRECT, i, rect_remote)
                    if not ok:
                        continue
                    rect_local = wintypes.RECT()
                    kernel32.ReadProcessMemory(h_proc, rect_remote,
                                               ctypes.byref(rect_local), 16,
                                               ctypes.byref(bytes_read))
                    pt_tl = wintypes.POINT(rect_local.left, rect_local.top)
                    pt_br = wintypes.POINT(rect_local.right, rect_local.bottom)
                    user32.ClientToScreen(toolbar_hwnd, ctypes.byref(pt_tl))
                    user32.ClientToScreen(toolbar_hwnd, ctypes.byref(pt_br))
                    return (pt_tl.x, pt_tl.y, pt_br.x, pt_br.y)
                finally:
                    kernel32.VirtualFreeEx(h_proc, rect_remote, 0, 0x8000)
            return None
        finally:
            kernel32.VirtualFreeEx(h_proc, remote_buf, 0, 0x8000)
    finally:
        kernel32.CloseHandle(h_proc)
```

- [ ] **Step 2: Run tests to verify all 3 pass**

Run: `python -m pytest tests/test_win32_tray.py -v`
Expected: 3 passed

- [ ] **Step 3: Commit**

```bash
git add src/voice_bridge/_win32_tray.py tests/__init__.py tests/test_win32_tray.py
git commit -m "feat: add _win32_tray helper for tray icon rect lookup"
```

### Task A3: Mouse gesture detector state machine — failing test

**Files:**
- Create: `tests/test_gesture_listener.py`

- [ ] **Step 1: Write failing test for state machine logic**

Create `tests/test_gesture_listener.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gesture_listener.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.voice_bridge.gesture_listener'`

### Task A4: Gesture state machines — implementation

**Files:**
- Create: `src/voice_bridge/gesture_listener.py` (state machines only, no OS hooks yet)

- [ ] **Step 1: Implement the two state machines**

Create `src/voice_bridge/gesture_listener.py` with the following content (OS hook integration is added in A5; this task delivers the pure logic so tests pass):

```python
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
```

- [ ] **Step 2: Run tests to verify all pass**

Run: `python -m pytest tests/test_gesture_listener.py -v`
Expected: 8 passed

- [ ] **Step 3: Commit**

```bash
git add src/voice_bridge/gesture_listener.py tests/test_gesture_listener.py
git commit -m "feat: add gesture state machines (mouse long-press, double-click, caps long-press)"
```

### Task A5: Wrap state machines with pynput hooks + threading.Timer

**Files:**
- Modify: `src/voice_bridge/gesture_listener.py`

- [ ] **Step 1: Append GestureListener class to gesture_listener.py**

Append to the end of `src/voice_bridge/gesture_listener.py`:

```python


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
```

- [ ] **Step 2: Run unit tests to confirm no regression**

Run: `python -m pytest tests/test_gesture_listener.py tests/test_win32_tray.py -v`
Expected: 11 passed

- [ ] **Step 3: Commit**

```bash
git add src/voice_bridge/gesture_listener.py
git commit -m "feat: add GestureListener with pynput hooks + Win32 tray rect detection"
```

### Task A6: Smoke-import test for the full module

**Files:**
- Modify: `tests/test_gesture_listener.py`

- [ ] **Step 1: Append import smoke test**

Append to `tests/test_gesture_listener.py`:

```python


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
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/test_gesture_listener.py -v`
Expected: 9 passed (8 state machine + 1 smoke)

- [ ] **Step 3: Commit**

```bash
git add tests/test_gesture_listener.py
git commit -m "test: add smoke construction test for GestureListener"
```

---

## Stream B — Random sound pack

### Task B1: Random pack resolver in notify-tts.py — failing test

**Files:**
- Create: `tests/test_random_pack.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_random_pack.py`:

```python
"""Tests for random pack resolver in scripts/notify-tts.py.

We import the script as a module to test the pure resolver function.
"""
import importlib.util
import sys
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


def test_resolve_pack_returns_configured_pack_when_not_random(notify_tts, monkeypatch):
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "r2d2"})
    assert notify_tts.get_sound_pack() == "r2d2"


def test_resolve_pack_picks_random_when_random_configured(notify_tts, monkeypatch, tmp_path):
    # Create fake sounds dir with three pack subdirs
    fake_sounds = tmp_path / "sounds"
    fake_sounds.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (fake_sounds / name).mkdir()
    # Also a non-dir entry (should be ignored) and a _hidden dir (should be ignored)
    (fake_sounds / "readme.txt").write_text("ignore")
    (fake_sounds / "_disabled").mkdir()

    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "random"})
    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", fake_sounds)

    seen = set()
    for _ in range(50):
        seen.add(notify_tts.get_sound_pack())
    assert seen.issubset({"alpha", "beta", "gamma"})
    assert "_disabled" not in seen
    assert "readme.txt" not in seen
    # Statistically very likely to see all 3 in 50 picks
    assert len(seen) >= 2


def test_resolve_pack_random_with_no_packs_falls_back(notify_tts, monkeypatch, tmp_path):
    fake_sounds = tmp_path / "empty_sounds"
    fake_sounds.mkdir()
    monkeypatch.setattr(notify_tts, "load_config", lambda: {"sound_pack": "random"})
    monkeypatch.setattr(notify_tts, "SOUNDS_DIR", fake_sounds)
    assert notify_tts.get_sound_pack() == "r2d2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_random_pack.py -v`
Expected: 2 of 3 fail (the random ones) — current `get_sound_pack` returns the literal string `"random"`.

### Task B2: Random pack resolver — implementation

**Files:**
- Modify: `scripts/notify-tts.py`

- [ ] **Step 1: Replace `get_sound_pack`**

Locate this in `scripts/notify-tts.py`:

```python
def get_sound_pack() -> str:
    return load_config().get("sound_pack", "r2d2")
```

Replace with:

```python
_RANDOM_FALLBACK_LOGGED = False


def get_sound_pack() -> str:
    """Return the configured sound pack name.

    If config value is "random", picks a random installed pack from
    SOUNDS_DIR (excluding non-directory entries and names starting
    with "_"). Falls back to "r2d2" if no packs are installed.

    Each call returns a fresh random pick — since notify-tts.py runs
    as a fresh subprocess per Claude Code event, this naturally
    rotates the pack per event.
    """
    global _RANDOM_FALLBACK_LOGGED
    configured = load_config().get("sound_pack", "r2d2")
    if configured != "random":
        return configured

    if not SOUNDS_DIR.is_dir():
        return "r2d2"

    candidates = [
        p.name for p in SOUNDS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    ]
    if not candidates:
        if not _RANDOM_FALLBACK_LOGGED:
            print("[notify-tts] random pack: no packs installed, "
                  "falling back to 'r2d2'", file=sys.stderr)
            _RANDOM_FALLBACK_LOGGED = True
        return "r2d2"

    return random.choice(candidates)
```

- [ ] **Step 2: Run test to verify all 3 pass**

Run: `python -m pytest tests/test_random_pack.py -v`
Expected: 3 passed

- [ ] **Step 3: Commit**

```bash
git add scripts/notify-tts.py tests/test_random_pack.py
git commit -m "feat: random sound pack — resolves to a fresh pick per hook invocation"
```

### Task B3: Tray menu — Random entry + dashboard display

**Files:**
- Modify: `src/voice_bridge/tray_icon.py`

- [ ] **Step 1: Add `🎲 Random` entry as the first item in the Sound Pack submenu**

Locate this block in `src/voice_bridge/tray_icon.py` (around lines 525–538):

```python
        # --- Sound Pack submenu ---
        pack_items = []
        if SOUNDS_DIR.is_dir():
            for pack_dir in sorted(SOUNDS_DIR.iterdir()):
                if pack_dir.is_dir():
                    pack_name = pack_dir.name
                    count = len(list(pack_dir.glob("*.mp3")))
                    checked = (pack_name == current_pack)
                    sel = E_SELECTED if checked else E_UNSELECTED
                    pe = PACK_EMOJI.get(pack_name, "📦")
                    pack_items.append(pystray.MenuItem(
                        f"{sel} {pe} {pack_name} ({count})",
                        self._make_set_sound_pack(pack_name),
                    ))
```

Replace with:

```python
        # --- Sound Pack submenu ---
        pack_items = []
        # Random meta-entry first
        random_checked = (current_pack == "random")
        random_sel = E_SELECTED if random_checked else E_UNSELECTED
        pack_items.append(pystray.MenuItem(
            f"{random_sel} 🎲 Random (mix all packs)",
            self._make_set_sound_pack("random"),
        ))
        if SOUNDS_DIR.is_dir():
            for pack_dir in sorted(SOUNDS_DIR.iterdir()):
                if pack_dir.is_dir() and not pack_dir.name.startswith("_"):
                    pack_name = pack_dir.name
                    count = len(list(pack_dir.glob("*.mp3")))
                    checked = (pack_name == current_pack)
                    sel = E_SELECTED if checked else E_UNSELECTED
                    pe = PACK_EMOJI.get(pack_name, "📦")
                    pack_items.append(pystray.MenuItem(
                        f"{sel} {pe} {pack_name} ({count})",
                        self._make_set_sound_pack(pack_name),
                    ))
```

- [ ] **Step 2: Update dashboard line to show `🎲 random` when active**

Locate this line in `_build_menu()` (around line 638):

```python
            pystray.MenuItem(f"  {mode_icon} {current_mode}  │  🔊 {current_vol}%  │  {pack_icon} {current_pack}", None, enabled=False),
```

And the line just above the dashboard build (around line 631):

```python
        pack_icon = PACK_EMOJI.get(current_pack, "📦")
```

Replace the `pack_icon` line with:

```python
        if current_pack == "random":
            pack_icon = "🎲"
            pack_display = "random"
        else:
            pack_icon = PACK_EMOJI.get(current_pack, "📦")
            pack_display = current_pack
```

And the dashboard MenuItem line with:

```python
            pystray.MenuItem(f"  {mode_icon} {current_mode}  │  🔊 {current_vol}%  │  {pack_icon} {pack_display}", None, enabled=False),
```

- [ ] **Step 3: Update top-level "Sound Pack" menu label to also handle random**

Locate this line in `_build_menu()` (around line 643):

```python
            pystray.MenuItem(f"🎵 Sound Pack [{current_pack}]", pystray.Menu(*pack_items)),
```

Replace with:

```python
            pystray.MenuItem(
                f"🎵 Sound Pack [{'🎲 random' if current_pack == 'random' else current_pack}]",
                pystray.Menu(*pack_items),
            ),
```

- [ ] **Step 4: Manual smoke check (visual)**

Run the bridge: `python -m src.voice_bridge.bridge`
Open the tray menu, navigate to **Sound Pack**. Verify:
- `🎲 Random (mix all packs)` is the first entry
- Clicking it sets `sound_pack="random"` in `~/.claude/cache/tts/tts_config.json`
- Top-level shows `🎵 Sound Pack [🎲 random]`
- Dashboard line shows `... │ 🎲 random`

If pack `_disabled`-style hidden dirs exist, confirm they are now filtered out.

Stop the bridge with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add src/voice_bridge/tray_icon.py
git commit -m "feat: tray menu — add 🎲 Random sound pack entry + dashboard display"
```

### Task B4: Preview submenu — handle random pack gracefully

**Files:**
- Modify: `src/voice_bridge/tray_icon.py`

- [ ] **Step 1: Fix preview block to fallback when pack=random**

Locate this in `_build_menu()` (around lines 564–572):

```python
        # --- Preview Sounds submenu ---
        preview_items = []
        pack_dir = SOUNDS_DIR / current_pack
        if pack_dir.is_dir():
            for mp3 in sorted(pack_dir.glob("*.mp3"))[:15]:
                preview_items.append(pystray.MenuItem(
                    f"  ▶️ {mp3.stem}",
                    self._make_preview_sound(str(mp3)),
                ))
```

Replace with:

```python
        # --- Preview Sounds submenu ---
        # When pack=random, preview shows mp3s from a deterministic pack
        # so the menu doesn't reshuffle every rebuild.
        preview_pack = current_pack
        if preview_pack == "random" and SOUNDS_DIR.is_dir():
            real_packs = sorted(
                p.name for p in SOUNDS_DIR.iterdir()
                if p.is_dir() and not p.name.startswith("_")
            )
            preview_pack = real_packs[0] if real_packs else current_pack
        preview_items = []
        pack_dir = SOUNDS_DIR / preview_pack
        if pack_dir.is_dir():
            for mp3 in sorted(pack_dir.glob("*.mp3"))[:15]:
                preview_items.append(pystray.MenuItem(
                    f"  ▶️ {mp3.stem}",
                    self._make_preview_sound(str(mp3)),
                ))
```

- [ ] **Step 2: Manual smoke check**

Run bridge, set pack to `🎲 Random`, open Preview submenu — verify items appear (from the first alphabetical real pack) instead of being empty.

- [ ] **Step 3: Commit**

```bash
git add src/voice_bridge/tray_icon.py
git commit -m "fix: tray preview submenu falls back to first pack when random selected"
```

---

## Stream C — Config defaults

### Task C1: Add gesture fields to VoiceBridgeConfig

**Files:**
- Modify: `src/voice_bridge/config.py`

- [ ] **Step 1: Add fields**

Open `src/voice_bridge/config.py`. After the existing `sound_output_duration` line (currently the last field, line 52), append before the closing of the dataclass:

```python

    # Gesture triggers (mouse on tray sphere + Caps Lock long-press)
    gesture_enabled: bool = True
    gesture_mouse_long_press_ms: int = 480
    gesture_caps_long_press_ms: int = 660
    gesture_double_click_ms: int = 350
    gesture_debounce_ms: int = 200
```

- [ ] **Step 2: Quick import smoke check**

Run:

```bash
python -c "from src.voice_bridge.config import VoiceBridgeConfig; c = VoiceBridgeConfig(); print(c.gesture_enabled, c.gesture_mouse_long_press_ms, c.gesture_caps_long_press_ms, c.gesture_double_click_ms, c.gesture_debounce_ms)"
```

Expected output: `True 480 660 350 200`

- [ ] **Step 3: Commit**

```bash
git add src/voice_bridge/config.py
git commit -m "feat: add gesture trigger config defaults to VoiceBridgeConfig"
```

### Task C2: Test for config defaults

**Files:**
- Create: `tests/test_config.py`

- [ ] **Step 1: Write test**

Create `tests/test_config.py`:

```python
"""Tests for VoiceBridgeConfig gesture defaults."""
from src.voice_bridge.config import VoiceBridgeConfig


def test_gesture_defaults():
    c = VoiceBridgeConfig()
    assert c.gesture_enabled is True
    assert c.gesture_mouse_long_press_ms == 480
    assert c.gesture_caps_long_press_ms == 660
    assert c.gesture_double_click_ms == 350
    assert c.gesture_debounce_ms == 200


def test_gesture_overridable():
    c = VoiceBridgeConfig(gesture_enabled=False, gesture_mouse_long_press_ms=999)
    assert c.gesture_enabled is False
    assert c.gesture_mouse_long_press_ms == 999
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/test_config.py -v`
Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_config.py
git commit -m "test: gesture config defaults"
```

---

## Integration — Wire GestureListener into VoiceBridge

### Task I1: Wire toggle callback + lock + debounce in bridge.py

**Files:**
- Modify: `src/voice_bridge/bridge.py`

- [ ] **Step 1: Add imports and instance setup**

In `src/voice_bridge/bridge.py`, locate the import block at the top and add `GestureListener` to it. Find this line:

```python
from .tray_icon import TrayIcon
```

Add immediately after:

```python
from .gesture_listener import GestureListener
```

- [ ] **Step 2: Add `_last_toggle_ts` and `_toggle_lock` attributes in `__init__`**

Locate this in `VoiceBridge.__init__` (around lines 51–55):

```python
        self.config = config or VoiceBridgeConfig()
        self._state = BridgeState.IDLE
        self._running = False
        self._lock = threading.Lock()
```

Replace with:

```python
        self.config = config or VoiceBridgeConfig()
        self._state = BridgeState.IDLE
        self._running = False
        self._lock = threading.Lock()
        self._toggle_lock = threading.Lock()
        self._last_toggle_ts = 0.0
```

- [ ] **Step 3: Construct the GestureListener at the end of `__init__`**

After the `self._tray = TrayIcon(...)` block (currently ending around line 89), append:

```python
        self._gesture: GestureListener | None = None
        if self.config.gesture_enabled:
            self._gesture = GestureListener(
                on_toggle_rec=self._gesture_toggle_recording,
                mouse_long_press_ms=self.config.gesture_mouse_long_press_ms,
                caps_long_press_ms=self.config.gesture_caps_long_press_ms,
                double_click_ms=self.config.gesture_double_click_ms,
            )
```

- [ ] **Step 4: Add the unified toggle method**

After `_on_hotkey_release` (currently ending around line 152), insert this method:

```python
    def _gesture_toggle_recording(self) -> None:
        """Toggle REC from gesture trigger (mouse long-press, double-click,
        or Caps Lock long-press). Same effect as a hotkey toggle press.

        Debounced + locked to prevent double-fires when multiple sources
        (e.g. hotkey + gesture) trigger nearly simultaneously.
        """
        with self._toggle_lock:
            now = time.monotonic()
            debounce_s = self.config.gesture_debounce_ms / 1000
            if now - self._last_toggle_ts < debounce_s:
                logger.debug("Gesture toggle debounced")
                return
            self._last_toggle_ts = now

        if self._state == BridgeState.IDLE:
            self._on_hotkey_press()
        elif self._state == BridgeState.RECORDING:
            self._on_hotkey_release()
        else:
            logger.debug(f"Gesture toggle ignored (state={self._state.value})")
```

- [ ] **Step 5: Start the gesture listener in `start()`**

Locate this block in `start()` (around lines 246–248):

```python
        # Start hotkey listener
        self._hotkey.start()
        self._set_state(BridgeState.IDLE)
```

Insert before `self._set_state(BridgeState.IDLE)`:

```python
        # Start gesture listener (mouse on tray + Caps Lock long-press)
        if self._gesture:
            self._gesture.start()
```

So the block becomes:

```python
        # Start hotkey listener
        self._hotkey.start()

        # Start gesture listener (mouse on tray + Caps Lock long-press)
        if self._gesture:
            self._gesture.start()

        self._set_state(BridgeState.IDLE)
```

- [ ] **Step 6: Stop the gesture listener in `stop()`**

Locate this in `stop()` (around line 300):

```python
        self._hotkey.stop()
        self._recorder.cleanup()
```

Insert before `self._recorder.cleanup()`:

```python
        if self._gesture:
            self._gesture.stop()
```

- [ ] **Step 7: Run all tests to confirm no regression**

Run: `python -m pytest tests/ -v`
Expected: 14 passed (3 win32 + 9 gesture + 3 random pack + 2 config — note that the smoke construction test counts as gesture)

Wait — recount: A1=3 win32, A4 state machines=8, A6 smoke=1, B2 random pack=3, C2 config=2 → total 17. Verify by running.

- [ ] **Step 8: Commit**

```bash
git add src/voice_bridge/bridge.py
git commit -m "feat: wire GestureListener into VoiceBridge with debounced toggle"
```

### Task I2: Manual smoke test — full workflow

**Files:** none (verification only)

- [ ] **Step 1: Run the bridge**

Run: `python -m src.voice_bridge.bridge`
Wait for `Voice Bridge ready! Hold hotkey to dictate.` log line and the startup jingle.

- [ ] **Step 2: Verify hotkey still works**

Press `Ctrl+Alt+Space` once → REC start (sphere goes red, start beep). Press again → REC stop (transcribe → output).
Expected: behavior unchanged from before.

- [ ] **Step 3: Verify mouse long-press on tray sphere**

With the parlaconclaudio icon visible in the system tray (NOT inside the overflow popup):
- Left-click and hold the sphere for ~600 ms → REC start.
- Release. Left-click and hold again ~600 ms → REC stop, transcribe, output.

Expected: same effect as the hotkey.

If the icon is in overflow, the log will show:
`Tray icon not found in notification area — mouse long-press / double-click disabled.`
That's the documented fallback. Pin the icon out of overflow and restart to test.

- [ ] **Step 4: Verify double-click on tray sphere**

Two quick left-clicks (< 350 ms apart) on the sphere → REC start. Repeat → REC stop.

- [ ] **Step 5: Verify Caps Lock long-press**

Hold Caps Lock for ~800 ms → REC start.
**Critical check**: after REC starts, look at any keyboard's Caps Lock LED or open Notepad and type a letter — Caps Lock must be **OFF** (state compensated).
Hold again 800 ms → REC stop.

Then verify normal Caps Lock still works: a quick tap of Caps Lock → LED flips, typing produces uppercase. Tap again → off.

- [ ] **Step 6: Verify debounce**

Press `Ctrl+Alt+Space` and within 100 ms also tap Caps Lock for 800 ms.
Expected: only one toggle effect (REC starts once, not start-stop ping-pong).

- [ ] **Step 7: Verify random pack**

In the tray menu → **Sound Pack** → click `🎲 Random (mix all packs)`.
Trigger 3 hook events from a Claude Code session (or run notify-tts.py manually:

```bash
echo '{"hook_event_name":"TaskCompleted","task_subject":"test"}' | python scripts/notify-tts.py
```

three times). Listen carefully — chimes should sound different across the 3 invocations (different packs). Check `~/.claude/cache/tts/tts_config.json` confirms `"sound_pack": "random"`.

- [ ] **Step 8: Verify gesture_enabled=false works**

Stop the bridge. Edit `~/.claude/cache/tts/tts_config.json` (or whatever shared config — actually, gesture_enabled is in VoiceBridgeConfig, not the JSON). For this smoke test, edit `bridge.py:main()` temporarily to pass `gesture_enabled=False`, run, confirm only hotkey works, then revert.

Actually skip step 8 if you've verified the conditional `if self._gesture:` branch in code review — the field is currently only set via dataclass default and not exposed in JSON config. (Consider as future work if needed.)

- [ ] **Step 9: Stop the bridge with Ctrl+C; check logs**

Tail `voicebridge.log` and confirm:
- `GestureListener started (mouse_long=480ms, caps_long=660ms, double_click=350ms)` appeared at startup
- `GestureListener stopped` appears at shutdown
- No tracebacks

### Task I3: Final commit + branch summary

**Files:** none (final checks)

- [ ] **Step 1: Run the full test suite one more time**

Run: `python -m pytest tests/ -v`
Expected: all green.

- [ ] **Step 2: Check git status is clean**

Run: `git status`
Expected: nothing to commit, working tree clean (all features committed in their own tasks).

- [ ] **Step 3: Print summary commits**

Run: `git log --oneline -20`
Expected: recent log shows the chain of commits from this plan, ready for review/PR.

---

## Self-review checklist (run after writing the plan)

**Spec coverage:**

- [x] Long-press mouse 480 ms → Tasks A3–A6, I1
- [x] Double-click 350 ms → Tasks A3–A4, I1
- [x] Caps Lock long-press 660 ms + compensation → Tasks A3–A5, I1
- [x] Coexists with Ctrl+Alt+Space → Task I1 (`_gesture_toggle_recording` calls existing `_on_hotkey_press/release`)
- [x] Lock + debounce 200 ms → Task I1 step 4
- [x] Win32 tray rect with fallback → Tasks A1–A2, A5 (warning log)
- [x] Pack random per-event → Tasks B1–B2
- [x] Pack random tray menu entry → Task B3
- [x] Pack random preview fallback → Task B4
- [x] Config defaults → Tasks C1–C2
- [x] Master switch `gesture_enabled=false` → Task I1 step 3 (`if self.config.gesture_enabled`)
- [x] Smoke test all triggers → Task I2

**Placeholder scan:** No TBD / TODO / "implement later" remaining.

**Type consistency:** `MouseGestureState`, `CapsLockGestureState`, `GestureListener` names consistent across A3, A4, A5, A6, I1. `is_inside_tray_icon` consistent across A1, A2, A5. `get_sound_pack` consistent across B1, B2.

**Discrepancy fixed:** Spec originally said `sounds.py` for random pack — corrected inline (commit `a2596aa`) and plan correctly targets `scripts/notify-tts.py`.
