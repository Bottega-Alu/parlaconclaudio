"""Batch audio-file transcriber with multilingual translation.

For each audio file the user selects, this module produces up to four
text outputs saved next to the source:

  <stem>.original.txt   — Whisper transcribe, source language
  <stem>.en.txt         — Whisper translate (English)
  <stem>.it.txt         — Groq LLM translate (Italian)
  <stem>.pt.txt         — Groq LLM translate (Portuguese)

The translation step for the language matching the source is skipped
(no point translating Italian to Italian). All four texts are also
concatenated into a single clipboard payload at the end of the batch
so the user can paste the full report somewhere.

Translation backend: Groq Cloud (OpenAI-compatible /chat/completions
endpoint), reads its API key from the shared tts_config.json or the
GROQ_API_KEY env variable. If no key is present the IT/PT files are
omitted and a warning is logged once.

This module is OS-agnostic (no Win32 calls). It is invoked from a
background thread so the tray UI stays responsive.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Audio formats accepted by Whisper / ffmpeg
SUPPORTED_EXTENSIONS = (
    ".mp3", ".wav", ".m4a", ".ogg", ".oga", ".opus", ".flac",
    ".mp4", ".mkv", ".webm", ".aac", ".wma", ".aiff",
)

# Groq settings — model is a reasonable default; the SDK isn't required,
# we hit the REST API directly so the only dep is `requests` (already
# pulled in by edge-tts via aiohttp transitively, plus pip-installed
# in any normal python). Fall back to urllib if requests isn't available.
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_TIMEOUT_S = 60
# Cloudflare in front of api.groq.com blocks Python-urllib's default
# User-Agent (returns 1010). Send a browser-style UA so the request
# isn't classified as automated traffic from a banned signature.
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"


@dataclass
class FileResult:
    """Outcome of processing a single audio file."""
    source: Path
    detected_language: str = ""
    original: str = ""
    english: str = ""
    italian: str = ""
    portuguese: str = ""
    saved_files: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    """Aggregate outcome of a batch run."""
    files: list[FileResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for f in self.files if not f.errors and f.original)

    @property
    def total(self) -> int:
        return len(self.files)


def _load_groq_key() -> str | None:
    """Resolve Groq API key: env var first, then tts_config.json."""
    env_key = os.environ.get("GROQ_API_KEY")
    if env_key:
        return env_key.strip()
    try:
        if TTS_CONFIG.is_file():
            cfg = json.loads(TTS_CONFIG.read_text(encoding="utf-8"))
            key = cfg.get("stt_api_key_groq") or cfg.get("groq_api_key")
            if key:
                return str(key).strip()
    except Exception:
        pass
    return None


def _groq_translate(text: str, target_lang_name: str, api_key: str) -> str:
    """Call Groq /chat/completions to translate text into target_lang_name.

    Returns the translated string. Raises on failure — caller catches.
    Empty input short-circuits to empty output.
    """
    if not text.strip():
        return ""
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You are a professional translator. Translate the user's "
                    f"text into {target_lang_name}. Output ONLY the translation "
                    f"with no preamble, no quotes, no commentary. Preserve line "
                    f"breaks. If the text is already in {target_lang_name}, "
                    f"return it verbatim."
                ),
            },
            {"role": "user", "content": text},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": HTTP_USER_AGENT,
    }
    # Prefer requests, fall back to urllib so we don't add a hard dep
    try:
        import requests  # type: ignore
        r = requests.post(GROQ_API_URL, data=body, headers=headers,
                          timeout=GROQ_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(GROQ_API_URL, data=body, headers=headers,
                                      method="POST")
        with urllib.request.urlopen(req, timeout=GROQ_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


_LANG_NAMES = {
    "it": "Italian",
    "pt": "Portuguese (Brazilian variant)",
    "en": "English",
}


class AudioFileProcessor:
    """Drive the four-output pipeline for one or more audio files.

    The Transcriber dependency must already be initialized with a local
    Whisper engine — translation to English uses Whisper task=translate
    which is only available locally in this codebase.
    """

    def __init__(self, transcriber):
        self._transcriber = transcriber
        self._groq_key_cached: str | None = None
        self._groq_warned = False

    def _groq_key(self) -> str | None:
        if self._groq_key_cached is None:
            self._groq_key_cached = _load_groq_key() or ""
        return self._groq_key_cached or None

    def process_file(self, path: Path) -> FileResult:
        result = FileResult(source=path)
        if not path.is_file():
            result.errors.append(f"file not found: {path}")
            return result

        # 1) Source-language transcribe (auto-detect)
        try:
            text, lang = self._transcriber.transcribe_file(path, language=None,
                                                           task="transcribe")
            result.original = text
            result.detected_language = lang
        except Exception as e:
            msg = f"transcribe failed: {e}"
            logger.error(f"[{path.name}] {msg}")
            result.errors.append(msg)
            return result

        # 2) English via Whisper translate (skip if already English)
        if lang == "en":
            result.english = result.original
        else:
            try:
                en_text, _ = self._transcriber.transcribe_file(path,
                                                               language=None,
                                                               task="translate")
                result.english = en_text
            except Exception as e:
                msg = f"english translate failed: {e}"
                logger.warning(f"[{path.name}] {msg}")
                result.errors.append(msg)

        # 3) IT and PT via Groq LLM (skip if already that language)
        groq_key = self._groq_key()
        if groq_key:
            for lang_code in ("it", "pt"):
                if lang == lang_code:
                    setattr(result, _attr_for(lang_code), result.original)
                    continue
                try:
                    translated = _groq_translate(
                        result.original, _LANG_NAMES[lang_code], groq_key,
                    )
                    setattr(result, _attr_for(lang_code), translated)
                except Exception as e:
                    msg = f"{lang_code} translate failed: {e}"
                    logger.warning(f"[{path.name}] {msg}")
                    result.errors.append(msg)
        else:
            if not self._groq_warned:
                logger.warning(
                    "Groq API key not configured — skipping Italian/Portuguese "
                    "translations. Set GROQ_API_KEY env or stt_api_key_groq in "
                    "tts_config.json to enable them."
                )
                self._groq_warned = True

        # 4) Persist .txt files alongside the source
        result.saved_files = self._save_outputs(result)
        return result

    def process_files(
        self,
        paths: list[Path],
        on_progress: Callable[[int, int, Path], None] | None = None,
    ) -> BatchResult:
        batch = BatchResult()
        total = len(paths)
        for i, p in enumerate(paths):
            if on_progress:
                try:
                    on_progress(i + 1, total, p)
                except Exception:
                    pass
            batch.files.append(self.process_file(p))
        return batch

    @staticmethod
    def _save_outputs(result: FileResult) -> list[Path]:
        base = result.source.with_suffix("")
        saved: list[Path] = []
        for suffix, content in (
            (".original.txt", result.original),
            (".en.txt", result.english),
            (".it.txt", result.italian),
            (".pt.txt", result.portuguese),
        ):
            if not content:
                continue
            out = Path(str(base) + suffix)
            try:
                out.write_text(content, encoding="utf-8")
                saved.append(out)
            except Exception as e:
                logger.warning(f"failed to write {out}: {e}")
        return saved

    @staticmethod
    def build_clipboard_payload(batch: BatchResult) -> str:
        """Aggregate every file's outputs into one big text block."""
        chunks: list[str] = []
        for fr in batch.files:
            chunks.append(f"=== {fr.source.name} (lang={fr.detected_language or '?'}) ===")
            if fr.original:
                chunks.append(f"[ORIGINAL]\n{fr.original}")
            if fr.english and fr.english != fr.original:
                chunks.append(f"[EN]\n{fr.english}")
            if fr.italian and fr.italian != fr.original:
                chunks.append(f"[IT]\n{fr.italian}")
            if fr.portuguese and fr.portuguese != fr.original:
                chunks.append(f"[PT]\n{fr.portuguese}")
            if fr.errors:
                chunks.append("[ERRORS]\n" + "\n".join(fr.errors))
            chunks.append("")
        return "\n".join(chunks).strip() + "\n"


def _attr_for(lang_code: str) -> str:
    return {"it": "italian", "pt": "portuguese", "en": "english"}[lang_code]
