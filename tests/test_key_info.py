"""Cloud key details: masked form, provider decision tree, cache, and the panel's key UI."""
import urllib.error

import pytest

from src.voice_bridge import key_info


@pytest.fixture(autouse=True)
def _fresh_cache():
    key_info._cache.clear()
    yield
    key_info._cache.clear()


def fake_http(routes, calls=None):
    def get(url, auth):
        if calls is not None:
            calls.append(url)
        for fragment, answer in routes.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return 404, None
    return get


def test_mask_shows_only_first_and_last_characters():
    assert key_info.mask("sk-or-v1-61e0123456789abcdefc66") == "sk-or-v1-…fc66"
    assert key_info.mask("gsk_ABCDEFGHIJKLMNOPQRSTUVWX1234") == "gsk_AB…1234"
    assert key_info.mask("short") == "•••••"


def test_clipboard_prefill_only_for_the_providers_fixed_prefix():
    assert key_info.looks_like("groq", "  gsk_abcdefghijklmnop ")
    assert not key_info.looks_like("groq", "sk-or-v1-abcdefghijkl")
    assert not key_info.looks_like("groq", "gsk_ some sentence")
    assert not key_info.looks_like("deepgram", "fda6" * 10)  # no fixed prefix: never guessed


def test_openrouter_reports_budget_renewal_expiry_and_credit(monkeypatch):
    key_body = {"data": {"limit": 15, "limit_reset": "weekly", "limit_remaining": 14.69, "expires_at": None,
                         "is_free_tier": False, "usage_daily": 0.31, "usage_monthly": 0.93,
                         "effective_budget": {"limit_usd": 15, "remaining_usd": 14.69, "reset_interval": "weekly",
                                              "resets_at": "2026-11-09T00:00:00.000Z"}}}
    monkeypatch.setattr(key_info, "_http_get", fake_http({
        "/api/v1/key": (200, key_body), "/api/v1/credits": (200, {"data": {"total_credits": 690, "total_usage": 638.75}})}))
    report = key_info.check("openrouter", "sk-or-v1-x")
    lines = dict(report.lines)
    assert report.ok
    assert lines["Plafond chiave"] == "$14.69 rimasti su $15.00 (settimanale)"
    assert lines["Rinnovo"] in ("09/11/2026", "08/11/2026")  # local time zone of the machine
    assert lines["Scadenza"] == "Nessuna"
    assert lines["Credito account"] == "$51.25"


def test_deepgram_without_billing_scope_says_the_credit_is_not_readable(monkeypatch):
    monkeypatch.setattr(key_info, "_http_get", fake_http({
        "/balances": (403, None), "/v1/projects": (200, {"projects": [{"project_id": "p1", "name": "Mio"}]})}))
    lines = dict(key_info.check("deepgram", "k").lines)
    assert lines["Progetto"] == "Mio"
    assert "billing:read" in lines["Credito"]


def test_rejected_key_is_invalid_and_network_failure_is_not(monkeypatch):
    monkeypatch.setattr(key_info, "_http_get", fake_http({"groq.com": (401, None)}))
    report = key_info.check("groq", "gsk_bad")
    assert not report.ok and report.error.kind == "invalid" and report.error.status == 401
    monkeypatch.setattr(key_info, "_http_get", fake_http({"groq.com": urllib.error.URLError("offline")}))
    report = key_info.check("groq", "gsk_other")
    assert report.error.kind == "network" and "offline" in report.error.message()


def test_results_are_cached_per_key_but_network_errors_are_not(monkeypatch):
    calls = []
    monkeypatch.setattr(key_info, "_http_get", fake_http({"groq.com": (200, {})}, calls))
    key_info.check("groq", "gsk_one")
    key_info.check("groq", "gsk_one")
    assert len(calls) == 1
    key_info.check("groq", "gsk_two")  # a different key is checked again
    assert len(calls) == 2
    key_info.check("groq", "gsk_two", use_cache=False)  # the dialog always asks the provider
    assert len(calls) == 3
    monkeypatch.setattr(key_info, "_http_get", fake_http({"groq.com": urllib.error.URLError("x")}, calls))
    key_info.forget("groq")
    key_info.check("groq", "gsk_two")
    assert key_info.cached("groq", "gsk_two") is None


def test_panel_key_cards_dialog_and_tooltip(monkeypatch):
    """Active key card stands out; the dialog checks before saving and rejects a refused key."""
    from src.core.stt_engine.key_manager import KeyManager
    from src.voice_bridge import tray_icon, key_ui
    from src.voice_bridge.control_panel import ControlPanel
    from src.voice_bridge.menu_dpi import native_menu_dpi

    store = {"openrouter": ("sk-or-v1-abcdefghijklmnop1234", "keyring")}
    monkeypatch.setattr(KeyManager, "resolve", staticmethod(lambda p: store.get(p, (None, None))))
    monkeypatch.setattr(KeyManager, "set_key", staticmethod(lambda p, k: store.__setitem__(p, (k, "keyring")) or "keyring"))
    monkeypatch.setattr(tray_icon, "_load_config", lambda: {"stt_mode": "auto"})
    monkeypatch.setattr(key_info, "_http_get", fake_http({
        "openrouter.ai/api/v1/key": (200, {"data": {"limit": None}}), "groq.com": (401, None)}))
    tray = tray_icon.TrayIcon()
    tray._list_mics = lambda: []
    with native_menu_dpi():
        panel = ControlPanel(tray)
        panel._check_autostart = lambda: None
        panel._build()
        try:
            panel._go("links")
            assert set(panel._key_badges) == {"openrouter"}  # only the configured key is emphasised
            card = panel._key_badges["openrouter"].master.master
            assert card.cget("highlightbackground") == panel.colors["accent"]

            dialog = key_ui.KeyDialog(panel, "groq")
            dialog.value.set("gsk_refused_key_123456")
            dialog._checked("gsk_refused_key_123456", key_info.check("groq", "gsk_refused_key_123456"))
            assert "groq" not in store  # refused key never saved
            assert "401" in dialog.status.cget("text")
            dialog.close()

            report = key_info.check("openrouter", store["openrouter"][0])
            panel._key_tooltip._draw(card, "openrouter", store["openrouter"][0], "keyring", report)
            texts = [w.cget("text") for w in _walk(panel._key_tooltip.top) if w.winfo_class() == "Label"]
            assert "sk-or-v1-…1234" in texts and "Nessun limite sulla chiave" in texts
            assert store["openrouter"][0] not in texts  # the full key is never shown
            panel._key_tooltip.hide()
        finally:
            panel.root.destroy()


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)
