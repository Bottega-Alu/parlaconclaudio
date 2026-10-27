"""What starts dictation: a keyboard combination or a mouse button, toggle or hold.

Stored in tts_config.json as::

    "dictation_trigger": {"kind": "keys" | "mouse",
                          "keys": "<ctrl>+<alt>+<space>",
                          "button": "middle" | "x1" | "x2",
                          "mode": "toggle" | "hold"}

``DictationTrigger`` is the single listener the bridge talks to (start, stop,
reset_toggle). The app window changes it live with ``reconfigure`` and can
route its events to a test area instead of starting a recording.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable

from .hotkey_listener import HotkeyListener

logger = logging.getLogger(__name__)

MOUSE_BUTTONS = {
    "middle": "Tasto centrale del mouse",
    "x1": "Tasto laterale Indietro",
    "x2": "Tasto laterale Avanti",
}
MODES = {
    "toggle": "Premi per avviare e per fermare",
    "hold": "Tieni premuto mentre parli",
}
MODIFIERS = ("<ctrl>", "<alt>", "<shift>", "<cmd>")
_KEY_LABELS = {"<ctrl>": "Ctrl", "<alt>": "Alt", "<shift>": "Maiusc", "<cmd>": "Win",
               "<space>": "Spazio", "<enter>": "Invio", "<esc>": "Esc", "<pause>": "Pausa",
               "<insert>": "Ins", "<delete>": "Canc", "<page_up>": "Pag su", "<page_down>": "Pag giù",
               "<print_screen>": "Stamp", "<scroll_lock>": "Bloc scorr", "<backspace>": "Backspace"}
# Tk and pynput name a few keys differently; every other Tk keysym lowercased is the pynput name.
_TK_TO_PYNPUT = {"return": "enter", "prior": "page_up", "next": "page_down", "escape": "esc",
                 "print": "print_screen", "win_l": "cmd", "win_r": "cmd"}
_TK_MODIFIERS = {"control_l": "<ctrl>", "control_r": "<ctrl>", "alt_l": "<alt>", "alt_r": "<alt>",
                 "shift_l": "<shift>", "shift_r": "<shift>", "win_l": "<cmd>", "win_r": "<cmd>"}

WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C


@dataclass(frozen=True)
class Trigger:
    kind: str = "keys"
    keys: str = "<ctrl>+<alt>+<space>"
    button: str = "middle"
    mode: str = "toggle"

    def to_config(self) -> dict:
        return asdict(self)


def trigger_from_config(config: dict, default_keys: str = "<ctrl>+<alt>+<space>",
                        default_mode: str = "toggle") -> Trigger:
    saved = config.get("dictation_trigger") or {}
    default_mode = "hold" if default_mode in ("hold", "push_to_talk", "ptt") else "toggle"
    trigger = Trigger(
        kind=saved.get("kind", "keys"),
        keys=saved.get("keys") or default_keys,
        button=saved.get("button", "middle"),
        mode=saved.get("mode", default_mode),
    )
    if trigger.kind not in ("keys", "mouse") or trigger.button not in MOUSE_BUTTONS \
            or trigger.mode not in MODES:
        logger.warning("Invalid dictation_trigger %s, using the default", saved)
        return Trigger(keys=default_keys, mode=default_mode)
    return trigger


def key_caps(trigger: Trigger) -> list[str]:
    """Labels for keycaps: ['Ctrl', 'Alt', 'Spazio'] or ['Tasto centrale del mouse']."""
    if trigger.kind == "mouse":
        return [MOUSE_BUTTONS[trigger.button]]
    caps = []
    for part in trigger.keys.split("+"):
        part = part.strip().lower()
        label = _KEY_LABELS.get(part)
        if label is None:
            name = part.strip("<>")
            label = name.upper() if len(name) <= 3 else name.replace("_", " ").capitalize()
        caps.append(label)
    return caps


def describe(trigger: Trigger) -> str:
    return " + ".join(key_caps(trigger))


def combo_from_tk(modifiers: set[str], keysym: str) -> tuple[str | None, str | None]:
    """Tk key event -> (pynput combination, error). Plain letters/digits need a modifier."""
    name = keysym.lower()
    if name in _TK_MODIFIERS:
        return None, None  # still composing
    if len(name) == 1 and name.isalnum():
        key = name
        if not modifiers:
            return None, "Aggiungi Ctrl, Alt o Maiusc: una lettera da sola scriverebbe nel testo."
    else:
        key = f"<{_TK_TO_PYNPUT.get(name, name)}>"
        from pynput import keyboard
        if key.strip("<>") not in keyboard.Key.__members__:
            return None, f"Il tasto {keysym} non si può usare per la dettatura."
    ordered = [m for m in MODIFIERS if m in modifiers]
    return "+".join(ordered + [key]), None


def modifier_from_tk(keysym: str) -> str | None:
    return _TK_MODIFIERS.get(keysym.lower())


class MouseButtonListener:
    """One mouse button starts/stops dictation; the click never reaches other apps."""

    def __init__(self, button: str, on_press: Callable[[], None], on_release: Callable[[], None],
                 mode: str = "toggle"):
        self._button = button
        self._on_press = on_press
        self._on_release = on_release
        self._mode = mode
        self._listener = None
        self._recording = False
        self._active = False
        self._last_toggle = 0.0
        self._debounce = 0.3

    def _matches(self, msg: int, data) -> tuple[bool, bool]:
        """(is our button, is press)."""
        if self._button == "middle" and msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            return True, msg == WM_MBUTTONDOWN
        if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            which = {1: "x1", 2: "x2"}.get(data.mouseData >> 16)
            if which == self._button:
                return True, msg == WM_XBUTTONDOWN
        return False, False

    def _filter(self, msg, data):
        ours, pressed = self._matches(msg, data)
        if not ours or data.flags & 0x01:  # LLMHF_INJECTED: synthetic clicks pass through
            return True
        self.handle(pressed)
        self._listener.suppress_event()

    def handle(self, pressed: bool) -> None:
        if self._mode == "toggle":
            if not pressed:
                return
            now = time.monotonic()
            if now - self._last_toggle < self._debounce:
                return
            self._last_toggle = now
            self._recording = not self._recording
            self._active = self._recording
            callback = self._on_press if self._recording else self._on_release
        elif pressed and not self._active:
            self._active = True
            callback = self._on_press
        elif not pressed and self._active:
            self._active = False
            callback = self._on_release
        else:
            return
        # Never block the system-wide mouse hook.
        threading.Thread(target=callback, daemon=True).start()

    def start(self) -> None:
        from pynput import mouse
        self._listener = mouse.Listener(win32_event_filter=self._filter)
        self._listener.daemon = True
        self._listener.start()
        logger.info("Mouse dictation button started: %s (mode: %s)", self._button, self._mode)

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
            self._listener = None

    def reset_toggle(self) -> None:
        self._recording = False
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active


class DictationTrigger:
    """The bridge's dictation listener, reconfigurable live from the app window."""

    def __init__(self, trigger: Trigger, on_press: Callable[[], None], on_release: Callable[[], None]):
        self._on_press = on_press
        self._on_release = on_release
        self._lock = threading.Lock()
        self._started = False
        self.trigger = trigger
        self.test_sink: Callable[[str], None] | None = None
        self._listener = self._make(trigger)

    def _make(self, trigger: Trigger):
        press, release = (lambda: self._fire("press")), (lambda: self._fire("release"))
        if trigger.kind == "mouse":
            return MouseButtonListener(trigger.button, press, release, trigger.mode)
        mode = "toggle" if trigger.mode == "toggle" else "push_to_talk"
        return HotkeyListener(hotkey_str=trigger.keys, on_press=press, on_release=release, mode=mode)

    def _fire(self, kind: str) -> None:
        sink = self.test_sink
        if sink:
            sink(kind)
            return
        (self._on_press if kind == "press" else self._on_release)()

    def start(self) -> None:
        with self._lock:
            self._listener.start()
            self._started = True

    def stop(self) -> None:
        with self._lock:
            self._listener.stop()
            self._started = False

    def pause(self) -> None:
        """While the window records a new combination, the old one must not dictate."""
        with self._lock:
            if self._started:
                self._listener.stop()

    def resume(self) -> None:
        with self._lock:
            if self._started:
                self._listener = self._make(self.trigger)
                self._listener.start()

    def reconfigure(self, trigger: Trigger) -> None:
        with self._lock:
            if self._started:
                self._listener.stop()
            self.trigger = trigger
            self._listener = self._make(trigger)
            if self._started:
                self._listener.start()
        logger.info("Dictation trigger set to %s (%s)", describe(trigger), trigger.mode)

    def reset_toggle(self) -> None:
        self._listener.reset_toggle()

    @property
    def is_active(self) -> bool:
        return self._listener.is_active
