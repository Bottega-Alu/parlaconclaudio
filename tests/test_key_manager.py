"""API keys live in the Windows Credential Manager; tts_config.json is only a last resort."""
import json
import sys
import types

import pytest

from src.core.stt_engine import key_manager
from src.core.stt_engine.key_manager import KeyManager


class FakeKeyring(types.ModuleType):
    def __init__(self, fail=False):
        super().__init__("keyring")
        self.store, self.fail = {}, fail

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.fail:
            raise RuntimeError("no backend")
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.store.pop((service, name))


@pytest.fixture
def vault(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    return fake


@pytest.fixture(autouse=True)
def _no_env_keys(monkeypatch):
    for name in ("GROQ_API_KEY", "DEEPGRAM_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def write_config(data):
    key_manager.TTS_CONFIG.write_text(json.dumps(data), encoding="utf-8")


def read_config():
    return json.loads(key_manager.TTS_CONFIG.read_text(encoding="utf-8"))


def test_plaintext_keys_move_to_the_vault_and_leave_the_file(vault):
    write_config({"stt_api_key_groq": "gsk_1", "stt_api_key_openrouter": "sk-or-2", "theme": "dark"})
    assert KeyManager.migrate_json_keys() == ["groq", "openrouter"]
    assert vault.store == {("parlaconclaudio", "groq"): "gsk_1", ("parlaconclaudio", "openrouter"): "sk-or-2"}
    assert read_config() == {"theme": "dark"}
    assert KeyManager.get_key("groq") == "gsk_1"


def test_migration_keeps_the_file_key_when_the_vault_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring(fail=True))
    write_config({"stt_api_key_groq": "gsk_1"})
    assert KeyManager.migrate_json_keys() == []
    assert read_config() == {"stt_api_key_groq": "gsk_1"}


def test_migration_without_keyring_changes_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)  # import keyring -> ImportError
    write_config({"stt_api_key_groq": "gsk_1"})
    assert KeyManager.migrate_json_keys() == []
    assert read_config() == {"stt_api_key_groq": "gsk_1"}


def test_a_corrupt_config_is_never_overwritten(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    key_manager.TTS_CONFIG.write_text("{not json", encoding="utf-8")
    with pytest.raises(key_manager.ConfigUnreadable):
        KeyManager.set_key("groq", "gsk_1")
    assert key_manager.TTS_CONFIG.read_text(encoding="utf-8") == "{not json"


def test_saving_a_key_also_clears_an_old_plaintext_copy(vault):
    write_config({"stt_api_key_groq": "gsk_old", "theme": "dark"})
    assert KeyManager.set_key("groq", "gsk_new") == "keyring"
    assert read_config() == {"theme": "dark"}
    assert KeyManager.get_key("groq") == "gsk_new"


def test_groq_translation_key_comes_from_the_vault(vault):
    from src.voice_bridge import audio_file_processor
    vault.set_password("parlaconclaudio", "groq", "gsk_vault")
    assert audio_file_processor._load_groq_key() == "gsk_vault"


def test_keyring_is_installed_by_the_bootstrap(monkeypatch):
    import importlib.util
    path = key_manager.Path(__file__).resolve().parents[1] / "installer" / "vb_bootstrap.py"
    spec = importlib.util.spec_from_file_location("vb_bootstrap_probe", path)
    bootstrap = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "vb_bootstrap_probe", bootstrap)
    spec.loader.exec_module(bootstrap)
    assert any(dep.startswith("keyring") for dep in bootstrap.BASE_DEPS)
