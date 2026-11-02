"""What the app knows about a cloud API key: provider metadata, masked form, live check.

check() is a decision tree per provider, using only what each provider really exposes:
- OpenRouter: /api/v1/key (key budget, reset, expiry) + /api/v1/credits (account credit)
- Deepgram: /v1/projects, then /balances (needs the billing:read scope, else "not readable")
- Groq: /openai/v1/models validates the key; Groq has no balance or expiry API.
Errors are structured (KeyCheckError.kind), never bare strings. Results are cached
for CACHE_TTL seconds per key, so hovering a card does not call the provider every time.
"""
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from .i18n import N_, _

logger = logging.getLogger(__name__)

CACHE_TTL = 300
TIMEOUT = 8

PROVIDERS = {
    "groq": {
        "label": "Groq",
        "console": "https://console.groq.com/keys",
        "prefix": "gsk_",
        "about": N_("Riconoscimento vocale cloud, veloce e con piano gratuito. La chiave inizia con gsk_."),
    },
    "deepgram": {
        "label": "Deepgram",
        "console": "https://console.deepgram.com/",
        "prefix": None,
        "about": N_("Riconoscimento vocale cloud di riserva. Crea la chiave nel progetto Deepgram."),
    },
    "openrouter": {
        "label": "OpenRouter",
        "console": "https://openrouter.ai/keys",
        "prefix": "sk-or-",
        "about": N_("Corregge la trascrizione con un LLM. La chiave inizia con sk-or-."),
    },
}

SOURCES = {"env": N_("Variabile d'ambiente"), "keyring": N_("Portachiavi di Windows"),
           "json": N_("File di configurazione (in chiaro)")}
INTERVALS = {"daily": N_("giornaliero"), "weekly": N_("settimanale"), "monthly": N_("mensile")}


def mask(key):
    """First and last characters only: 'sk-or-v1-61…4c66'."""
    key = (key or "").strip()
    if len(key) <= 10:
        return "•" * len(key)
    head = 9 if key.startswith("sk-or-") else 6
    return f"{key[:head]}…{key[-4:]}"


def looks_like(provider, text):
    """True when *text* has the provider's fixed key prefix (only for prefixed providers)."""
    prefix = PROVIDERS[provider]["prefix"]
    text = (text or "").strip()
    return bool(prefix) and text.startswith(prefix) and " " not in text and len(text) > len(prefix) + 8


@dataclass
class KeyCheckError:
    kind: str          # "invalid" (rejected by the provider) | "network" | "http"
    status: int | None = None
    detail: str = ""

    def message(self):
        if self.kind == "invalid":
            return _("Il provider ha rifiutato la chiave (HTTP {code}). Controlla di averla copiata tutta.").format(
                code=self.status)
        if self.kind == "network":
            return _("Impossibile contattare il provider: {detail}").format(detail=self.detail)
        return _("Risposta inattesa dal provider (HTTP {code}).").format(code=self.status)


@dataclass
class KeyReport:
    provider: str
    ok: bool
    lines: list = field(default_factory=list)   # [(label, value)] already translated
    error: KeyCheckError | None = None
    checked_at: float = field(default_factory=time.time)


def _http_get(url, auth):
    """(status, json body or None). Raises urllib.error.URLError / OSError on network failure."""
    request = urllib.request.Request(url, headers={"Authorization": auth, "User-Agent": "parlaconclaudio/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def _money(value):
    return f"${value:,.2f}"


def _date(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y")
    except (AttributeError, ValueError):
        return text


def _rejected(status):
    return KeyCheckError("invalid" if status in (401, 403) else "http", status)


def _check_groq(key):
    status, _body = _http_get("https://api.groq.com/openai/v1/models", f"Bearer {key}")
    if status != 200:
        return KeyReport("groq", False, error=_rejected(status))
    return KeyReport("groq", True, [
        (_("Stato"), _("Valida")),
        (_("Plafond"), _("Groq non espone saldo né scadenza: vedi i limiti su console.groq.com")),
    ])


def _check_deepgram(key):
    auth = f"Token {key}"
    status, body = _http_get("https://api.deepgram.com/v1/projects", auth)
    if status != 200:
        return KeyReport("deepgram", False, error=_rejected(status))
    projects = (body or {}).get("projects") or []
    lines = [(_("Stato"), _("Valida"))]
    if projects:
        project = projects[0]
        lines.append((_("Progetto"), project.get("name") or project.get("project_id", "")))
        b_status, b_body = _http_get(
            f"https://api.deepgram.com/v1/projects/{project['project_id']}/balances", auth)
        balances = ((b_body or {}).get("balances") or []) if b_status == 200 else None
        if balances:
            for balance in balances:
                units = balance.get("units", "usd")
                amount = balance.get("amount", 0)
                lines.append((_("Credito"), _money(amount) if units == "usd" else f"{amount} {units}"))
        elif balances is None:
            lines.append((_("Credito"), _("Non leggibile: la chiave non ha il permesso billing:read")))
        else:
            lines.append((_("Credito"), _money(0)))
    return KeyReport("deepgram", True, lines)


def _check_openrouter(key):
    auth = f"Bearer {key}"
    status, body = _http_get("https://openrouter.ai/api/v1/key", auth)
    if status != 200:
        return KeyReport("openrouter", False, error=_rejected(status))
    data = (body or {}).get("data") or {}
    lines = [(_("Stato"), _("Valida") if not data.get("is_free_tier") else _("Valida, piano gratuito"))]
    budget = data.get("effective_budget") or {}
    limit = budget.get("limit_usd", data.get("limit"))
    remaining = budget.get("remaining_usd", data.get("limit_remaining"))
    interval = budget.get("reset_interval") or data.get("limit_reset")
    if limit is None:
        lines.append((_("Plafond chiave"), _("Nessun limite sulla chiave")))
    else:
        text = _("{left} rimasti su {limit}").format(left=_money(remaining or 0), limit=_money(limit))
        if interval:
            text += f" ({_(INTERVALS.get(interval, interval))})"
        lines.append((_("Plafond chiave"), text))
    if budget.get("resets_at"):
        lines.append((_("Rinnovo"), _date(budget["resets_at"])))
    lines.append((_("Scadenza"), _date(data["expires_at"]) if data.get("expires_at") else _("Nessuna")))
    if data.get("usage_monthly") is not None:
        lines.append((_("Spesa"), _("oggi {today}, questo mese {month}").format(
            today=_money(data.get("usage_daily") or 0), month=_money(data["usage_monthly"]))))
    try:
        c_status, c_body = _http_get("https://openrouter.ai/api/v1/credits", auth)
        credits = ((c_body or {}).get("data") or {}) if c_status == 200 else {}
        if "total_credits" in credits:
            lines.append((_("Credito account"), _money(credits["total_credits"] - credits.get("total_usage", 0))))
    except OSError as e:  # the key is already known valid: the credit line is optional
        logger.debug(f"OpenRouter credits unavailable: {type(e).__name__}")
    return KeyReport("openrouter", True, lines)


_CHECKS = {"groq": _check_groq, "deepgram": _check_deepgram, "openrouter": _check_openrouter}
_cache = {}
_lock = threading.Lock()


def _fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def cached(provider, key):
    """Last report for this exact key if still fresh, else None."""
    with _lock:
        entry = _cache.get(provider)
    if entry and entry[0] == _fingerprint(key) and time.time() - entry[1].checked_at < CACHE_TTL:
        return entry[1]
    return None


def check(provider, key, use_cache=True):
    """Live check of *key*; network errors become a report with kind "network", never an exception."""
    if use_cache and (report := cached(provider, key)):
        return report
    try:
        report = _CHECKS[provider](key.strip())
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", None) or e
        report = KeyReport(provider, False, error=KeyCheckError("network", detail=str(reason)[:120]))
    if report.error is None or report.error.kind != "network":
        with _lock:
            _cache[provider] = (_fingerprint(key.strip()), report)
    logger.info(f"Key check {provider}: {'ok' if report.ok else report.error.kind}")
    return report


def forget(provider):
    with _lock:
        _cache.pop(provider, None)
