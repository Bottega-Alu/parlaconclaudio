"""Scoped native menu DPI must not change the rest of the Python UI."""
import ctypes
from types import SimpleNamespace
import sys

import pytest

from src.voice_bridge import menu_dpi, tray_icon


def test_context_restored_even_when_menu_creation_fails(monkeypatch):
    calls = []

    class Setter:
        def __call__(self, context):
            calls.append(context.value if isinstance(context, ctypes.c_void_p) else context)
            return 123

    monkeypatch.setattr(menu_dpi.ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(SetThreadDpiAwarenessContext=Setter()))
    with pytest.raises(RuntimeError):
        with menu_dpi.native_menu_dpi():
            raise RuntimeError("menu failed")
    assert calls == [ctypes.c_void_p(-4).value, 123]


def test_missing_dpi_api_does_not_break_the_menu(monkeypatch):
    monkeypatch.setattr(menu_dpi.ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace())
    with menu_dpi.native_menu_dpi():
        pass


@pytest.mark.skipif(sys.platform != "win32", reason="native DPI contexts")
def test_real_tray_run_context_is_pm_v2_then_restored():
    user32 = ctypes.WinDLL("user32")
    get = user32.GetThreadDpiAwarenessContext
    get.restype = ctypes.c_void_p
    equal = user32.AreDpiAwarenessContextsEqual
    equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    equal.restype = ctypes.c_bool
    before = get()
    observed = []
    tray = tray_icon.TrayIcon()
    tray._icon = SimpleNamespace(run=lambda: observed.append(get()))
    tray._run_icon()
    assert equal(observed[0], ctypes.c_void_p(-4))
    assert equal(get(), before)


def test_native_volume_selection_and_action_stay_in_sync(monkeypatch, tmp_path):
    from src.voice_bridge import autostart
    from src.core import voice_hooks
    from src.core.stt_engine.key_manager import KeyManager

    config = {"volume": 125, "whisper_language": "it", "tts_mode": "full"}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda new: config.update(new))
    monkeypatch.setattr(tray_icon, "SOUNDS_DIR", tmp_path)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    monkeypatch.setattr(voice_hooks, "status", lambda agent: False)
    monkeypatch.setattr(KeyManager, "get_key", lambda provider: None)
    tray = tray_icon.TrayIcon()
    menu = tray._build_menu()
    volume = next(item for item in menu if item.text.startswith("Volume suoni:"))
    levels = [item for item in volume.submenu if item.text.endswith("%")]
    assert [item.text for item in levels if item.checked] == ["125%"]
    assert all(item.radio for item in levels)
    next(item for item in levels if item.text == "75%")(None)
    assert config["volume"] == 75
    rebuilt = tray._build_menu()
    volume = next(item for item in rebuilt if item.text.startswith("Volume suoni:"))
    assert [item.text for item in volume.submenu if item.checked] == ["75%"]
