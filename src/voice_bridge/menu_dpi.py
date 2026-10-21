"""DPI-native Windows menus without changing Tk or the process-wide policy."""
from contextlib import contextmanager
import ctypes
import sys


@contextmanager
def native_menu_dpi():
    """Create tray windows/menus in PMv2, then restore the calling thread.

    Changing the whole Python process would change the coordinate system of
    the existing Tk panel. Windows supports a separate context per UI thread.
    Old Windows versions or an unavailable API simply keep their current mode.
    """
    setter = None
    previous = None
    if sys.platform == "win32":
        try:
            setter = ctypes.WinDLL("user32", use_last_error=True).SetThreadDpiAwarenessContext
            setter.argtypes = [ctypes.c_void_p]
            setter.restype = ctypes.c_void_p
            previous = setter(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            pass
    try:
        yield
    finally:
        if setter is not None and previous:
            setter(previous)
