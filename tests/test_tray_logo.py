"""Flat monochrome tray logos: still at rest, bounded loops, theme colour, user choice."""
from types import SimpleNamespace

from src.voice_bridge import fluent, tray_icon, tray_logo


def test_every_style_renders_every_state_quickly():
    import time
    for style in tray_logo.STYLE_IDS:
        if style == "marble":
            continue
        for state in tray_logo.STATES:
            start = time.perf_counter()
            image = tray_logo.frame(style, state, 3, 64, (255, 255, 255))
            assert image.size == (64, 64) and image.mode == "RGBA"
            assert image.getbbox(), f"{style}/{state} is empty"
            assert time.perf_counter() - start < 0.05


def test_idle_is_one_still_frame_and_work_states_move():
    for style in ("wave", "rings", "mech", "mech_soft", "gyro", "alien"):
        assert tray_logo.frame_count(style, "idle") == 1
        first = tray_logo.frame(style, "recording", 0).tobytes()
        assert any(tray_logo.frame(style, "recording", i).tobytes() != first for i in range(1, 4)), style


def test_loops_are_seamless():
    for style in ("wave", "rings", "mech_soft"):
        count = tray_logo.frame_count(style, "transcribing")
        assert tray_logo.frame(style, "transcribing", count).tobytes() == \
            tray_logo.frame(style, "transcribing", 0).tobytes()


def test_logo_animator_idle_renders_once_and_sleeps(monkeypatch):
    monkeypatch.setattr(fluent, "dark_mode", lambda surface="apps": True)
    animator = tray_icon._IconAnimator()
    assert animator._style == "wave"  # default
    animator._icon_ref = SimpleNamespace(icon=None)
    animator._running = True
    waits = []

    def wait(timeout):
        waits.append(timeout)
        animator._running = False
    animator._wake = SimpleNamespace(clear=lambda: None, wait=wait, set=lambda: None)
    animator._animation_loop()
    assert waits == [None] and animator._icon_ref.icon is not None


def test_logo_cache_is_bounded_and_follows_taskbar_mode(monkeypatch):
    dark = {"value": True}
    monkeypatch.setattr(fluent, "dark_mode", lambda surface="apps": dark["value"])
    animator = tray_icon._IconAnimator("rings")
    frames = [animator._frame("recording", i) for i in range(60)]
    assert len(animator._frames) == tray_logo.frame_count("rings", "recording")
    assert frames[0] is frames[tray_logo.frame_count("rings", "recording")]
    white = animator._frame("idle", 0).getpixel((32, 4))  # top of the outer ring
    dark["value"] = False
    black = animator._frame("idle", 0).getpixel((32, 4))
    assert white[:3] == (255, 255, 255) and black[:3] == (26, 26, 26)


def test_choice_is_saved_and_unknown_falls_back_to_default(monkeypatch):
    config = {}
    monkeypatch.setattr(tray_icon, "_load_config", lambda: dict(config))
    monkeypatch.setattr(tray_icon, "_save_config", lambda value: config.update(value))
    tray = tray_icon.TrayIcon()
    tray._set_tray_logo("alien")
    assert config["tray_logo"] == "alien" and tray._animator._style == "alien"
    tray._set_tray_logo("nonsense")
    assert config["tray_logo"] == tray_logo.DEFAULT
    assert tray_icon._IconAnimator("nonsense")._style == tray_logo.DEFAULT
