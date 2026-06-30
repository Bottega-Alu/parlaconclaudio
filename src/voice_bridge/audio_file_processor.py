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

from .stt_cleanup import cleanup_text

logger = logging.getLogger(__name__)

# Defaults for the STT cleanup agent — overridable via tts_config.json
# (mirrors VoiceBridgeConfig.stt_cleanup_model / stt_cleanup_fallback).
DEFAULT_CLEANUP_MODEL = "google/gemini-2.5-flash"
DEFAULT_CLEANUP_FALLBACK = "qwen/qwen-2.5-7b-instruct"
# Above this many characters, only the (cleaned) transcription is produced —
# the English/IT/PT translations and TTS dubs are skipped.
DEFAULT_EXTRAS_MAX_CHARS = 2500

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
    cleaned: str | None = None  # language-preserving STT cleanup; None = not run/failed
    english: str = ""
    italian: str = ""
    portuguese: str = ""
    saved_files: list[Path] = field(default_factory=list)
    dub_files: list[Path] = field(default_factory=list)
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

# Default voices for the TTS dub round-trip (one per target language).
# Edge-TTS supports many alternatives; these are picked from the same
# catalogue used in tray_icon.py.
#
# Note: Italian deliberately uses the *English* AvaMultilingual voice in
# cross-lingual mode rather than a native it-IT voice. The "Multilingual"
# neural voices (Ava, Thalita) are a newer generation and, in A/B testing,
# read Italian more naturally than the older native it-IT-IsabellaNeural —
# and beat local models (XTTS-v2, Kokoro, Piper) too. Multilingual voices
# are designed for exactly this kind of cross-lingual synthesis.
DUB_VOICES = {
    "it": "en-US-AvaMultilingualNeural",
    "pt": "pt-BR-ThalitaMultilingualNeural",
    "en": "en-US-AvaMultilingualNeural",
}


def _load_tts_config() -> dict:
    """Read the shared tts_config.json once (empty dict on any failure)."""
    try:
        if TTS_CONFIG.is_file():
            return json.loads(TTS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _is_dub_enabled() -> bool:
    """Read transcribe_tts_dub flag from tts_config.json (default True)."""
    return bool(_load_tts_config().get("transcribe_tts_dub", True))


def _normalize_glossary(raw) -> list[str]:
    """Normalize the configured glossary to a clean list of terms.

    Accepts a list/tuple of strings or a single comma-separated string;
    anything else (or empty) yields an empty list.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        return []
    return [str(t).strip() for t in items if str(t).strip()]


def _synthesize_dub(text: str, voice: str, output_path: Path) -> bool:
    """Synthesize one TTS dub file via edge-tts. Returns True on success.

    Empty text is treated as success-with-nothing-to-do (no file written).
    Output files smaller than 512 bytes are removed (silent failures).
    """
    if not text.strip():
        return True
    try:
        import asyncio
        import edge_tts
    except Exception as e:
        logger.warning(f"edge-tts not available: {e}")
        return False
    try:
        async def _run():
            comm = edge_tts.Communicate(text, voice, rate="+0%", pitch="+0Hz")
            await comm.save(str(output_path))
        asyncio.run(_run())
    except Exception as e:
        logger.warning(f"edge-tts synth failed for {output_path.name}: {e}")
        if output_path.is_file() and output_path.stat().st_size < 512:
            try:
                output_path.unlink()
            except Exception:
                pass
        return False
    if not output_path.is_file() or output_path.stat().st_size < 512:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    return True


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
        self._openrouter_key_cached: str | None = None

    def _groq_key(self) -> str | None:
        if self._groq_key_cached is None:
            self._groq_key_cached = _load_groq_key() or ""
        return self._groq_key_cached or None

    def _openrouter_key(self) -> str | None:
        """Resolve the OpenRouter key via the layered KeyManager (env→keyring→json)."""
        if self._openrouter_key_cached is None:
            try:
                from ..core.stt_engine.key_manager import KeyManager
                self._openrouter_key_cached = KeyManager.get_key("openrouter") or ""
            except Exception as e:
                logger.debug(f"OpenRouter key resolution failed: {e}")
                self._openrouter_key_cached = ""
        return self._openrouter_key_cached or None

    def process_file(self, path: Path) -> FileResult:
        result = FileResult(source=path)
        if not path.is_file():
            result.errors.append(f"file not found: {path}")
            return result

        cfg = _load_tts_config()

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

        # 1b) Language-preserving STT cleanup (OpenRouter). NullObject: on any
        # failure `cleaned` stays None and downstream falls back to `original`.
        if bool(cfg.get("enable_stt_cleanup", True)):
            result.cleaned = cleanup_text(
                result.original,
                model=str(cfg.get("stt_cleanup_model", DEFAULT_CLEANUP_MODEL)),
                fallback=str(cfg.get("stt_cleanup_fallback", DEFAULT_CLEANUP_FALLBACK)),
                api_key=self._openrouter_key(),
                glossary=_normalize_glossary(cfg.get("stt_cleanup_glossary", [])),
            )
            if result.cleaned and result.cleaned != result.original:
                logger.info(f"[{path.name}] STT cleanup applied "
                            f"({len(result.original)}→{len(result.cleaned)} chars)")

        # All text-derived steps run on the cleaned text when available.
        base = result.cleaned or result.original

        # Long-file guard: above the cap, produce ONLY the (cleaned) source
        # transcription — skip English/IT/PT translations and TTS dubs.
        max_chars = int(cfg.get("transcribe_extras_max_chars", DEFAULT_EXTRAS_MAX_CHARS))
        suppress_extras = max_chars > 0 and len(base) > max_chars
        if suppress_extras:
            logger.info(
                f"[{path.name}] long transcription ({len(base)} chars > {max_chars}) "
                f"— skipping translations and TTS dubs (transcription only)"
            )

        translations_enabled = bool(cfg.get("transcribe_translations", True))

        # 2) English via Whisper translate (re-transcribes the AUDIO, not the
        # text). Skipped for long files or when translations are disabled.
        if not suppress_extras and translations_enabled:
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

        # 3) IT and PT via Groq LLM, run on the cleaned text (skip if already
        # that language). Skipped for long files or when translations disabled.
        if not suppress_extras and translations_enabled:
            groq_key = self._groq_key()
            if groq_key:
                for lang_code in ("it", "pt"):
                    if lang == lang_code:
                        setattr(result, _attr_for(lang_code), base)
                        continue
                    try:
                        translated = _groq_translate(
                            base, _LANG_NAMES[lang_code], groq_key,
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

        # 4) Persist .txt files alongside the source (source-lang .txt = cleaned)
        result.saved_files = self._save_outputs(result)

        # 5) TTS round-trip: synthesize spoken versions for each non-empty
        # translation in a language different from the source. Skipped for
        # long files; otherwise gated by the transcribe_tts_dub flag.
        if not suppress_extras and _is_dub_enabled():
            result.dub_files = self._synthesize_dubs(result)

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
    def _synthesize_dubs(result: FileResult) -> list[Path]:
        """Generate <stem>.<lang>.mp3 for IT/PT/EN translations.

        Skips the language that matches the detected source language
        (no point re-synthesizing the original), and any empty text.
        """
        base = result.source.with_suffix("")
        produced: list[Path] = []
        targets = (
            ("it", result.italian),
            ("pt", result.portuguese),
            ("en", result.english),
        )
        for lang_code, text in targets:
            if not text:
                continue
            if result.detected_language == lang_code:
                # Source language already covered by the source audio.
                continue
            voice = DUB_VOICES.get(lang_code)
            if not voice:
                continue
            out = Path(str(base) + f".{lang_code}.mp3")
            ok = _synthesize_dub(text, voice, out)
            if ok and out.is_file():
                produced.append(out)
            else:
                result.errors.append(f"{lang_code} TTS dub failed")
        return produced

    @staticmethod
    def _save_outputs(result: FileResult) -> list[Path]:
        base = result.source.with_suffix("")
        saved: list[Path] = []
        for suffix, content in (
            # Source-language file carries the cleaned text when available;
            # result.original keeps the raw transcription in-memory for audit.
            (".original.txt", result.cleaned or result.original),
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
            if fr.dub_files:
                chunks.append("[DUB FILES]\n" + "\n".join(str(p) for p in fr.dub_files))
            if fr.errors:
                chunks.append("[ERRORS]\n" + "\n".join(fr.errors))
            chunks.append("")
        return "\n".join(chunks).strip() + "\n"


def _attr_for(lang_code: str) -> str:
    return {"it": "italian", "pt": "portuguese", "en": "english"}[lang_code]
