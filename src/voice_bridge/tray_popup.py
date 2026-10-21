"""Windows tray clicks open our quick controls; the full native menu remains."""
import pystray
from pystray._util import win32

_SHOW_FULL_MENU = 0x8000 + 47


class FlyoutIcon(pystray.Icon):
    def __init__(self, *args, on_quick_controls, **kwargs):
        self._on_quick_controls = on_quick_controls
        super().__init__(*args, **kwargs)
        self._message_handlers[_SHOW_FULL_MENU] = self._full_menu

    def _on_notify(self, wparam, lparam):
        if lparam in (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP):
            self._on_quick_controls(left_click=lparam == win32.WM_LBUTTONUP)
        else:
            super()._on_notify(wparam, lparam)

    def _full_menu(self, wparam, lparam):
        super()._on_notify(0, win32.WM_RBUTTONUP)

    def show_full_menu(self):
        # Keep TrackPopupMenu on the native tray thread, not on the Tk thread.
        win32.PostMessage(self._hwnd, _SHOW_FULL_MENU, 0, 0)
