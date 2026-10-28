"""Interface language.

Source strings are Italian and wrapped in `_()`; `locales/<code>.json` maps each
one to its translation. Decision tree for the language shown:
  1. `ui_language` in tts_config.json set to a supported code -> that language
  2. "system" (the default)  -> the Windows display language, if supported
  3. otherwise               -> Italian
A string missing from a catalogue shows in Italian, never empty; tests keep the
catalogues complete (tests/test_i18n.py).

Placeholders use named fields: `_("Dispositivo {number}").format(number=n)`.
Labels kept in module constants are marked with `N_()` (no translation at import
time, when the language is not known yet) and translated where shown: `_(label)`.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = "ParlaConClaudio"
SOURCE = "it"
LANGUAGES = {"it": "Italiano", "en": "English", "pt-BR": "Português (Brasil)"}
SYSTEM = "system"
LOCALES_DIR = Path(__file__).resolve().parent / "locales"

# Windows primary language ids (LANGID & 0x3FF) -> supported code.
_WINDOWS_PRIMARY = {0x10: "it", 0x09: "en", 0x16: "pt-BR"}

_catalogs: dict[str, dict[str, str]] = {}
_current = SOURCE


def windows_language() -> str | None:
    try:
        import ctypes
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    except (AttributeError, OSError):
        return None
    return _WINDOWS_PRIMARY.get(langid & 0x3FF)


def resolve(setting: str | None) -> str:
    if setting in LANGUAGES:
        return setting
    return windows_language() or SOURCE


def catalog(code: str) -> dict[str, str]:
    if code == SOURCE:
        return {}
    if code not in _catalogs:
        try:
            _catalogs[code] = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("Interface language %s unavailable: showing Italian", code)
            _catalogs[code] = {}
    return _catalogs[code]


def set_language(setting: str | None) -> str:
    """Apply the saved setting ("system" or a code); returns the language now shown."""
    global _current
    _current = resolve(setting)
    return _current


def current() -> str:
    return _current


def _(text: str) -> str:
    return catalog(_current).get(text) or text


def N_(text: str) -> str:
    """Mark a constant label for the catalogues; translate it where shown with _()."""
    return text
