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
