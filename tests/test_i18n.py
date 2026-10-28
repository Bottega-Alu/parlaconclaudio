"""Every interface string wrapped in _() has an English and a Brazilian Portuguese translation."""
import ast
import json
import string
from pathlib import Path

import pytest

from src.voice_bridge import i18n

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "voice_bridge"


def wrapped_strings():
    found = {}
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "N_")
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, f"{path.name}:{node.lineno}")
    return found


def fields(text):
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name is not None)


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGES if code != i18n.SOURCE])
def test_catalogue_covers_every_wrapped_string(code):
    catalogue = json.loads((i18n.LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
    sources = wrapped_strings()
    missing = {text: where for text, where in sources.items() if not catalogue.get(text)}
    assert not missing, f"{code}: {len(missing)} untranslated, e.g. {list(missing.items())[:5]}"
    unused = sorted(set(catalogue) - set(sources))
    assert not unused, f"{code}: unused keys {unused[:5]}"
    wrong = [text for text in sources if fields(text) != fields(catalogue[text])]
    assert not wrong, f"{code}: placeholders differ in {wrong[:5]}"


def test_language_decision_tree(monkeypatch):
    monkeypatch.setattr(i18n, "windows_language", lambda: "pt-BR")
    assert i18n.resolve("en") == "en"
    assert i18n.resolve("system") == "pt-BR"
    # Unknown or empty is "never chose": Italian, as the announcements hook decides.
    assert i18n.resolve("xx") == "it"
    assert i18n.resolve(None) == "it"
    monkeypatch.setattr(i18n, "windows_language", lambda: None)
    assert i18n.resolve("system") == "it"
    assert i18n.resolve("xx") == "it"


def test_missing_translation_falls_back_to_italian(monkeypatch):
    monkeypatch.setitem(i18n._catalogs, "en", {"Esci": "Exit"})
    i18n.set_language("en")
    try:
        assert i18n._("Esci") == "Exit"
        assert i18n._("Frase senza traduzione") == "Frase senza traduzione"
    finally:
        i18n.set_language("it")
