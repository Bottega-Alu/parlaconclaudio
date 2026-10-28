"""Lightweight quick controls, using the Windows 11 Quick Settings visual language.

A grid of tiles opens list pages (sound packs, announcements, microphone,
recognition) shaped like the Windows Wi-Fi list. The native menu remains
available for all advanced features. Tk and all its variables belong to one
dedicated thread; application actions run in workers.
"""
import logging
import queue
import threading

from .i18n import _, N_, APP_NAME
from . import i18n
from .menu_dpi import native_menu_dpi
from . import flyout_window
from . import fluent
from .fluent import ICON

logger = logging.getLogger(__name__)

WIDTH = 380
PAD = 16

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
        self._focus_key = None
        self.mics = {}
        self.packs = []
        self.root = None

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
        self.root.bind("<Escape>", lambda event: self._escape())
        self.root.bind("<FocusOut>", lambda event: self.root.after(180, self._dismiss_if_outside))
        self.root.bind("<FocusIn>", lambda event: setattr(self, "_had_focus", True))
        self.root.bind("<MouseWheel>", self._wheel)
        self.root.bind("<<FlyoutWake>>", lambda event: self._drain())
        self._dpi = flyout_window.window_dpi(self.root)
        self.scale = self._dpi / 96
        self._curtain = flyout_window.Curtain(self.root)
        self._build_surface()
        self.root.after(150, self._tick)

    def _build_surface(self):
        self.root.tk.call("tk", "scaling", self._dpi / 72)
        self.dark = _dark_mode()
        self.colors = fluent.colors(self.dark)
        self._theme = (self.dark, self.colors["accent"])
        self.font = fluent.text_font(self.root)
        self.icons = fluent.icon_font(self.root)
        self.root.configure(bg=self.colors["surface"])
        self._build_pages()

    def _build_pages(self):
        """Build every page once, stacked in the same place, while the window is hidden.

        Tk on Windows paints a freshly built page piece by piece (each widget is a
        native child window and settles its geometry over several passes): building
        pages in front of the user is what made the quick controls flicker. Pages are
        therefore built only here (start, theme/DPI change, new microphones or packs),
        switching page just raises an already painted one, and actions update texts
        and colours in place.
        """
        import tkinter as tk
        for child in self.root.winfo_children():
            child.destroy()
        self._tile_images = {}
        self._pages, self._lists = {}, {}
        self.list_canvas, self.rows = None, []
        for page in ("main",) + ("packs", "voice", "mic", "engine"):
            frame = tk.Frame(self.root, bg=self.colors["surface"])
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            self.page = frame
            if page == "main":
                self._main_page()
            else:
                self._list_page(page)
            self._pages[page] = frame
        self._built_for = self._content_signature()
        self._raise_page(self._page)

    def _content_signature(self):
        """What the pages were built from; anything else is updated in place."""
        return (tuple(self.mics), tuple(self.packs), tuple(self._caps()), i18n.current())

    def _raise_page(self, page):
        self._page = page
        self.page = self._pages[page]
        self.page.tkraise()
        self.list_canvas, self.rows = self._lists.get(page, (None, []))

    def _render(self):
        """Show the current page; rebuilding happens only in _build_pages."""
        self._raise_page(self._page)

    def _glyph(self, parent, name, size=12, fg=None, bg=None):
        import tkinter as tk
        c = self.colors
        if not self.icons:
            return tk.Frame(parent, bg=bg or c["surface"], width=1)
        return tk.Label(parent, text=ICON[name], font=(self.icons, size), fg=fg or c["text"],
                        bg=bg or c["surface"])

    def _icon_button(self, parent, name, hint, command, bg=None):
        import tkinter as tk
        c = self.colors
        bg = bg or c["surface"]
        button = tk.Button(parent, text=ICON[name] if self.icons else hint,
                           font=(self.icons, 12) if self.icons else (self.font, 9),
                           command=command, bg=bg, fg=c["text"], activebackground=c["hover"],
                           activeforeground=c["text"], relief="flat", bd=0, cursor="hand2",
                           padx=self._p(9), pady=self._p(5), highlightthickness=self._p(1),
                           highlightbackground=bg, highlightcolor=c["accent"])
        button.bind("<Enter>", lambda event: (button.configure(bg=c["hover"]), self._hint(hint)))
        button.bind("<Leave>", lambda event: (button.configure(bg=bg), self._hint(None)))
        return button

    def _text_button(self, parent, text, command, primary=False, enabled=True):
        import tkinter as tk
        c = self.colors
        bg, fg = (c["accent"], c["on"]) if primary else (c["hover"], c["text"])
        return tk.Button(parent, text=text, command=command, font=(self.font, 9),
                         bg=bg, fg=fg, activebackground=c["line"] if not primary else bg,
                         activeforeground=fg, disabledforeground=c["subtle"],
                         state="normal" if enabled else "disabled", relief="flat", bd=0,
                         padx=self._p(14), pady=self._p(5), cursor="hand2",
                         highlightthickness=self._p(1), highlightbackground=c["line"],
                         highlightcolor=c["text"])

    # ── main page: tiles, volume, footer ────────────────────────────

    def _main_page(self):
        import tkinter as tk
        from .tray_icon import MAX_VOLUME
        c = self.colors
        body = tk.Frame(self.page, bg=c["surface"], padx=self._p(PAD), pady=self._p(PAD))
        body.pack(fill="both", expand=True)
        header = tk.Frame(body, bg=c["surface"])
        header.pack(fill="x")
        tk.Label(header, text=APP_NAME, font=(self.font, 12, "bold"),
                 fg=c["text"], bg=c["surface"]).pack(side="left")
        status = tk.Frame(body, bg=c["surface"])
        status.pack(fill="x", pady=(self._p(4), self._p(14)))
        self.status_dot = tk.Canvas(status, width=self._p(10), height=self._p(10),
                                    bg=c["surface"], highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, self._p(7)))
        self.state = tk.StringVar(master=status, value=_(STATE_LABELS["idle"]))
        tk.Label(status, textvariable=self.state, font=(self.font, 9), fg=c["muted"],
                 bg=c["surface"], anchor="w").pack(side="left")

        grid = tk.Frame(body, bg=c["surface"])
        grid.pack(fill="x")
        grid.columnconfigure((0, 1, 2), weight=1, uniform="tiles")
        values = self._tile_values()
        specs = [
            ("record", "mic", _("Dettatura"), _("Avvia"), self._record, None),
            ("audio", "speaker", _("Audio"), values["audio"], self._mute, None),
            ("packs", "music", _("Suoni"), values["packs"], None, "packs"),
            ("voice", "chat", _("Avvisi vocali"), values["voice"], None, "voice"),
            ("mic", "mic", _("Microfono"), values["mic"], None, "mic"),
            ("engine", "chip", _("Riconoscimento"), values["engine"], None, "engine"),
        ]
        self.tiles = {}
        for index, (key, icon, title, value, action, page) in enumerate(specs):
            cell = tk.Frame(grid, bg=c["surface"])
            cell.grid(row=index // 3, column=index % 3, sticky="ew",
                      padx=(0 if index % 3 == 0 else self._p(4), 0 if index % 3 == 2 else self._p(4)),
                      pady=(0, self._p(10)))
            self.tiles[key] = self._tile(cell, key, icon, title, value, action, page)
        self.record_tile, self.audio_tile = self.tiles["record"], self.tiles["audio"]

        volume = tk.Frame(body, bg=c["surface"])
        volume.pack(fill="x", pady=(self._p(4), 0))
        self.volume_icon = self._icon_button(volume, "mute" if self._muted else "speaker",
                                             _("Attiva o disattiva l'audio"), self._mute)
        self.volume_icon.pack(side="left")
        self.volume_text = tk.StringVar(master=volume, value=f"{self.volume}%")
        tk.Label(volume, textvariable=self.volume_text, width=5, font=(self.font, 9),
                 bg=c["surface"], fg=c["muted"], anchor="e").pack(side="right")
        self.slider = fluent.Slider(volume, c, self.scale, self.volume, self._save_volume,
                                    maximum=MAX_VOLUME)
        self.slider.listeners.append(lambda value: self.volume_text.set(f"{value}%"))
        self.slider.canvas.pack(side="left", fill="x", expand=True, padx=(self._p(4), 0))
        voices = tk.Frame(body, bg=c["surface"])
        voices.pack(fill="x")
        # Same widget as the mute button so both sliders start at the same x.
        self._icon_button(voices, "chat", _("Volume delle voci"), lambda: None).pack(side="left")
        self.voice_text = tk.StringVar(master=voices, value=f"{self.voice_volume}%")
        tk.Label(voices, textvariable=self.voice_text, width=5, font=(self.font, 9),
                 bg=c["surface"], fg=c["muted"], anchor="e").pack(side="right")
        self.voice_slider = fluent.Slider(voices, c, self.scale, self.voice_volume,
                                          self._save_voice_volume, maximum=MAX_VOLUME)
        self.voice_slider.listeners.append(lambda value: self.voice_text.set(f"{value}%"))
        self.voice_slider.canvas.pack(side="left", fill="x", expand=True, padx=(self._p(4), 0))
        self.engine = tk.StringVar(master=body)
        tk.Label(body, textvariable=self.engine, font=(self.font, 8), fg=c["subtle"],
                 bg=c["surface"], anchor="w").pack(fill="x", pady=(self._p(6), 0))

        footer = tk.Frame(self.page, bg=c["footer"], padx=self._p(PAD - 4), pady=self._p(8))
        footer.pack(fill="x", side="bottom")
        tk.Frame(self.page, bg=c["line"], height=1).pack(fill="x", side="bottom")
        self.hint = tk.StringVar(master=footer)
        self.keys = tk.Frame(footer, bg=c["footer"])
        self.keys.pack(side="left", padx=(self._p(4), 0))
        self.hint_label = tk.Label(footer, textvariable=self.hint, font=(self.font, 9),
                                   fg=c["muted"], bg=c["footer"])
        for index, key in enumerate(self._caps()):
            if index:
                tk.Label(self.keys, text="+", font=(self.font, 8), fg=c["subtle"],
                         bg=c["footer"]).pack(side="left", padx=self._p(2))
            tk.Label(self.keys, text=key, font=(self.font, 8), fg=c["text"], bg=c["raised"],
                     padx=self._p(6), pady=self._p(1), highlightthickness=1,
                     highlightbackground=c["line"]).pack(side="left")
        self._icon_button(footer, "power", _("Esci da {app}").format(app=APP_NAME), self._exit, c["footer"]).pack(side="right")
        self._icon_button(footer, "settings", _("Impostazioni"), self._settings, c["footer"]).pack(side="right")
        self.files = self._icon_button(footer, "file", _("Trascrivi file audio"), self._files, c["footer"])
        self.files.pack(side="right")
        self._paint_status()

    def _caps(self):
        from .dictation_trigger import key_caps
        caps = key_caps(self.tray.dictation_trigger())
        if len(caps) == 1 and len(caps[0]) > 12:
            caps = [caps[0].replace("Tasto ", "").replace(" del mouse", " mouse").capitalize()]
        return caps

    def _hint(self, text):
        if self._page != "main":
            return
        if text:
            self.keys.pack_forget()
            self.hint.set(text)
            self.hint_label.pack(side="left", padx=(self._p(4), 0))
        else:
            self.hint_label.pack_forget()
            self.keys.pack(side="left", padx=(self._p(4), 0))

    def _tile_values(self):
        from .tray_icon import TTS_MODES, STT_MODES
        config = self._config
        pack = config.get("sound_pack", "r2d2")
        mode = config.get("tts_mode", "full")
        mic = next((name for name, value in self.mics.items()
                    if value == config.get("mic_device_id")), _("Predefinito"))
        return {
            "audio": _("Disattivato") if self._muted else _("Suoni {volume}% · voci {voices}%").format(
                volume=self.volume, voices=self.voice_volume),
            "packs": _("Casuale") if pack == "random" else fluent.pretty_pack(pack),
            "voice": next((_(label) for label, value in TTS_MODES.items() if value == mode), mode),
            "mic": _t(mic.split(" [")[0]),
            "engine": _t(STT_MODES.get(config.get("stt_mode", "auto"), "")).split(" (")[0],
        }

    def _update_main(self):
        """After an action on the main page: change texts and colours, rebuild nothing."""
        for key, text in self._tile_values().items():
            self.tiles[key]["value"].configure(text=_short(text))
        self._paint_tile(self.audio_tile, not self._muted)
        icon = "mute" if self._muted else "speaker"
        if self.icons and self.volume_icon.winfo_exists():
            self.volume_icon.configure(text=ICON[icon])
        self._draw_slider()
        self._update_state()

    def _tile(self, cell, key, icon, title, value, action, page):
        """Quick Settings tile: rounded button above a two-line caption."""
        import tkinter as tk
        c = self.colors
        canvas = tk.Canvas(cell, height=self._p(48), bg=c["surface"], highlightthickness=0,
                           takefocus=1, cursor="hand2")
        canvas.pack(fill="x")
        tile = {"key": key, "canvas": canvas, "icon": icon, "active": False, "hover": False,
                "page": page, "action": action, "enabled": True}
        tile["title"] = tk.Label(cell, text=title, font=(self.font, 9), fg=c["text"], bg=c["surface"])
        tile["title"].pack(pady=(self._p(5), 0))
        tile["value"] = tk.Label(cell, text=_short(value), font=(self.font, 8), fg=c["subtle"], bg=c["surface"])
        tile["value"].pack()
        def activate(event=None):
            if not tile["enabled"]:
                return
            if tile["page"]:
                self._open_page(tile["page"])
            else:
                tile["action"]()
        canvas.bind("<Button-1>", activate)
        canvas.bind("<Return>", activate)
        canvas.bind("<space>", activate)
        canvas.bind("<Configure>", lambda event: self._paint_tile(tile, tile["active"]))
        canvas.bind("<FocusIn>", lambda event: self._paint_tile(tile, tile["active"]))
        canvas.bind("<FocusOut>", lambda event: self._paint_tile(tile, tile["active"]))
        canvas.bind("<Enter>", lambda event: (tile.update(hover=True), self._paint_tile(tile, tile["active"])))
        canvas.bind("<Leave>", lambda event: (tile.update(hover=False), self._paint_tile(tile, tile["active"])))
        return tile

    def _paint_tile(self, tile, selected, focused=None):
        c = self.colors
        canvas = tile["canvas"]
        if not canvas.winfo_exists():
            return
        tile["active"] = selected
        if focused is None:
            focused = self.root.focus_get() is canvas
        w, h = max(canvas.winfo_width(), self._p(100)), self._p(48)
        if selected:
            fill, fg, outline = c["accent"], c["on"], c["accent"]
        else:
            fill, fg, outline = (c["hover"] if tile["hover"] else c["raised"]), c["text"], c["line"]
        if not tile["enabled"]:
            fg = c["subtle"]
        key = (w, h, fill, c["text"] if focused else outline)
        image = self._tile_images.get(key)
        if image is None:
            image = self._tile_images[key] = fluent.rounded(self.root, w, h, self._p(6), key[2], key[3])
        tile["image"] = image
        canvas.delete("all")
        canvas.create_image(0, 0, image=image, anchor="nw")
        glyph = tile["icon"]
        if tile["key"] == "audio" and not selected:
            glyph = "mute"
        if tile["key"] == "record" and selected:
            glyph = "mic_on"
        if self.icons:
            if tile["page"]:
                canvas.create_text(w * 0.38, h / 2, text=ICON[glyph], font=(self.icons, 13), fill=fg)
                canvas.create_line(w * 0.68, h * 0.3, w * 0.68, h * 0.7, fill=c["line"] if not selected else fg)
                canvas.create_text(w * 0.84, h / 2, text=ICON["next"], font=(self.icons, 8), fill=fg)
            else:
                canvas.create_text(w / 2, h / 2, text=ICON[glyph], font=(self.icons, 13), fill=fg)
        else:
            canvas.create_text(w / 2, h / 2, text=tile["title"]["text"], font=(self.font, 9), fill=fg)

    def _paint_status(self):
        if not getattr(self, "status_dot", None) or not self.status_dot.winfo_exists():
            return
        c = self.colors
        state = self.tray._state
        color = {"recording": c["rec"], "transcribing": c["busy"], "loading": c["busy"],
                 "output": c["busy"]}.get(state, c["ok"])
        if getattr(self.tray, "batch_busy", False):
            color = c["busy"]
        size = self._p(8)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, size, size, fill=color, outline="")

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
            items = [(value, _t(name.split(" [")[0]), _("Segue l'impostazione di Windows") if value is None
                      else _("Dispositivo {number}").format(number=value), "mic") for name, value in self.mics.items()]
            return (_("Microfono"), items, config.get("mic_device_id"), False,
                    _("Il dispositivo usato per la dettatura."))
        items = [(key, _t(label).split(" (")[0], _t(STT_MODE_DETAILS.get(key, "")), "cloud" if key.startswith("cloud") else "chip")
                 for key, label in STT_MODES.items()]
        return (_("Riconoscimento"), items, config.get("stt_mode", "auto"), False,
                _("Dove viene trascritta la voce."))

    def _list_page(self, page):
        import tkinter as tk
        c = self.colors
        title, items, current, expandable, note = self._page_spec(page)
        header = tk.Frame(self.page, bg=c["surface"], padx=self._p(PAD - 6), pady=self._p(12))
        header.pack(fill="x")
        back = self._icon_button(header, "back", _("Indietro"), self._back)
        back.pack(side="left")
        tk.Label(header, text=title, font=(self.font, 12, "bold"), fg=c["text"],
                 bg=c["surface"]).pack(side="left", padx=(self._p(6), 0))
        tk.Label(self.page, text=note, font=(self.font, 8), fg=c["subtle"], bg=c["surface"],
                 anchor="w", justify="left", wraplength=self._p(WIDTH - 3 * PAD)).pack(
                     fill="x", padx=self._p(PAD), pady=(0, self._p(8)))
        holder = tk.Frame(self.page, bg=c["surface"], padx=self._p(PAD - 8))
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=c["surface"], highlightthickness=0,
                           height=self._p(52))  # fills what the fixed window height leaves
        canvas.pack(fill="both", expand=True)
        rows_frame = tk.Frame(canvas, bg=c["surface"])
        window = canvas.create_window(0, 0, window=rows_frame, anchor="nw")
        rows_frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        rows = [self._row(rows_frame, key, label, detail, icon, expandable)
                for key, label, detail, icon in items]
        for row in rows:
            self._style_row(row, row._row_key == current, False)
        self._lists[page] = (canvas, rows)
        footer = tk.Frame(self.page, bg=c["footer"], padx=self._p(PAD - 4), pady=self._p(8))
        footer.pack(fill="x", side="bottom")
        tk.Frame(self.page, bg=c["line"], height=1).pack(fill="x", side="bottom")
        link = tk.Label(footer, text=_("Altre impostazioni"), font=(self.font, 9, "underline"),
                        fg=c["accent"], bg=c["footer"], cursor="hand2", takefocus=1)
        link.pack(side="left", padx=(self._p(4), 0), pady=self._p(5))
        link.bind("<Button-1>", lambda event: self._settings())
        link.bind("<Return>", lambda event: self._settings())

    def _row(self, parent, key, label, detail, icon, expandable):
        """A list row whose look is set by _style_row, so selection never rebuilds it."""
        import tkinter as tk
        c = self.colors
        frame = tk.Frame(parent, cursor="hand2", takefocus=1, highlightthickness=1,
                         highlightcolor=c["text"])
        frame.pack(fill="x", pady=(0, self._p(2)))
        bar = tk.Frame(frame, width=self._p(3))
        bar.pack(side="left", fill="y", pady=self._p(12))
        content = tk.Frame(frame, padx=self._p(10), pady=self._p(8))
        content.pack(side="left", fill="x", expand=True)
        top = tk.Frame(content)
        top.pack(fill="x")
        glyph = self._glyph(top, icon, 13)
        glyph.pack(side="left", padx=(0, self._p(12)))
        texts = tk.Frame(top)
        texts.pack(side="left", fill="x", expand=True)
        title = tk.Label(texts, text=label, fg=c["text"], anchor="w")
        title.pack(fill="x")
        caption = tk.Label(texts, font=(self.font, 8), fg=c["muted"], anchor="w")
        caption.pack(fill="x")
        check = self._glyph(top, "check", 11, c["accent"])
        parts = {"frame": frame, "bar": bar, "content": content, "top": top, "glyph": glyph,
                 "texts": texts, "title": title, "caption": caption, "check": check}
        if expandable:
            actions = tk.Frame(content)
            use = self._text_button(actions, _("Usa"), lambda: self._select(key), primary=True)
            use.pack(side="right")
            listen = self._text_button(actions, _("Ascolta"), lambda: self._preview(key))
            listen.pack(side="right", padx=(0, self._p(8)))
            parts.update(actions=actions, use=use)
        frame._row_key, frame._parts, frame._detail = key, parts, detail
        frame._expandable, frame._current, frame._expanded = expandable, False, False

        def click(event=None):
            if expandable:
                self._toggle_row(key)
            else:
                self._select(key)

        def hover(on):
            if frame._current or frame._expanded:
                return
            self._paint_row(frame, c["hover"] if on else c["surface"])

        for name in ("frame", "content", "top", "glyph", "texts", "title", "caption", "check"):
            widget = parts[name]
            widget.bind("<Button-1>", click)
            widget.bind("<Enter>", lambda event: hover(True))
            widget.bind("<Leave>", lambda event: hover(False))
        frame.bind("<Return>", click)
        frame.bind("<space>", click)
        frame.bind("<FocusIn>", lambda event: self._scroll_to(frame))
        return frame

    def _paint_row(self, row, base):
        parts = row._parts
        for name in ("content", "top", "glyph", "texts", "title", "caption", "check", "actions"):
            if name in parts:
                parts[name].configure(bg=base)
        parts["frame"].configure(bg=base, highlightbackground=base)
        parts["bar"].configure(bg=self.colors["accent"] if row._current else base)

    def _style_row(self, row, current, expanded):
        """Selected / expanded look, changed in place."""
        if (row._current, row._expanded) == (current, expanded) and getattr(row, "_styled", False):
            return
        c = self.colors
        row._current, row._expanded, row._styled = current, expanded, True
        parts = row._parts
        self._paint_row(row, c["raised"] if (current or expanded) else c["surface"])
        if self.icons:
            parts["glyph"].configure(fg=c["accent"] if current else c["text"])
        parts["title"].configure(font=(self.font, 10, "bold" if current else "normal"))
        parts["caption"].configure(text=_("In uso · {detail}").format(detail=row._detail) if current and row._expandable else row._detail)
        if current and not row._expandable:
            parts["check"].pack(side="right")
        else:
            parts["check"].pack_forget()
        if row._expandable:
            parts["use"].configure(text=_("In uso") if current else _("Usa"), state="disabled" if current else "normal")
            if expanded:
                parts["actions"].pack(fill="x", pady=(self._p(10), self._p(2)))
            else:
                parts["actions"].pack_forget()

    def _sync_rows(self):
        """Every list page reflects the saved settings, without rebuilding anything."""
        for page, (canvas, rows) in self._lists.items():
            current = self._page_spec(page)[2]
            for row in rows:
                self._style_row(row, row._row_key == current,
                                page == self._page and row._expandable and row._row_key == self._expanded)

    def _toggle_row(self, key):
        """Expand a row in place, like a network in the Windows Wi-Fi list."""
        self._expanded = None if self._expanded == key else key
        self._focus_key = key
        with self._curtain.held():
            self._sync_rows()
        self._focus_row(key)

    def _focus_row(self, key):
        row = next((row for row in self.rows if row._row_key == key), None)
        if row is not None:
            self.root.update_idletasks()
            row.focus_set()
            self._scroll_to(row)

    def _scroll_to(self, frame):
        canvas = self.list_canvas
        if not canvas:
            return
        total = max(1, frame.master.winfo_height())
        top, bottom = frame.winfo_y(), frame.winfo_y() + frame.winfo_height()
        view_top, view_bottom = (fraction * total for fraction in canvas.yview())
        if top < view_top:
            canvas.yview_moveto(top / total)
        elif bottom > view_bottom:
            canvas.yview_moveto((bottom - canvas.winfo_height()) / total)

    def _wheel(self, event):
        if self.list_canvas and self.list_canvas.winfo_exists():
            self.list_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _open_page(self, page):
        if self._curtain.active:
            return
        self._expanded = None
        self._focus_key = None
        with self._curtain.held():
            self._raise_page(page)
            self._sync_rows()
            self.list_canvas.yview_moveto(0)
        if self.rows:
            current = self._page_spec(page)[2]
            target = next((row for row in self.rows if row._row_key == current), self.rows[0])
            self._focus_row(target._row_key)

    def _back(self):
        if self._curtain.active:
            return
        previous = self._page
        self._expanded = None
        with self._curtain.held():
            self._sync_rows()
            self._raise_page("main")
            self._update_main()
        tile = {"packs": "packs", "voice": "voice", "mic": "mic", "engine": "engine"}.get(previous)
        if tile in self.tiles:
            self.tiles[tile]["canvas"].focus_set()

    def _escape(self):
        if self._page != "main":
            self._back()
        else:
            self._hide()

    def _select(self, key):
        page = self._page
        self._focus_key = key
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
        self.mics = {N_("Predefinito di Windows"): None}
        self.mics.update({f"{d.name} [{d.device_id}]": d.device_id for d in self.tray._list_mics()})
        self.packs = list_sound_packs()

    def _refresh(self, rebuild=True):
        """Reload settings and update the pages in place.

        Pages are rebuilt only when what they were built from changed (microphones,
        sound packs, dictation trigger) or on request, and only while hidden.
        """
        self._load()
        hidden = self.root.state() == "withdrawn"
        if hidden and (rebuild and self._content_signature() != getattr(self, "_built_for", None)):
            self._build_pages()
        # After an action the page is visible: show the new look in one step.
        with self._curtain.held():
            self._sync_rows()
            self._update_main()
        if self._page != "main" and self._focus_key is not None:
            self._focus_row(self._focus_key)

    def _draw_slider(self):
        if getattr(self, "slider", None):
            self.slider.set(self.volume, disabled=self._muted)
        if getattr(self, "voice_slider", None):
            self.voice_slider.set(self.voice_volume, disabled=self._muted)

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

    def _record(self):
        self._hide()
        # Give Windows time to return focus to the previous editor before paste.
        self.root.after(180, lambda: self._work(self.tray._on_toggle_recording))

    def _mute(self):
        self._focus_key = None
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
        if self._page != "main":
            # Next opening starts from the tiles, like Windows Quick Settings.
            self._expanded = None
            self._raise_page("main")
            self._sync_rows()

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
        self.root.update_idletasks()
        # One height for every page, set by the tiles: switching page never resizes
        # or moves the window (each resize repainted it); lists scroll inside.
        height = self._pages["main"].winfo_reqheight()
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
        self._refresh()
        self._update_state()  # final captions first: they set the height
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
        self._place()  # the native shadow frame changed the client height
        # Map and paint every page while still transparent: it appears complete.
        self.root.update()
        self.root.attributes("-alpha", 1.0)
        self.root.lift()
        self.root.focus_force()
        if self.record_tile:
            self.record_tile["canvas"].focus_set()

    def _update_state(self):
        if self._page != "main" or not self.record_tile:
            return
        state = self.tray._state
        batch = self.tray.batch_busy
        self.state.set(_("Trascrizione file in corso") if batch
                       else _(STATE_LABELS[state]) if state in STATE_LABELS else state)
        self._paint_status()
        recording = state == "recording"
        tile = self.record_tile
        tile["value"].configure(text=_("In ascolto") if recording else _("Avvia"))
        enabled = bool(self.tray._on_toggle_recording) and state in ("idle", "recording") and not batch
        if tile["active"] != recording or tile["enabled"] != enabled:
            tile["enabled"] = enabled
            self._paint_tile(tile, recording)
        self.files.configure(state="normal" if state == "idle" and not batch else "disabled")
        self.engine.set(_("Motore attivo: {engine}").format(engine=self.tray._get_stt_status({})["active_label"]))

    def _tick(self):
        if not self._drain():
            return
        if self.root.state() != "withdrawn":
            self._update_state()
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
                if command == "error" and self._page == "main":
                    self.state.set(_("Operazione non riuscita. Riprova."))
                if command == "silent":
                    logger.info("Sound pack preview: no sounds available")
        except queue.Empty:
            pass
        return True
