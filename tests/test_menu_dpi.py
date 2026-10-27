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


def test_volume_action_saves_the_level(monkeypatch):
    config = {"volume": 125}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda new: config.update(new))
    tray = tray_icon.TrayIcon()
    tray._make_set_volume(75)(None, None)
    assert config["volume"] == 75