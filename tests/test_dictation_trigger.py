"""Dictation trigger: config, labels, Tk capture, key matching, mouse suppression, live test routing."""
import time
from types import SimpleNamespace

import pytest
from pynput import keyboard

from src.voice_bridge import dictation_trigger as dt
from src.voice_bridge.hotkey_listener import HotkeyListener


def test_config_defaults_and_invalid_values_fall_back():
    assert dt.trigger_from_config({}) == dt.Trigger()
    assert dt.trigger_from_config({}, "<f9>", "push_to_talk") == dt.Trigger(keys="<f9>", mode="hold")
    saved = {"dictation_trigger": {"kind": "mouse", "button": "x2", "mode": "hold"}}
    assert dt.trigger_from_config(saved) == dt.Trigger(kind="mouse", button="x2", mode="hold")
    assert dt.trigger_from_config({"dictation_trigger": {"kind": "mouse", "button": "x9"}}) == dt.Trigger()


def test_labels_for_keys_and_mouse():
    assert dt.key_caps(dt.Trigger()) == ["Ctrl", "Alt", "Spazio"]
    assert dt.describe(dt.Trigger(keys="<ctrl>+<shift>+d")) == "Ctrl + Maiusc + D"
    assert dt.describe(dt.Trigger(keys="<f9>")) == "F9"
    assert dt.describe(dt.Trigger(keys="<page_up>")) == "Pag su"
    assert dt.key_caps(dt.Trigger(kind="mouse", button="middle")) == ["Tasto centrale del mouse"]


@pytest.mark.parametrize("held, keysym, combo", [
    ({"<ctrl>", "<shift>"}, "D", "<ctrl>+<shift>+d"),
    ({"<alt>", "<ctrl>"}, "space", "<ctrl>+<alt>+<space>"),
    (set(), "F9", "<f9>"),
    (set(), "Pause", "<pause>"),
    ({"<ctrl>"}, "Prior", "<ctrl>+<page_up>"),
])
def test_tk_keys_become_pynput_combinations(held, keysym, combo):
    assert dt.combo_from_tk(held, keysym) == (combo, None)


def test_tk_capture_rejects_plain_letters_and_waits_on_modifiers():
    combo, error = dt.combo_from_tk(set(), "a")
    assert combo is None and "Ctrl" in error
    assert dt.combo_from_tk({"<ctrl>"}, "Control_L") == (None, None)
    assert dt.modifier_from_tk("Win_L") == "<cmd>"


def _wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_letter_combination_matches_with_ctrl_held():
    pressed = []
    listener = HotkeyListener("<ctrl>+<shift>+d", on_press=lambda: pressed.append(1))
    listener._on_key_press(keyboard.Key.ctrl_r)
    listener._on_key_press(keyboard.Key.shift)
    # Windows reports Ctrl+D as a control character; the virtual key still says D.
    listener._on_key_press(keyboard.KeyCode(vk=ord("D"), char="\x04"))
    assert _wait(lambda: pressed == [1])


def test_function_key_alone_in_hold_mode():
    events = []
    listener = HotkeyListener("<f9>", on_press=lambda: events.append("down"),
                              on_release=lambda: events.append("up"), mode="push_to_talk")
    listener._on_key_press(keyboard.Key.f9)
    listener._on_key_release(keyboard.Key.f9)
    assert _wait(lambda: events == ["down", "up"])


class _Suppressed(Exception):
    pass


def _mouse(button, mode="toggle"):
    events = []
    listener = dt.MouseButtonListener(button, lambda: events.append("press"),
                                      lambda: events.append("release"), mode)
    listener._listener = SimpleNamespace(suppress_event=lambda: (_ for _ in ()).throw(_Suppressed()))
    return listener, events


def test_mouse_button_is_swallowed_and_toggles_dictation():
    listener, events = _mouse("middle")
    for msg in (dt.WM_MBUTTONDOWN, dt.WM_MBUTTONUP):
        with pytest.raises(_Suppressed):
            listener._filter(msg, SimpleNamespace(mouseData=0, flags=0))
    listener._last_toggle = 0  # past the debounce window
    with pytest.raises(_Suppressed):
        listener._filter(dt.WM_MBUTTONDOWN, SimpleNamespace(mouseData=0, flags=0))
    assert _wait(lambda: events == ["press", "release"])


def test_other_buttons_and_injected_clicks_pass_through():
    listener, events = _mouse("x2", mode="hold")
    assert listener._filter(dt.WM_MBUTTONDOWN, SimpleNamespace(mouseData=0, flags=0)) is True
    assert listener._filter(dt.WM_XBUTTONDOWN, SimpleNamespace(mouseData=1 << 16, flags=0)) is True
    assert listener._filter(dt.WM_XBUTTONDOWN, SimpleNamespace(mouseData=2 << 16, flags=1)) is True
    for msg in (dt.WM_XBUTTONDOWN, dt.WM_XBUTTONUP):
        with pytest.raises(_Suppressed):
            listener._filter(msg, SimpleNamespace(mouseData=2 << 16, flags=0))
    assert _wait(lambda: events == ["press", "release"])


def test_test_sink_receives_events_instead_of_recording_and_reconfigure_swaps_listener():
    real, tested = [], []
    trigger = dt.DictationTrigger(dt.Trigger(), on_press=lambda: real.append("press"),
                                  on_release=lambda: real.append("release"))
    trigger.test_sink = tested.append
    trigger._fire("press")
    trigger.test_sink = None
    trigger._fire("release")
    assert tested == ["press"] and real == ["release"]
    trigger.reconfigure(dt.Trigger(kind="mouse", button="x1", mode="hold"))
    assert isinstance(trigger._listener, dt.MouseButtonListener)
    assert trigger.trigger.button == "x1"


def test_tray_saves_trigger_and_reconfigures_live(monkeypatch):
    from src.voice_bridge import tray_icon
    config = {}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    live = dt.DictationTrigger(dt.Trigger(), on_press=lambda: None, on_release=lambda: None)
    tray = tray_icon.TrayIcon(dictation_trigger=live)
    new = dt.Trigger(kind="mouse", button="middle", mode="toggle")
    tray._set_dictation_trigger(new)
    assert config["dictation_trigger"] == new.to_config()
    assert tray.dictation_trigger() == new
