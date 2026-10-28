"""
API Key Manager — secure key storage with layered fallback.

Resolution order:
1. Environment variables (GROQ_API_KEY, DEEPGRAM_API_KEY)
2. OS keyring via `keyring` library (if installed)
3. tts_config.json as explicit last resort (only when no keyring backend works)

Keys found in tts_config.json are moved into the keyring at start-up
(migrate_json_keys), so an install never keeps them in plaintext when it can avoid it.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"

_ENV_VARS = {
    "groq": "GROQ_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

_JSON_KEYS = {
    "groq": "stt_api_key_groq",
    "deepgram": "stt_api_key_deepgram",
    "openrouter": "stt_api_key_openrouter",
}

_KEYRING_SERVICE = "parlaconclaudio"


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


class ConfigUnreadable(RuntimeError):
    """tts_config.json exists but is not valid JSON: rewriting it would wipe every setting."""


def _load_json_config() -> dict:
    """Read-only view: a missing or unreadable file reads as empty."""
    try:
        return _load_json_config_for_update()
    except ConfigUnreadable:
        return {}


def _load_json_config_for_update() -> dict:
    """Current content before a rewrite; raises instead of returning {} for a corrupt file."""
    if not TTS_CONFIG.is_file():
        return {}
    try:
        config = json.loads(TTS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigUnreadable(f"{TTS_CONFIG}: {e}") from e
    if not isinstance(config, dict):
        raise ConfigUnreadable(f"{TTS_CONFIG}: not a JSON object")
    return config


def _save_json_config(config: dict) -> None:
    """Atomic: a crash mid-write leaves the previous file, never a truncated one."""
    TTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = TTS_CONFIG.with_name(TTS_CONFIG.name + ".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, TTS_CONFIG)


def _drop_json_keys(providers) -> None:
    config = _load_json_config_for_update()
    names = [_JSON_KEYS[p] for p in providers if _JSON_KEYS.get(p) in config]
    if names:
        for name in names:
            config.pop(name)
        _save_json_config(config)


class KeyManager:
    """Layered API key resolution: env → keyring → JSON config."""

    @staticmethod
    def get_key(provider: str) -> str | None:
        """Retrieve API key for *provider* ('groq' or 'deepgram')."""
        provider = provider.lower()

        # 1. Environment variable
        env_var = _ENV_VARS.get(provider)
        if env_var:
            val = os.environ.get(env_var)
            if val:
                return val

        # 2. OS keyring
        if _keyring_available():
            try:
                import keyring
                val = keyring.get_password(_KEYRING_SERVICE, provider)
                if val:
                    return val
            except Exception as e:
                logger.debug(f"Keyring read failed for {provider}: {e}")

        # 3. JSON config fallback
        json_key = _JSON_KEYS.get(provider)
        if json_key:
            config = _load_json_config()
            val = config.get(json_key)
            if val:
                return val

        return None

    @staticmethod
    def set_key(provider: str, key: str) -> str:
        """Store API key. Returns storage location used ('keyring' or 'json').

        Tries keyring first; falls back to JSON with a warning.
        """
        provider = provider.lower()

        if _keyring_available():
            try:
                import keyring
                keyring.set_password(_KEYRING_SERVICE, provider, key)
                logger.info(f"API key for {provider} saved to OS keyring")
                # An older plaintext copy would otherwise outlive the new key.
                _drop_json_keys([provider])
                return "keyring"
            except Exception as e:
                logger.warning(f"Keyring write failed for {provider}: {e} — falling back to JSON")

        # Fallback: JSON config
        json_key = _JSON_KEYS.get(provider)
        if json_key:
            config = _load_json_config_for_update()
            config[json_key] = key
            _save_json_config(config)
            logger.info(f"API key for {provider} saved to tts_config.json (plaintext fallback)")
            return "json"

        raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def delete_key(provider: str) -> None:
        """Remove stored key from all stores."""
        provider = provider.lower()

        if _keyring_available():
            try:
                import keyring
                keyring.delete_password(_KEYRING_SERVICE, provider)
            except Exception:
                pass

        if provider in _JSON_KEYS:
            _drop_json_keys([provider])

    @staticmethod
    def migrate_json_keys() -> list[str]:
        """Move plaintext keys from tts_config.json into the keyring; returns the providers moved.

        A key leaves the file only after the keyring returns it back unchanged.
        Without a working keyring nothing changes (the JSON stays the fallback).
        """
        if not _keyring_available():
            return []
        try:
            config = _load_json_config_for_update()
        except ConfigUnreadable as e:
            logger.warning(f"Key migration skipped: {e}")
            return []
        import keyring
        moved = []
        for provider, json_key in _JSON_KEYS.items():
            value = config.get(json_key)
            if not value:
                continue
            try:
                keyring.set_password(_KEYRING_SERVICE, provider, value)
                if keyring.get_password(_KEYRING_SERVICE, provider) == value:
                    moved.append(provider)
            except Exception as e:
                logger.warning(f"Key migration to keyring failed for {provider}: {type(e).__name__}")
        if moved:
            _drop_json_keys(moved)
            logger.info(f"API keys moved from tts_config.json to the OS keyring: {', '.join(moved)}")
        return moved

    @staticmethod
    def has_any_cloud_key() -> bool:
        """Check if at least one cloud API key is configured."""
        return bool(KeyManager.get_key("groq") or KeyManager.get_key("deepgram"))
