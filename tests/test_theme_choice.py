"""A theme chosen in the app wins over Windows on every surface; "system" follows Windows."""
from src.voice_bridge import fluent


def test_chosen_theme_overrides_windows(monkeypatch):
    try:
        fluent.set_theme("dark")
        assert fluent.dark_mode() is True and fluent.dark_mode("system") is True
        fluent.set_theme("light")
        assert fluent.dark_mode() is False and fluent.dark_mode("system") is False
        fluent.set_theme("bogus")
        assert fluent.theme() == "system"
    finally:
        fluent.set_theme("system")
