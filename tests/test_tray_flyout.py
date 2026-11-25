"""Quick controls preserve native fallback and avoid work while hidden."""
import ctypes
from types import SimpleNamespace

from src.voice_bridge.tray_flyout import TrayFlyout
from src.voice_bridge.tray_popup import FlyoutIcon
from pystray._util import win32


def test_left_click_opens_quick_controls_and_right_click_the_app_window():
    received = []
    icon = SimpleNamespace(_on_quick_controls=lambda **kwargs: received.append(("quick", kwargs)),
                           _on_open_panel=lambda: received.append(("panel", {})),
                           _on_double_click=None, _swallow_up=False)
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


def test_focus_never_received_does_not_dismiss_but_focus_lost_does():
    hidden = []
    flyout = TrayFlyout(SimpleNamespace())
    flyout._hide = lambda: hidden.append(True)
    flyout.root = SimpleNamespace(state=lambda: "normal", focus_displayof=lambda: None)
    flyout._had_focus = False  # Windows refused the focus when it opened
    flyout._dismiss_if_outside()
    assert hidden == []
    flyout._had_focus = True  # user clicked elsewhere afterwards
    flyout._dismiss_if_outside()
    assert hidden == [True]


def _icon(received):
    return SimpleNamespace(_on_quick_controls=lambda **kwargs: received.append("quick"),
                           _on_open_panel=lambda: received.append("panel"),
                           _on_double_click=lambda: received.append("toggle"),
                           _swallow_up=False, _remember_app=lambda: received.append("remember"))


def test_double_click_on_the_tray_icon_toggles_recording_once():
    """Shell sequence for a double-click: UP, DBLCLK, UP. The first UP schedules the
    quick controls (the tray cancels them), the closing UP must not reopen them."""
    from src.voice_bridge.tray_popup import WM_LBUTTONDBLCLK
    received = []
    icon = _icon(received)
    for message in (win32.WM_LBUTTONUP, WM_LBUTTONDBLCLK, win32.WM_LBUTTONUP):
        FlyoutIcon._on_notify(icon, 0, message)
    assert received == ["quick", "toggle"]
    FlyoutIcon._on_notify(icon, 0, win32.WM_LBUTTONUP)  # a later single click works again
    assert received == ["quick", "toggle", "quick"]


def test_pointer_over_the_icon_remembers_the_app_to_paste_into():
    from src.voice_bridge.tray_popup import WM_MOUSEMOVE, WM_LBUTTONDOWN
    received = []
    icon = _icon(received)
    FlyoutIcon._on_notify(icon, 0, WM_MOUSEMOVE)
    FlyoutIcon._on_notify(icon, 0, WM_LBUTTONDOWN)
    assert received == ["remember", "remember"]


def test_taskbar_and_own_windows_are_not_paste_targets(monkeypatch):
    from src.voice_bridge import tray_popup
    monkeypatch.setattr(tray_popup, "_window_pid", lambda hwnd: {1: 999999, 2: 999999}.get(hwnd, tray_popup.os.getpid()))
    monkeypatch.setattr(tray_popup, "_window_class", lambda hwnd: {1: "Notepad", 2: "Shell_TrayWnd"}.get(hwnd, "TkTopLevel"))
    assert tray_popup.is_paste_target(1)
    assert not tray_popup.is_paste_target(2)   # taskbar
    assert not tray_popup.is_paste_target(3)   # our own window
    assert not tray_popup.is_paste_target(0)


def test_tray_double_click_cancels_quick_controls_and_returns_focus(monkeypatch):
    import threading
    from src.voice_bridge import tray_icon
    calls = []
    tray = SimpleNamespace(_flyout_timer=SimpleNamespace(cancel=lambda: calls.append("cancel")),
                           return_focus=lambda: calls.append("focus"),
                           _on_toggle_recording=lambda: calls.append("toggle"))
    started = []
    monkeypatch.setattr(threading, "Thread", lambda target, daemon: SimpleNamespace(start=lambda: started.append(target)))
    tray_icon.TrayIcon._tray_double_click(tray)
    assert calls == ["cancel", "focus"] and tray._flyout_timer is None
    started[0]()
    assert calls[-1] == "toggle"


def _listening_flyout(state):
    tray = SimpleNamespace(_state=state, toggled=[], focused=[])
    tray._on_toggle_recording = lambda: tray.toggled.append(True)
    tray.return_focus = lambda: tray.focused.append(True)
    flyout = TrayFlyout(tray)
    flyout.root = SimpleNamespace(withdraw=lambda: None, after=lambda ms, fn: fn(),
                                  event_generate=lambda *a, **k: None)
    flyout._work = lambda action: action()
    return tray, flyout


def test_dictation_tile_starts_without_hiding_the_quick_controls():
    tray, flyout = _listening_flyout("idle")
    hidden = []
    flyout._hide = lambda: hidden.append(True)
    flyout._record()
    assert tray.toggled == [True] and hidden == [] and flyout._listening


def test_dictation_tile_stops_by_hiding_and_returning_focus_first():
    tray, flyout = _listening_flyout("recording")
    flyout._listening = True
    order = []
    flyout.root.withdraw = lambda: order.append("hide")
    tray.return_focus = lambda: order.append("focus")
    tray._on_toggle_recording = lambda: order.append("stop")
    flyout._record()
    assert order == ["hide", "focus", "stop"]
    assert not flyout._listening


def test_recording_ended_elsewhere_closes_a_listening_flyout_once():
    tray, flyout = _listening_flyout("recording")
    assert not flyout.recording_ended()          # not started from the tile
    flyout._listening = True
    assert flyout.recording_ended()
    assert flyout._commands.get_nowait() == "hide"
    assert not flyout.recording_ended()


def test_tray_state_change_hands_focus_back_when_the_flyout_was_listening():
    from src.voice_bridge import tray_icon
    focused = []
    tray = SimpleNamespace(_state="recording", _icon=None, return_focus=lambda: focused.append(True),
                           _flyout=SimpleNamespace(recording_ended=lambda: True))
    tray_icon.TrayIcon.set_state(tray, "transcribing")
    assert focused == [True] and tray._state == "transcribing"
    tray._flyout = SimpleNamespace(recording_ended=lambda: False)
    tray_icon.TrayIcon.set_state(tray, "recording")
    tray_icon.TrayIcon.set_state(tray, "transcribing")
    assert focused == [True]


def test_listening_flag_expires_when_the_recording_never_starts(monkeypatch):
    from src.voice_bridge import tray_flyout
    tray, flyout = _listening_flyout("idle")
    flyout.root = SimpleNamespace(state=lambda: "normal", after=lambda *a: None)
    flyout._update_state = lambda: None
    flyout.tray._animator = SimpleNamespace(follow_theme=lambda: None)
    flyout._listening, flyout._listen_since = True, tray_flyout.time.monotonic() - 5
    flyout._tick()
    assert not flyout._listening
