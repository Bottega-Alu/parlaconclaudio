"""Native, keyboard-accessible control window for the tray application.

Laid out like Windows 11 Settings: navigation on the left, setting cards on the
right. Tk belongs exclusively to this window's thread; callbacks that touch
engines or configuration run in workers. Closing hides the window and keeps
dictation alive.
"""
import logging
import queue
import threading

from . import fluent
from .fluent import ICON

logger = logging.getLogger(__name__)

PAGES = (
    ("home", "home", "Home"),
    ("dictation", "mic", "Dettatura"),
    ("alerts", "bell", "Avvisi vocali"),
    ("links", "link", "Collegamenti"),
    ("about", "info", "Informazioni"),
)
STATE_LABELS = {"idle": "Pronto per dettare", "recording": "Registrazione in corso",
                "transcribing": "Trascrizione in corso", "loading": "Caricamento del motore",
                "output": "Invio del testo"}


class ControlPanel:
    def __init__(self, tray):
        self.tray = tray
        self._commands = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._page = "home"
        self._autostart = None  # unknown until a worker has asked Windows

    def show(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="control-panel")
                self._thread.start()
            else:
                self._commands.put("show")

    def stop(self):
        self._commands.put("stop")

    def _run(self):
        from .menu_dpi import native_menu_dpi
        try:
            with native_menu_dpi():
                self._build()
                self.root.mainloop()
        except Exception:
            logger.exception("Impossibile aprire il pannello")
            self.tray._notify("Pannello non disponibile. Usa il menu dell'icona nell'area di notifica.")

    def _p(self, value):
        return round(value * self.scale)

    # ── window and theme ────────────────────────────────────────────

    def _build(self):
        import tkinter as tk
        from pathlib import Path
        from .build_info import VERSION
        from . import flyout_window

        self.root = root = tk.Tk()
        root.withdraw()
        root.title(f"parlaconclaudio • {VERSION}")
        app_icon = Path(__file__).resolve().parents[2] / "assets" / "voicebridge.ico"
        if app_icon.is_file():
            root.iconbitmap(str(app_icon))
        dpi = flyout_window.window_dpi(root)
        self.scale = dpi / 96
        root.tk.call("tk", "scaling", dpi / 72)
        root.geometry(f"{self._p(920)}x{self._p(660)}")
        root.minsize(self._p(780), self._p(560))
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        root.bind("<Escape>", lambda event: root.withdraw())
        root.bind("<MouseWheel>", self._wheel)
        self.dark = fluent.dark_mode()
        self.colors = c = fluent.colors(self.dark)
        self.font = fluent.text_font(root)
        self.icons = fluent.icon_font(root)
        root.configure(bg=c["surface"])
        self._style()
        self.nav = tk.Frame(root, bg=c["surface"], width=self._p(250), padx=self._p(10), pady=self._p(18))
        self.nav.pack(side="left", fill="y")
        self.nav.pack_propagate(False)
        self.content_holder = tk.Frame(root, bg=c["surface"])
        self.content_holder.pack(side="left", fill="both", expand=True)
        self._load()
        self._render()
        root.deiconify()
        fluent.dark_title_bar(root, self.dark)
        self._check_autostart()
        root.after(100, self._tick)

    def _style(self):
        from tkinter import ttk
        c = self.colors
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Card.TCombobox", foreground=c["text"], background=c["raised"],
                        fieldbackground=c["raised"], arrowcolor=c["text"], bordercolor=c["line"],
                        lightcolor=c["line"], darkcolor=c["line"], padding=self._p(6),
                        arrowsize=self._p(12))
        style.map("Card.TCombobox", fieldbackground=[("readonly", c["raised"])],
                  foreground=[("readonly", c["text"])], selectbackground=[("readonly", c["raised"])],
                  selectforeground=[("readonly", c["text"])], bordercolor=[("focus", c["accent"])])
        style.configure("Card.Vertical.TScrollbar", background=c["raised"], troughcolor=c["surface"],
                        bordercolor=c["surface"], arrowcolor=c["muted"], lightcolor=c["raised"],
                        darkcolor=c["raised"])
        self.root.option_add("*TCombobox*Listbox.font", (self.font, 10))
        self.root.option_add("*TCombobox*Listbox.background", c["raised"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["on"])

    # ── data ────────────────────────────────────────────────────────

    def _load(self):
        from .tray_icon import _load_config, list_sound_packs
        from ..core import voice_hooks
        self.config = _load_config()
        self.mics = {"Predefinito di Windows": None}
        self.mics.update({f"{dev.name} [{dev.device_id}]": dev.device_id for dev in self.tray._list_mics()})
        self.packs = list_sound_packs()
        self.hooks = {agent: voice_hooks.status(agent) is True for agent in voice_hooks.AGENTS}

    def _check_autostart(self):
        def run():
            from . import autostart
            try:
                self._autostart = autostart.is_enabled()
            except Exception:
                logger.exception("Stato dell'avvio automatico non disponibile")
            self._commands.put("refresh")
        threading.Thread(target=run, daemon=True).start()

    # ── layout ──────────────────────────────────────────────────────

    def _render(self):
        import tkinter as tk
        c = self.colors
        for frame in (self.nav, self.content_holder):
            for child in frame.winfo_children():
                child.destroy()
        self._images = []
        self.state_label = self.record_button = self.files_button = None
        self._nav()
        self.canvas = tk.Canvas(self.content_holder, bg=c["surface"], highlightthickness=0)
        from tkinter import ttk
        scrollbar = ttk.Scrollbar(self.content_holder, orient="vertical", command=self.canvas.yview,
                                  style="Card.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=c["surface"], padx=self._p(28), pady=self._p(22))
        window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        title = dict((key, label) for key, _, label in PAGES)[self._page]
        tk.Label(self.body, text=title, font=(self.font, 20, "bold"), fg=c["text"],
                 bg=c["surface"], anchor="w").pack(fill="x", pady=(0, self._p(16)))
        getattr(self, f"_page_{self._page}")()
        self._update_state()

    def _nav(self):
        import tkinter as tk
        from .build_info import VERSION
        c = self.colors
        brand = tk.Frame(self.nav, bg=c["surface"], padx=self._p(8))
        brand.pack(fill="x", pady=(0, self._p(18)))
        tk.Label(brand, text="parlaconclaudio", font=(self.font, 12, "bold"), fg=c["text"],
                 bg=c["surface"], anchor="w").pack(fill="x")
        tk.Label(brand, text=f"Versione {VERSION}", font=(self.font, 8), fg=c["subtle"],
                 bg=c["surface"], anchor="w").pack(fill="x")
        for key, icon, label in PAGES:
            selected = key == self._page
            bg = c["raised"] if selected else c["surface"]
            item = tk.Frame(self.nav, bg=bg, cursor="hand2", takefocus=1, highlightthickness=1,
                            highlightbackground=bg, highlightcolor=c["text"])
            item.pack(fill="x", pady=(0, self._p(3)))
            bar = tk.Frame(item, bg=c["accent"] if selected else bg, width=self._p(3))
            bar.pack(side="left", fill="y", pady=self._p(9))
            glyph = self._glyph(item, icon, 11, c["text"], bg)
            glyph.pack(side="left", padx=(self._p(10), self._p(12)), pady=self._p(8))
            text = tk.Label(item, text=label, font=(self.font, 10), fg=c["text"], bg=bg, anchor="w")
            text.pack(side="left", fill="x")
            widgets = (item, glyph, text)
            for widget in widgets:
                widget.bind("<Button-1>", lambda event, k=key: self._go(k))
                if not selected:
                    widget.bind("<Enter>", lambda event, w=widgets: [x.configure(bg=c["hover"]) for x in w])
                    widget.bind("<Leave>", lambda event, w=widgets, b=bg: [x.configure(bg=b) for x in w])
            item.bind("<Return>", lambda event, k=key: self._go(k))
            item.bind("<space>", lambda event, k=key: self._go(k))
        tk.Label(self.nav, text="Chiudendo la finestra l'app\nresta nell'area di notifica.",
                 font=(self.font, 8), fg=c["subtle"], bg=c["surface"], justify="left",
                 anchor="w").pack(side="bottom", fill="x", padx=self._p(8))

    def _go(self, page):
        self._page = page
        self._load()
        self._render()
        self.canvas.yview_moveto(0)

    def _wheel(self, event):
        if getattr(self, "canvas", None) and self.canvas.winfo_exists():
            if self.body.winfo_height() > self.canvas.winfo_height():
                self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ── building blocks ─────────────────────────────────────────────

    def _glyph(self, parent, name, size, fg, bg):
        import tkinter as tk
        if not self.icons:
            return tk.Frame(parent, bg=bg, width=1)
        return tk.Label(parent, text=ICON[name], font=(self.icons, size), fg=fg, bg=bg)

    def _section(self, text):
        import tkinter as tk
        c = self.colors
        tk.Label(self.body, text=text, font=(self.font, 10, "bold"), fg=c["text"], bg=c["surface"],
                 anchor="w").pack(fill="x", pady=(self._p(14), self._p(6)))

    def _card(self, icon, title, description=None, on_click=None):
        """Settings card; returns the frame on the right that hosts the control."""
        import tkinter as tk
        c = self.colors
        card = tk.Frame(self.body, bg=c["card"], highlightthickness=1, highlightbackground=c["line"],
                        padx=self._p(16), pady=self._p(12))
        card.pack(fill="x", pady=(0, self._p(4)))
        glyph = self._glyph(card, icon, 14, c["text"], c["card"])
        glyph.pack(side="left", padx=(0, self._p(16)))
        # Packed before the texts: the control keeps its size, the description wraps.
        control = tk.Frame(card, bg=c["card"])
        control.pack(side="right", padx=(self._p(16), 0))
        texts = tk.Frame(card, bg=c["card"])
        texts.pack(side="left", fill="x", expand=True)
        labels = [tk.Label(texts, text=title, font=(self.font, 10), fg=c["text"], bg=c["card"], anchor="w")]
        labels[0].pack(fill="x")
        if description:
            labels.append(tk.Label(texts, text=description, font=(self.font, 8), fg=c["muted"],
                                   bg=c["card"], anchor="w", justify="left", wraplength=self._p(380)))
            labels[1].pack(fill="x")
            texts.bind("<Configure>", lambda event, label=labels[1]: label.configure(
                wraplength=max(self._p(120), event.width - self._p(4))))
        if on_click:
            widgets = [card, glyph, texts] + labels
            next_glyph = self._glyph(control, "next", 9, c["muted"], c["card"])
            next_glyph.pack(side="right")
            widgets.append(next_glyph)
            for widget in widgets:
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda event: on_click())
                widget.bind("<Enter>", lambda event: [w.configure(bg=c["hover"]) for w in widgets + [control]])
                widget.bind("<Leave>", lambda event: [w.configure(bg=c["card"]) for w in widgets + [control]])
        return control

    def _button(self, parent, text, command, primary=False, enabled=True):
        import tkinter as tk
        c = self.colors
        bg, fg = (c["accent"], c["on"]) if primary else (c["hover"], c["text"])
        button = tk.Button(parent, text=text, command=command, font=(self.font, 9), bg=bg, fg=fg,
                           activebackground=bg if primary else c["line"], activeforeground=fg,
                           disabledforeground=c["subtle"], relief="flat", bd=0, cursor="hand2",
                           padx=self._p(14), pady=self._p(6), highlightthickness=1,
                           highlightbackground=c["line"], highlightcolor=c["text"],
                           state="normal" if enabled else "disabled")
        if not primary:
            button.bind("<Enter>", lambda event: button.configure(bg=c["line"]))
            button.bind("<Leave>", lambda event: button.configure(bg=bg))
        return button

    def _combo(self, parent, values, current, on_select, width=30):
        import tkinter as tk
        from tkinter import ttk
        variable = tk.StringVar(value=current)
        box = ttk.Combobox(parent, textvariable=variable, values=list(values), state="readonly",
                           font=(self.font, 9), style="Card.TCombobox", width=width)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda event: on_select(variable.get()))
        return box

    def _switch(self, parent, value, on_toggle, label=None):
        import tkinter as tk
        c = self.colors
        switch = fluent.Switch(parent, c, self.scale, on_toggle, value)
        switch.canvas.pack(side="right")
        if label:
            tk.Label(parent, text=label, font=(self.font, 9), fg=c["muted"], bg=parent["bg"]).pack(
                side="right", padx=(0, self._p(10)))
        return switch

    # ── pages ───────────────────────────────────────────────────────

    def _page_home(self):
        import tkinter as tk
        from .tray_icon import TTS_MODES
        c = self.colors
        hero = tk.Frame(self.body, bg=c["card"], highlightthickness=1, highlightbackground=c["line"],
                        padx=self._p(20), pady=self._p(18))
        hero.pack(fill="x", pady=(0, self._p(4)))
        top = tk.Frame(hero, bg=c["card"])
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=self._p(12), height=self._p(12), bg=c["card"], highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, self._p(10)))
        self.state_label = tk.Label(top, font=(self.font, 15, "bold"), fg=c["text"], bg=c["card"], anchor="w")
        self.state_label.pack(side="left")
        self.engine_label = tk.Label(hero, font=(self.font, 9), fg=c["muted"], bg=c["card"], anchor="w")
        self.engine_label.pack(fill="x", pady=(self._p(4), self._p(14)))
        actions = tk.Frame(hero, bg=c["card"])
        actions.pack(fill="x")
        self.record_button = self._button(actions, "Avvia dettatura", self._toggle_recording, primary=True)
        self.record_button.pack(side="left", padx=(0, self._p(8)))
        self.files_button = self._button(actions, "Trascrivi file audio…",
                                         lambda: self.tray._transcribe_files_clicked(None, None))
        self.files_button.pack(side="left")
        keys = tk.Frame(hero, bg=c["card"])
        keys.pack(fill="x", pady=(self._p(16), 0))
        for index, key in enumerate(("Ctrl", "Alt", "Spazio")):
            if index:
                tk.Label(keys, text="+", font=(self.font, 8), fg=c["subtle"], bg=c["card"]).pack(side="left", padx=self._p(3))
            tk.Label(keys, text=key, font=(self.font, 8), fg=c["text"], bg=c["raised"], padx=self._p(7),
                     pady=self._p(2), highlightthickness=1, highlightbackground=c["line"]).pack(side="left")
        tk.Label(keys, text="  in qualsiasi app: detta, poi il testo viene incollato dove stai scrivendo.",
                 font=(self.font, 8), fg=c["muted"], bg=c["card"]).pack(side="left")

        config = self.config
        self._section("Impostazioni rapide")
        mic = next((name for name, value in self.mics.items() if value == config.get("mic_device_id")),
                   "Predefinito di Windows")
        self._card("mic", "Microfono", mic.split(" [")[0], on_click=lambda: self._go("dictation"))
        pack = config.get("sound_pack", "r2d2")
        mode = next((label for label, value in TTS_MODES.items() if value == config.get("tts_mode", "full")), "")
        sound = "Casuale" if pack == "random" else fluent.pretty_pack(pack)
        volume = "audio disattivato" if config.get("muted") else f"volume {config.get('volume', 200)}%"
        self._card("bell", "Avvisi vocali", f"{mode} · {sound} · {volume}", on_click=lambda: self._go("alerts"))
        from ..core import voice_hooks
        linked = [voice_hooks.AGENT_LABEL[a] for a, on in self.hooks.items() if on]
        self._card("link", "Collegamenti", ("Avvisi attivi per " + " e ".join(linked)) if linked
                   else "Nessun agente collegato agli avvisi vocali", on_click=lambda: self._go("links"))

    def _page_dictation(self):
        from .tray_icon import STT_MODES, WHISPER_LANGUAGES
        config = self.config
        self._section("Ingresso")
        mic = next((name for name, value in self.mics.items() if value == config.get("mic_device_id")),
                   "Predefinito di Windows")
        self._combo(self._card("mic", "Microfono", "Il dispositivo usato per la dettatura."),
                    self.mics, mic, self._apply_mic)
        self._section("Trascrizione")
        self._combo(self._card("chip", "Riconoscimento", "GPU locale, cloud o scelta automatica."),
                    STT_MODES.values(), STT_MODES.get(config.get("stt_mode", "auto"), STT_MODES["auto"]),
                    self._apply_mode)
        language = next((label for label, code in WHISPER_LANGUAGES.items()
                         if code == config.get("whisper_language")), "Rilevamento automatico")
        self._combo(self._card("globe", "Lingua", "Fissarla evita errori di riconoscimento su frasi brevi."),
                    WHISPER_LANGUAGES, language, self._apply_language)
        self._section("Testo dettato")
        from .tray_icon import auto_paste_enabled
        paste = auto_paste_enabled(config)
        self._switch(self._card("file", "Incolla automaticamente",
                                "Spento: il testo resta solo negli appunti e lo incolli tu con Ctrl+V."),
                     paste, lambda on: self._work(self.tray._toggle_auto_paste_clicked),
                     "Attivo" if paste else "Solo appunti")
        self._section("Memoria GPU")
        control = self._card("pulse", "Modello locale",
                             "Libera la VRAM quando ti serve per altro: il modello si ricarica al prossimo uso.")
        self._button(control, "Ricarica modello", lambda: self.tray._purge_vram_clicked(None, None)).pack(side="right")
        self._button(control, "Libera memoria", lambda: self.tray._unload_vram_clicked(),
                     enabled=bool(self.tray._on_unload_vram)).pack(side="right", padx=(0, self._p(8)))

    def _page_alerts(self):
        import tkinter as tk
        from .tray_icon import TTS_MODES, VOICE_PRESETS
        c = self.colors
        config = self.config
        muted = bool(config.get("muted", False))
        self._section("Audio")
        self._switch(self._card("speaker", "Suoni e voce", "Disattiva tutti gli avvisi senza perdere le impostazioni."),
                     not muted, lambda on: self._work(lambda: self.tray._make_toggle_mute()(None, None)),
                     "Attivi" if not muted else "Disattivati")
        from .tray_icon import voice_volume
        levels = (
            ("music", "Volume suoni", "Il pacchetto suoni. Oltre il 100% amplifica i suoni più deboli.",
             int(config.get("volume", 200)), self.tray._make_set_volume),
            ("chat", "Volume voci", "Gli annunci parlati, regolabili a parte rispetto ai suoni.",
             voice_volume(config), self.tray._make_set_voice_volume),
        )
        for icon, title, description, level, make_handler in levels:
            control = self._card(icon, title, description)
            value = tk.Label(control, text=f"{level}%", width=5, font=(self.font, 9),
                             fg=c["muted"], bg=c["card"], anchor="e")
            value.pack(side="right")
            slider = fluent.Slider(control, c, self.scale, level,
                                   lambda new, make=make_handler: self._work(lambda: make(new)(None, None)))
            slider.disabled = muted
            slider.listeners.append(lambda new, label=value: label.configure(text=f"{new}%"))
            slider.canvas.configure(width=self._p(220))
            slider.canvas.pack(side="right")
        self._section("Annunci")
        mode = next((label for label, v in TTS_MODES.items() if v == config.get("tts_mode", "full")), "Voce e suoni")
        self._combo(self._card("chat", "Modalità", "Quando parlare e quando usare solo un suono."),
                    TTS_MODES, mode,
                    lambda label: self._work(lambda: self.tray._make_set_tts_mode(TTS_MODES[label])(None, None)))
        voice_id = config.get("voice", {}).get("voice", "it-IT-IsabellaNeural")
        presets = dict(VOICE_PRESETS)
        current = next((name for name, data in presets.items() if data["voice"] == voice_id),
                       self.tray._voice_display_name(voice_id))
        self._combo(self._card("chat", "Voce", "Altre voci per lingua nel menu completo dell'icona."),
                    list(presets) if current in presets else [current] + list(presets), current,
                    lambda name: name in presets and self._work(
                        lambda: self.tray._make_set_voice_preset(name)(None, None)))
        self._section("Suoni")
        names = {"Casuale": "random"}
        names.update({f"{fluent.pretty_pack(name)} ({count})": name for name, count in self.packs})
        pack = config.get("sound_pack", "r2d2")
        current = next((label for label, name in names.items() if name == pack), pack)
        control = self._card("music", "Pacchetto suoni", "Il suono che accompagna ogni avviso.")
        self._button(control, "Ascolta", lambda: self._preview(names.get(box.get(), pack))).pack(
            side="right", padx=(self._p(8), 0))
        box = self._combo(control, names, current,
                          lambda label: self._work(lambda: self.tray._make_set_sound_pack(names[label])(None, None)),
                          width=24)

    def _page_links(self):
        from ..core import voice_hooks
        self._section("Avvisi vocali degli agenti")
        for agent in voice_hooks.AGENTS:
            label = voice_hooks.AGENT_LABEL[agent]
            self._switch(self._card("chat", label, f"{label} annuncia la fine delle attività e le richieste di permesso."),
                         self.hooks.get(agent, False), lambda on, a=agent: self._set_hook(a, on))
        self._section("Sistema")
        control = self._card("power", "Avvia all'accesso a Windows", "L'app parte nell'area di notifica.")
        if self._autostart is None:
            import tkinter as tk
            tk.Label(control, text="Verifica…", font=(self.font, 9), fg=self.colors["muted"],
                     bg=self.colors["card"]).pack(side="right")
        else:
            self._switch(control, self._autostart, lambda on: self._toggle_autostart())
        self._section("Riconoscimento cloud")
        control = self._card("cloud", "Chiavi API", "Servono solo per Groq o Deepgram. Restano nel portachiavi di Windows.")
        self._button(control, "Deepgram…", lambda: self.tray._ask_api_key_deepgram(None, None)).pack(side="right")
        self._button(control, "Groq…", lambda: self.tray._ask_api_key_groq(None, None)).pack(
            side="right", padx=(0, self._p(8)))
        control = self._card("refresh", "Verifica connessione", "Prova i provider cloud configurati.")
        self._button(control, "Verifica", lambda: self.tray._test_cloud_connection(None, None)).pack(side="right")

    def _page_about(self):
        from .build_info import VERSION, IS_TEST_BUILD
        self._section("Applicazione")
        self._card("info", f"parlaconclaudio {VERSION}",
                   "Build di test" if IS_TEST_BUILD else "Dettatura vocale per Windows con avvisi per Claude Code e Codex.")
        control = self._card("link", "Progetto e guida", "Documentazione, novità e segnalazioni.")
        self._button(control, "Apri", lambda: self.tray._open_repo(None, None)).pack(side="right")
        control = self._card("list", "Menu completo", "Tutte le opzioni avanzate dall'icona nell'area di notifica.")
        self._button(control, "Apri", self.tray._show_full_menu).pack(side="right")

    # ── actions ─────────────────────────────────────────────────────

    def _work(self, callback):
        if callback:
            def run():
                try:
                    callback()
                except Exception:
                    logger.exception("Azione del pannello non riuscita")
                    self.tray._notify("Operazione non riuscita. Riprova dal menu dell'icona.")
                finally:
                    self._commands.put("refresh")
            threading.Thread(target=run, daemon=True).start()

    def _toggle_recording(self):
        # Restore the previous application before the bridge starts/stops capture.
        self.root.iconify()
        self.root.after(180, lambda: self._work(self.tray._on_toggle_recording))

    def _set_hook(self, agent, enabled):
        self._work(lambda: self.tray._apply_voice_hooks([(agent, enabled)]))

    def _toggle_autostart(self):
        def run():
            from . import autostart
            result = autostart.disable() if autostart.is_enabled() else autostart.enable()
            (logger.info if result.ok else logger.error)(result.log_line())
            self.tray._notify(result.summary())
            self._autostart = autostart.is_enabled()
        self._work(run)

    def _preview(self, pack):
        from .tray_icon import preview_sound_pack
        self._work(lambda: preview_sound_pack(pack))

    def _apply_mic(self, label):
        value = self.mics[label]
        self._work(lambda: self.tray._make_set_mic(value)(None, None))

    def _apply_mode(self, label):
        from .tray_icon import STT_MODES
        mode = next(key for key, value in STT_MODES.items() if value == label)
        self._work(lambda: self.tray._make_set_stt_mode(mode)(None, None))

    def _apply_language(self, label):
        from .tray_icon import WHISPER_LANGUAGES
        code = WHISPER_LANGUAGES[label]
        self._work(lambda: self.tray._make_set_language(code)(None, None))

    # ── live state ──────────────────────────────────────────────────

    def _update_state(self):
        if not self.state_label or not self.state_label.winfo_exists():
            return
        c = self.colors
        state = self.tray._state
        batch = self.tray._batch_lock.locked()
        self.state_label.configure(text="Trascrizione file in corso" if batch else STATE_LABELS.get(state, state))
        color = {"recording": c["rec"], "transcribing": c["busy"], "loading": c["busy"],
                 "output": c["busy"]}.get(state, c["ok"])
        size = self._p(11)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, size, size, fill=c["busy"] if batch else color, outline="")
        self.engine_label.configure(text="Motore attivo: " + self.tray._get_stt_status({})["active_label"])
        self.record_button.configure(text="Ferma dettatura" if state == "recording" else "Avvia dettatura",
                                     state="normal" if self.tray._on_toggle_recording and state in ("idle", "recording") and not batch else "disabled")
        self.files_button.configure(state="normal" if state == "idle" and not batch else "disabled")

    def _tick(self):
        try:
            while True:
                command = self._commands.get_nowait()
                if command == "stop":
                    self.root.destroy()
                    return
                if command == "show":
                    self.root.deiconify()
                    self.root.lift()
                    self._check_autostart()
                if command in ("refresh", "show"):
                    focus = self.root.focus_get()
                    self._load()
                    # Re-rendering would close an open combobox list or drop a drag.
                    if not (focus and focus.winfo_class() in ("TCombobox", "ComboboxPopdown")):
                        y = self.canvas.yview()[0]
                        self._render()
                        self.root.update_idletasks()
                        self.canvas.yview_moveto(y)
        except queue.Empty:
            pass
        if self.root.state() == "withdrawn":
            # A hidden panel needs no periodic disk reads or updates.
            self.root.after(750, self._tick)
            return
        self._update_state()
        self.root.after(750, self._tick)
