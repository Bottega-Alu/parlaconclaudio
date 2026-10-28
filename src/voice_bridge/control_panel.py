"""Native, keyboard-accessible control window for the tray application.

Laid out like Windows 11 Settings: navigation on the left, setting cards on the
right. Tk belongs exclusively to this window's thread; callbacks that touch
engines or configuration run in workers. Closing hides the window and keeps
dictation alive.
"""
import logging
import queue
import threading

from . import fluent, i18n
from .fluent import ICON
from .i18n import APP_NAME, N_, _

logger = logging.getLogger(__name__)

PAGES = (
    ("home", "home", N_("Home")),
    ("dictation", "mic", N_("Dettatura")),
    ("alerts", "bell", N_("Avvisi vocali")),
    ("links", "link", N_("Collegamenti")),
    ("about", "info", N_("Informazioni")),
)
STATE_LABELS = {"idle": N_("Pronto per dettare"), "recording": N_("Registrazione in corso"),
                "transcribing": N_("Trascrizione in corso"), "loading": N_("Caricamento del motore"),
                "output": N_("Invio del testo")}


HISTORY_SHOWN = 5
OUTCOME_LABELS = {"pasted": N_("Incollata"), "clipboard": N_("Negli appunti"), "failed": N_("Non inviata")}


def _when(timestamp):
    """'14:32' today, '27/10 14:32' on other days."""
    from datetime import datetime
    try:
        moment = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return ""
    return moment.strftime("%H:%M" if moment.date() == datetime.now().date() else "%d/%m %H:%M")


def _clip(text, limit):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _join_and(items):
    """'A', 'A e B', 'A, B e C' in the interface language."""
    if len(items) < 2:
        return "".join(items)
    return _("{first} e {second}").format(first=", ".join(items[:-1]), second=items[-1])


class ControlPanel:
    def __init__(self, tray):
        self.tray = tray
        self._commands = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self._page = "home"
        self.root = None
        self._voice_language = None
        # Dictation trigger editing: recording a new combination / live test area.
        self._capturing = False
        self._held = set()
        self._trigger_error = None
        self._testing = False
        self._test_events = []
        self._autostart = None  # unknown until a worker has asked Windows
        self._history_all = False
        self._history_mtime = None

    def show(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True, name="control-panel")
                self._thread.start()
            else:
                self._commands.put("show")
                self._wake()

    def stop(self):
        self._commands.put("stop")
        self._wake()

    def _wake(self):
        """Handle queued commands now instead of at the next hidden poll."""
        try:
            self.root.event_generate("<<PanelWake>>", when="tail")
        except Exception:
            pass  # not built yet or already gone: the poll still picks it up

    def _run(self):
        from .menu_dpi import native_menu_dpi
        try:
            with native_menu_dpi():
                self._build()
                self.root.mainloop()
        except Exception:
            logger.exception("Impossibile aprire il pannello")
            self.tray._notify(_("Finestra non disponibile. Riavvia {app}.").format(app=APP_NAME))

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
        self._curtain = flyout_window.Curtain(root)
        root.title(f"{APP_NAME} • {VERSION}")
        app_icon = Path(__file__).resolve().parents[2] / "assets" / "voicebridge.ico"
        if app_icon.is_file():
            root.iconbitmap(str(app_icon))
        dpi = flyout_window.window_dpi(root)
        self.scale = dpi / 96
        root.tk.call("tk", "scaling", dpi / 72)
        root.geometry(f"{self._p(920)}x{self._p(660)}")
        root.minsize(self._p(780), self._p(560))
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.bind("<Escape>", lambda event: self._escape())
        root.bind("<KeyPress>", self._capture_key, add="+")
        root.bind("<KeyRelease>", self._capture_release, add="+")
        root.bind("<MouseWheel>", self._wheel)
        root.bind("<<PanelWake>>", lambda event: self._drain())
        self.dark = fluent.dark_mode()
        self.colors = c = fluent.colors(self.dark)
        self._theme = (self.dark, c["accent"])
        self._theme_ticks = 0
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
        self.mics = {_("Predefinito di Windows"): None}
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
        # Rebuilding the page in front of the user paints it widget by widget:
        # the curtain shows the old content until the new one is complete.
        with self._curtain.held():
            self._render_now()

    def _render_now(self):
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
        title = _(dict((key, label) for key, _icon, label in PAGES)[self._page])
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
        tk.Label(brand, text=APP_NAME, font=(self.font, 12, "bold"), fg=c["text"],
                 bg=c["surface"], anchor="w").pack(fill="x")
        tk.Label(brand, text=_("Versione {version}").format(version=VERSION), font=(self.font, 8), fg=c["subtle"],
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
            text = tk.Label(item, text=_(label), font=(self.font, 10), fg=c["text"], bg=bg, anchor="w")
            text.pack(side="left", fill="x")
            widgets = (item, glyph, text)
            for widget in widgets:
                widget.bind("<Button-1>", lambda event, k=key: self._go(k))
                if not selected:
                    widget.bind("<Enter>", lambda event, w=widgets: [x.configure(bg=c["hover"]) for x in w])
                    widget.bind("<Leave>", lambda event, w=widgets, b=bg: [x.configure(bg=b) for x in w])
            item.bind("<Return>", lambda event, k=key: self._go(k))
            item.bind("<space>", lambda event, k=key: self._go(k))
        tk.Label(self.nav, text=_("Chiudendo la finestra l'app\nresta nell'area di notifica."),
                 font=(self.font, 8), fg=c["subtle"], bg=c["surface"], justify="left",
                 anchor="w").pack(side="bottom", fill="x", padx=self._p(8))

    def _close(self):
        self._end_trigger_editing()
        self.root.withdraw()

    def _escape(self):
        if self._capturing:
            self._end_trigger_editing()
            self._render()
        else:
            self._close()

    def _go(self, page):
        if page != self._page:
            self._end_trigger_editing()
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

    def _keycaps(self, parent, bg, caps=None, with_mouse=True):
        import tkinter as tk
        from .dictation_trigger import MOUSE_BUTTONS, key_caps
        c = self.colors
        trigger = self.tray.dictation_trigger()

        def cap(text):
            tk.Label(parent, text=text, font=(self.font, 8), fg=c["text"], bg=c["raised"], padx=self._p(7),
                     pady=self._p(2), highlightthickness=1, highlightbackground=c["line"]).pack(side="left")

        for index, key in enumerate(caps or key_caps(trigger)):
            if index:
                tk.Label(parent, text="+", font=(self.font, 8), fg=c["subtle"], bg=bg).pack(side="left", padx=self._p(3))
            cap(_(key))
        if caps is None and with_mouse and trigger.kind == "both":
            tk.Label(parent, text=_("oppure"), font=(self.font, 8), fg=c["subtle"], bg=bg).pack(
                side="left", padx=self._p(6))
            cap(_(MOUSE_BUTTONS[trigger.button]))

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
        variable = tk.StringVar(master=parent, value=current)
        box = ttk.Combobox(parent, textvariable=variable, values=list(values), state="readonly",
                           font=(self.font, 9), style="Card.TCombobox", width=width)
        box.pack(side="right")
        box.bind("<<ComboboxSelected>>", lambda event: on_select(variable.get()))
        return box

    def _segmented(self, parent, options, current, on_select):
        """Every choice visible at once, the current one filled with the accent."""
        import tkinter as tk
        c = self.colors
        row = tk.Frame(parent, bg=c["line"], padx=1, pady=1)
        row.pack(side="right")
        for index, (value, label) in enumerate(options):
            selected = value == current
            bg, fg = (c["accent"], c["on"]) if selected else (c["raised"], c["text"])
            button = tk.Button(row, text=label, font=(self.font, 9, "bold" if selected else "normal"),
                               bg=bg, fg=fg, activebackground=bg if selected else c["hover"],
                               activeforeground=fg, relief="flat", bd=0, cursor="hand2",
                               padx=self._p(12), pady=self._p(6), highlightthickness=0,
                               command=(lambda v=value: on_select(v)) if not selected else None)
            button.pack(side="left", padx=(1 if index else 0, 0))
            if not selected:
                button.bind("<Enter>", lambda event, b=button: b.configure(bg=c["hover"]))
                button.bind("<Leave>", lambda event, b=button, color=bg: b.configure(bg=color))
        return row

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
        self.record_button = self._button(actions, _("Avvia dettatura"), self._toggle_recording, primary=True)
        self.record_button.pack(side="left", padx=(0, self._p(8)))
        self.files_button = self._button(actions, _("Trascrivi file audio…"),
                                         lambda: self.tray._transcribe_files_clicked(None, None))
        self.files_button.pack(side="left")
        keys = tk.Frame(hero, bg=c["card"])
        keys.pack(fill="x", pady=(self._p(16), 0))
        self._keycaps(keys, c["card"])
        from .tray_icon import auto_paste_enabled, sound_volume
        # Own line: next to long keycaps ("Ctrl + Alt + Spazio oppure Tasto laterale…") it was cut.
        tk.Label(hero, text=(_("in qualsiasi app: detta, poi il testo viene incollato dove stai scrivendo.")
                             if auto_paste_enabled(self.config) else
                             _("in qualsiasi app: detta, poi il testo viene copiato negli appunti.")),
                 font=(self.font, 8), fg=c["muted"], bg=c["card"], anchor="w").pack(fill="x", pady=(self._p(6), 0))

        self._appearance_section()
        self._history_section()
        self._quick_line()

    def _appearance_section(self):
        self._section(_("Aspetto"))
        self._segmented(self._card("settings", _("Tema"), _("Chiaro, scuro o come Windows.")),
                        [("system", _("Sistema")), ("light", _("Chiaro")), ("dark", _("Scuro"))],
                        fluent.theme(), self._apply_theme)
        # Language names stay in their own language: findable whatever is shown now.
        self._segmented(self._card("globe", _("Lingua"), _("Menu, finestre e notifiche.")),
                        [(i18n.SYSTEM, _("Sistema"))] + list(i18n.LANGUAGES.items()),
                        self.config.get("ui_language", i18n.SOURCE), self._apply_ui_language)

    def _history_section(self):
        import tkinter as tk
        from . import history
        c = self.colors
        try:
            self._history_mtime = history.history_file().stat().st_mtime_ns
        except OSError:
            self._history_mtime = None
        saved = list(reversed(history.entries()))
        head = tk.Frame(self.body, bg=c["surface"])
        head.pack(fill="x", pady=(self._p(14), self._p(6)))
        tk.Label(head, text=_("Ultime trascrizioni"), font=(self.font, 10, "bold"), fg=c["text"],
                 bg=c["surface"], anchor="w").pack(side="left")
        if not saved:
            self._card("file", _("Ancora nessuna trascrizione"),
                       _("Quello che detti compare qui, anche se non è stato incollato."))
            return
        self._button(head, _("Cancella storico"), self._clear_history).pack(side="right")
        for item in saved if self._history_all else saved[:HISTORY_SHOWN]:
            self._history_row(item)
        if len(saved) > HISTORY_SHOWN:
            more = tk.Label(self.body, text=_("Mostra meno") if self._history_all
                            else _("Mostra tutte ({count})").format(count=len(saved)),
                            font=(self.font, 9), fg=c["accent"], bg=c["surface"], cursor="hand2", anchor="w")
            more.pack(fill="x", pady=(self._p(2), 0))
            more.bind("<Button-1>", lambda event: self._toggle_history())

    def _history_row(self, item):
        import tkinter as tk
        c = self.colors
        card = tk.Frame(self.body, bg=c["card"], highlightthickness=1, highlightbackground=c["line"],
                        padx=self._p(16), pady=self._p(10))
        card.pack(fill="x", pady=(0, self._p(4)))
        copy = self._button(card, _("Copia"), None)
        copy.configure(command=lambda: self._copy_text(item["text"], copy))
        copy.pack(side="right", padx=(self._p(16), 0))
        texts = tk.Frame(card, bg=c["card"])
        texts.pack(side="left", fill="x", expand=True)
        outcome = item.get("outcome", "failed")
        meta = f"{_when(item.get('ts'))} · {_(OUTCOME_LABELS.get(outcome, OUTCOME_LABELS['failed']))}"
        tk.Label(texts, text=meta, font=(self.font, 8), fg=c["rec"] if outcome == "failed" else c["muted"],
                 bg=c["card"], anchor="w").pack(fill="x")
        body = tk.Label(texts, text=_clip(item["text"], 220), font=(self.font, 10), fg=c["text"],
                        bg=c["card"], anchor="w", justify="left", wraplength=self._p(420))
        body.pack(fill="x")
        texts.bind("<Configure>", lambda event: body.configure(
            wraplength=max(self._p(120), event.width - self._p(4))))

    def _quick_line(self):
        """Quick settings on one line: each part opens its page."""
        import tkinter as tk
        from .tray_icon import TTS_MODES, sound_volume
        from ..core import voice_hooks
        c = self.colors
        config = self.config
        mic = next((name for name, value in self.mics.items() if value == config.get("mic_device_id")),
                   _("Predefinito di Windows"))
        mode = _(next((label for label, value in TTS_MODES.items() if value == config.get("tts_mode", "full")), ""))
        pack = config.get("sound_pack", "r2d2")
        sound = _("Casuale") if pack == "random" else fluent.pretty_pack(pack)
        volume = _("audio disattivato") if config.get("muted") else f"{sound_volume(config)}%"
        linked = [voice_hooks.AGENT_LABEL[a] for a, on in self.hooks.items() if on]
        parts = (
            ("mic", _clip(mic.split(" [")[0], 26), "dictation"),
            ("bell", _clip(f"{mode} · {sound} · {volume}", 40), "alerts"),
            ("link", _clip(_join_and(linked), 26) if linked else _("Nessun agente"), "links"),
        )
        self._section(_("Impostazioni rapide"))
        line = tk.Frame(self.body, bg=c["card"], highlightthickness=1, highlightbackground=c["line"],
                        padx=self._p(12), pady=self._p(8))
        line.pack(fill="x")
        for icon, text, page in parts:
            chip = tk.Frame(line, bg=c["card"], cursor="hand2", padx=self._p(6), pady=self._p(3))
            chip.pack(side="left", padx=(0, self._p(8)))
            glyph = self._glyph(chip, icon, 10, c["muted"], c["card"])
            glyph.pack(side="left", padx=(0, self._p(6)))
            label = tk.Label(chip, text=text, font=(self.font, 9), fg=c["text"], bg=c["card"], cursor="hand2")
            label.pack(side="left")
            widgets = (chip, glyph, label)
            for widget in widgets:
                widget.bind("<Button-1>", lambda event, p=page: self._go(p))
                widget.bind("<Enter>", lambda event, w=widgets: [x.configure(bg=c["hover"]) for x in w])
                widget.bind("<Leave>", lambda event, w=widgets: [x.configure(bg=c["card"]) for x in w])

    def _page_dictation(self):
        from .tray_icon import STT_MODES, WHISPER_LANGUAGES
        config = self.config
        self._trigger_section()
        self._section(_("Ingresso"))
        mic = next((name for name, value in self.mics.items() if value == config.get("mic_device_id")),
                   _("Predefinito di Windows"))
        self._combo(self._card("mic", _("Microfono"), _("Il dispositivo usato per la dettatura.")),
                    self.mics, mic, self._apply_mic)
        self._section(_("Trascrizione"))
        self._combo(self._card("chip", _("Riconoscimento"), self._engine_summary()),
                    [_(v) for v in STT_MODES.values()],
                    _(STT_MODES.get(config.get("stt_mode", "auto"), STT_MODES["auto"])),
                    self._apply_mode)
        language = _(next((label for label, code in WHISPER_LANGUAGES.items()
                           if code == config.get("whisper_language")), "Rilevamento automatico"))
        self._combo(self._card("globe", _("Lingua"), _("Fissarla evita errori di riconoscimento su frasi brevi.")),
                    [_(label) for label in WHISPER_LANGUAGES], language, self._apply_language)
        self._section(_("Testo dettato"))
        from .tray_icon import auto_paste_enabled
        paste = auto_paste_enabled(config)
        self._switch(self._card("file", _("Incolla automaticamente"),
                                _("Spento: il testo resta solo negli appunti e lo incolli tu con Ctrl+V.")),
                     paste, lambda on: self._work(self.tray._toggle_auto_paste_clicked),
                     _("Attivo") if paste else _("Solo appunti"))
        self._section(_("Memoria GPU"))
        control = self._card("pulse", _("Modello locale"),
                             _("Libera la VRAM quando ti serve per altro: il modello si ricarica al prossimo uso."))
        self._button(control, _("Ricarica modello"), lambda: self.tray._purge_vram_clicked(None, None)).pack(side="right")
        self._button(control, _("Libera memoria"), lambda: self.tray._unload_vram_clicked(),
                     enabled=bool(self.tray._on_unload_vram)).pack(side="right", padx=(0, self._p(8)))

    def _trigger_section(self):
        import tkinter as tk
        from .dictation_trigger import MODES, MOUSE_BUTTONS
        c = self.colors
        trigger = self.tray.dictation_trigger()
        self._section(_("Comando di dettatura"))
        kinds = {_("Combinazione di tasti"): "keys", _("Tasto del mouse"): "mouse",
                 _("Tasti oppure mouse"): "both"}
        current = next(label for label, kind in kinds.items() if kind == trigger.kind)
        self._combo(self._card("settings", _("Comando"),
                               _("Un tasto del mouse assegnato alla dettatura non arriva più alle altre app.")),
                    kinds, current, lambda label: self._save_trigger(kind=kinds[label]))
        if trigger.kind in ("mouse", "both"):
            buttons = {_(label): button for button, label in MOUSE_BUTTONS.items()}
            self._combo(self._card("settings", _("Tasto del mouse"), _("Il tasto centrale o uno dei laterali.")),
                        buttons, _(MOUSE_BUTTONS[trigger.button]),
                        lambda label: self._save_trigger(button=buttons[label]))
        if trigger.kind in ("keys", "both"):
            control = self._card("list", _("Combinazione"),
                                 _(self._trigger_error) if self._trigger_error else
                                 (_("Premi la combinazione da usare. Esc per annullare.")
                                  if self._capturing else
                                  _("Funziona in qualsiasi app, anche a finestra chiusa.")))
            if self._capturing:
                tk.Label(control, text=_("In ascolto…"), font=(self.font, 9, "bold"), fg=c["accent"],
                         bg=c["card"]).pack(side="right")
            else:
                self._button(control, _("Cambia…"), self._start_capture).pack(side="right", padx=(self._p(12), 0))
                caps = tk.Frame(control, bg=c["card"])
                caps.pack(side="right")
                self._keycaps(caps, c["card"], with_mouse=False)  # the mouse button has its own card
        self._combo(self._card("refresh", _("Modalità"), _("Come si comporta il comando.")),
                    [_(v) for v in MODES.values()], _(MODES[trigger.mode]),
                    lambda label: self._save_trigger(mode=next(k for k, v in MODES.items() if _(v) == label)))
        control = self._card("pulse", _("Prova il comando"),
                             self._test_summary() if self._testing else
                             _("Usa il comando qui senza avviare una dettatura vera."))
        self._button(control, _("Termina prova") if self._testing else _("Avvia prova"),
                     self._toggle_test, primary=self._testing).pack(side="right")

    def _test_summary(self):
        if not self._test_events:
            return _("In attesa: usa il comando adesso, in qualsiasi finestra.")
        labels = {"press": _("avvio"), "release": _("fine")}
        recent = " → ".join(labels[event] for event in self._test_events[-6:])
        return _("Ricevuto: {events}. Funziona.").format(events=recent)

    def _save_trigger(self, **changes):
        from dataclasses import replace
        trigger = replace(self.tray.dictation_trigger(), **changes)
        self._trigger_error = None
        self._work(lambda: self.tray._set_dictation_trigger(trigger))

    def _start_capture(self):
        self._end_trigger_editing()
        self._capturing, self._held, self._trigger_error = True, set(), None
        if self.tray._dictation_trigger is not None:
            self.tray._dictation_trigger.pause()
        self._render()
        self.root.focus_force()

    def _capture_key(self, event):
        if not self._capturing:
            return None
        from .dictation_trigger import combo_from_tk, modifier_from_tk
        modifier = modifier_from_tk(event.keysym)
        if modifier:
            self._held.add(modifier)
            return "break"
        if event.keysym == "Escape":
            return None  # handled by _escape
        combo, error = combo_from_tk(self._held, event.keysym)
        if error:
            self._trigger_error = error
            self._render()
            return "break"
        if combo:
            self._end_trigger_editing()
            # A new combination keeps "keys or mouse" if that was the choice.
            self._save_trigger(kind="both" if self.tray.dictation_trigger().kind == "both" else "keys",
                               keys=combo)
        return "break"

    def _capture_release(self, event):
        if self._capturing:
            from .dictation_trigger import modifier_from_tk
            self._held.discard(modifier_from_tk(event.keysym))
            return "break"
        return None

    def _toggle_test(self):
        if self._testing:
            self._end_trigger_editing()
        else:
            self._end_trigger_editing()
            listener = self.tray._dictation_trigger
            if listener is None:
                self._trigger_error = N_("La prova funziona solo con la dettatura avviata.")
            else:
                self._testing, self._test_events = True, []
                listener.reset_toggle()
                listener.test_sink = self._test_event
        self._render()

    def _test_event(self, kind):
        # Runs on the listener's thread: hand over to Tk.
        self._test_events.append(kind)
        self._commands.put("refresh")
        self._wake()

    def _end_trigger_editing(self):
        listener = self.tray._dictation_trigger
        if self._capturing:
            self._capturing = False
            self._held = set()
            if listener is not None:
                listener.resume()
        if self._testing:
            self._testing = False
            if listener is not None:
                listener.test_sink = None
                listener.reset_toggle()

    def _engine_summary(self):
        status = self.tray._get_stt_status(self.config)
        gpu = _({"loaded": N_("caricata"), "not_loaded": N_("non ancora caricata"),
                 "not_detected": N_("non rilevata"), "cloud": N_("presente, non in uso")}.get(
            status["gpu_state"], N_("non disponibile")))
        engine = _(status["active_label"])
        if status["gpu"] not in ("Unknown", "Not detected"):
            summary = _("In uso: {engine} · GPU {state} ({name})").format(engine=engine, state=gpu, name=status["gpu"])
        else:
            summary = _("In uso: {engine} · GPU {state}").format(engine=engine, state=gpu)
        if status["active"] == "disabled":
            summary += ". " + _("Configura un provider cloud per dettare.")
        return summary

    def _page_alerts(self):
        import tkinter as tk
        from .tray_icon import TTS_MODES, VOICE_PRESETS
        c = self.colors
        config = self.config
        muted = bool(config.get("muted", False))
        self._section(_("Audio"))
        self._switch(self._card("speaker", _("Suoni e voce"), _("Disattiva tutti gli avvisi senza perdere le impostazioni.")),
                     not muted, lambda on: self._work(lambda: self.tray._make_toggle_mute()(None, None)),
                     _("Attivi") if not muted else _("Disattivati"))
        from .tray_icon import MAX_VOLUME, sound_volume, voice_volume
        levels = (
            ("music", _("Volume suoni"), _("Il pacchetto suoni. Oltre il 100% amplifica i suoni più deboli."),
             sound_volume(config), self.tray._make_set_volume),
            ("chat", _("Volume voci"), _("Gli annunci parlati, regolabili a parte rispetto ai suoni."),
             voice_volume(config), self.tray._make_set_voice_volume),
        )
        for icon, title, description, level, make_handler in levels:
            control = self._card(icon, title, description)
            value = tk.Label(control, text=f"{level}%", width=5, font=(self.font, 9),
                             fg=c["muted"], bg=c["card"], anchor="e")
            value.pack(side="right")
            slider = fluent.Slider(control, c, self.scale, level,
                                   lambda new, make=make_handler: self._work(lambda: make(new)(None, None)),
                                   maximum=MAX_VOLUME)
            slider.disabled = muted
            slider.listeners.append(lambda new, label=value: label.configure(text=f"{new}%"))
            slider.canvas.configure(width=self._p(220))
            slider.canvas.pack(side="right")
        self._section(_("Annunci"))
        modes = {_(label): value for label, value in TTS_MODES.items()}
        mode = _(next((label for label, v in TTS_MODES.items() if v == config.get("tts_mode", "full")), "Voce e suoni"))
        self._combo(self._card("chat", _("Modalità"), _("Quando parlare e quando usare solo un suono.")),
                    modes, mode,
                    lambda label: self._work(lambda: self.tray._make_set_tts_mode(modes[label])(None, None)))
        voice_id = config.get("voice", {}).get("voice", "it-IT-IsabellaNeural")
        presets = dict(VOICE_PRESETS)
        shown = {_(name): name for name in presets}  # label shown -> preset name
        current = next((_(name) for name, data in presets.items() if data["voice"] == voice_id),
                       self.tray._voice_display_name(voice_id))
        self._combo(self._card("chat", _("Voce rapida"), _("Le voci più usate.")),
                    list(shown) if current in shown else [current] + list(shown), current,
                    lambda label: label in shown and self._work(
                        lambda: self.tray._make_set_voice_preset(shown[label])(None, None)))
        self._all_voices(voice_id)
        self._section(_("Suoni"))
        names = {_("Casuale"): "random"}
        names.update({f"{fluent.pretty_pack(name)} ({count})": name for name, count in self.packs})
        pack = config.get("sound_pack", "r2d2")
        current = next((label for label, name in names.items() if name == pack), pack)
        control = self._card("music", _("Pacchetto suoni"), _("Il suono che accompagna ogni avviso."))
        self._button(control, _("Ascolta"), lambda: self._preview(names.get(box.get(), pack))).pack(
            side="right", padx=(self._p(8), 0))
        box = self._combo(control, names, current,
                          lambda label: self._work(lambda: self.tray._make_set_sound_pack(names[label])(None, None)),
                          width=24)

    def _all_voices(self, voice_id):
        from .tray_icon import EDGE_VOICES
        language = self._voice_language or next(
            (lang for lang, voices in EDGE_VOICES.items() if any(v["id"] == voice_id for v in voices)),
            next(iter(EDGE_VOICES)))
        voices = {(_("{name} (femminile)") if v["gender"] == "F" else _("{name} (maschile)")).format(name=v["name"]):
                  v["id"] for v in EDGE_VOICES[language]}
        current = next((label for label, vid in voices.items() if vid == voice_id), _("Scegli…"))
        languages = {_(lang): lang for lang in EDGE_VOICES}  # label shown -> language key
        control = self._card("globe", _("Tutte le voci"), _("Scegli la lingua, poi la voce."))
        self._combo(control, voices, current,
                    lambda label: self._work(lambda: self.tray._make_set_voice(voices[label])(None, None)),
                    width=22)
        self._combo(control, list(languages), _(language),
                    lambda label: self._pick_voice_language(languages[label]), width=14).pack_configure(
            padx=(0, self._p(8)))

    def _pick_voice_language(self, language):
        self._voice_language = language
        self._commands.put("refresh")
        self._wake()

    def _page_links(self):
        from ..core import voice_hooks
        self._section(_("Avvisi vocali degli agenti"))
        for agent in voice_hooks.AGENTS:
            label = voice_hooks.AGENT_LABEL[agent]
            self._switch(self._card("chat", label, _("{agent} annuncia la fine delle attività e le richieste di permesso.").format(agent=label)),
                         self.hooks.get(agent, False), lambda on, a=agent: self._set_hook(a, on))
        self._section(_("Sistema"))
        control = self._card("power", _("Avvia all'accesso a Windows"), _("L'app parte nell'area di notifica."))
        if self._autostart is None:
            import tkinter as tk
            tk.Label(control, text=_("Verifica…"), font=(self.font, 9), fg=self.colors["muted"],
                     bg=self.colors["card"]).pack(side="right")
        else:
            self._switch(control, self._autostart, lambda on: self._toggle_autostart())
        self._section(_("Riconoscimento cloud"))
        from ..core.stt_engine.key_manager import KeyManager
        for provider, label, ask, get in (
                ("groq", "Groq", self.tray._ask_api_key_groq, self.tray._open_groq_console),
                ("deepgram", "Deepgram", self.tray._ask_api_key_deepgram, self.tray._open_deepgram_console)):
            configured = bool(KeyManager.get_key(provider))
            control = self._card("cloud", _("Chiave {provider}").format(provider=label),
                                 _("Configurata. Resta nel portachiavi di Windows.") if configured
                                 else _("Non configurata. Resta nel portachiavi di Windows."))
            self._button(control, _("Imposta…"), lambda ask=ask: ask(None, None)).pack(side="right")
            self._button(control, _("Ottieni chiave"), lambda get=get: get(None, None)).pack(
                side="right", padx=(0, self._p(8)))
        control = self._card("refresh", _("Verifica connessione"), _("Prova i provider cloud configurati."))
        self._button(control, _("Verifica"), lambda: self.tray._test_cloud_connection(None, None)).pack(side="right")
        self._section(_("Correzione del testo"))
        self._switch(self._card("chat", _("Correggi la trascrizione con un LLM"),
                                _("Sistema punteggiatura e parole fraintese tramite OpenRouter, "
                                  "con la tua chiave. Senza chiave il testo resta com'è.")),
                     bool(self.config.get("enable_stt_cleanup", True)),
                     lambda on: self._work(lambda: self.tray._set_text_cleanup(on)))
        configured = bool(KeyManager.get_key("openrouter"))
        control = self._card("cloud", _("Chiave {provider}").format(provider="OpenRouter"),
                             _("Configurata. Resta nel portachiavi di Windows.") if configured
                             else _("Non configurata. Resta nel portachiavi di Windows."))
        self._button(control, _("Imposta…"),
                     lambda: self.tray._ask_api_key_openrouter(None, None, on_done=self._refresh_soon)).pack(side="right")
        self._button(control, _("Ottieni chiave"), lambda: self.tray._open_openrouter_console(None, None)).pack(
            side="right", padx=(0, self._p(8)))

    def _page_about(self):
        from .build_info import VERSION, IS_TEST_BUILD
        self._section(_("Applicazione"))
        self._card("info", f"{APP_NAME} {VERSION}",
                   _("Build di test") if IS_TEST_BUILD else _("Dettatura vocale per Windows con avvisi per Claude Code e Codex."))
        control = self._card("link", _("Progetto e guida"), _("Documentazione, novità e segnalazioni."))
        self._button(control, _("Apri"), lambda: self.tray._open_repo(None, None)).pack(side="right")
        control = self._card("power", _("Esci"), _("Chiude l'app: dettatura e avvisi si fermano fino al prossimo avvio."))
        self._button(control, _("Esci"), self._exit).pack(side="right")

    # ── actions ─────────────────────────────────────────────────────

    def _refresh_soon(self):
        """From any thread: reload settings and redraw the page."""
        self._commands.put("refresh")
        self._wake()

    def _apply_theme(self, choice):
        self.tray._set_ui_theme(choice)
        self._load()
        if not self._follow_theme():
            self._render()  # same colours (e.g. Sistema -> Scuro on a dark Windows): show the choice

    def _apply_ui_language(self, setting):
        self.tray._set_ui_language(setting)
        self._load()
        self._render()

    def _toggle_history(self):
        self._history_all = not self._history_all
        self._render()

    def _clear_history(self):
        from . import history
        history.clear()
        self._history_all = False
        self._render()

    def _copy_text(self, text, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        button.configure(text=_("Copiato"))
        self.root.after(1500, lambda: button.winfo_exists() and button.configure(text=_("Copia")))

    def _work(self, callback):
        if callback:
            def run():
                try:
                    callback()
                except Exception:
                    logger.exception("Azione del pannello non riuscita")
                    self.tray._notify(_("Operazione non riuscita. Riprova."))
                finally:
                    self._commands.put("refresh")
            threading.Thread(target=run, daemon=True).start()

    def _exit(self):
        self._close()
        threading.Thread(target=self.tray._exit_clicked, args=(None, None), daemon=True).start()

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
        mode = next(key for key, value in STT_MODES.items() if _(value) == label)
        self._work(lambda: self.tray._make_set_stt_mode(mode)(None, None))

    def _apply_language(self, label):
        from .tray_icon import WHISPER_LANGUAGES
        code = next(code for name, code in WHISPER_LANGUAGES.items() if _(name) == label)
        self._work(lambda: self.tray._make_set_language(code)(None, None))

    # ── live state ──────────────────────────────────────────────────

    def _update_state(self):
        if not self.state_label or not self.state_label.winfo_exists():
            return
        c = self.colors
        state = self.tray._state
        batch = self.tray._batch_lock.locked()
        self.state_label.configure(text=_("Trascrizione file in corso") if batch else _(STATE_LABELS.get(state, state)))
        color = {"recording": c["rec"], "transcribing": c["busy"], "loading": c["busy"],
                 "output": c["busy"]}.get(state, c["ok"])
        size = self._p(11)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, size, size, fill=c["busy"] if batch else color, outline="")
        self.engine_label.configure(
            text=_("Motore attivo: {engine}").format(engine=_(self.tray._get_stt_status({})["active_label"])))
        self.record_button.configure(text=_("Ferma dettatura") if state == "recording" else _("Avvia dettatura"),
                                     state="normal" if self.tray._on_toggle_recording and state in ("idle", "recording") and not batch else "disabled")
        self.files_button.configure(state="normal" if state == "idle" and not batch else "disabled")

    def _tick(self):
        if not self._drain():
            return
        if self.root.state() == "withdrawn":
            # A hidden panel needs no periodic disk reads or updates.
            self.root.after(750, self._tick)
            return
        self._theme_ticks += 1
        if self._theme_ticks % 4 == 0:  # ~3 s: an open window follows a light/dark switch
            self._follow_theme()
        if self._page == "home" and self._history_changed():
            y = self.canvas.yview()[0]
            self._render()
            self.root.update_idletasks()
            self.canvas.yview_moveto(y)
        self._update_state()
        self.root.after(750, self._tick)

    def _history_changed(self):
        from . import history
        try:
            mtime = history.history_file().stat().st_mtime_ns
        except OSError:
            mtime = None
        return mtime != self._history_mtime

    def _follow_theme(self):
        """Repaint if light/dark or the accent changed; True when it did."""
        dark = fluent.dark_mode()
        if (dark, fluent.accent(dark)) == self._theme:
            return False
        self.dark = dark
        self.colors = c = fluent.colors(dark)
        self._theme = (dark, c["accent"])
        self.root.configure(bg=c["surface"])
        for frame in (self.nav, self.content_holder):
            frame.configure(bg=c["surface"])
        self._style()
        fluent.dark_title_bar(self.root, dark)
        self._render()
        return True

    def _drain(self):
        """Run queued commands; False once the window is destroyed."""
        try:
            while True:
                command = self._commands.get_nowait()
                if command == "stop":
                    self.root.destroy()
                    return False
                if command == "show":
                    self._follow_theme()
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
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
        return True
