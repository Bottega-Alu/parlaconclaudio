"""Sound pack helpers, Wi-Fi-style pack selection and auto-hide taskbar placement."""
import ctypes
from types import SimpleNamespace

from src.voice_bridge import flyout_window, tray_icon
from src.voice_bridge.menu_dpi import native_menu_dpi
from src.voice_bridge.tray_flyout import TrayFlyout


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
            keys = [row._row_key for row in flyout.rows]
            assert keys == ["random", "dune", "empty", "r2d2"]
            assert flyout.root.focus_get() is flyout.rows[1]  # current pack
            flyout._toggle_row("r2d2")
            assert flyout._expanded == "r2d2"
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
