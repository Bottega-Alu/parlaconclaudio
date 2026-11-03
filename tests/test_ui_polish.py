"""Sound pack helpers, Wi-Fi-style pack selection and auto-hide taskbar placement."""
import ctypes
from types import SimpleNamespace

from src.voice_bridge import flyout_window, tray_icon
from src.voice_bridge.menu_dpi import native_menu_dpi
from src.voice_bridge.tray_flyout import TrayFlyout


def _texts(flyout):
    """Every text drawn on the quick controls' single canvas."""
    canvas = flyout.canvas
    return [canvas.itemcget(item, "text") for item in canvas.find_all() if canvas.type(item) == "text"]


def _packs(tmp_path, monkeypatch):
    for name, count in (("r2d2", 2), ("dune", 1), ("_private", 3), ("empty", 0)):
        folder = tmp_path / name
        folder.mkdir()
        for index in range(count):
            (folder / f"s{index}.mp3").write_bytes(b"")
    monkeypatch.setattr(tray_icon, "SOUNDS_DIR", tmp_path)


def test_sound_packs_are_listed_sorted_without_private_ones(tmp_path, monkeypatch):
    _packs(tmp_path, monkeypatch)
    assert tray_icon.list_sound_packs() == [("dune", 1), ("empty", 0), ("r2d2", 2)]


def test_preview_plays_from_the_chosen_pack_and_reports_empty_ones(tmp_path, monkeypatch):
    _packs(tmp_path, monkeypatch)
    played = []
    monkeypatch.setattr(tray_icon, "_play_sound", played.append)
    assert tray_icon.preview_sound_pack("dune")
    assert played == [str(tmp_path / "dune" / "s0.mp3")]
    assert not tray_icon.preview_sound_pack("empty")
    assert tray_icon.preview_sound_pack("random")  # never picks an empty pack
    assert "empty" not in played[-1]


def test_pack_rows_expand_like_wifi_and_use_selects_the_pack(tmp_path, monkeypatch):
    _packs(tmp_path, monkeypatch)
    config = {"sound_pack": "dune", "volume": 100}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            flyout._open_page("packs")
            keys = [row["key"] for row in flyout.rows]
            assert keys == ["random", "dune", "empty", "r2d2"]
            assert flyout._focus == ("row", "dune")  # current pack
            flyout._toggle_row("r2d2")
            assert flyout._expanded == "r2d2"
            assert "Ascolta" in _texts(flyout) and "Usa" in _texts(flyout)
            flyout._work = lambda action: action()
            flyout._select("r2d2")
            assert config["sound_pack"] == "r2d2"
            flyout._escape()
            assert flyout._page == "main"
        finally:
            flyout.root.destroy()


def test_autohide_taskbar_edge_is_kept_free(monkeypatch):
    monitor = SimpleNamespace(left=0, top=0, right=1920, bottom=1080)

    def fake_shell(name):
        def message(code, data):
            data = data._obj
            if code == 4:
                return 1  # ABS_AUTOHIDE
            data.uEdge = 3
            data.rc.left, data.rc.top, data.rc.right, data.rc.bottom = 0, 1032, 1920, 1080
            return 1
        return SimpleNamespace(SHAppBarMessage=message)

    monkeypatch.setattr(flyout_window.ctypes, "WinDLL", fake_shell)
    assert flyout_window._without_autohide_taskbar([0, 0, 1920, 1080], monitor) == [0, 0, 1920, 1032]


def test_visible_taskbar_leaves_work_area_untouched(monkeypatch):
    monkeypatch.setattr(flyout_window.ctypes, "WinDLL",
                        lambda name: SimpleNamespace(SHAppBarMessage=lambda code, data: 0))
    monitor = SimpleNamespace(left=0, top=0, right=1920, bottom=1080)
    assert flyout_window._without_autohide_taskbar([0, 0, 1920, 1040], monitor) == [0, 0, 1920, 1040]


def test_auto_paste_defaults_on_and_can_be_turned_off(monkeypatch):
    config = {}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: (config.clear(), config.update(value)))
    assert tray_icon.output_mode_from_config("clipboard_paste") == "clipboard_paste"
    tray = tray_icon.TrayIcon()
    tray._toggle_auto_paste_clicked()
    assert config["auto_paste"] is False
    assert tray_icon.output_mode_from_config("clipboard_paste") == "clipboard_only"


def test_clipboard_only_never_sends_ctrl_v(monkeypatch):
    from src.voice_bridge import output_handler
    copied, keys = [], []
    monkeypatch.setattr(output_handler.pyperclip, "copy", copied.append)
    monkeypatch.setattr(output_handler, "_send_ctrl_v", lambda: keys.append("ctrl+v"))
    handler = output_handler.OutputHandler(mode="clipboard_paste")
    handler.mode = "clipboard_only"
    assert handler.deliver("ciao")
    assert copied == ["ciao"] and keys == []


def test_voice_volume_follows_sound_volume_until_set(monkeypatch):
    config = {"volume": 40}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    assert tray_icon.voice_volume(config) == 40
    tray = tray_icon.TrayIcon()
    tray._make_set_voice_volume(120)(None, None)
    assert config == {"volume": 40, "voice_volume": 120}
    assert tray_icon.voice_volume(config) == 120


def test_hook_plays_voices_at_voice_volume(monkeypatch, tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "notify_tts_under_test", Path(__file__).resolve().parents[1] / "scripts" / "notify-tts.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(hook, "load_config", lambda: {"volume": 30, "voice_volume": 110})
    spawned = []
    monkeypatch.setattr(hook, "_spawn_hidden", lambda args: spawned.append(args) or True)
    clip = tmp_path / "voice.mp3"
    clip.write_bytes(b"")
    hook.play_mp3(str(clip), voice=True)
    hook.play_mp3(str(clip))
    assert spawned == [["--play", "voice", str(clip)], ["--play", "sound", str(clip)]]

    # No detached player: ffplay at the level read now.
    calls = []
    monkeypatch.setattr(hook, "_spawn_hidden", lambda args: False)
    monkeypatch.setattr(hook.subprocess, "Popen", lambda args, **kw: calls.append(args))
    hook.play_mp3(str(clip), voice=True)
    hook.play_mp3(str(clip))
    assert [args[args.index("-af") + 1] for args in calls] == ["volume=110/100", "volume=30/100"]


def test_main_page_actions_redraw_the_single_canvas(monkeypatch):
    config = {"volume": 100, "muted": False, "sound_pack": "random"}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            canvas = flyout.canvas
            config.update(muted=True, volume=40)
            flyout._commands.put("refresh")
            flyout._drain()
            assert flyout.canvas is canvas
            assert flyout.root.winfo_children() == [canvas]  # no child windows to repaint one by one
            assert flyout.tiles["audio"]["value"] == "Disattivato" and "Disattivato" in _texts(flyout)
            assert flyout.volume == 40 and "40%" in _texts(flyout)
        finally:
            flyout.root.destroy()


def test_page_switch_redraws_one_canvas_without_resizing(monkeypatch):
    """Pages are scenes on the same canvas: no window is created, mapped or resized."""
    monkeypatch.setattr(tray_icon, "_load_config", lambda: {"volume": 100, "sound_pack": "random"})
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            height = flyout.root.winfo_height()
            for action in (lambda: flyout._open_page("engine"), flyout._back,
                           lambda: flyout._open_page("packs"), flyout._escape):
                action()
                flyout.root.update()
                assert flyout.root.winfo_children() == [flyout.canvas]
                assert flyout.root.winfo_height() == height
            assert flyout._page == "main" and flyout._focus == ("tile", "packs")
        finally:
            flyout.root.destroy()


def test_keyboard_moves_focus_and_activates(monkeypatch):
    config = {"volume": 100, "sound_pack": "random", "tts_mode": "full"}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    tray = tray_icon.TrayIcon()
    tray._on_toggle_recording = lambda: None  # dictation wired: its tile takes the focus
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            assert flyout._focus == ("tile", "record")
            for _ in range(3):
                flyout._move_focus(1)
            assert flyout._focus == ("tile", "voice")
            flyout._activate_focus()
            assert flyout._page == "voice" and flyout._focus == ("row", "full")
            flyout._arrow(1)
            assert flyout._focus == ("row", "semi-silent")
            flyout._work = lambda action: action()
            flyout._activate_focus()
            assert config["tts_mode"] == "semi-silent"
            flyout._escape()
            assert flyout._page == "main"
        finally:
            flyout.root.destroy()


def test_slider_drag_commits_once_on_release(monkeypatch):
    config = {"volume": 100, "voice_volume": 100}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            saved = []
            flyout._work = saved.append
            start, end, maximum = flyout._slider_track[("slider", "volume")]
            box = next(hit for hit in flyout._hits if hit[0] == ("slider", "volume"))
            y = (box[2] + box[4]) / 2
            event = lambda x: type("E", (), {"x": x, "y": y})()
            flyout._on_press(event(start))
            flyout._on_drag(event(start + (end - start) / 2))
            assert flyout.volume == round(maximum / 2) and not saved
            flyout._on_release(event(start + (end - start) / 2))
            assert len(saved) == 1
        finally:
            flyout.root.destroy()

def test_quick_controls_follow_system_mode_switched_after_prewarm(monkeypatch):
    from src.voice_bridge import tray_flyout, fluent
    monkeypatch.setattr(tray_icon, "_load_config", lambda: {"volume": 100})
    asked = []
    monkeypatch.setattr(fluent, "dark_mode", lambda surface="apps": asked.append(surface) or state["dark"])
    state = {"dark": False}
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            assert flyout.colors == fluent.colors(False)
            state["dark"] = True  # e.g. Windows' scheduled switch to dark
            flyout._show()
            assert flyout.dark and flyout.colors == fluent.colors(True)
            assert flyout.root["bg"] == fluent.colors(True)["surface"]
            assert set(asked) == {"system"}  # tray surface: taskbar mode, like Quick Settings
        finally:
            flyout.root.destroy()


def test_open_app_window_follows_app_mode_switch(monkeypatch):
    from src.voice_bridge import control_panel, fluent, autostart
    from src.core import voice_hooks
    state = {"dark": True}
    monkeypatch.setattr(fluent, "dark_mode", lambda surface="apps": state["dark"])
    monkeypatch.setattr(tray_icon, "_load_config", lambda: {"volume": 100})
    monkeypatch.setattr(voice_hooks, "status", lambda agent: False)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)
    panel = control_panel.ControlPanel(tray_icon.TrayIcon())
    with native_menu_dpi():
        panel._build()
        try:
            assert panel.colors == fluent.colors(True)
            state["dark"] = False
            panel._follow_theme()
            assert not panel.dark and panel.colors == fluent.colors(False)
            assert panel.nav["bg"] == fluent.colors(False)["surface"]
        finally:
            panel.root.destroy()


def test_volume_range_is_0_to_125_and_clamps_old_configs():
    assert tray_icon.MAX_VOLUME == 125
    assert tray_icon.sound_volume({"volume": 300}) == 125
    assert tray_icon.voice_volume({"volume": 200}) == 125
    assert tray_icon.voice_volume({"volume": 40, "voice_volume": 12}) == 12
    assert tray_icon.sound_volume({}) == tray_icon.DEFAULT_VOLUME == 100


def test_controls_show_their_value_when_another_tk_root_exists_first(monkeypatch):
    """The quick controls and the app window are two Tk roots: a Tk variable
    without a master lands in the first one, and a widget of the other shows
    nothing (empty drop-downs in the app window)."""
    import tkinter as tk
    from tkinter import ttk
    from src.voice_bridge import control_panel, fluent, autostart
    from src.core import voice_hooks
    monkeypatch.setattr(fluent, "dark_mode", lambda surface="apps": True)
    monkeypatch.setattr(tray_icon, "_load_config",
                        lambda: {"volume": 40, "voice_volume": 60, "tts_mode": "full", "sound_pack": "r2d2"})
    monkeypatch.setattr(voice_hooks, "status", lambda agent: False)
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)

    def walk(widget):
        for child in widget.winfo_children():
            yield child
            yield from walk(child)

    with native_menu_dpi():
        first = tk.Tk()
        first.withdraw()
        panel = control_panel.ControlPanel(tray_icon.TrayIcon())
        panel._build()
        try:
            panel._go("alerts")
            boxes = [w for w in walk(panel.body) if isinstance(w, ttk.Combobox)]
            assert boxes and all(box.get() for box in boxes)
        finally:
            panel.root.destroy()

        flyout = TrayFlyout(tray_icon.TrayIcon())
        flyout._build()
        try:
            flyout._show()
            assert "40%" in _texts(flyout) and "60%" in _texts(flyout)
            assert "Pronto per dettare" in _texts(flyout)
        finally:
            flyout.root.destroy()
            first.destroy()


def test_theme_and_language_tiles_redraw_in_place(monkeypatch):
    """Theme and language change colours and words on the same canvas, in one redraw."""
    config = {"volume": 100, "sound_pack": "random", "ui_language": "it", "ui_theme": "light"}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: (config.clear(), config.update(value)))
    tray = tray_icon.TrayIcon()
    with native_menu_dpi():
        flyout = TrayFlyout(tray)
        flyout._build()
        try:
            flyout._show()
            canvas = flyout.canvas
            flyout._next_theme()
            assert config["ui_theme"] != "light" and flyout.canvas["bg"] == flyout.colors["surface"]
            flyout._next_language()
            assert config["ui_language"] != "it"
            assert flyout.canvas is canvas and flyout.root.winfo_children() == [canvas]
        finally:
            flyout.root.destroy()  # conftest restores the Italian interface and system theme
