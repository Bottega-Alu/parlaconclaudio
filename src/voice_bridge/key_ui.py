"""Key dialog and key tooltip of the control window (Windows 11 look, panel Tk thread only).

KeyDialog replaces the old simpledialog prompt: masked entry with show/paste, the
current key in masked form, a live check against the provider before saving, and
removal. KeyTooltip shows, on hover over a key card, the masked key, where it is
stored and what the provider reports (budget, renewal, expiry). Network work runs
in workers; results come back through panel._call_soon (Tk is single-threaded).
"""
import logging
import threading
import webbrowser

from . import fluent, key_info
from .i18n import _

logger = logging.getLogger(__name__)


def _resolve(provider):
    from ..core.stt_engine.key_manager import KeyManager
    return KeyManager.resolve(provider)


class KeyDialog:
    """Modal child of the panel window; on_saved() runs on the panel thread after a save or removal."""

    def __init__(self, panel, provider, on_saved=None):
        import tkinter as tk
        self.panel, self.provider, self.on_saved = panel, provider, on_saved
        self.meta = key_info.PROVIDERS[provider]
        self.current, self.source = _resolve(provider)
        self._force = False
        self._busy = False
        c, p, font = panel.colors, panel._p, panel.font
        self.top = top = tk.Toplevel(panel.root, bg=c["surface"])
        top.withdraw()
        top.title(_("Chiave {provider}").format(provider=self.meta["label"]))
        top.transient(panel.root)
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda event: self.close())
        top.bind("<Return>", lambda event: self.save())

        body = tk.Frame(top, bg=c["surface"], padx=p(24), pady=p(20))
        body.pack(fill="both", expand=True)
        head = tk.Frame(body, bg=c["surface"])
        head.pack(fill="x")
        panel._glyph(head, "cloud", 18, c["accent"], c["surface"]).pack(side="left", padx=(0, p(12)))
        tk.Label(head, text=_("Chiave {provider}").format(provider=self.meta["label"]),
                 font=(font, 15, "bold"), fg=c["text"], bg=c["surface"]).pack(side="left")
        tk.Label(body, text=_(self.meta["about"]), font=(font, 9), fg=c["muted"], bg=c["surface"],
                 justify="left", anchor="w", wraplength=p(420)).pack(fill="x", pady=(p(8), p(14)))

        if self.current:
            row = tk.Frame(body, bg=c["card"], highlightthickness=1, highlightbackground=c["line"],
                           padx=p(12), pady=p(8))
            row.pack(fill="x", pady=(0, p(14)))
            tk.Label(row, text="●", font=(font, 9), fg=c["ok"], bg=c["card"]).pack(side="left", padx=(0, p(8)))
            tk.Label(row, text=_("Chiave attuale"), font=(font, 9), fg=c["muted"], bg=c["card"]).pack(side="left")
            tk.Label(row, text=key_info.mask(self.current), font=("Consolas", 10), fg=c["text"],
                     bg=c["card"]).pack(side="left", padx=p(10))
            tk.Label(row, text=_(key_info.SOURCES[self.source]), font=(font, 8), fg=c["subtle"],
                     bg=c["card"]).pack(side="right")
        if self.source == "env":
            tk.Label(body, text=_("La variabile d'ambiente {name} ha la precedenza: finché esiste, la chiave "
                                  "salvata qui non viene usata.").format(name=f"{provider.upper()}_API_KEY"),
                     font=(font, 8), fg=c["busy"], bg=c["surface"], justify="left", anchor="w",
                     wraplength=p(420)).pack(fill="x", pady=(0, p(12)))

        tk.Label(body, text=_("Nuova chiave") if self.current else _("Chiave"), font=(font, 9, "bold"),
                 fg=c["text"], bg=c["surface"], anchor="w").pack(fill="x", pady=(0, p(4)))
        field = tk.Frame(body, bg=c["raised"], highlightthickness=1, highlightbackground=c["line"],
                         highlightcolor=c["accent"])
        field.pack(fill="x")
        self.value = tk.StringVar(master=top)
        self.value.trace_add("write", lambda *args: self._edited())
        self.entry = tk.Entry(field, textvariable=self.value, show="•", font=("Consolas", 11), relief="flat", bd=0,
                              bg=c["raised"], fg=c["text"], insertbackground=c["text"], width=40)
        self.entry.pack(side="left", fill="x", expand=True, padx=p(10), pady=p(8))
        self.entry.bind("<FocusIn>", lambda event: field.configure(highlightbackground=c["accent"]))
        self.entry.bind("<FocusOut>", lambda event: field.configure(highlightbackground=c["line"]))
        self.reveal = self._flat(field, _("Mostra"), self._toggle_reveal)
        self.reveal.pack(side="right", padx=(0, p(6)))
        self._flat(field, _("Incolla"), self._paste).pack(side="right")
        self.status = tk.Label(body, text=" ", font=(font, 9), fg=c["muted"], bg=c["surface"], anchor="w",
                               justify="left", wraplength=p(420))
        self.status.pack(fill="x", pady=(p(8), 0))

        footer = tk.Frame(top, bg=c["footer"], padx=p(24), pady=p(14))
        footer.pack(fill="x")
        self.save_button = panel._button(footer, _("Verifica e salva"), self.save, primary=True, enabled=False)
        self.save_button.pack(side="right")
        panel._button(footer, _("Annulla"), self.close).pack(side="right", padx=(0, p(8)))
        link = tk.Label(footer, text=_("Ottieni nuova chiave") + "  ↗", font=(font, 9, "underline"),
                        fg=c["accent"], bg=c["footer"], cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda event: webbrowser.open(self.meta["console"]))
        if self.current and self.source != "env":
            panel._button(footer, _("Rimuovi"), self.remove).pack(side="left", padx=(p(12), 0))

        self._prefill()
        top.update_idletasks()
        root = panel.root
        x = root.winfo_rootx() + (root.winfo_width() - top.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - top.winfo_reqheight()) // 3
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
        top.deiconify()
        fluent.dark_title_bar(top, panel.dark)
        top.grab_set()
        self.entry.focus_set()

    def _flat(self, parent, text, command):
        import tkinter as tk
        c = self.panel.colors
        button = tk.Button(parent, text=text, command=command, font=(self.panel.font, 8), bg=c["raised"],
                           fg=c["muted"], activebackground=c["hover"], activeforeground=c["text"], relief="flat",
                           bd=0, cursor="hand2", padx=self.panel._p(8), pady=self.panel._p(2))
        button.bind("<Enter>", lambda event: button.configure(bg=c["hover"]))
        button.bind("<Leave>", lambda event: button.configure(bg=c["raised"]))
        return button

    def _say(self, text, tone="muted"):
        if self.top.winfo_exists():
            self.status.configure(text=text, fg=self.panel.colors[tone])

    def _clipboard(self):
        try:
            return self.top.clipboard_get().strip()
        except Exception:
            return ""

    def _prefill(self):
        """A key with the provider's prefix already on the clipboard is filled in for the user."""
        text = self._clipboard()
        if key_info.looks_like(self.provider, text) and text != self.current:
            self.value.set(text)
            self._say(_("Chiave trovata negli appunti: controlla e salva."))

    def _paste(self):
        text = self._clipboard()
        if text:
            self.value.set(text)
        else:
            self._say(_("Gli appunti sono vuoti."), "rec")

    def _toggle_reveal(self):
        hidden = self.entry.cget("show") == "•"
        self.entry.configure(show="" if hidden else "•")
        self.reveal.configure(text=_("Nascondi") if hidden else _("Mostra"))

    def _edited(self):
        self._force = False
        self.save_button.configure(text=_("Verifica e salva"),
                                   state="normal" if self.value.get().strip() and not self._busy else "disabled")

    def save(self):
        key = self.value.get().strip()
        if not key or self._busy:
            return
        if self._force:
            self._store(key)
            return
        self._busy = True
        self.save_button.configure(state="disabled")
        self._say(_("Verifica della chiave con {provider}…").format(provider=self.meta["label"]))

        def run():
            report = key_info.check(self.provider, key, use_cache=False)
            self.panel._call_soon(lambda: self._checked(key, report))
        threading.Thread(target=run, daemon=True, name="key-check").start()

    def _checked(self, key, report):
        self._busy = False
        if not self.top.winfo_exists():
            return
        if report.ok:
            self._store(key)
            return
        self._say(report.error.message(), "rec")
        if report.error.kind == "network":
            self._force = True  # offline is not proof the key is wrong: let the user decide
            self.save_button.configure(text=_("Salva comunque"), state="normal")
        else:
            self.save_button.configure(state="normal")

    def _store(self, key):
        from ..core.stt_engine.key_manager import KeyManager
        try:
            where = KeyManager.set_key(self.provider, key)
        except Exception as e:
            logger.error(f"{self.provider} key not saved: {type(e).__name__}")
            self._say(_("Salvataggio non riuscito: {error}").format(error=type(e).__name__), "rec")
            return
        logger.info(f"{self.provider} API key saved ({where})")
        self.close()
        if self.on_saved:
            self.on_saved()

    def remove(self):
        from ..core.stt_engine.key_manager import KeyManager
        KeyManager.delete_key(self.provider)
        key_info.forget(self.provider)
        logger.info(f"{self.provider} API key removed")
        self.close()
        if self.on_saved:
            self.on_saved()

    def close(self):
        if self.top.winfo_exists():
            self.top.grab_release()
            self.top.destroy()


class KeyTooltip:
    """One borderless popup reused by every key card of the panel."""

    DELAY_MS = 350

    def __init__(self, panel):
        self.panel = panel
        self.top = None
        self._pending = None
        self._card = None

    def attach(self, card, provider):
        widgets = [card]
        while widgets:
            widget = widgets.pop()
            widget.bind("<Enter>", lambda event: self._enter(card, provider), add="+")
            widget.bind("<Leave>", lambda event: self._leave(card), add="+")
            widgets.extend(widget.winfo_children())

    def _enter(self, card, provider):
        if self._card is card and (self._pending or self.top):
            return
        self.hide()
        self._card = card
        self._pending = self.panel.root.after(self.DELAY_MS, lambda: self._open(card, provider))

    def _leave(self, card):
        def check():
            try:
                x, y = card.winfo_pointerxy()
                inside = str(card.winfo_containing(x, y) or "").startswith(str(card))
            except Exception:
                inside = False
            if not inside and self._card is card:
                self.hide()
        self.panel.root.after(60, check)

    def hide(self):
        if self._pending:
            try:
                self.panel.root.after_cancel(self._pending)
            except Exception:
                pass
            self._pending = None
        if self.top is not None:
            try:
                self.top.destroy()
            except Exception:
                pass
            self.top = None
        self._card = None

    def _open(self, card, provider):
        self._pending = None
        if not card.winfo_exists():
            return
        key, source = _resolve(provider)
        if not key:
            return
        report = key_info.cached(provider, key)
        self._draw(card, provider, key, source, report)
        if report is None:
            def run():
                fresh = key_info.check(provider, key)
                self.panel._call_soon(lambda: self._update(card, provider, key, source, fresh))
                self.panel._call_soon(lambda: self.panel._key_checked(provider, fresh))
            threading.Thread(target=run, daemon=True, name="key-info").start()

    def _update(self, card, provider, key, source, report):
        if self.top is not None and self._card is card and card.winfo_exists():
            self._draw(card, provider, key, source, report)

    def _draw(self, card, provider, key, source, report):
        import tkinter as tk
        panel, c, p, font = self.panel, self.panel.colors, self.panel._p, self.panel.font
        if self.top is not None:
            self.top.destroy()
        self.top = top = tk.Toplevel(panel.root, bg=c["line"])
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        frame = tk.Frame(top, bg=c["raised"], padx=p(14), pady=p(10))
        frame.pack(padx=1, pady=1)
        head = tk.Frame(frame, bg=c["raised"])
        head.pack(fill="x")
        tk.Label(head, text=key_info.PROVIDERS[provider]["label"], font=(font, 10, "bold"), fg=c["text"],
                 bg=c["raised"]).pack(side="left")
        tk.Label(head, text=key_info.mask(key), font=("Consolas", 10), fg=c["accent"],
                 bg=c["raised"]).pack(side="left", padx=(p(10), 0))
        rows = [(_("Salvata in"), _(key_info.SOURCES[source]))]
        if report is None:
            rows.append((_("Stato"), _("Lettura dal provider…")))
        elif report.ok:
            rows += report.lines
        else:
            rows.append((_("Stato"), report.error.message()))
        grid = tk.Frame(frame, bg=c["raised"])
        grid.pack(fill="x", pady=(p(6), 0))
        for index, (label, value) in enumerate(rows):
            tk.Label(grid, text=label, font=(font, 8), fg=c["subtle"], bg=c["raised"], anchor="nw").grid(
                row=index, column=0, sticky="nw", padx=(0, p(12)), pady=p(1))
            failed = report is not None and not report.ok and label == _("Stato")
            tk.Label(grid, text=value, font=(font, 9), fg=c["rec"] if failed else c["text"], bg=c["raised"],
                     anchor="w", justify="left", wraplength=p(300)).grid(row=index, column=1, sticky="w", pady=p(1))
        top.update_idletasks()
        x = card.winfo_rootx() + card.winfo_width() - top.winfo_reqwidth() - p(12)
        y = card.winfo_rooty() + card.winfo_height() + p(4)
        if y + top.winfo_reqheight() > card.winfo_screenheight():
            y = card.winfo_rooty() - top.winfo_reqheight() - p(4)
        top.geometry(f"+{max(0, x)}+{max(0, y)}")
