"""Windows tray clicks: left opens the quick controls, right opens the app window."""
import pystray
from pystray._util import win32


class FlyoutIcon(pystray.Icon):
    def __init__(self, *args, on_quick_controls, on_open_panel, **kwargs):
        self._on_quick_controls = on_quick_controls
        self._on_open_panel = on_open_panel
        super().__init__(*args, **kwargs)

    def _on_notify(self, wparam, lparam):
        if lparam == win32.WM_LBUTTONUP:
            self._on_quick_controls(left_click=True)
        elif lparam == win32.WM_RBUTTONUP:
            self._on_open_panel()
        else:
            super()._on_notify(wparam, lparam)
