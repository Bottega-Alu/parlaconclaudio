"""Shared Windows 11 look for the quick controls and the control window.

Theme and accent are read from Windows; glyphs come from the system icon font
(Segoe Fluent Icons on Windows 11, Segoe MDL2 Assets on Windows 10). All widgets
are plain Tk, created and touched only on the owning window's thread.
"""

# Icon font code points shared by both surfaces.
ICON = {
    "mic": "", "mic_on": "", "speaker": "", "mute": "",
    "music": "", "chat": "", "chip": "", "cloud": "",
    "globe": "", "file": "", "settings": "", "list": "",
    "next": "", "back": "", "check": "", "play": "",
    "shuffle": "", "home": "", "link": "", "info": "",
    "bell": "", "refresh": "", "power": "", "pulse": "",
}

_PALETTE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"


def dark_mode(surface="apps"):
    """Windows "app mode" for windows; "system" mode (taskbar, Quick Settings) for tray surfaces."""
    value = "SystemUsesLightTheme" if surface == "system" else "AppsUseLightTheme"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, value)[0] == 0
    except OSError:
        return False


def accent(dark):
    """System accent as Windows 11 uses it: Light2 on dark, Dark1 on light."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PALETTE_KEY) as key:
            palette = winreg.QueryValueEx(key, "AccentPalette")[0]
        r, g, b = palette[(1 if dark else 4) * 4:(1 if dark else 4) * 4 + 3]
        return f"#{r:02x}{g:02x}{b:02x}"
    except (OSError, ValueError, TypeError):
        return "#4cc2ff" if dark else "#005fb8"


def colors(dark):
    base = ({"surface": "#202020", "raised": "#2d2d2d", "hover": "#383838", "card": "#2b2b2b",
             "footer": "#1c1c1c", "text": "#ffffff", "muted": "#c5c5c5", "subtle": "#9a9a9a",
             "line": "#3d3d3d", "on": "#000000", "ok": "#6ccb5f", "busy": "#fce100", "rec": "#ff99a4"}
            if dark else
            {"surface": "#f3f3f3", "raised": "#fbfbfb", "hover": "#eaeaea", "card": "#fbfbfb",
             "footer": "#eeeeee", "text": "#1a1a1a", "muted": "#5c5c5c", "subtle": "#8a8a8a",
             "line": "#e0e0e0", "on": "#ffffff", "ok": "#0f7b0f", "busy": "#9d5d00", "rec": "#c42b1c"})
    base["accent"] = accent(dark)
    return base


def icon_font(root):
    import tkinter.font as tkfont
    families = set(tkfont.families(root))
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in families:
            return name
    return None


def text_font(root):
    import tkinter.font as tkfont
    return "Segoe UI Variable Text" if "Segoe UI Variable Text" in set(tkfont.families(root)) else "Segoe UI"


def pretty_pack(name):
    """Folder name of a sound pack as a readable title."""
    return name.replace("-", " ").replace("_", " ").title()


def rounded(root, width, height, radius, fill, outline=None, background=None):
    """Antialiased rounded rectangle as a PhotoImage (Tk has no native one)."""
    from PIL import Image, ImageDraw, ImageTk
    k = 3
    image = Image.new("RGBA", (width * k, height * k), background or (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, width * k - 1, height * k - 1), radius=radius * k, fill=fill,
        outline=outline or fill, width=k)
    return ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=root)


def dark_title_bar(root, dark):
    """Match the native title bar to the app theme (Windows 10 2004+)."""
    try:
        import ctypes
        from .flyout_window import window_handle
        value = ctypes.c_int(int(dark))
        hwnd = window_handle(root)
        ctypes.WinDLL("dwmapi").DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        # Repaint the frame now: a theme switch on an open window would otherwise wait for a resize.
        user = ctypes.WinDLL("user32")
        user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
    except (AttributeError, OSError):
        pass


class Slider:
    """Horizontal 0..maximum slider; `on_change` fires when the value is committed."""

    def __init__(self, parent, theme, scale, value, on_change, maximum=300, step=5):
        import tkinter as tk
        self.theme, self.scale, self.value = theme, scale, value
        self.on_change, self.maximum, self.step = on_change, maximum, step
        self.disabled = False
        self.canvas = tk.Canvas(parent, bg=parent["bg"], height=self._p(32), highlightthickness=0,
                                takefocus=1, cursor="hand2")
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.canvas.bind("<Button-1>", self._move)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", lambda event: self.on_change(self.value))
        self.canvas.bind("<Left>", lambda event: self._key(-step))
        self.canvas.bind("<Right>", lambda event: self._key(step))
        self.canvas.bind("<FocusIn>", lambda event: self.draw())
        self.canvas.bind("<FocusOut>", lambda event: self.draw())
        self.listeners = []

    def _p(self, value):
        return round(value * self.scale)

    def set(self, value, disabled=None):
        self.value = max(0, min(self.maximum, value))
        if disabled is not None:
            self.disabled = disabled
        self.draw()

    def _notify(self):
        for listener in self.listeners:
            listener(self.value)

    def _move(self, event):
        self.canvas.focus_set()
        start, end = self._p(10), self.canvas.winfo_width() - self._p(10)
        self.set(round((event.x - start) / max(1, end - start) * self.maximum))
        self._notify()

    def _key(self, step):
        self.set(self.value + step)
        self._notify()
        self.on_change(self.value)

    def draw(self):
        c, canvas = self.theme, self.canvas
        if not canvas.winfo_exists():
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), self._p(160))
        x0, x1, y = self._p(10), width - self._p(10), self._p(16)
        x = x0 + (x1 - x0) * self.value / self.maximum
        canvas.create_line(x0, y, x1, y, fill=c["line"], width=self._p(4), capstyle="round")
        if not self.disabled:
            canvas.create_line(x0, y, x, y, fill=c["accent"], width=self._p(4), capstyle="round")
        outer, inner = self._p(10), self._p(5)
        canvas.create_oval(x - outer, y - outer, x + outer, y + outer, fill=c["raised"], outline=c["line"])
        canvas.create_oval(x - inner, y - inner, x + inner, y + inner,
                           fill=c["subtle"] if self.disabled else c["accent"], outline="")
        if canvas.focus_get() is canvas:
            canvas.create_rectangle(1, 1, width - 1, self._p(31), outline=c["text"], dash=(1, 2))


class Switch:
    """Windows 11 toggle switch drawn on a canvas; keyboard and mouse accessible."""

    def __init__(self, parent, theme, scale, command, value=False):
        import tkinter as tk
        self.theme, self.scale, self.command, self.value = theme, scale, command, value
        self.canvas = tk.Canvas(parent, width=self._p(44), height=self._p(24), bg=parent["bg"],
                                highlightthickness=0, takefocus=1, cursor="hand2")
        self.canvas.bind("<Button-1>", lambda event: self.toggle())
        self.canvas.bind("<space>", lambda event: self.toggle())
        self.canvas.bind("<Return>", lambda event: self.toggle())
        self.canvas.bind("<FocusIn>", lambda event: self.draw())
        self.canvas.bind("<FocusOut>", lambda event: self.draw())
        self.draw()

    def _p(self, value):
        return round(value * self.scale)

    def set(self, value):
        if value != self.value:
            self.value = value
            self.draw()

    def toggle(self):
        self.set(not self.value)
        self.command(self.value)

    def draw(self):
        c, canvas = self.theme, self.canvas
        canvas.delete("all")
        w, h = self._p(44), self._p(22)
        fill = c["accent"] if self.value else canvas["bg"]
        outline = c["accent"] if self.value else c["muted"]
        self._image = rounded(canvas, w - 2, h - 2, (h - 2) // 2, fill, outline)
        canvas.create_image(1, 2, image=self._image, anchor="nw")
        knob = self._p(6 if self.value else 5)
        x = w - h // 2 - 1 if self.value else h // 2 + 1
        y = h // 2 + 2
        canvas.create_oval(x - knob, y - knob, x + knob, y + knob,
                           fill=c["on"] if self.value else c["muted"], outline="")
        if canvas.focus_get() is canvas:
            canvas.create_rectangle(0, 0, w + 1, self._p(24) - 1, outline=c["text"], dash=(1, 2))
