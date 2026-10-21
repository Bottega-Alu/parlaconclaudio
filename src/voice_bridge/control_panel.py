"""Native, keyboard-accessible control window for the tray application.

Tk belongs exclusively to this window's thread; callbacks that touch engines or
configuration run in workers. Closing hides the window and keeps dictation alive.
"""
import logging
import queue
import threading

logger = logging.getLogger(__name__)


class ControlPanel:
    def __init__(self, tray):
        self.tray = tray
        self._commands = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

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
        try:
            self._build()
            self.root.mainloop()
        except Exception:
            logger.exception("Impossibile aprire il pannello")
            self.tray._notify("Pannello non disponibile. Usa il menu dell'icona nell'area di notifica.")

    def _build(self):
        import tkinter as tk
        from tkinter import ttk
        from .tray_icon import _load_config, STT_MODES
        from ..core import voice_hooks
        from .build_info import VERSION

        self.root = root = tk.Tk()
        root.title(f"parlaconclaudio • {VERSION}")
        from pathlib import Path
        app_icon = Path(__file__).resolve().parents[2] / "assets" / "voicebridge.ico"
        if app_icon.is_file():
            root.iconbitmap(str(app_icon))
        root.geometry("650x740")
        root.minsize(610, 700)
        root.configure(background="#f4f5f7")
        root.protocol("WM_DELETE_WINDOW", root.withdraw)
        root.bind("<Escape>", lambda event: root.withdraw())
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TFrame", background="#f4f5f7")
        style.configure("TLabel", background="#f4f5f7", foreground="#202733", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("State.TLabel", font=("Segoe UI", 15, "bold"), foreground="#185d47")
        style.configure("Hint.TLabel", foreground="#4a5565", font=("Segoe UI", 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        body = ttk.Frame(root, padding=26)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text="parlaconclaudio", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(body, text="Detta un testo. Ritrovalo dove stai scrivendo.", style="Hint.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 22))
        self.state = tk.StringVar(value="Pronto per dettare")
        ttk.Label(body, textvariable=self.state, style="State.TLabel").grid(row=2, column=0, sticky="w")
        self.engine = tk.StringVar()
        ttk.Label(body, textvariable=self.engine, wraplength=570).grid(row=3, column=0, sticky="w", pady=(5, 12))
        actions = ttk.Frame(body)
        actions.grid(row=4, column=0, sticky="ew")
        self.record = ttk.Button(actions, text="Avvia dettatura", command=self._toggle_recording)
        self.record.pack(side="left", padx=(0, 10))
        self.files = ttk.Button(actions, text="Trascrivi file audio…", command=lambda: self.tray._transcribe_files_clicked(None, None))
        self.files.pack(side="left")
        ttk.Label(body, text="Il pannello si riduce a icona prima della dettatura. Per fermarla usa la scorciatoia\nconfigurata oppure riapri il pannello. Il testo va nella finestra attiva.", wraplength=570, style="Hint.TLabel").grid(row=5, column=0, sticky="w", pady=(10, 20))
        ttk.Separator(body).grid(row=6, column=0, sticky="ew")
        ttk.Label(body, text="Dettatura", style="Section.TLabel").grid(row=7, column=0, sticky="w", pady=(18, 10))
        settings = ttk.Frame(body)
        settings.grid(row=8, column=0, sticky="ew")
        settings.columnconfigure(1, weight=1)
        config = _load_config()
        devices = self.tray._list_mics()
        mic_options = {"Predefinito di Windows": None}
        mic_options.update({f"{dev.name} [{dev.device_id}]": dev.device_id for dev in devices})
        self.mic_options = mic_options
        current_mic = next((label for label, value in mic_options.items() if value == config.get("mic_device_id")), "Predefinito di Windows")
        self.mic = tk.StringVar(value=current_mic)
        self.mode = tk.StringVar(value=STT_MODES.get(config.get("stt_mode", "auto"), STT_MODES["auto"]))
        for row, (label, variable, values) in enumerate((("Microfono", self.mic, list(mic_options)), ("Motore", self.mode, list(STT_MODES.values())))):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=5)
            widget = ttk.Combobox(settings, textvariable=variable, values=values, state="readonly", width=42)
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            widget.bind("<<ComboboxSelected>>", self._apply_mic if row == 0 else self._apply_mode)
        memory = ttk.Frame(body)
        memory.grid(row=9, column=0, sticky="w", pady=(12, 20))
        ttk.Button(memory, text="Libera memoria GPU", command=lambda: self.tray._unload_vram_clicked(), state="normal" if self.tray._on_unload_vram else "disabled").pack(side="left", padx=(0, 10))
        ttk.Button(memory, text="Ricarica modello", command=lambda: self.tray._purge_vram_clicked(None, None)).pack(side="left")
        ttk.Separator(body).grid(row=10, column=0, sticky="ew")
        ttk.Label(body, text="Notifiche vocali e collegamenti", style="Section.TLabel").grid(row=11, column=0, sticky="w", pady=(18, 8))
        hooks = ttk.Frame(body)
        hooks.grid(row=12, column=0, sticky="ew")
        self.hook_vars = {}
        for column, agent in enumerate(voice_hooks.AGENTS):
            var = tk.BooleanVar(value=voice_hooks.status(agent) is True)
            self.hook_vars[agent] = var
            ttk.Checkbutton(hooks, text=voice_hooks.AGENT_LABEL[agent], variable=var,
                            command=lambda a=agent, v=var: self._set_hook(a, v.get())).grid(row=0, column=column, sticky="w", padx=(0, 24))
        ttk.Label(body, text="Gli hook fanno parlare Codex e Claude Code al termine delle loro attività.\nVoce, volume, suoni e chiavi API sono nel menu dell'icona accanto all'orologio.", wraplength=570, style="Hint.TLabel").grid(row=13, column=0, sticky="w", pady=(8, 18))
        ttk.Button(body, text="Apri progetto e guida", command=lambda: self.tray._open_repo(None, None)).grid(row=14, column=0, sticky="w")
        body.rowconfigure(15, weight=1)
        ttk.Label(body, text="Chiudendo questo pannello, l'app rimane nell'area di notifica.", style="Hint.TLabel").grid(row=16, column=0, sticky="w", pady=(20, 0))
        root.after(100, self._tick)

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

    def _apply_mic(self, event=None):
        value = self.mic_options[self.mic.get()]
        self._work(lambda: self.tray._make_set_mic(value)(None, None))

    def _apply_mode(self, event=None):
        from .tray_icon import STT_MODES
        mode = next(key for key, value in STT_MODES.items() if value == self.mode.get())
        self._work(lambda: self.tray._make_set_stt_mode(mode)(None, None))

    def _tick(self):
        from .tray_icon import _load_config, STT_MODES
        from ..core import voice_hooks
        try:
            while True:
                command = self._commands.get_nowait()
                if command == "stop":
                    self.root.destroy()
                    return
                if command == "show":
                    self.root.deiconify()
                    self.root.lift()
                if command in ("refresh", "show"):
                    config = _load_config()
                    self.mode.set(STT_MODES.get(config.get("stt_mode", "auto"), STT_MODES["auto"]))
                    self.mic.set(next((label for label, value in self.mic_options.items() if value == config.get("mic_device_id")), "Predefinito di Windows"))
                    for agent, variable in self.hook_vars.items():
                        variable.set(voice_hooks.status(agent) is True)
        except queue.Empty:
            pass
        if self.root.state() == "withdrawn":
            self.root.after(750, self._tick)
            return
        state = self.tray._state
        batch = self.tray._batch_lock.locked()
        labels = {"idle": "Pronto per dettare", "recording": "Registrazione in corso", "transcribing": "Trascrizione in corso", "loading": "Caricamento del motore"}
        self.state.set("Trascrizione file aperta o in corso" if batch else labels.get(state, state))
        self.engine.set("Motore: " + self.tray._get_stt_status({})["active_label"])
        self.record.configure(text="Ferma dettatura" if state == "recording" else "Avvia dettatura",
                              state="normal" if self.tray._on_toggle_recording and state in ("idle", "recording") and not batch else "disabled")
        self.files.configure(state="normal" if state == "idle" and not batch else "disabled")
        # A hidden panel needs no periodic disk reads or updates.
        self.root.after(750, self._tick)
