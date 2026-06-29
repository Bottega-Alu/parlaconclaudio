# stt-cleanup — SPEC (lean)

**Data:** 2026-06-29
**Sorgente decisioni:** brainstorm sessione 2026-06-29 + Bench-1 (ledger `rollouts/llm-bench/ledger.json`, run `stt_cleanup` 2026-06-29).
**REQUIRED SUB-SKILL (impl):** superpowers:test-driven-development.

## Outcome anchor
- **COSA:** dopo la trascrizione audio, un agente piccolo e veloce (LLM via OpenRouter) ripulisce il testo — corregge sviste STT, restaura i termini tech EN resi foneticamente, e **preserva la lingua parlata** (mai tradurre IT↔EN).
- **A CHI:** Frisco che detta note miste IT/EN e oggi riceve testo sporco e/o tradotto a forza.
- **SUCCESSO:** detto in EN → testo EN pulito; detto in IT → IT pulito; "spicciuto testo" → "speech to text"; nessun flip di lingua; nessuna parafrasi.

## Decisioni lockate
- **Modello:** chosen `qwen/qwen-2.5-7b-instruct`, fallback `amazon/nova-micro-v1` (vincitori Bench-1: language-preservation + fedeltà). Provider **OpenRouter** (OpenAI-compatible `/chat/completions`).
- **Prompt:** `rollouts/llm-bench/prompt_stt_cleanup.txt` (language-preserving, no-paraphrase, output solo testo). È la fonte unica; copiarlo in `src/` o leggerlo come asset.
- **Key:** `OPENROUTER_API_KEY` env-first / `stt_api_key_openrouter` in `~/.claude/cache/tts/tts_config.json` (già configurata).
- **Scope slice-1:** path **file-drop** (`AudioFileProcessor`). Live-mic cleanup = non-goal (fase 2).
- **Lingua:** NON passare `detected_language` di Whisper come autorevole (è l'etichetta inaffidabile, radice del bug); il modello inferisce la lingua dal testo. SEPARATAMENTE: fixare il bias `it` di Whisper a monte.
- **Degradazione:** OpenRouter giù/lento/errore → ritorna il testo **grezzo** (`original`), log strutturato, **mai bloccare** il pipeline. NullObject + retry/circuit-breaker.
- **Persistenza:** tenere `original` (grezzo) **e** `cleaned` (nuovo). Le traduzioni Groq downstream girano su `cleaned ?? original`; `original` resta per audit.
- **Flag:** `enable_stt_cleanup` (default da decidere; raccomandato `true` una volta validato).

## Reuse map (attiva, non ricostruire)
| primitiva esistente | stato oggi | cosa diventa |
|---|---|---|
| `_groq_translate()` `audio_file_processor.py:103-147` | client HTTP OpenAI-compatible (Bearer, requests+urllib fallback, timeout, UA spoofing) | **template** per `_openrouter_cleanup()` |
| `key_manager.py` `_ENV_VARS:19` / `_JSON_KEYS:24` | risolve groq/deepgram (env→keyring→json) | + entry `openrouter` (verificare se già presenti) |
| `NullSTTEngine` `base.py:78` | fallback STT che non crasha | pattern per cleanup che ritorna `original` su fallimento |
| retry 429/5xx `groq_stt.py:98-118` | retry con sleep su `Retry-After` | stesso schema per OpenRouter |
| `VoiceBridgeConfig` `config.py:8-63` | dataclass config (`whisper_language:23`) | + `enable_stt_cleanup`, `stt_cleanup_model`, `stt_cleanup_fallback` |

## Binding notes (file:line — contratto, ALTER non rewrite)
- **Hook:** `audio_file_processor.py:246-250` — inserire il cleanup TRA `result.original = text` e la chiamata a `_groq_translate`. **Estendere**, non rimpiazzare.
- `result.original` resta il **grezzo**; aggiungere `result.cleaned`. Il downstream (translate, salvataggio `.txt`) usa `cleaned ?? original`.
- `_groq_translate` è il **template**: copiarne la struttura (requests+urllib fallback, Bearer, timeout tipo `GROQ_TIMEOUT_S:47`) ma puntare a `https://openrouter.ai/api/v1/chat/completions` — **non** riusare URL/key di Groq.
- **Whisper `it`-bias:** `transcriber.py:106-115` — il fallback hardcoded `"it"` va reso auto-detect/config-driven. **ALTER** della logica di fallback; NON toccare il config-read `:90-99`.
- **Prompt:** system = contenuto di `prompt_stt_cleanup.txt`; user = testo grezzo. `temperature≈0.2`, `max_tokens≈512`.

## Decision tree (runtime)
1. `transcribe` → `text, lang`
2. `if not enable_stt_cleanup` → `cleaned=None`; comportamento attuale (usa `original`)
3. `else` → `_openrouter_cleanup(text, model=chosen)`:
   - ok → `result.cleaned = cleaned_text`
   - errore/timeout (dopo 1 retry) → fallback `nova-micro`
     - ok → `cleaned`
     - errore → `cleaned=None`, log warning strutturato; **circuit-breaker**: dopo N fail consecutivi, skip cleanup per la sessione
4. downstream usa `cleaned ?? original`

## Capability matrix
| capability | slice-1 | fase 2 |
|---|---|---|
| file-drop cleanup | ✅ | |
| live-mic cleanup | | ✅ (stesso agente, hook `transcriber.py`) |
| language preservation (prompt) | ✅ | |
| Whisper `it`-bias fix | ✅ (1-line, config) | |
| engine swap (Qwen3-ASR) | — (Bench-2, traccia separata) | |
| multi-speaker/diarization | non-goal | non-goal |

## Non-goals
- live-mic cleanup (fase 2, stesso agente).
- engine STT swap (Bench-2, traccia separata).
- multi-speaker/diarization (deferred — cloud free-tier se mai servirà).
- passare `detected_language` come autorevole.

## Implementation sketch (file → azione → responsabilità)
| file | azione | responsabilità | test |
|---|---|---|---|
| `src/voice_bridge/stt_cleanup.py` | **CREA** | `cleanup_text(text, *, model, fallback) -> str|None`; retry/circuit-breaker; NullObject su errore | unit |
| `src/voice_bridge/audio_file_processor.py` | **MODIFICA** `:246-250` | chiama cleanup, set `result.cleaned`; downstream `cleaned ?? original` | integration |
| `src/voice_bridge/config.py` | **MODIFICA** | `+enable_stt_cleanup`, `+stt_cleanup_model`, `+stt_cleanup_fallback` | unit |
| `src/core/stt_engine/key_manager.py` | **VERIFICA/MODIFICA** `:19-27` | entry `openrouter` (env+json) | — |
| `src/voice_bridge/transcriber.py` | **MODIFICA** `:106-115` | fix `it`-bias (auto-detect default) | unit |
| `tests/test_stt_cleanup.py` | **CREA** | mock HTTP; happy/fallback/degradation/disabled; invarianti language-preservation | — |

## Test plan
- **TDD:** test prima dell'impl. Mock dell'HTTP OpenRouter (nessuna rete nei test).
- **Casi:** (1) happy → `cleaned`; (2) primary fail → fallback; (3) entrambi fail → `original`; (4) `enable_stt_cleanup=false` → `original`; (5) invarianti language-preservation riusando gli item di `eval_set_stt_cleanup.json` (lingua preservata, termine restaurato, no-add).
- **Comando:** `pytest tests/test_stt_cleanup.py`

## Self-review
- Nessun placeholder. Scope = ancora (file-drop cleanup language-preserving). Reuse > rebuild (template `_groq_translate`, NullObject, retry esistenti). Decisioni lockate dal Bench-1 reale, non a memoria.
