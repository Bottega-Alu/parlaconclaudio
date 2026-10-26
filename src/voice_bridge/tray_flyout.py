"""Lightweight quick controls, using the Windows 11 flyout visual language.

The native menu remains available for all advanced features. Tk and all its
variables belong to one dedicated thread; application actions run in workers.
"""
import logging
import queue
import threading

from .menu_dpi import native_menu_dpi
from . import flyout_window

logger = logging.getLogger(__name__)


def _dark_mode():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class TrayFlyout:
    def __init__(self, tray):
        self.tray = tray
        self._commands = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None
        self._working = False
        self._pending_actions = []

    def show(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="tray-quick-controls", daemon=True)
                self._thread.start()
            else:
                self._commands.put("show")

    def stop(self):
        self._commands.put("stop")

    def _run(self):
        try:
            with native_menu_dpi():
                self._build()
                self._show()
                self.root.mainloop()
        except Exception:
            logger.exception("Quick controls could not open")
            self.tray._open_panel(None, None)

    def _p(self, value):
        return round(value * self.scale)

    def _build(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("parlaconclaudio - Comandi rapidi")
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self._hide)
        self.root.bind("<Escape>", lambda event: self._hide())
        self.root.bind("<FocusOut>", lambda event: self.root.after(180, self._dismiss_if_outside))
        self._dpi = flyout_window.window_dpi(self.root)
        self.scale = self._dpi / 96
        self._build_surface()
        self.root.after(150, self._tick)

    def _build_surface(self):
        import tkinter as tk
        from tkinter import ttk
        from .tray_icon import STT_MODES
        for child in self.root.winfo_children():
            child.destroy()
        self.root.tk.call("tk", "scaling", self._dpi / 72)
        self.dark = _dark_mode()
        self.colors = ({"surface": "#242424", "raised": "#303030", "text": "#ffffff",
                        "muted": "#c5c5c5", "line": "#474747", "accent": "#60cdff", "on": "#003449"}
                       if self.dark else
                       {"surface": "#f7f7f7", "raised": "#ffffff", "text": "#1a1a1a",
                        "muted": "#5c5c5c", "line": "#d6d6d6", "accent": "#0067c0", "on": "#ffffff"})
        c = self.colors
        self.root.configure(bg=c["surface"])
        self.font = "Segoe UI"
        body = tk.Frame(self.root, bg=c["surface"], padx=self._p(24), pady=self._p(20))
        body.pack(fill="both", expand=True)
        header = tk.Frame(body, bg=c["surface"])
        header.pack(fill="x")
        tk.Label(header, text="parlaconclaudio", font=(self.font, 15, "bold"),
                 fg=c["text"], bg=c["surface"]).pack(side="left")
        self._flat_button(header, "Chiudi", self._hide, small=True).pack(side="right")
        self.state = tk.StringVar(value="Pronto per dettare")
        tk.Label(body, textvariable=self.state, font=(self.font, 10), fg=c["muted"],
                 bg=c["surface"], anchor="w").pack(fill="x", pady=(self._p(3), self._p(18)))
        tiles = tk.Frame(body, bg=c["surface"])
        tiles.pack(fill="x")
        tiles.columnconfigure((0, 1), weight=1, uniform="tiles")
        self.record_tile = self._tile(tiles, "Dettatura\nAvvia", "wave", self._record)
        self.record_tile[0].grid(row=0, column=0, sticky="ew", padx=(0, self._p(6)))
        self.audio_tile = self._tile(tiles, "Audio\nAttivo", "speaker", self._mute)
        self.audio_tile[0].grid(row=0, column=1, sticky="ew", padx=(self._p(6), 0))
        volume_header = tk.Frame(body, bg=c["surface"])
        volume_header.pack(fill="x", pady=(self._p(20), 0))
        self.volume_text = tk.StringVar(value="125%")
        tk.Label(volume_header, text="Volume notifiche", font=(self.font, 10), bg=c["surface"], fg=c["text"]).pack(side="left")
        tk.Label(volume_header, textvariable=self.volume_text, font=(self.font, 10), bg=c["surface"], fg=c["muted"]).pack(side="right")
        self.volume = 125
        self.slider = tk.Canvas(body, bg=c["surface"], height=self._p(34), highlightthickness=0,
                                takefocus=1, cursor="hand2")
        self.slider.pack(fill="x", pady=(self._p(3), self._p(16)))
        self.slider.bind("<Configure>", lambda event: self._draw_slider())
        self.slider.bind("<Button-1>", self._slider_move)
        self.slider.bind("<B1-Motion>", self._slider_move)
        self.slider.bind("<ButtonRelease-1>", lambda event: self._save_volume())
        self.slider.bind("<Left>", lambda event: self._slider_key(-5))
        self.slider.bind("<Right>", lambda event: self._slider_key(5))
        self.slider.bind("<FocusIn>", lambda event: self._draw_slider())
        self.slider.bind("<FocusOut>", lambda event: self._draw_slider())
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Flyout.TCombobox", foreground=c["text"], background=c["raised"],
                        fieldbackground=c["raised"], arrowcolor=c["text"], bordercolor=c["line"],
                        lightcolor=c["line"], darkcolor=c["line"], padding=self._p(8))
        style.map("Flyout.TCombobox", fieldbackground=[("readonly", c["raised"])],
                  foreground=[("readonly", c["text"])], selectbackground=[("readonly", c["raised"])],
                  selectforeground=[("readonly", c["text"])])
        self.root.option_add("*TCombobox*Listbox.font", (self.font, 10))
        self.root.option_add("*TCombobox*Listbox.background", c["raised"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", c["on"])
        self.mic = tk.StringVar()
        self.mode = tk.StringVar()
        self.mic_box = self._selector(body, "Microfono", self.mic, self._mic_changed)
        self.mode_box = self._selector(body, "Riconoscimento", self.mode, self._mode_changed)
        self.mode_box.configure(values=list(STT_MODES.values()))
        self.engine = tk.StringVar()
        tk.Label(body, textvariable=self.engine, font=(self.font, 9), fg=c["muted"], bg=c["surface"],
                 anchor="w").pack(fill="x", pady=(self._p(8), self._p(18)))
        actions = tk.Frame(body, bg=c["surface"])
        actions.pack(fill="x")
        self.files = self._flat_button(actions, "Trascrivi file", self._files)
        self.files.pack(side="left")
        self._flat_button(actions, "Impostazioni", self._settings).pack(side="right")
        tk.Frame(body, bg=c["line"], height=self._p(1)).pack(fill="x", pady=self._p(17))
        footer = tk.Frame(body, bg=c["surface"])
        footer.pack(fill="x")
        tk.Label(footer, text="Ctrl + Alt + Spazio", font=(self.font, 9), fg=c["muted"], bg=c["surface"]).pack(side="left")
        self._flat_button(footer, "Menu completo", self._full_menu, small=True).pack(side="right")
        self._refresh()

    def _flat_button(self, parent, text, command, small=False):
        import tkinter as tk
        c = self.colors
        return tk.Button(parent, text=text, command=command, font=(self.font, 9 if small else 10),
                         bg=c["surface"] if small else c["raised"], fg=c["muted"] if small else c["text"],
                         activebackground=c["raised"], activeforeground=c["text"], relief="flat", bd=0,
                         padx=self._p(6 if small else 14), pady=self._p(5 if small else 9),
                         highlightthickness=self._p(1), highlightbackground=c["surface"] if small else c["line"],
                         highlightcolor=c["accent"], cursor="hand2")

    def _tile(self, parent, text, icon, command):
        import tkinter as tk
        canvas = tk.Canvas(parent, height=self._p(96), bg=self.colors["surface"], highlightthickness=0)
        button = tk.Button(canvas, text=text, command=command, font=(self.font, 10),
                           relief="flat", bd=0, highlightthickness=0, cursor="hand2", compound="top")
        button.place(x=self._p(10), y=self._p(9), relwidth=1, width=-self._p(20), height=self._p(78))
        record = [canvas, button, icon, False, None, None]
        canvas.bind("<Configure>", lambda event: self._paint_tile(record, record[3]))
        button.bind("<FocusIn>", lambda event: self._paint_tile(record, record[3], True))
        button.bind("<FocusOut>", lambda event: self._paint_tile(record, record[3]))
        return record

    def _paint_tile(self, tile, selected, focused=False):
        from PIL import Image, ImageDraw, ImageTk
        canvas, button, icon = tile[:3]
        tile[3] = selected
        c = self.colors
        bg, fg = (c["accent"], c["on"]) if selected else (c["raised"], c["text"])
        w, h = max(canvas.winfo_width(), self._p(145)), self._p(96)
        image = Image.new("RGB", (w * 2, h * 2), c["surface"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((1, 1, w * 2 - 2, h * 2 - 2), radius=self._p(10) * 2,
                               fill=bg, outline=c["accent"] if focused else (bg if selected else c["line"]),
                               width=2 if focused else 1)
        tile[4] = ImageTk.PhotoImage(image.resize((w, h), Image.Resampling.LANCZOS), master=self.root)
        canvas.delete("all")
        canvas.create_image(0, 0, image=tile[4], anchor="nw")
        icon_size = self._p(22)
        icon_image = Image.new("RGB", (icon_size * 3, icon_size * 3), bg)
        draw = ImageDraw.Draw(icon_image)
        factor = icon_size * 3 / 24
        points = ([(3, 12), (6, 12), (8, 5), (11, 19), (14, 3), (17, 16), (19, 12), (22, 12)]
                  if icon == "wave" else [(3, 9), (7, 9), (12, 5), (12, 19), (7, 15), (3, 15), (3, 9)])
        draw.line([(round(x * factor), round(y * factor)) for x, y in points], fill=fg,
                  width=max(2, round(1.5 * factor)), joint="curve")
        if icon == "speaker":
            draw.arc((13 * factor, 5 * factor, 23 * factor, 19 * factor), -65, 65, fill=fg, width=max(2, round(1.5 * factor)))
        tile[5] = ImageTk.PhotoImage(icon_image.resize((icon_size, icon_size), Image.Resampling.LANCZOS), master=self.root)
        button.configure(bg=bg, fg=fg, activebackground=bg, activeforeground=fg, image=tile[5])

    def _selector(self, body, label, variable, action):
        import tkinter as tk
        from tkinter import ttk
        c = self.colors
        tk.Label(body, text=label, font=(self.font, 10), fg=c["text"], bg=c["surface"],
                 anchor="w").pack(fill="x", pady=(self._p(8), self._p(5)))
        box = ttk.Combobox(body, textvariable=variable, state="readonly", font=(self.font, 10),
                           style="Flyout.TCombobox", width=1)
        box.pack(fill="x")
        box.bind("<<ComboboxSelected>>", action)
        return box

    def _refresh(self):
        from .tray_icon import _load_config, STT_MODES
        config = _load_config()
        self._muted = bool(config.get("muted", False))
        self.volume = max(0, min(300, int(config.get("volume", 200))))
        self.volume_text.set(f"{self.volume}%")
        self._paint_tile(self.audio_tile, not self._muted)
        self.audio_tile[1].configure(text="Audio\n" + ("Disattivato" if self._muted else "Attivo"))
        self.mics = {"Predefinito di Windows": None}
        self.mics.update({f"{d.name} [{d.device_id}]": d.device_id for d in self.tray._list_mics()})
        self.mic_box.configure(values=list(self.mics))
        self.mic.set(next((name for name, value in self.mics.items() if value == config.get("mic_device_id")), "Predefinito di Windows"))
        self.mode.set(STT_MODES.get(config.get("stt_mode", "auto"), STT_MODES["auto"]))
        self._draw_slider()
        self._update_state()

    def _draw_slider(self):
        if not self.slider.winfo_exists():
            return
        c = self.colors
        self.slider.delete("all")
        width = max(self.slider.winfo_width(), self._p(300))
        x0, x1, y = self._p(10), width - self._p(10), self._p(17)
        x = x0 + (x1 - x0) * self.volume / 300
        self.slider.create_line(x0, y, x1, y, fill=c["line"], width=self._p(4), capstyle="round")
        self.slider.create_line(x0, y, x, y, fill=c["accent"], width=self._p(4), capstyle="round")
        radius = self._p(7)
        self.slider.create_oval(x - radius, y - radius, x + radius, y + radius,
                                fill=c["accent"], outline=c["raised"], width=self._p(3))
        if self.root.focus_get() is self.slider:
            self.slider.create_rectangle(1, 1, width - 1, self._p(33), outline=c["accent"], dash=(2, 3))

    def _slider_move(self, event):
        self.slider.focus_set()
        start, end = self._p(10), self.slider.winfo_width() - self._p(10)
        self.volume = max(0, min(300, round((event.x - start) / max(1, end - start) * 300)))
        self.volume_text.set(f"{self.volume}%")
        self._draw_slider()

    def _slider_key(self, step):
        self.volume = max(0, min(300, self.volume + step))
        self.volume_text.set(f"{self.volume}%")
        self._draw_slider()
        self._save_volume()

    def _save_volume(self):
        value = self.volume
        self._work(lambda: self.tray._make_set_volume(value)(None, None))

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
        self._work(lambda: self.tray._make_toggle_mute()(None, None))

    def _mic_changed(self, event):
        value = self.mics[self.mic.get()]
        self._work(lambda: self.tray._make_set_mic(value)(None, None))

    def _mode_changed(self, event):
        from .tray_icon import STT_MODES
        value = next(key for key, label in STT_MODES.items() if label == self.mode.get())
        self._work(lambda: self.tray._make_set_stt_mode(value)(None, None))

    def _files(self):
        self._hide()
        self.tray._transcribe_files_clicked(None, None)

    def _settings(self):
        self._hide()
        self.tray._open_panel(None, None)

    def _full_menu(self):
        self._hide()
        self.tray._show_full_menu()

    def _hide(self):
        self.root.withdraw()

    def _dismiss_if_outside(self):
        if self.root.state() != "withdrawn" and self.root.focus_displayof() is None:
            self._hide()

    def _show(self):
        self._refresh()
        left, top, right, bottom = flyout_window.work_area()
        width = self._p(384)
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        gap = self._p(12)
        x, y = max(left + gap, right - width - gap), max(top + gap, bottom - height - gap)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        actual_dpi = flyout_window.window_dpi(self.root)
        if actual_dpi != self._dpi:
            self._dpi, self.scale = actual_dpi, actual_dpi / 96
            self._build_surface()
            self._show()
            return
        self.root.deiconify()
        flyout_window.finish_window(self.root, self.dark)
        self.root.lift()
        self.root.focus_force()
        self.record_tile[1].focus_set()

    def _update_state(self):
        state = self.tray._state
        batch = self.tray.batch_busy
        labels = {"idle": "Pronto per dettare", "recording": "Registrazione in corso",
                  "transcribing": "Trascrizione in corso", "loading": "Caricamento del motore…", "output": "Invio del testo…"}
        self.state.set("Trascrizione file in corso" if batch else labels.get(state, state))
        recording = state == "recording"
        self.record_tile[1].configure(text="Dettatura\n" + ("Ferma" if recording else "Avvia"),
                                      state="normal" if self.tray._on_toggle_recording and state in ("idle", "recording") and not batch else "disabled")
        if self.record_tile[3] != recording:
            self._paint_tile(self.record_tile, recording)
        self.files.configure(state="normal" if state == "idle" and not batch else "disabled")
        self.engine.set("Motore attivo: " + self.tray._get_stt_status({})["active_label"])

    def _tick(self):
        try:
            while True:
                command = self._commands.get_nowait()
                if command == "stop":
                    self.root.destroy()
                    return
                if command == "show":
                    self._show()
                if command == "refresh":
                    self._working = False
                    self._refresh()
                    if self._pending_actions:
                        self._work(self._pending_actions.pop(0))
                if command == "error":
                    self.state.set("Operazione non riuscita. Riprova.")
        except queue.Empty:
            pass
        if self.root.state() != "withdrawn":
            self._update_state()
        # A hidden flyout never polls files/devices or renders frames.
        self.root.after(1000 if self.root.state() == "withdrawn" else 250, self._tick)
