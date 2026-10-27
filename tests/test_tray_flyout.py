"""Quick controls preserve native fallback and avoid work while hidden."""
import ctypes
from types import SimpleNamespace

from src.voice_bridge.tray_flyout import TrayFlyout
from src.voice_bridge.tray_popup import FlyoutIcon
from pystray._util import win32


def test_left_click_opens_quick_controls_and_right_click_the_app_window():
    received = []
    icon = SimpleNamespace(_on_quick_controls=lambda **kwargs: received.append(("quick", kwargs)),
                           _on_open_panel=lambda: received.append(("panel", {})))
    FlyoutIcon._on_notify(icon, 0, win32.WM_LBUTTONUP)
    FlyoutIcon._on_notify(icon, 0, win32.WM_RBUTTONUP)
    assert received == [("quick", {"left_click": True}), ("panel", {})]


def test_hidden_flyout_does_not_poll_settings_devices_or_render():
    flyout = TrayFlyout(SimpleNamespace())
    scheduled = []
    flyout.root = SimpleNamespace(state=lambda: "withdrawn", after=lambda *args: scheduled.append(args))
    flyout._update_state = lambda: (_ for _ in ()).throw(AssertionError("hidden redraw"))
    flyout._refresh = lambda: (_ for _ in ()).throw(AssertionError("hidden refresh"))
    flyout._tick()
    assert scheduled[0][0] == 1000


def test_rapid_actions_are_serialized_and_not_dropped():
    flyout = TrayFlyout(SimpleNamespace())
    flyout._working = True
    calls = []
    callback = lambda: calls.append("saved")
    flyout._work(callback)
    assert flyout._pending_actions == [callback]
    assert not calls
    flyout._commands.put("refresh")
    flyout.root = SimpleNamespace(state=lambda: "withdrawn", after=lambda *a: None)
    flyout._refresh = lambda rebuild=True: None
    started = []
    flyout._work = lambda action: started.append(action)
    flyout._tick()
    assert started == [callback]
    assert not flyout._pending_actions


def test_real_popup_is_visible_and_inside_work_area(monkeypatch):
    """Exercise production borderless window, separate from capture decoration."""
    from src.voice_bridge import tray_icon
    from src.voice_bridge import flyout_window
    from src.voice_bridge.menu_dpi import native_menu_dpi
    monkeypatch.setattr(tray_icon, "_load_config", lambda: {"volume": 125})
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            flyout.root.update()
            user = ctypes.WinDLL("user32")
            user.IsWindowVisible.argtypes = [ctypes.c_void_p]
            assert user.IsWindowVisible(flyout_window.window_handle(flyout.root))
            left, top, right, bottom = flyout_window.work_area()
            assert left <= flyout.root.winfo_x()
            assert top <= flyout.root.winfo_y()
            assert flyout.root.winfo_x() + flyout.root.winfo_width() <= right
            assert flyout.root.winfo_y() + flyout.root.winfo_height() <= bottom
            flyout._hide()
            assert flyout.root.state() == "withdrawn"
        finally:
            flyout.root.destroy()
