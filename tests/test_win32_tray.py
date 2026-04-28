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
