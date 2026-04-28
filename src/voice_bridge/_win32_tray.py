"""Win32 helper to locate the parlaconclaudio tray icon rect.

Used by gesture_listener.MouseGestureDetector to decide whether a
left-click happened on our tray icon (vs anywhere else on screen).

Strategy: enumerate Shell_TrayWnd > TrayNotifyWnd > SysPager >
ToolbarWindow32, read each button's tooltip via TB_GETBUTTONTEXTW,
match against tooltip prefix, return TB_GETITEMRECT.

Fallback: if the icon is in the Windows overflow popup (hidden),
the lookup returns None and is_inside_tray_icon() returns False.
The caller logs a warning once and disables mouse long-press.

All Win32 calls are wrapped in try/except — failures degrade
gracefully (return None / False).
"""
import ctypes
import logging
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

TOOLTIP_PREFIX = "parlaconclaudio"
_CACHE_TTL_S = 2.0
_RECT_CACHE: dict = {}  # tooltip_prefix -> (rect_or_None, expires_at)

# Win32 constants
_TB_BUTTONCOUNT = 0x418
_TB_GETBUTTON = 0x417
_TB_GETBUTTONTEXTW = 0x44B
_TB_GETITEMRECT = 0x41D
_PROCESS_VM_READ = 0x10
_PROCESS_QUERY_INFORMATION = 0x400


def is_inside_tray_icon(x: int, y: int) -> bool:
    """Return True if (x, y) is inside our tray icon rect."""
    rect = _get_cached_rect(TOOLTIP_PREFIX)
    if rect is None:
        return False
    left, top, right, bottom = rect
    return left <= x <= right and top <= y <= bottom


def _get_cached_rect(tooltip_prefix: str):
    now = time.monotonic()
    cached = _RECT_CACHE.get(tooltip_prefix)
    if cached is not None and cached[1] > now:
        return cached[0]
    rect = _find_tray_icon_rect(tooltip_prefix)
    _RECT_CACHE[tooltip_prefix] = (rect, now + _CACHE_TTL_S)
    return rect


def _find_tray_icon_rect(tooltip_prefix: str):
    """Enumerate the system tray and return rect of icon matching tooltip prefix.

    Returns (left, top, right, bottom) in screen coordinates, or None.
    """
    try:
        user32 = ctypes.windll.user32
        find_window_ex = user32.FindWindowExW
        find_window_ex.argtypes = [wintypes.HWND, wintypes.HWND,
                                    wintypes.LPCWSTR, wintypes.LPCWSTR]
        find_window_ex.restype = wintypes.HWND

        tray = find_window_ex(None, None, "Shell_TrayWnd", None)
        if not tray:
            return None
        notify = find_window_ex(tray, None, "TrayNotifyWnd", None)
        if not notify:
            return None
        pager = find_window_ex(notify, None, "SysPager", None)
        toolbar = None
        if pager:
            toolbar = find_window_ex(pager, None, "ToolbarWindow32", None)
        # Fallback: some Win11 builds have the toolbar directly under TrayNotifyWnd
        if not toolbar:
            toolbar = find_window_ex(notify, None, "ToolbarWindow32", None)
        if not toolbar:
            return None

        return _scan_toolbar_for_tooltip(toolbar, tooltip_prefix)
    except Exception as e:
        logger.debug(f"Win32 tray rect lookup failed: {e}")
        return None


def _scan_toolbar_for_tooltip(toolbar_hwnd: int, prefix: str):
    """Iterate buttons in toolbar, match tooltip prefix, return rect."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    send_message = user32.SendMessageW
    send_message.argtypes = [wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM]
    send_message.restype = wintypes.LPARAM

    count = send_message(toolbar_hwnd, _TB_BUTTONCOUNT, 0, 0)
    if count <= 0:
        return None

    # Need to read memory in toolbar's process — use cross-process buffer trick
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(toolbar_hwnd, ctypes.byref(pid))
    h_proc = kernel32.OpenProcess(_PROCESS_VM_READ | _PROCESS_QUERY_INFORMATION,
                                    False, pid)
    if not h_proc:
        return None
    try:
        # Allocate buffer in remote process for tooltip text
        remote_buf = kernel32.VirtualAllocEx(h_proc, 0, 512, 0x1000, 0x40)
        if not remote_buf:
            return None
        try:
            for i in range(count):
                # Get tooltip text into remote buffer
                length = send_message(toolbar_hwnd, _TB_GETBUTTONTEXTW,
                                      i, remote_buf)
                if length <= 0:
                    continue
                local_buf = (ctypes.c_wchar * 256)()
                bytes_read = ctypes.c_size_t(0)
                kernel32.ReadProcessMemory(h_proc, remote_buf, local_buf,
                                           min(length * 2 + 2, 510),
                                           ctypes.byref(bytes_read))
                tooltip = local_buf.value
                if not tooltip.startswith(prefix):
                    continue
                # Found the icon — get its rect
                rect_remote = kernel32.VirtualAllocEx(h_proc, 0, 16, 0x1000, 0x40)
                if not rect_remote:
                    continue
                try:
                    ok = send_message(toolbar_hwnd, _TB_GETITEMRECT, i, rect_remote)
                    if not ok:
                        continue
                    rect_local = wintypes.RECT()
                    kernel32.ReadProcessMemory(h_proc, rect_remote,
                                               ctypes.byref(rect_local), 16,
                                               ctypes.byref(bytes_read))
                    pt_tl = wintypes.POINT(rect_local.left, rect_local.top)
                    pt_br = wintypes.POINT(rect_local.right, rect_local.bottom)
                    user32.ClientToScreen(toolbar_hwnd, ctypes.byref(pt_tl))
                    user32.ClientToScreen(toolbar_hwnd, ctypes.byref(pt_br))
                    return (pt_tl.x, pt_tl.y, pt_br.x, pt_br.y)
                finally:
                    kernel32.VirtualFreeEx(h_proc, rect_remote, 0, 0x8000)
            return None
        finally:
            kernel32.VirtualFreeEx(h_proc, remote_buf, 0, 0x8000)
    finally:
        kernel32.CloseHandle(h_proc)
