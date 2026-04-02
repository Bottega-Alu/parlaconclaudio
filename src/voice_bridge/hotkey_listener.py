"""
Global Hotkey Listener - Detects toggle or push-to-talk key combinations.

Uses pynput for global keyboard hooks on Windows.
Default hotkey: Ctrl+Alt+Space (toggle mode)
"""

import logging
import threading
import time
from typing import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)


class HotkeyListener:
    """
    Listens for a global hotkey combination.

    Toggle mode (default):
    - Press hotkey once -> on_press (start recording)
    - Press hotkey again -> on_release (stop recording)

    Push-to-talk mode:
    - Hold hotkey -> on_press (start recording)
    - Release hotkey -> on_release (stop recording)
    """

    def __init__(
        self,
        hotkey_str: str = "<ctrl>+<alt>+<space>",
        on_press: Callable[[], None] | None = None,
        on_release: Callable[[], None] | None = None,
        mode: str = "toggle",
    ):
        self._on_press = on_press
        self._on_release = on_release
        self._mode = mode
        self._listener: keyboard.Listener | None = None
        self._hotkey_active = False
        self._recording = False  # Toggle state
        self._debounce_time = 0.3  # Prevent double-triggers
        self._last_toggle = 0.0

        # Parse hotkey string into modifier set + trigger key
        self._modifiers, self._trigger = self._parse_hotkey(hotkey_str)
        self._pressed_keys: set = set()

        logger.info(f"Hotkey configured: {hotkey_str} (mode: {mode})")

    @staticmethod
    def _parse_hotkey(hotkey_str: str) -> tuple[set, keyboard.Key | keyboard.KeyCode]:
        """Parse '<ctrl>+<alt>+<space>' into (modifier_set, trigger_key)."""
        parts = [p.strip() for p in hotkey_str.split("+")]
        modifiers = set()
        trigger = None

        key_map = {
            "<ctrl>": keyboard.Key.ctrl_l,
            "<shift>": keyboard.Key.shift_l,
            "<alt>": keyboard.Key.alt_l,
            "<space>": keyboard.Key.space,
            "<enter>": keyboard.Key.enter,
            "<tab>": keyboard.Key.tab,
        }

        for part in parts:
            lower = part.lower()
            if lower in ("<ctrl>", "<shift>", "<alt>"):
                modifiers.add(key_map[lower])
            elif lower in key_map:
                trigger = key_map[lower]
            else:
                trigger = keyboard.KeyCode.from_char(lower.strip("<>"))

        if trigger is None:
            trigger = keyboard.Key.space

        return modifiers, trigger

    def _normalize_key(self, key) -> keyboard.Key | keyboard.KeyCode:
        """Normalize left/right variants to a single modifier."""
        normalize_map = {
            keyboard.Key.ctrl_r: keyboard.Key.ctrl_l,
            keyboard.Key.shift_r: keyboard.Key.shift_l,
            keyboard.Key.alt_r: keyboard.Key.alt_l,
            keyboard.Key.alt_gr: keyboard.Key.alt_l,
        }
        return normalize_map.get(key, key)

    def _hotkey_combo_pressed(self, normalized) -> bool:
        """Check if the full hotkey combo is currently pressed."""
        mods_pressed = all(m in self._pressed_keys for m in self._modifiers)
        trigger_pressed = normalized == self._trigger or self._trigger in self._pressed_keys
        return mods_pressed and trigger_pressed

    def _on_key_press(self, key) -> None:
        """Handle key press events."""
        normalized = self._normalize_key(key)
        self._pressed_keys.add(normalized)

        if not self._hotkey_combo_pressed(normalized):
            return

        if self._mode == "toggle":
            # Debounce
            now = time.monotonic()
            if now - self._last_toggle < self._debounce_time:
                return
            self._last_toggle = now

            if not self._recording:
                # Start recording
                self._recording = True
                self._hotkey_active = True
                logger.debug("Toggle ON - starting recording")
                if self._on_press:
                    self._on_press()
            else:
                # Stop recording
                self._recording = False
                self._hotkey_active = False
                logger.debug("Toggle OFF - stopping recording")
                if self._on_release:
                    self._on_release()
        else:
            # Push-to-talk: start on press
            if not self._hotkey_active:
                self._hotkey_active = True
                logger.debug("Hotkey pressed - starting recording")
                if self._on_press:
                    self._on_press()

    def _on_key_release(self, key) -> None:
        """Handle key release events."""
        normalized = self._normalize_key(key)
        self._pressed_keys.discard(normalized)

        if self._mode == "toggle":
            # In toggle mode, release does nothing
            return

        # Push-to-talk: stop on release
        if self._hotkey_active and normalized == self._trigger:
            self._hotkey_active = False
            logger.debug("Hotkey released - stopping recording")
            if self._on_release:
                self._on_release()

    def start(self) -> None:
        """Start listening for hotkey events (non-blocking)."""
        self._listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info("Hotkey listener started")

    def stop(self) -> None:
        """Stop listening."""
        if self._listener:
            self._listener.stop()
            self._listener = None
            logger.info("Hotkey listener stopped")

    def reset_toggle(self) -> None:
        """Reset toggle state (called after transcription completes)."""
        self._recording = False
        self._hotkey_active = False

    @property
    def is_active(self) -> bool:
        """Whether the hotkey is currently active (recording)."""
        return self._hotkey_active
