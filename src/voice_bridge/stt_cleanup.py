"""Language-preserving STT cleanup agent (OpenRouter, OpenAI-compatible).

After Whisper transcribes an audio file the raw text is often dirty:
STT mis-hears words, English technical terms come out phonetically
("spicciuto testo" -> "speech to text"), punctuation is missing. This
module runs that raw text through a small, fast LLM that *cleans* it
while **preserving the spoken language** — it never translates IT<->EN.

Design notes
------------
* Transport mirrors ``audio_file_processor._groq_translate``: prefer
  ``requests`` and fall back to ``urllib`` so we add no hard dependency,
  Bearer auth, browser-style User-Agent, finite timeout. It points at
  OpenRouter — it deliberately does NOT reuse Groq's URL or key.
* NullObject degradation: on *any* failure ``cleanup_text`` returns
  ``None`` and the caller keeps the raw transcription. The pipeline is
  never blocked by a flaky/slow/dead cleanup service.
* Retry: one retry on 429 (honouring ``Retry-After``) and on 5xx /
  network errors — the same shape as ``groq_stt._call_api``.
* Fallback model: if the primary model fails, a secondary model is
  tried before giving up.
* Circuit-breaker: after N consecutive *full* failures in a session the
  agent stops calling the network and returns ``None`` immediately, so a
  prolonged outage costs nothing per file.
* The detected language is NOT passed to the model as authoritative —
  the model infers the language from the text itself (the Whisper
  language label is the unreliable signal we are working around).

This module is OS-agnostic and import-light (no numpy / no edge-tts).
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

# OpenRouter, OpenAI-compatible Chat Completions endpoint. NOT Groq.
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_TIMEOUT_S = 30

# Some edge networks block Python's default urllib User-Agent; send a
# browser-style UA so the request isn't classified as bot traffic.
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sampling: low temperature (deterministic, minimum-edit). max_tokens is
# computed per-call from the input length (see _chat_completion): a fixed 512
# truncated long transcriptions, and thinking-models (gemini-2.5-flash) also
# spend tokens on internal reasoning, so the cap must scale with the text.
CLEANUP_TEMPERATURE = 0.2
_MAX_TOKENS_FLOOR = 1024
_MAX_TOKENS_CEIL = 4096

_MAX_RETRIES = 1                 # one retry on 429 / 5xx / network (à la groq_stt)
_CIRCUIT_BREAKER_THRESHOLD = 3   # consecutive full failures before we give up

# System prompt — single source of truth is
# rollouts/llm-bench/prompt_stt_cleanup.txt; embedded here as a constant so
# the module has no filesystem dependency at runtime.
SYSTEM_PROMPT = (
    "You are a transcript cleanup tool for speech-to-text (STT) output. You "
    "receive RAW STT text and return a CLEANED version. Follow these rules "
    "exactly.\n\n"
    "1. LANGUAGE — preserve, never translate.\n"
    "   Keep the dominant language of the input unchanged. If the speaker "
    "spoke Italian, output Italian; if English, output English. NEVER "
    "translate from one language to the other. Sentences that mix Italian "
    "with English technical terms STAY mixed — do not translate the technical "
    "terms into Italian, and do not flip the whole sentence to English just "
    "because it contains English words.\n\n"
    "2. FIX STT ERRORS — recover misheard words from context.\n"
    "   Correct words the STT misheard, especially technical terms rendered "
    "phonetically. Examples:\n"
    '   - "spicciuto testo" -> "speech to text"\n'
    '   - "circus breaker" -> "circuit breaker"\n'
    '   - "gee pee you" -> "GPU", "are tee ex five thousand eighty" -> "RTX '
    '5080", "v ram" -> "VRAM"\n'
    "   - \"un'acchi\" -> \"una key\"\n"
    "   Use the surrounding context to infer the intended term.\n\n"
    "3. RESTORE TECHNICAL TERMS — correct English form, not translated.\n"
    "   English tech terms inside Italian text are restored in their correct "
    'English spelling, e.g. "open router" -> "OpenRouter", "voice bridge" -> '
    '"voice bridge". Never translate a correct technical term (e.g. do NOT '
    'turn "circuit breaker" into "interruttore di circuito").\n\n'
    "4. MECHANICS.\n"
    "   Fix punctuation, capitalization, spacing, and obvious word-splits "
    '("stream ing" -> "streaming") to natural written form.\n\n'
    "5. DO NOT.\n"
    "   Do not paraphrase, rephrase, summarize, expand, add, or remove "
    "content. Do not change meaning, tone, or register. Do not formalize "
    'colloquial speech ("boh", "vedi tu" stay as they are). Make the MINIMUM '
    "edits needed.\n\n"
    "6. OUTPUT.\n"
    "   Return ONLY the cleaned text. No preamble, no quotes, no explanation, "
    "no notes, no markdown."
)


def _build_system_prompt(glossary: list[str] | None) -> str:
    """Base prompt, optionally extended with a config-driven glossary section.

    The glossary grounds the LLM (LLM-first, whitelist passed in the prompt)
    so it can recover SEVERE phonetic garbles ("di tab" -> "GitHub") that
    generic context alone can't disambiguate. Empty/None -> unchanged prompt
    (backward compatible).
    """
    if not glossary:
        return SYSTEM_PROMPT
    terms = ", ".join(t.strip() for t in glossary if t and t.strip())
    if not terms:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + (
        "\n\n7. GLOSSARY — the speaker uses these exact terms; STT may render "
        "them as SEVERE phonetic garbles. When context clearly fits, map the "
        "garbled token to the correct term:\n" + terms
    )


# --------------------------------------------------------------------------- #
# Circuit-breaker (session-scoped consecutive-failure counter)
# --------------------------------------------------------------------------- #

_consecutive_failures = 0


def _breaker_open() -> bool:
    return _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD


def _record_failure() -> None:
    global _consecutive_failures
    _consecutive_failures += 1


def _record_success() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


def reset_circuit_breaker() -> None:
    """Reset the session failure counter (used at session start / in tests)."""
    global _consecutive_failures
    _consecutive_failures = 0


# --------------------------------------------------------------------------- #
# HTTP transport
# --------------------------------------------------------------------------- #

class _HttpError(Exception):
    """Normalised HTTP failure carrying status + Retry-After.

    ``status is None`` denotes a transport-level error (timeout / connection
    reset) which is treated as retryable, like a 5xx.
    """

    def __init__(self, status: int | None = None, retry_after: str | None = None,
                 message: str = ""):
        super().__init__(message or (f"http_{status}" if status else "network_error"))
        self.status = status
        self.retry_after = retry_after


def _http_post_json(url: str, body: bytes, headers: dict, timeout: float) -> dict:
    """POST raw JSON bytes and return the parsed response dict.

    Prefers ``requests``; falls back to ``urllib`` when it isn't installed.
    Raises ``_HttpError`` (with ``status``/``retry_after``) on HTTP >=400,
    or ``_HttpError(status=None)`` on network/timeout failures.
    """
    try:
        import requests  # type: ignore
    except ImportError:
        requests = None  # type: ignore

    if requests is not None:
        try:
            r = requests.post(url, data=body, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as e:  # type: ignore[attr-defined]
            raise _HttpError(status=None, message=str(e))
        if r.status_code >= 400:
            raise _HttpError(status=r.status_code,
                             retry_after=r.headers.get("Retry-After"))
        return r.json()

    # urllib fallback — no third-party dependency required.
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _HttpError(status=e.code,
                         retry_after=e.headers.get("Retry-After"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _HttpError(status=None, message=str(e))


def _safe_float(value: str | None, default: float = 1.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _chat_completion(text: str, *, model: str, api_key: str,
                     system_prompt: str = SYSTEM_PROMPT, _retry: int = 0) -> str:
    """Call one model once (with bounded retry) and return the cleaned text.

    Raises ``_HttpError`` when the model ultimately fails — the caller decides
    whether to try the fallback model or give up.
    """
    # Cleanup output ≈ input length; scale the cap so long notes aren't
    # truncated, leaving headroom for thinking-model reasoning tokens.
    max_tokens = min(_MAX_TOKENS_CEIL, max(_MAX_TOKENS_FLOOR, len(text) // 3 + 256))
    payload = {
        "model": model,
        "temperature": CLEANUP_TEMPERATURE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": HTTP_USER_AGENT,
        # Optional OpenRouter attribution headers (harmless if ignored).
        "X-Title": "parlaconclaudio-stt-cleanup",
    }

    try:
        data = _http_post_json(OPENROUTER_API_URL, body, headers, OPENROUTER_TIMEOUT_S)
    except _HttpError as e:
        if e.status == 429:
            if _retry < _MAX_RETRIES and e.retry_after:
                wait = min(_safe_float(e.retry_after), 10.0)
                logger.warning(
                    "STT cleanup [%s]: rate limited (429), retrying in %.1fs",
                    model, wait,
                )
                time.sleep(wait)
                return _chat_completion(text, model=model, api_key=api_key,
                                        system_prompt=system_prompt,
                                        _retry=_retry + 1)
            raise
        if (e.status is None or e.status >= 500) and _retry < _MAX_RETRIES:
            logger.warning(
                "STT cleanup [%s]: %s, retrying...",
                model, "server error" if e.status else "network error",
            )
            time.sleep(1)
            return _chat_completion(text, model=model, api_key=api_key,
                                    system_prompt=system_prompt,
                                    _retry=_retry + 1)
        raise

    return data["choices"][0]["message"]["content"].strip()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def cleanup_text(
    text: str,
    *,
    model: str,
    fallback: str | None,
    api_key: str | None,
    glossary: list[str] | None = None,
) -> str | None:
    """Clean raw STT *text* with a language-preserving LLM.

    Returns the cleaned string, or ``None`` on any failure (NullObject —
    the caller falls back to the raw transcription). Tries ``model`` first,
    then ``fallback`` if given. A session circuit-breaker short-circuits
    once the service has failed repeatedly.

    ``glossary`` is an optional list of exact terms the speaker uses; when
    non-empty it is appended to the system prompt so the model can recover
    severe phonetic garbles. The detected language is intentionally not
    passed in — the model infers it from the text, sidestepping Whisper's
    unreliable label.
    """
    if not text or not text.strip():
        return None  # nothing to clean
    if not api_key:
        logger.warning("OpenRouter API key not configured — skipping STT cleanup")
        return None
    if _breaker_open():
        logger.info(
            "STT cleanup circuit-breaker open (%d consecutive failures) — "
            "using raw text", _consecutive_failures,
        )
        return None

    system_prompt = _build_system_prompt(glossary)
    candidates = [model]
    if fallback and fallback != model:
        candidates.append(fallback)

    for candidate in candidates:
        try:
            cleaned = _chat_completion(text, model=candidate, api_key=api_key,
                                       system_prompt=system_prompt)
        except Exception as e:  # noqa: BLE001 — degrade on anything
            logger.warning("STT cleanup model '%s' failed: %s", candidate, e)
            continue
        if cleaned:
            _record_success()
            return cleaned
        logger.warning("STT cleanup model '%s' returned empty output", candidate)

    _record_failure()
    logger.warning(
        "STT cleanup failed for all models (%s) — keeping raw transcription",
        ", ".join(candidates),
    )
    return None
