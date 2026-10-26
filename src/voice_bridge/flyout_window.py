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
        area = [info.work.left, info.work.top, info.work.right, info.work.bottom]
        return tuple(_without_autohide_taskbar(area, info.monitor))
    return 0, 0, user.GetSystemMetrics(0), user.GetSystemMetrics(1)


def _without_autohide_taskbar(area, monitor):
    """An auto-hide taskbar is not excluded from the work area, yet it pops
    up over the flyout: keep its edge free, as Windows Quick Settings does."""
    class AppBarData(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uCallbackMessage", wintypes.UINT), ("uEdge", wintypes.UINT),
                    ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]

    try:
        shell = ctypes.WinDLL("shell32")
        shell.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(AppBarData)]
        shell.SHAppBarMessage.restype = ctypes.c_size_t
        data = AppBarData(cbSize=ctypes.sizeof(AppBarData))
        if not shell.SHAppBarMessage(4, ctypes.byref(data)) & 1:  # ABM_GETSTATE, ABS_AUTOHIDE
            return area
        if not shell.SHAppBarMessage(5, ctypes.byref(data)):  # ABM_GETTASKBARPOS
            return area
    except (AttributeError, OSError):
        return area
    rc = data.rc
    if rc.right <= monitor.left or rc.left >= monitor.right or rc.bottom <= monitor.top or rc.top >= monitor.bottom:
        return area  # taskbar lives on another monitor
    size = (rc.right - rc.left) if data.uEdge in (0, 2) else (rc.bottom - rc.top)
    index = {0: 0, 1: 1, 2: 2, 3: 3}.get(data.uEdge)  # left, top, right, bottom
    if index is None:
        return area
    area[index] += size if index < 2 else -size
    return area


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
