"""Small Win32 helpers for the application's own quick-controls window."""
import ctypes
from contextlib import contextmanager
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


def _grab(x, y, w, h):
    """What the user sees in a screen rectangle, as a PIL image (GDI BitBlt)."""
    from PIL import Image
    # Private DLL objects: argtypes set here must not leak into other ctypes users.
    user, gdi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    # Every handle typed as a pointer: an untyped 64-bit handle overflows ctypes' C int
    # (intermittent OverflowError -> no curtain -> flicker).
    gdi.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi.SelectObject.restype = ctypes.c_void_p
    user.GetDC.argtypes = [ctypes.c_void_p]
    gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi.BitBlt.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_void_p] + [ctypes.c_int] * 2 + [wintypes.DWORD]
    gdi.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi.DeleteDC.argtypes = [ctypes.c_void_p]
    user.GetDC.restype = ctypes.c_void_p
    user.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    screen = user.GetDC(None)
    memory = gdi.CreateCompatibleDC(screen)
    bitmap = gdi.CreateCompatibleBitmap(screen, w, h)
    try:
        gdi.SelectObject(memory, bitmap)
        gdi.BitBlt(memory, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY
        header = (ctypes.c_int32 * 10)(40, w, -h, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)  # BITMAPINFOHEADER
        pixels = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(memory, bitmap, 0, h, pixels, header, 0)
        return Image.frombuffer("RGB", (w, h), pixels.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(None, screen)


class Curtain:
    """Keep a still image of a window over it while it changes, then lift it in one step.

    Tk on Windows paints a page widget by widget (each widget is a native child
    window, and whatever was covered must be painted again), so switching or
    rebuilding a page showed every intermediate state: that was the flicker.
    The curtain is a borderless topmost window showing a screenshot of the current
    content; the change happens behind it and appears complete when it is lifted.
    """

    def __init__(self, root):
        self.root = root
        self.top = None
        self.label = None
        self.image = None
        self.active = False

    @contextmanager
    def held(self):
        if self.active or self.root.state() == "withdrawn":
            yield  # nested, or nothing on screen to protect
            return
        focus = self.root.focus_get()
        try:
            self._drop()
        except Exception:
            yield  # no curtain is better than no change
            return
        self.active = True
        try:
            yield
        finally:
            try:
                self.root.update()  # everything behind the curtain is painted now
                ctypes.windll.gdi32.GdiFlush()
            finally:
                self.active = False
                self.top.withdraw()
                if focus is not None:
                    # Showing the curtain activated it: give the keyboard back.
                    self.root.focus_force()
                    if focus.winfo_exists():
                        focus.focus_set()

    def _drop(self):
        import tkinter as tk
        from PIL import ImageTk
        root = self.root
        x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
        picture = _grab(x, y, w, h)
        if self.top is None or not self.top.winfo_exists():
            self.top = tk.Toplevel(root)
            self.top.withdraw()
            self.top.overrideredirect(True)
            self.top.attributes("-topmost", True)
            self.label = tk.Label(self.top, bd=0, highlightthickness=0, padx=0, pady=0)
            self.label.pack(fill="both", expand=True)
        self.image = ImageTk.PhotoImage(picture, master=root)
        self.label.configure(image=self.image)
        self.top.geometry(f"{w}x{h}+{x}+{y}")
        # Shown only once its picture is painted, so the curtain itself never flashes.
        self.top.attributes("-alpha", 0.0)
        self.top.deiconify()
        try:
            corner = ctypes.c_int(2)  # match the rounded corners of the window below
            ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(
                window_handle(self.top), 33, ctypes.byref(corner), ctypes.sizeof(corner))
        except (AttributeError, OSError):
            pass
        self.top.update()
        self.top.attributes("-alpha", 1.0)
        self.top.update_idletasks()


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
