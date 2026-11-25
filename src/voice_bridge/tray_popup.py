"""Windows tray clicks: left opens the quick controls, right opens the app window,
double-click starts or stops a recording.

A double-click arrives as LBUTTONUP, LBUTTONDBLCLK, LBUTTONUP: the first release
schedules the quick controls (TrayIcon._open_flyout waits ~0.37 s for this), the
double-click cancels them and toggles recording, the last release is swallowed.
The shell sends LBUTTONDBLCLK to the icon itself, so this works where the global
mouse gesture cannot find the icon (Windows 11 notification area).

Clicking the tray moves the focus to the taskbar, where a paste is lost. The
icon receives WM_MOUSEMOVE while the pointer is still on its way to the click,
when the app the user was writing in still has the focus: that window is kept
so a recording started or stopped from the tray can paste back into it."""
import ctypes
import ctypes.wintypes
import logging
import os

import pystray
from pystray._util import win32

logger = logging.getLogger(__name__)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONDBLCLK = 0x0203

# Taskbar, its overflow flyout and the Windows 11 XAML hosts: never a paste target.
_SHELL_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                  "TopLevelWindowForOverflowXamlIsland", "Windows.UI.Core.CoreWindow",
                  "XamlExplorerHostIslandWindow", "Progman", "WorkerW")


def _user32():
    return ctypes.windll.user32


def _window_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32().GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_pid(hwnd) -> int:
    pid = ctypes.wintypes.DWORD()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_paste_target(hwnd) -> bool:
    """A window of another app the user can type into, not the shell or us."""
    if not hwnd:
        return False
    try:
        return (_window_pid(hwnd) != os.getpid()
                and _window_class(hwnd) not in _SHELL_CLASSES)
    except Exception:
        return False


def focus_window(hwnd) -> bool:
    """Give the focus back to hwnd; False if it is gone or Windows refused."""
    if not hwnd:
        return False
    try:
        user32 = _user32()
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if user32.SetForegroundWindow(hwnd) and user32.GetForegroundWindow() == hwnd:
            return True
        # Foreground lock (we did not get the last input): join the input queue of
        # the window that has the focus now, which lifts the lock for this call.
        current = user32.GetForegroundWindow()
        theirs = user32.GetWindowThreadProcessId(current, None)
        ours = ctypes.windll.kernel32.GetCurrentThreadId()
        attached = theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(ours, theirs, False)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        logger.debug("Focus restore failed", exc_info=True)
        return False


class FlyoutIcon(pystray.Icon):
    def __init__(self, *args, on_quick_controls, on_open_panel, on_double_click=None, **kwargs):
        self._on_quick_controls = on_quick_controls
        self._on_open_panel = on_open_panel
        self._on_double_click = on_double_click
        self._swallow_up = False
        self.last_app_hwnd = None
        super().__init__(*args, **kwargs)

    def _remember_app(self):
        try:
            hwnd = _user32().GetForegroundWindow()
        except Exception:
            return
        if is_paste_target(hwnd):
            self.last_app_hwnd = hwnd

    def _on_notify(self, wparam, lparam):
        if lparam in (WM_MOUSEMOVE, WM_LBUTTONDOWN):
            self._remember_app()
            return
        if lparam == WM_LBUTTONDBLCLK and self._on_double_click:
            self._swallow_up = True
            self._on_double_click()
        elif lparam == win32.WM_LBUTTONUP:
            if self._swallow_up:
                self._swallow_up = False
                return
            self._on_quick_controls(left_click=True)
        elif lparam == win32.WM_RBUTTONUP:
            self._on_open_panel()
        else:
            super()._on_notify(wparam, lparam)
