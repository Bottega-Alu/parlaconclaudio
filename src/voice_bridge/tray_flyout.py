"""Lightweight quick controls, using the Windows 11 Quick Settings visual language.

A grid of tiles opens list pages (sound packs, announcements, microphone,
recognition) shaped like the Windows Wi-Fi list.

The whole surface is ONE Tk canvas. Tk on Windows paints every widget as its own
native child window, piece by piece: switching or rebuilding a page made of widgets
showed each intermediate state (the flicker that needed a screenshot "curtain").
A canvas draws its items off screen and copies the result in one step, so here
every change - hover, page switch, theme, language - is a full redraw of a scene
that appears complete. Hover, focus, keyboard and scrolling are handled by the
scene's hit list. Tk and all its state belong to one dedicated thread;
application actions run in workers.
"""
import logging
import queue
import threading
import time

from .i18n import _, N_, APP_NAME
from . import i18n
from .menu_dpi import native_menu_dpi
from . import flyout_window
from . import fluent
from .fluent import ICON
from .microphones import choices as microphone_choices

logger = logging.getLogger(__name__)

WIDTH = 380
PAD = 16
SCROLL_STEP = 40

TTS_MODE_DETAILS = {
    "full": N_("Suono e voce a ogni avviso"),
    "semi-silent": N_("Voce solo quando l'agente termina"),
    "silent": N_("Solo suoni, nessuna voce"),
}
STT_MODE_DETAILS = {
    "auto": N_("GPU locale se disponibile, altrimenti cloud"),
    "local": N_("Richiede una GPU NVIDIA compatibile"),
    "cloud_groq": N_("Richiede una chiave API Groq"),
    "cloud_deepgram": N_("Richiede una chiave API Deepgram"),
}
STATE_LABELS = {"idle": N_("Pronto per dettare"), "recording": N_("Registrazione in corso"),
                "transcribing": N_("Trascrizione in corso"), "loading": N_("Caricamento del motore…"),
                "output": N_("Invio del testo…")}

THEME_LABELS = {"system": N_("Sistema"), "light": N_("Chiaro"), "dark": N_("Scuro")}
LIST_PAGES = ("packs", "voice", "mic", "engine")


def _dark_mode():
    # Next to the taskbar, like Quick Settings: follows the system mode, not the app mode.
    return fluent.dark_mode("system")


def _t(text):
    """Translate a display-only label (empty stays empty)."""
    return _(text) if text else text


def _short(text, limit=17):
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class TrayFlyout:
    def __init__(self, tray):
        self.tray = tray
        self._commands = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._working = False
        self._pending_actions = []
        self._page = "main"
        self._expanded = None
        self._config = {}
        self._muted = False
        self.volume = 100
        self.voice_volume = 100
        self.mics = {}
        self._default_mic = self._missing_mic = None
        self.packs = []
        self.root = None
        self.canvas = None
        # Scene state: what the pointer and the keyboard are on.
        self._hits = []          # [(key, x0, y0, x1, y1)] of the scene on screen, topmost last
        self._order = []         # focusable keys in Tab order
        self._hover = None
        self._focus = None
        self._keyboard = False   # focus ring only after keyboard use, like Windows
        self._pressed = None
        self._drag = None
        self._scroll = 0
        self._list_view = (0, 0, 0)   # top, bottom, content height of the list page
        self._row_boxes = {}
        self._error_until = 0.0
        self._state_signature = None
        self._fonts = {}
        self._images = {}
        self.rows = []
        self.tiles = {}

    def show(self):
        self._start(visible=True)

    def prewarm(self):
        """Build the hidden window ahead of the first click."""
        self._start(visible=False)

    def _start(self, visible):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, args=(visible,),
                                                name="tray-quick-controls", daemon=True)
                self._thread.start()
            elif visible:
                self._commands.put("show")
                self._wake()

    def stop(self):
        self._commands.put("stop")
        self._wake()

    def _wake(self):
        """Handle queued commands now instead of at the next hidden poll (up to 1 s)."""
        try:
            self.root.event_generate("<<FlyoutWake>>", when="tail")
        except Exception:
            pass  # not built yet or already gone: the poll still picks the command up

    def _run(self, visible=True):
        try:
            with native_menu_dpi():
                self._build()
                if visible or not self._commands.empty():
                    self._show()
                self.root.mainloop()
        except Exception:
            logger.exception("Quick controls could not open")
            self.tray._open_panel(None, None)

    def _p(self, value):
        return round(value * self.scale)

    # ── construction ────────────────────────────────────────────────

    def _build(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(_("{app} - Comandi rapidi").format(app=APP_NAME))
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self._hide)
        self.root.bind("<FocusOut>", lambda event: self.root.after(180, self._dismiss_if_outside))
        self.root.bind("<FocusIn>", lambda event: setattr(self, "_had_focus", True))
        self.root.bind("<<FlyoutWake>>", lambda event: self._drain())
        self._dpi = flyout_window.window_dpi(self.root)
        self.scale = self._dpi / 96
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, takefocus=1)
        self.canvas.pack(fill="both", expand=True)
        canvas = self.canvas
        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Leave>", lambda event: self._set_hover(None))
        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<MouseWheel>", self._wheel)
        canvas.bind("<Configure>", lambda event: self._render())
        for sequence, handler in (("<Escape>", lambda: self._escape()), ("<Tab>", lambda: self._move_focus(1)),
                                  ("<Shift-Tab>", lambda: self._move_focus(-1)), ("<Return>", self._activate_focus),
                                  ("<space>", self._activate_focus), ("<Up>", lambda: self._arrow(-1)),
                                  ("<Down>", lambda: self._arrow(1)), ("<Left>", lambda: self._arrow(-1, True)),
                                  ("<Right>", lambda: self._arrow(1, True))):
            canvas.bind(sequence, lambda event, handler=handler: (handler(), "break")[1])
        self._build_surface()
        self.root.after(150, self._tick)

    def _build_surface(self):
        """Colours, fonts and scale; the scene is drawn from them on every render."""
        self.root.tk.call("tk", "scaling", self._dpi / 72)
        self.dark = _dark_mode()
        self.colors = fluent.colors(self.dark)
        self._theme = (self.dark, self.colors["accent"])
        self.font = fluent.text_font(self.root)
        self.icons = fluent.icon_font(self.root)
        self._fonts, self._images = {}, {}
        self.root.configure(bg=self.colors["surface"])
        self.canvas.configure(bg=self.colors["surface"])
        self._main_height = self._measure_main()
        self._render()

    # ── drawing primitives ──────────────────────────────────────────

    def _font(self, size, weight="normal", icons=False):
        import tkinter.font as tkfont
        key = (icons, size, weight)
        font = self._fonts.get(key)
        if font is None:
            family = self.icons if icons else self.font
            font = self._fonts[key] = tkfont.Font(root=self.root, family=family, size=size, weight=weight)
        return font

    def _line(self, size, weight="normal"):
        return self._font(size, weight).metrics("linespace")

    def _text(self, x, y, text, size=9, fill=None, anchor="nw", weight="normal", width=None):
        return self.canvas.create_text(x, y, text=text, font=self._font(size, weight), anchor=anchor,
                                       fill=fill or self.colors["text"], width=width or 0)

    def _fit(self, text, size, width, weight="normal"):
        """*text* cut with an ellipsis to *width* pixels (canvas text does not clip)."""
        font = self._font(size, weight)
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text.rstrip() + "…"

    def _glyph(self, x, y, name, size=12, fill=None):
        if self.icons:
            self.canvas.create_text(x, y, text=ICON[name], font=self._font(size, icons=True),
                                    fill=fill or self.colors["text"])

    def _box(self, x, y, w, h, radius, fill, outline=None):
        """Antialiased rounded rectangle, cached per size and colour."""
        w, h = max(2, round(w)), max(2, round(h))
        key = (w, h, radius, fill, outline)
        image = self._images.get(key)
        if image is None:
            image = self._images[key] = fluent.rounded(self.root, w, h, radius, fill, outline)
        self.canvas.create_image(round(x), round(y), image=image, anchor="nw")

    def _size(self):
        """Drawable size: the native frame that carries the shadow takes a few pixels of the window."""
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        return (width if width > 1 else self._p(WIDTH)), (height if height > 1 else self._main_height)

    def _hit(self, key, x0, y0, x1, y1, focusable=True):
        self._hits.append((key, x0, y0, x1, y1))
        if focusable and key not in self._order:
            self._order.append(key)

    def _focused(self, key):
        return self._keyboard and self._focus == key

    def _ring(self, x0, y0, x1, y1):
        self._box(x0, y0, x1 - x0, y1 - y0, self._p(6), None, self.colors["text"])

    def _icon_button(self, key, x, y, name, disabled=False, bg=None):
        c = self.colors
        w, h = self._p(36), self._p(32)
        if self._hover == key and not disabled:
            self._box(x, y, w, h, self._p(4), c["hover"])
        if self._focused(key):
            self._ring(x, y, x + w, y + h)
        if self.icons:
            self._glyph(x + w / 2, y + h / 2, name, 12, c["subtle"] if disabled else c["text"])
        else:
            self._text(x + w / 2, y + h / 2, name, 8, anchor="center")
        self._hit(key, x, y, x + w, y + h, focusable=not disabled)
        return w

    def _text_button(self, key, right, y, text, primary=False, enabled=True):
        """Button whose right edge is at *right*; returns its left edge."""
        c = self.colors
        font = self._font(9)
        w, h = font.measure(text) + self._p(28), self._p(30)
        x = right - w
        if primary:
            fill, fg = (c["accent"], c["on"]) if enabled else (c["line"], c["subtle"])
        else:
            fill, fg = (c["line"] if self._hover == key else c["hover"]), c["text"]
        self._box(x, y, w, h, self._p(4), fill, c["line"] if not primary else fill)
        if self._focused(key):
            self._ring(x - self._p(2), y - self._p(2), right + self._p(2), y + h + self._p(2))
        self._text(x + w / 2, y + h / 2, text, 9, fg, anchor="center")
        if enabled:
            self._hit(key, x, y, right, y + h)
        return x

    # ── scene ───────────────────────────────────────────────────────

    def _render(self):
        """Redraw the whole current page. The canvas paints off screen: it appears in one step."""
        if self.canvas is None:
            return
        self.canvas.delete("all")
        self._hits, self._order = [], []
        if self._page == "main":
            self._draw_main()
        else:
            self._draw_list(self._page)
        if self._focus not in self._order:
            self._focus = self._order[0] if self._order else None
        self._update_cursor()

    def _caps(self):
        from .dictation_trigger import SHORT_MOUSE_BUTTONS, key_caps
        trigger = self.tray.dictation_trigger()
        if trigger.kind == "mouse":
            return [_(SHORT_MOUSE_BUTTONS[trigger.button])]
        return key_caps(trigger)

    def _tile_values(self):
        from .tray_icon import TTS_MODES, STT_MODES
        config = self._config
        pack = config.get("sound_pack", "r2d2")
        mode = config.get("tts_mode", "full")
        mic = next((name for name, value in self.mics.items()
                    if value == config.get("mic_device_name")), _("Predefinito"))
        return {
            "audio": _("Disattivato") if self._muted else _("Suoni {volume}% · voci {voices}%").format(
                volume=self.volume, voices=self.voice_volume),
            "packs": _("Casuale") if pack == "random" else fluent.pretty_pack(pack),
            "voice": next((_(label) for label, value in TTS_MODES.items() if value == mode), mode),
            "mic": _t(mic.split(" [")[0]),
            "engine": _t(STT_MODES.get(config.get("stt_mode", "auto"), "")).split(" (")[0],
            "theme": _(THEME_LABELS[fluent.theme()]),
            "language": i18n.LANGUAGES.get(config.get("ui_language", i18n.SOURCE), _("Sistema")),
        }

    def _tile_specs(self):
        values = self._tile_values()
        recording = self.tray._state == "recording"
        return [
            ("record", "mic", _("Dettatura"), _("In ascolto") if recording else _("Avvia"), None),
            ("audio", "speaker", _("Audio"), values["audio"], None),
            ("packs", "music", _("Suoni"), values["packs"], "packs"),
            ("voice", "chat", _("Avvisi vocali"), values["voice"], "voice"),
            ("mic", "mic", _("Microfono"), values["mic"], "mic"),
            ("engine", "chip", _("Riconoscimento"), values["engine"], "engine"),
            ("theme", "settings", _("Tema"), values["theme"], None),
            ("language", "globe", _("Lingua"), values["language"], None),
        ]

    def _status(self):
        """(text, colour) of the status line, and whether dictation and files can start."""
        c = self.colors
        state, batch = self.tray._state, getattr(self.tray, "batch_busy", False)
        if time.monotonic() < self._error_until:
            text = _("Operazione non riuscita. Riprova.")
        elif batch:
            text = _("Trascrizione file in corso")
        else:
            text = _(STATE_LABELS[state]) if state in STATE_LABELS else state
        color = c["busy"] if batch else {"recording": c["rec"], "transcribing": c["busy"], "loading": c["busy"],
                                         "output": c["busy"]}.get(state, c["ok"])
        record_enabled = bool(self.tray._on_toggle_recording) and state in ("idle", "recording") and not batch
        return text, color, record_enabled, state == "idle" and not batch

    def _engine_label(self):
        return _("Motore attivo: {engine}").format(engine=self.tray._get_stt_status({})["active_label"])

    def _tile_row_height(self):
        return self._p(48) + self._p(5) + self._line(9) + self._line(8) + self._p(10)

    def _footer_height(self):
        return self._p(8) * 2 + self._p(32)

    def _measure_main(self):
        """Height of the tiles page: every page uses it, so switching never resizes the window."""
        rows = -(-len(self._tile_specs()) // 3)
        return (self._p(PAD) + self._line(12, "bold") + self._p(4) + self._line(9) + self._p(14)
                + rows * self._tile_row_height() + self._p(4) + 2 * self._p(32) + self._p(6)
                + self._line(8) + self._p(PAD) + 1 + self._footer_height())

    def _draw_main(self):
        from .tray_icon import MAX_VOLUME
        c, p = self.colors, self._p
        width = self._size()[0]
        left, right = p(PAD), width - p(PAD)
        y = p(PAD)
        self._text(left, y, APP_NAME, 12, weight="bold")
        y += self._line(12, "bold") + p(4)
        text, dot, record_enabled, files_enabled = self._status()
        line = self._line(9)
        size = p(8)
        self.canvas.create_oval(left + 1, y + line / 2 - size / 2, left + 1 + size, y + line / 2 + size / 2,
                                fill=dot, outline="")
        self._text(left + p(17), y, text, 9, c["muted"])
        y += line + p(14)

        gap = p(8)
        tile_w = (right - left - 2 * gap) / 3
        row_h = self._tile_row_height()
        self.tiles = {}
        for index, (key, icon, title, value, page) in enumerate(self._tile_specs()):
            x = left + (index % 3) * (tile_w + gap)
            top = y + (index // 3) * row_h
            enabled = record_enabled if key == "record" else True
            active = (self.tray._state == "recording") if key == "record" else (
                not self._muted if key == "audio" else False)
            self.tiles[key] = {"value": _short(value), "active": active, "enabled": enabled}
            self._draw_tile(("tile", key), x, top, tile_w, icon, title, _short(value), page, active, enabled)
        y += -(-len(self.tiles) // 3) * row_h + p(4)

        self._icon_button(("button", "mute"), left - p(4), y, "mute" if self._muted else "speaker")
        self._slider(("slider", "volume"), left + p(36), right - p(44), y + p(16), self.volume, MAX_VOLUME)
        self._text(right, y + p(16), f"{self.volume}%", 9, c["muted"], anchor="e")
        y += p(32)
        self._glyph(left - p(4) + p(18), y + p(16), "chat", 12)
        self._hit(("label", "voices"), left - p(4), y, left + p(32), y + p(32), focusable=False)
        self._slider(("slider", "voices"), left + p(36), right - p(44), y + p(16), self.voice_volume, MAX_VOLUME)
        self._text(right, y + p(16), f"{self.voice_volume}%", 9, c["muted"], anchor="e")
        y += p(32) + p(6)
        self._text(left, y, self._engine_label(), 8, c["subtle"])
        y += self._line(8) + p(PAD)

        self._footer(y)
        buttons = (("files", "file", not files_enabled), ("settings", "settings", False), ("power", "power", False))
        x = width - p(PAD - 4)
        top = y + 1 + p(8)
        hint = None
        for key, icon, disabled in reversed(buttons):
            x -= p(36)
            self._icon_button(("button", key), x, top, icon, disabled=disabled)
        for key, _icon, _disabled in buttons:
            if self._hover == ("button", key) or self._focused(("button", key)):
                hint = self._hint(key)
        if self._hover == ("button", "mute") or self._focused(("button", "mute")):
            hint = self._hint("mute")
        if self._hover == ("label", "voices"):
            hint = _("Volume delle voci")
        middle = top + p(16)
        if hint:
            self._text(p(PAD), middle, hint, 9, c["muted"], anchor="w")
        else:
            self._keycaps(p(PAD), middle)
        # Tab order of the page, as it reads.
        order = [k for k in self._order]
        self._order = ([k for k in order if k[0] == "tile"] + [("button", "mute"), ("slider", "volume"),
                       ("slider", "voices")] + [k for k in order if k[0] == "button" and k[1] != "mute"])
        self._order = [k for k in self._order if k in order]

    def _hint(self, key):
        return {"mute": _("Attiva o disattiva l'audio"), "files": _("Trascrivi file audio"),
                "settings": _("Impostazioni"), "power": _("Esci da {app}").format(app=APP_NAME)}[key]

    def _keycaps(self, x, middle):
        c, p = self.colors, self._p
        font = self._font(8)
        height = self._line(8) + p(2)
        for index, key in enumerate(self._caps()):
            if index:
                self._text(x + p(2), middle, "+", 8, c["subtle"], anchor="w")
                x += font.measure("+") + p(4)
            w = font.measure(key) + p(12)
            self._box(x, middle - height / 2, w, height, p(3), c["raised"], c["line"])
            self._text(x + w / 2, middle, key, 8, anchor="center")
            x += w

    def _footer(self, y):
        c, width = self.colors, self._size()[0]
        self.canvas.create_line(0, y, width, y, fill=c["line"])
        self.canvas.create_rectangle(0, y + 1, width, y + 1 + self._footer_height() + self._p(40),
                                     fill=c["footer"], outline="")

    def _draw_tile(self, key, x, y, w, icon, title, value, page, active, enabled):
        """Quick Settings tile: rounded button above a two-line caption."""
        c, p = self.colors, self._p
        h = p(48)
        if active:
            fill, fg, outline = c["accent"], c["on"], c["accent"]
        else:
            fill, fg, outline = (c["hover"] if self._hover == key and enabled else c["raised"]), c["text"], c["line"]
        if not enabled:
            fg = c["subtle"]
        self._box(x, y, w, h, p(6), fill, c["text"] if self._focused(key) else outline)
        name = key[1]
        glyph = "mute" if name == "audio" and not active else "mic_on" if name == "record" and active else icon
        if page:
            self._glyph(x + w * 0.38, y + h / 2, glyph, 13, fg)
            self.canvas.create_line(x + w * 0.68, y + h * 0.3, x + w * 0.68, y + h * 0.7,
                                    fill=fg if active else c["line"])
            self._glyph(x + w * 0.84, y + h / 2, "next", 8, fg)
        else:
            self._glyph(x + w / 2, y + h / 2, glyph, 13, fg)
        if not self.icons:
            self._text(x + w / 2, y + h / 2, title, 9, fg, anchor="center")
        caption = y + h + p(5)
        self._text(x + w / 2, caption, title, 9, anchor="n")
        self._text(x + w / 2, caption + self._line(9), value, 8, c["subtle"], anchor="n")
        self._hit(key, x, y, x + w, y + h, focusable=enabled)

    def _slider(self, key, x0, x1, y, value, maximum):
        c, p = self.colors, self._p
        start, end = x0 + p(10), x1 - p(10)
        x = start + (end - start) * value / maximum
        disabled = self._muted
        self.canvas.create_line(start, y, end, y, fill=c["line"], width=p(4), capstyle="round")
        if not disabled:
            self.canvas.create_line(start, y, x, y, fill=c["accent"], width=p(4), capstyle="round")
        outer, inner = p(10), p(7) if self._hover == key or self._drag == key else p(5)
        self.canvas.create_oval(x - outer, y - outer, x + outer, y + outer, fill=c["raised"], outline=c["line"])
        self.canvas.create_oval(x - inner, y - inner, x + inner, y + inner,
                                fill=c["subtle"] if disabled else c["accent"], outline="")
        if self._focused(key):
            self._ring(x0, y - p(15), x1, y + p(15))
        self._hit(key, x0, y - p(16), x1, y + p(16))
        self._slider_track = getattr(self, "_slider_track", {})
        self._slider_track[key] = (start, end, maximum)

    # ── list pages, shaped like the Windows Wi-Fi list ──────────────

    def _page_spec(self, page):
        from .tray_icon import TTS_MODES, STT_MODES
        config = self._config
        if page == "packs":
            items = [("random", _("Casuale"), _("Un pacchetto diverso a ogni avviso"), "shuffle")]
            items += [(name, fluent.pretty_pack(name), _("{count} suoni").format(count=count), "music")
                      for name, count in self.packs]
            return (_("Pacchetti suoni"), items, config.get("sound_pack", "r2d2"), True,
                    _("Ogni avviso di Claude Code e Codex usa un suono del pacchetto scelto."))
        if page == "voice":
            items = [(value, _(label), _t(TTS_MODE_DETAILS.get(value, "")), "chat")
                     for label, value in TTS_MODES.items()]
            return (_("Avvisi vocali"), items, config.get("tts_mode", "full"), False,
                    _("Come vengono annunciati la fine di un'attività e le richieste di permesso."))
        if page == "mic":
            def detail(value):
                if value is None:
                    return _("Segue l'impostazione di Windows")
                if value == self._missing_mic:
                    return _("Non collegato")
                return _("Predefinito di Windows") if value == self._default_mic else ""
            items = [(value, _t(name), detail(value), "mic") for name, value in self.mics.items()]
            return (_("Microfono"), items, config.get("mic_device_name"), False,
                    _("Il dispositivo usato per la dettatura."))
        items = [(key, _t(label).split(" (")[0], _t(STT_MODE_DETAILS.get(key, "")), "cloud" if key.startswith("cloud") else "chip")
                 for key, label in STT_MODES.items()]
        return (_("Riconoscimento"), items, config.get("stt_mode", "auto"), False,
                _("Dove viene trascritta la voce."))

    def _row_height(self, expanded):
        p = self._p
        base = p(8) + self._line(10, "bold") + self._line(8) + p(8)
        return base + (p(10) + p(30) + p(2) if expanded else 0)

    def _draw_list(self, page):
        c, p = self.colors, self._p
        width, height = self._size()
        title, items, current, expandable, note = self._page_spec(page)
        self.rows = [{"key": key, "label": label, "detail": detail, "icon": icon, "current": key == current}
                     for key, label, detail, icon in items]
        header = p(12) * 2 + p(32)
        note_top = header
        note_height = self._font(8).metrics("linespace") * self._note_lines(note)
        top = note_top + note_height + p(8)
        bottom = height - 1 - self._footer_height()
        rows_height = sum(self._row_height(expandable and row["key"] == self._expanded) + p(2) for row in self.rows)
        self._list_view = (top, bottom, rows_height)
        self._scroll = max(0, min(self._scroll, rows_height - (bottom - top)))

        # Rows first; header and footer are drawn over them and mask what scrolls out.
        y = top - self._scroll
        left, right = p(PAD - 8), width - p(PAD - 8)
        self._row_boxes = {}
        for row in self.rows:
            expanded = expandable and row["key"] == self._expanded
            h = self._row_height(expanded)
            self._draw_row(page, row, left, y, right, h, expandable, expanded, top, bottom)
            self._row_boxes[row["key"]] = (y + self._scroll, y + self._scroll + h)
            y += h + p(2)

        self.canvas.create_rectangle(0, 0, width, top, fill=c["surface"], outline="")
        self._icon_button(("back",), p(PAD - 6), p(12), "back")
        self._text(p(PAD - 6) + p(36) + p(6), p(12) + p(16), title, 12, anchor="w", weight="bold")
        self._text(p(PAD), note_top, note, 8, c["subtle"], width=width - 2 * p(PAD))
        self._footer(bottom)
        link = _("Altre impostazioni")
        font = self._font(9)
        middle = bottom + 1 + self._footer_height() / 2
        x = p(PAD)
        color = c["accent"]
        self.canvas.create_text(x, middle, text=link, anchor="w", fill=color,
                                font=(self.font, 9, "underline"))
        w = font.measure(link)
        if self._focused(("link",)):
            self._ring(x - p(4), middle - p(14), x + w + p(4), middle + p(14))
        self._hit(("link",), x, middle - p(12), x + w, middle + p(12))
        # Tab order: back, rows (with their buttons), link.
        order = self._order
        self._order = [("back",)] + [k for k in order if k[0] in ("row", "listen", "use")] + [("link",)]

    def _note_lines(self, note):
        """Lines the note wraps to at the page width (Tk wraps on word boundaries)."""
        item = self.canvas.create_text(0, 0, text=note, font=self._font(8), anchor="nw",
                                       width=self._size()[0] - 2 * self._p(PAD))
        x0, y0, x1, y1 = self.canvas.bbox(item)
        self.canvas.delete(item)
        return max(1, round((y1 - y0) / self._font(8).metrics("linespace")))

    def _draw_row(self, page, row, left, y, right, h, expandable, expanded, top, bottom):
        c, p = self.colors, self._p
        key = ("row", row["key"])
        current = row["current"]
        if current or expanded:
            self._box(left, y, right - left, h, p(4), c["raised"])
        elif self._hover == key:
            self._box(left, y, right - left, h, p(4), c["hover"])
        if current:
            bar = p(3)
            self._box(left, y + p(12), bar, max(bar, min(h, self._row_height(False)) - p(24)), bar // 2 or 1, c["accent"])
        if self._focused(key):
            self._ring(left, y, right, y + h)
        x = left + p(3) + p(10)
        line = self._line(10, "bold")
        text_top = y + p(8)
        self._glyph(x + p(10), text_top + (line + self._line(8)) / 2, row["icon"], 13,
                    c["accent"] if current else c["text"])
        tx = x + p(20) + p(12)
        room = right - tx - p(36)  # the check mark sits on the right
        weight = "bold" if current else "normal"
        self._text(tx, text_top, self._fit(row["label"], 10, room, weight), 10, weight=weight)
        detail = (_("In uso · {detail}").format(detail=row["detail"]) if current and expandable else row["detail"])
        self._text(tx, text_top + line, self._fit(detail, 8, room), 8, c["muted"])
        if current and not expandable:
            self._glyph(right - p(20), text_top + (line + self._line(8)) / 2, "check", 11, c["accent"])
        visible = (max(y, top), min(y + h, bottom))
        if visible[0] < visible[1]:
            self._hits.append((key, left, visible[0], right, visible[1]))
            if key not in self._order:
                self._order.append(key)
        if expanded:
            by = text_top + line + self._line(8) + p(10)
            used = current
            edge = self._text_button(("use", row["key"]), right - p(10), by, _("In uso") if used else _("Usa"),
                                     primary=True, enabled=not used)
            self._text_button(("listen", row["key"]), edge - p(8), by, _("Ascolta"))
            # Buttons hidden under the header/footer are not clickable.
            self._hits = [hit for hit in self._hits if hit[0][0] not in ("use", "listen")
                          or (top <= hit[2] and hit[4] <= bottom)]

    def _scroll_to(self, key):
        box = self._row_boxes.get(key)
        if not box:
            return
        top, bottom, _total = self._list_view
        view = bottom - top
        if box[0] < self._scroll:
            self._scroll = box[0]
        elif box[1] > self._scroll + view:
            self._scroll = box[1] - view

    def _wheel(self, event):
        if self._page == "main":
            return
        self._scroll += -SCROLL_STEP * self.scale if event.delta > 0 else SCROLL_STEP * self.scale
        self._render()
        self._on_motion(event)

    # ── pointer and keyboard ────────────────────────────────────────

    def _hit_at(self, x, y):
        for key, x0, y0, x1, y1 in reversed(self._hits):
            if x0 <= x < x1 and y0 <= y < y1:
                return key
        return None

    def _update_cursor(self):
        clickable = self._hover is not None and self._hover[0] != "label"
        self.canvas.configure(cursor="hand2" if clickable else "")

    def _set_hover(self, key):
        if key != self._hover:
            self._hover = key
            self._render()

    def _on_motion(self, event):
        if self._drag:
            return
        self._set_hover(self._hit_at(event.x, event.y))

    def _on_press(self, event):
        self.canvas.focus_set()
        key = self._hit_at(event.x, event.y)
        self._pressed = key
        if key is None:
            return
        self._keyboard = False
        self._focus = key
        if key[0] == "slider":
            self._drag = key
            self._slide_to(key, event.x)

    def _on_drag(self, event):
        if self._drag:
            self._slide_to(self._drag, event.x)

    def _on_release(self, event):
        if self._drag:
            key, self._drag = self._drag, None
            self._commit_slider(key)
            self._set_hover(self._hit_at(event.x, event.y))
            self._render()
            return
        key, self._pressed = self._pressed, None
        if key is not None and key == self._hit_at(event.x, event.y):
            self._activate(key)

    def _slide_to(self, key, x):
        start, end, maximum = self._slider_track[key]
        value = max(0, min(maximum, round((x - start) / max(1, end - start) * maximum)))
        if key[1] == "volume":
            self.volume = value
        else:
            self.voice_volume = value
        self._render()

    def _commit_slider(self, key):
        if key[1] == "volume":
            self._save_volume(self.volume)
        else:
            self._save_voice_volume(self.voice_volume)

    def _move_focus(self, step):
        if not self._order:
            return
        self._keyboard = True
        index = self._order.index(self._focus) if self._focus in self._order else -step
        self._focus = self._order[(index + step) % len(self._order)]
        if self._focus[0] == "row":
            self._scroll_to(self._focus[1])
        self._render()

    def _arrow(self, step, horizontal=False):
        focus = self._focus
        if focus and focus[0] == "slider" and horizontal:
            self._keyboard = True
            from .tray_icon import MAX_VOLUME
            if focus[1] == "volume":
                self.volume = max(0, min(MAX_VOLUME, self.volume + 5 * step))
            else:
                self.voice_volume = max(0, min(MAX_VOLUME, self.voice_volume + 5 * step))
            self._commit_slider(focus)
            self._render()
            return
        if self._page != "main" and not horizontal:
            rows = [("row", row["key"]) for row in self.rows]
            if rows:
                index = rows.index(focus) if focus in rows else (-1 if step > 0 else 0)
                self._keyboard = True
                self._focus = rows[max(0, min(len(rows) - 1, index + step))]
                self._scroll_to(self._focus[1])
                self._render()
            return
        self._move_focus(step)

    def _activate_focus(self):
        if self._focus is not None:
            self._keyboard = True
            self._activate(self._focus)

    def _activate(self, key):
        kind = key[0]
        if kind == "tile":
            name = key[1]
            if not self.tiles.get(name, {}).get("enabled", True):
                return
            if name in LIST_PAGES:
                self._open_page(name)
            else:
                {"record": self._record, "audio": self._mute, "theme": self._next_theme,
                 "language": self._next_language}[name]()
        elif kind == "button":
            {"mute": self._mute, "files": self._files, "settings": self._settings, "power": self._exit}[key[1]]()
        elif kind == "row":
            if self._page_spec(self._page)[3]:
                self._toggle_row(key[1])
            else:
                self._select(key[1])
        elif kind == "use":
            self._select(key[1])
        elif kind == "listen":
            self._preview(key[1])
        elif kind == "back":
            self._back()
        elif kind == "link":
            self._settings()

    # ── navigation ──────────────────────────────────────────────────

    def _toggle_row(self, key):
        """Expand a row in place, like a network in the Windows Wi-Fi list."""
        self._expanded = None if self._expanded == key else key
        self._focus = ("row", key)
        self._render()
        self._scroll_to(key)
        self._render()

    def _open_page(self, page):
        self._page = page
        self._expanded = None
        self._scroll = 0
        current = self._page_spec(page)[2]
        self._focus = ("row", current)
        self._render()
        self._scroll_to(current)
        self._render()

    def _back(self):
        previous = self._page
        self._page = "main"
        self._expanded = None
        self._focus = ("tile", previous) if previous in LIST_PAGES else None
        self._render()

    def _escape(self):
        if self._page != "main":
            self._back()
        else:
            self._hide()

    def _select(self, key):
        page = self._page
        self._focus = ("row", key)
        if page == "packs":
            self._work(lambda: self.tray._make_set_sound_pack(key)(None, None))
        elif page == "voice":
            self._work(lambda: self.tray._make_set_tts_mode(key)(None, None))
        elif page == "mic":
            self._work(lambda: self.tray._make_set_mic(key)(None, None))
        else:
            self._work(lambda: self.tray._make_set_stt_mode(key)(None, None))

    def _preview(self, key):
        from .tray_icon import preview_sound_pack
        self._work(lambda: preview_sound_pack(key) or self._commands.put("silent"))

    # ── state and actions ───────────────────────────────────────────

    def _load(self):
        from .tray_icon import _load_config, list_sound_packs, sound_volume, voice_volume
        config = _load_config()
        self._config = config
        self._muted = bool(config.get("muted", False))
        self.volume = sound_volume(config)
        self.voice_volume = voice_volume(config)
        self.mics, self._default_mic, self._missing_mic = microphone_choices(self.tray._list_mics(), config)
        self.packs = list_sound_packs()

    def _refresh(self, rebuild=True):
        """Reload settings and redraw (the scene is always drawn from the settings)."""
        self._load()
        if self._drag:
            return  # the user is moving a slider: the next refresh redraws
        self._render()

    def _save_volume(self, value):
        self.volume = value
        self._work(lambda: self.tray._make_set_volume(value)(None, None))

    def _save_voice_volume(self, value):
        self.voice_volume = value
        self._work(lambda: self.tray._make_set_voice_volume(value)(None, None))

    def _work(self, callback):
        if self._working:
            self._pending_actions.append(callback)
            return
        self._working = True
        def run():
            try:
                callback()
            except Exception:
                logger.exception("Quick control action failed")
                self._commands.put("error")
            finally:
                self._commands.put("refresh")
        threading.Thread(target=run, daemon=True).start()

    def _next_theme(self):
        """Tile action: Sistema -> Chiaro -> Scuro, redrawn in one step."""
        themes = list(THEME_LABELS)
        choice = themes[(themes.index(fluent.theme()) + 1) % len(themes)]
        self.tray._set_ui_theme(choice)
        self._restyle()

    def _next_language(self):
        """Tile action: Sistema -> each language in turn."""
        settings = [i18n.SYSTEM] + list(i18n.LANGUAGES)
        current = self._config.get("ui_language", i18n.SOURCE)
        current = current if current in settings else i18n.SYSTEM
        self.tray._set_ui_language(settings[(settings.index(current) + 1) % len(settings)])
        self._restyle()

    def _restyle(self):
        self._load()
        height = self._main_height
        self._build_surface()
        flyout_window.finish_window(self.root, self.dark)
        if self._main_height != height:
            self._place()

    def _record(self):
        self._hide()
        # Give Windows time to return focus to the previous editor before paste.
        self.root.after(180, lambda: self._work(self.tray._on_toggle_recording))

    def _mute(self):
        self._work(lambda: self.tray._make_toggle_mute()(None, None))

    def _files(self):
        self._hide()
        self.tray._transcribe_files_clicked(None, None)

    def _settings(self):
        self._hide()
        self.tray._open_panel(None, None)

    def _exit(self):
        self._hide()
        threading.Thread(target=self.tray._exit_clicked, args=(None, None), daemon=True).start()

    def _hide(self):
        self.root.withdraw()
        # Next opening starts from the tiles, like Windows Quick Settings.
        self._page, self._expanded, self._scroll = "main", None, 0
        self._hover = self._pressed = self._drag = None
        self._keyboard = False

    def _dismiss_if_outside(self):
        # Only a click elsewhere closes it: focus we had and lost. When Windows refuses
        # the focus at opening (foreground lock), there is nothing to dismiss.
        if not getattr(self, "_had_focus", False):
            return
        if self.root.state() != "withdrawn" and self.root.focus_displayof() is None:
            self._hide()

    def _place(self):
        left, top, right, bottom = flyout_window.work_area()
        width = self._p(WIDTH)
        # One height for every page, set by the tiles: switching page never resizes
        # or moves the window; lists scroll inside.
        height = self._main_height
        gap = self._p(12)
        x, y = max(left + gap, right - width - gap), max(top + gap, bottom - height - gap)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _theme_changed(self):
        dark = _dark_mode()
        return (dark, fluent.accent(dark)) != getattr(self, "_theme", None)

    def _show(self):
        if self._theme_changed():
            # Light/dark or accent changed since the window was built (e.g. scheduled switch).
            self._build_surface()
        self._load()
        self._page, self._focus = "main", ("tile", "record")
        self._render()
        self._place()
        actual_dpi = flyout_window.window_dpi(self.root)
        if actual_dpi != self._dpi:
            self._dpi, self.scale = actual_dpi, actual_dpi / 96
            self._build_surface()
            self._show()
            return
        self._had_focus = False
        # Native frame first and invisible until final: no jump on screen.
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        flyout_window.finish_window(self.root, self.dark)
        self._place()
        self.root.update()
        self.root.attributes("-alpha", 1.0)
        self.root.lift()
        self.root.focus_force()
        self.canvas.focus_set()

    def _update_state(self):
        """Live status (dictation state, engine): redraw only when it changed."""
        if self._page != "main":
            return
        signature = (self._status(), self._engine_label())
        if signature != self._state_signature:
            self._state_signature = signature
            if not self._drag:
                self._render()

    def _tick(self):
        if not self._drain():
            return
        if self.root.state() != "withdrawn":
            self._update_state()
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 5 == 0:  # ~5 s hidden: the tray logo follows a taskbar light/dark switch
            self.tray._animator.follow_theme()
        # A hidden flyout never polls files/devices or renders frames.
        self.root.after(1000 if self.root.state() == "withdrawn" else 250, self._tick)

    def _drain(self):
        """Run queued commands; False once the window is destroyed."""
        try:
            while True:
                command = self._commands.get_nowait()
                if command == "stop":
                    self.root.destroy()
                    return False
                if command == "show":
                    self._show()
                if command == "refresh":
                    self._working = False
                    self._refresh(rebuild=False)
                    if self._pending_actions:
                        self._work(self._pending_actions.pop(0))
                if command == "error":
                    self._error_until = time.monotonic() + 3
                    self._render()
                if command == "silent":
                    logger.info("Sound pack preview: no sounds available")
        except queue.Empty:
            pass
        return True
