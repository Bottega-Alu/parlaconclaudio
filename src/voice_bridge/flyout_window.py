"""Small Win32 helpers for the application's own quick-controls window."""
import ctypes
from ctypes import wintypes


def _user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    return user


def window_handle(root):
    root.update_idletasks()
    user = _user32()
    return user.GetParent(root.winfo_id()) or root.winfo_id()


def window_dpi(root):
    try:
        user = _user32()
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.GetDpiForWindow.restype = wintypes.UINT
        return user.GetDpiForWindow(window_handle(root)) or 96
    except (AttributeError, OSError):
        return 96


def work_area():
    """Work area of the monitor containing the pointer (taskbar excluded)."""
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user = _user32()
    point = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(point))
    user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user.MonitorFromPoint.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    monitor = user.MonitorFromPoint(point, 2)
    if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, user.GetSystemMetrics(0), user.GetSystemMetrics(1)


def finish_window(root, dark):
    """Native Windows 11 corner/shadow preferences, optional on Windows 10."""
    try:
        hwnd = window_handle(root)
        user = _user32()
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        # Tool window: no taskbar button for a transient tray surface.
        get_style = user.GetWindowLongPtrW
        set_style = user.SetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        set_style(hwnd, -20, (get_style(hwnd, -20) | 0x80) & ~0x40000)
        # A thin native resize frame gives DWM a real edge for its shadow.
        set_style(hwnd, -16, get_style(hwnd, -16) | 0x40000)
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                             ctypes.c_void_p, wintypes.DWORD]
        for attribute, value in ((20, int(dark)), (33, 2)):
            setting = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))
    except (AttributeError, OSError):
        pass
