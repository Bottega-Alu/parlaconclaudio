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
* Chunking: long transcriptions are split on sentence boundaries and
  cleaned one block per call, because a single call is capped by the
  model's own output budget (see ``_CLEANUP_CHUNK_CHARS``). A chunk that
  fails keeps its raw text instead of failing the whole transcription.
* Reasoning off: every call carries ``reasoning: {"effort": "none"}``.
  Cleanup is mechanical correction, and reasoning tokens are billed as
  output tokens out of the same ``max_tokens`` budget as the answer — a
  thinking model spends the budget on itself and then truncates. A
  provider that rejects the parameter gets one re-send without it.
* Truncation guard: a response with ``finish_reason == "length"`` is a
  *failure*, never a success — its body is mutilated by construction. The
  error carries the provider's token accounting (including
  ``reasoning_tokens``) so the next incident is read, not guessed.
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
import re
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

# Reasoning: OFF. Cleanup is mechanical text correction — there is nothing to
# think about, and on OpenRouter reasoning tokens are billed as output tokens
# drawn from the SAME `max_tokens` budget as the answer. gemini-2.5-flash is a
# thinking model, so it was spending that budget on an internal monologue and
# then running out mid-answer: every primary call failed the truncation guard
# (field log 2026-06-30 18:54, max_tokens=2915 / input=7977 chars) and silently
# demoted us to the qwen fallback, which the bench had rejected for severe
# phonetic garbles. `effort: "none"` is the value OpenRouter documents as
# "Disables reasoning entirely"; it is part of the `reasoning.effort` enum in
# the API reference, so it is provider-agnostic — OpenRouter normalises it per
# provider instead of us hardcoding Google's `thinkingBudget: 0`.
#
# The docs do NOT promise that a non-thinking model ignores the object, so this
# is belt AND braces: _chat_completion re-sends once without it on a 4xx
# reject (see _REASONING_REJECT_STATUSES). No list of "which models think".
CLEANUP_REASONING = {"effort": "none"}
_REASONING_REJECT_STATUSES = (400, 422)

# Sampling: low temperature (deterministic, minimum-edit). max_tokens is
# computed per-call from the input length (see _budget_for): a fixed 512
# truncated long transcriptions, so the cap must scale with the text.
CLEANUP_TEMPERATURE = 0.2
_MAX_TOKENS_FLOOR = 1024
_MAX_TOKENS_CEIL = 8192

# Headroom added on top of the text budget. With CLEANUP_REASONING this should
# be dead weight — it is deliberate belt-and-braces for the case we cannot test
# offline: a provider that quietly ignores `effort: "none"` and thinks anyway.
# It is free insurance, because max_tokens is a CAP, not a charge: unspent
# tokens are never generated and never billed.
_REASONING_ALLOWANCE = 2048

# Above this many characters the text is cleaned in several calls instead of
# one (see _chunk_text). The value is derived from _MAX_TOKENS_CEIL, it is not
# a taste call: a single call's budget is
# `max(_MAX_TOKENS_FLOOR, len(text)//3 + 256) + _REASONING_ALLOWANCE` capped at
# _MAX_TOKENS_CEIL, so a long enough text used to hit that cap and come back
# silently mutilated (a 2h / 74k-char transcription was cut to 15k chars in the
# field). Keeping the chunk well under that bound means the per-chunk budget
# can NEVER reach the ceiling:
#     max(1024, 8000//3 + 256) + 2048 = 2922 + 2048 = 4970 < 8192
# That inequality is the invariant; test_chunk_threshold_can_never_reach_the
# _token_ceiling asserts it against the real formula, not against a copy of it.
_CLEANUP_CHUNK_CHARS = 8000

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
# Chunking (sentence-boundary splitting for long transcriptions)
# --------------------------------------------------------------------------- #
#
# NOTE on the regex: the project rule is LLM-first for parsing/normalization,
# regex only as a last resort. This is the explicit exception — splitting a
# text into blocks is *tokenization*, one of the listed regex-acceptable uses.
# Nothing semantic is decided here: a wrong boundary costs a slightly awkward
# block, never a wrong word, and the LLM still sees full sentences.

# A boundary is: sentence-final punctuation followed by whitespace, or one or
# more newlines. The separator is kept with the block that precedes it, so
# "".join(chunks) reproduces the input byte for byte.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?…])\s+|\n+")


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentence-ish blocks, separators included."""
    pieces: list[str] = []
    last = 0
    for m in _SENTENCE_BOUNDARY_RE.finditer(text):
        pieces.append(text[last:m.end()])
        last = m.end()
    if last < len(text):
        pieces.append(text[last:])
    return pieces


def _hard_split(piece: str, limit: int) -> list[str]:
    """Break a single over-long block on word boundaries.

    Whisper can emit minutes of speech with no sentence terminator at all; a
    block like that would otherwise blow past the budget. Cuts on the last
    space inside the window, or mid-word if there is not even one.
    """
    if len(piece) <= limit:
        return [piece]
    out: list[str] = []
    rest = piece
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit)
        cut = limit if cut <= 0 else cut + 1  # keep the space on the left side
        out.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        out.append(rest)
    return out


def _chunk_text(text: str, limit: int = _CLEANUP_CHUNK_CHARS) -> list[str]:
    """Split *text* into blocks of at most *limit* chars on sentence boundaries.

    Returns ``[text]`` unchanged when it already fits — the common case (live
    dictation) keeps its exact single-call behaviour.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buf = ""
    for piece in _split_sentences(text):
        for block in _hard_split(piece, limit):
            if buf and len(buf) + len(block) > limit:
                chunks.append(buf)
                buf = block
            else:
                buf += block
    if buf:
        chunks.append(buf)
    return chunks


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

class _TruncatedOutputError(Exception):
    """The model stopped because it ran out of output budget.

    ``finish_reason == "length"`` means the body we got back is mutilated by
    construction — accepting it would silently destroy the tail of the user's
    transcription. It is raised *outside* the transport retry block on
    purpose: retrying the same model with the same cap would truncate again,
    so the caller moves straight on to the fallback model and, failing that,
    keeps the raw text.
    """


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


def _budget_for(text: str) -> int:
    """Output-token cap for one call on *text*.

    Two separate concerns, kept separate on purpose:
      * the *text* budget — cleanup output ≈ input length, floored so a short
        dictation still gets room, i.e. ``max(FLOOR, len//3 + 256)``;
      * the *reasoning* allowance — headroom for a provider that ignores our
        ``reasoning: {"effort": "none"}`` and thinks anyway.
    The sum is capped at ``_MAX_TOKENS_CEIL``, which chunking guarantees is
    never actually reached (see ``_CLEANUP_CHUNK_CHARS``).
    """
    text_budget = max(_MAX_TOKENS_FLOOR, len(text) // 3 + 256)
    return min(_MAX_TOKENS_CEIL, text_budget + _REASONING_ALLOWANCE)


def _usage_note(data: dict) -> str:
    """Render the token telemetry OpenRouter attached to *data*, if any.

    Returns e.g. ``", completion_tokens=2915, reasoning_tokens=2711"`` — the
    two numbers that turn "why was this truncated?" into a fact instead of a
    guess. Empty string when the provider sent no ``usage`` block; nothing is
    invented, and a malformed block never breaks the error path.
    """
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return ""
    bits = []
    completion = usage.get("completion_tokens")
    if completion is not None:
        bits.append(f"completion_tokens={completion}")
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        bits.append(f"reasoning_tokens={details['reasoning_tokens']}")
    return (", " + ", ".join(bits)) if bits else ""


def _chat_completion(text: str, *, model: str, api_key: str,
                     system_prompt: str = SYSTEM_PROMPT, _retry: int = 0,
                     _reasoning: bool = True) -> str:
    """Call one model once (with bounded retry) and return the cleaned text.

    Raises ``_HttpError`` when the model ultimately fails, or
    ``_TruncatedOutputError`` when the answer came back cut off — the caller
    decides whether to try the fallback model or give up.

    ``_reasoning`` is internal: it starts ``True`` (thinking explicitly off via
    ``CLEANUP_REASONING``) and flips to ``False`` for the single re-send that
    follows a provider rejecting the parameter.
    """
    max_tokens = _budget_for(text)
    payload = {
        "model": model,
        "temperature": CLEANUP_TEMPERATURE,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }
    if _reasoning:
        payload["reasoning"] = dict(CLEANUP_REASONING)
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
                                        _retry=_retry + 1, _reasoning=_reasoning)
            raise
        if e.status in _REASONING_REJECT_STATUSES and _reasoning:
            # OpenRouter documents `reasoning` as a unified parameter but does
            # NOT promise that a model without thinking ignores it. Rather than
            # keeping a hardcoded list of thinking-capable models (which rots
            # every time a default changes), we let the provider tell us: on a
            # 4xx we re-send once, without the parameter. `_reasoning=False`
            # makes this branch unreachable the second time, so a 400 that had
            # nothing to do with reasoning still terminates after one re-send.
            logger.warning(
                "STT cleanup [%s]: request rejected (HTTP %s) with the "
                "`reasoning` parameter — retrying once without it", model, e.status,
            )
            return _chat_completion(text, model=model, api_key=api_key,
                                    system_prompt=system_prompt,
                                    _retry=_retry, _reasoning=False)
        if (e.status is None or e.status >= 500) and _retry < _MAX_RETRIES:
            logger.warning(
                "STT cleanup [%s]: %s, retrying...",
                model, "server error" if e.status else "network error",
            )
            time.sleep(1)
            return _chat_completion(text, model=model, api_key=api_key,
                                    system_prompt=system_prompt,
                                    _retry=_retry + 1, _reasoning=_reasoning)
        raise

    choice = data["choices"][0]
    # Truncation guard — MUST come before we look at the content. A body that
    # stopped on "length" is incomplete; treating it as a success is exactly
    # how a 2h transcription silently lost 80% of its text.
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        # Carry the provider's own token accounting into the message: a high
        # reasoning_tokens here is the smoking gun that thinking ate the
        # budget (i.e. our `effort: "none"` was ignored), and that is the one
        # thing the previous incident could only be guessed at.
        raise _TruncatedOutputError(
            f"output truncated (finish_reason=length, max_tokens={max_tokens}, "
            f"input={len(text)} chars, "
            f"reasoning_param={'effort=none' if _reasoning else 'stripped'}"
            f"{_usage_note(data)})"
        )
    return choice["message"]["content"].strip()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _cleanup_chunk(chunk: str, *, candidates: list[str], api_key: str,
                   system_prompt: str) -> str | None:
    """Clean ONE block through the candidate models. ``None`` = all failed.

    Also the circuit-breaker accounting point: a long text is many calls, so
    the breaker is fed per chunk. That way a service outage halfway through a
    2h transcription stops the remaining calls instead of paying for one
    doomed request per block.
    """
    if _breaker_open():
        logger.info(
            "STT cleanup circuit-breaker open (%d consecutive failures) — "
            "using raw text", _consecutive_failures,
        )
        return None

    for candidate in candidates:
        try:
            cleaned = _chat_completion(chunk, model=candidate, api_key=api_key,
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

    Long texts are split into sentence-aligned chunks and cleaned one call
    per chunk (transparently — the signature does not change). A chunk that
    cannot be cleaned keeps its raw text and the rest of the transcription is
    still cleaned; only an all-chunks failure degrades to ``None``.

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

    chunks = _chunk_text(text)
    if len(chunks) == 1:
        return _cleanup_chunk(chunks[0], candidates=candidates,
                              api_key=api_key, system_prompt=system_prompt)

    logger.info(
        "STT cleanup: %d chars exceed the %d-char single-call budget — "
        "cleaning in %d chunks", len(text), _CLEANUP_CHUNK_CHARS, len(chunks),
    )
    parts: list[str] = []
    failed = 0
    for i, chunk in enumerate(chunks, start=1):
        cleaned = _cleanup_chunk(chunk, candidates=candidates,
                                 api_key=api_key, system_prompt=system_prompt)
        if cleaned:
            parts.append(cleaned)
        else:
            failed += 1
            logger.warning(
                "STT cleanup: chunk %d/%d failed — keeping its raw text "
                "(%d chars)", i, len(chunks), len(chunk),
            )
            parts.append(chunk.strip())

    if failed == len(chunks):
        logger.warning(
            "STT cleanup failed for every one of the %d chunks — keeping raw "
            "transcription", len(chunks),
        )
        return None
    if failed:
        logger.warning("STT cleanup: %d of %d chunks kept their raw text",
                       failed, len(chunks))
    # Blank line between blocks: the boundaries are sentence-aligned, so this
    # only adds paragraph breaks to what was one unreadable wall of text.
    return "\n\n".join(p for p in parts if p)
