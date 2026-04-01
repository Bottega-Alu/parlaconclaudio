"""
API Key Manager — secure key storage with layered fallback.

Resolution order:
1. Environment variables (GROQ_API_KEY, DEEPGRAM_API_KEY)
2. OS keyring via `keyring` library (if installed)
3. tts_config.json as explicit last resort
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
}

_JSON_KEYS = {
    "groq": "stt_api_key_groq",
    "deepgram": "stt_api_key_deepgram",
}

_KEYRING_SERVICE = "parlaconclaudio"


def _keyring_available() -> bool:
    try:
        import keyring  # noqa: F401
        return True
    except ImportError:
        return False


def _load_json_config() -> dict:
    try:
        if TTS_CONFIG.is_file():
            return json.loads(TTS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_json_config(config: dict) -> None:
    TTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    TTS_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


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
                return "keyring"
            except Exception as e:
                logger.warning(f"Keyring write failed for {provider}: {e} — falling back to JSON")

        # Fallback: JSON config
        json_key = _JSON_KEYS.get(provider)
        if json_key:
            config = _load_json_config()
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

        json_key = _JSON_KEYS.get(provider)
        if json_key:
            config = _load_json_config()
            config.pop(json_key, None)
            _save_json_config(config)

    @staticmethod
    def has_any_cloud_key() -> bool:
        """Check if at least one cloud API key is configured."""
        return bool(KeyManager.get_key("groq") or KeyManager.get_key("deepgram"))
