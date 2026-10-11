# parlaconclaudio

## Identità
Voice bridge Windows per Claude Code: STT push-to-talk (`Ctrl+Alt+Space`, GPU locale + fallback cloud) che detta ovunque via clipboard+paste, più un hook TTS che annuncia fine task/permessi. Nato per dettare a Claude Code, oggi usato come dettatura generale (batte lo Speech Recognition di Windows).

## Mappa
| Componente | Path | Ruolo |
|---|---|---|
| State machine STT | `src/voice_bridge/bridge.py` | IDLE→RECORDING→TRANSCRIBING→OUTPUT |
| Orchestratore multi-backend | `src/voice_bridge/transcriber.py` | decision tree: local GPU → Groq → Deepgram |
| Cleanup LLM post-STT | `src/voice_bridge/stt_cleanup.py` | NullObject + retry + circuit-breaker via OpenRouter |
| Config | `src/voice_bridge/config.py` | dataclass unica `VoiceBridgeConfig`, tutti i default runtime |
| Key storage | `src/core/stt_engine/key_manager.py` | risoluzione env > keyring > json |
| Engines STT | `src/core/stt_engine/{whisper_rtx,groq_stt,deepgram_stt}.py` | `base.py` = ABC + `NullSTTEngine` |
| Hook TTS | `scripts/notify-tts.py` | sorgente qui, ma l'hook reale in `~/.claude/settings.json` punta a `~/.claude/scripts/notify-tts.py` — copiare a mano dopo edit |
| Bench modelli cleanup | `rollouts/llm-bench/` | `ledger.json` + `HISTORY.md` append-only, via skill `llm-model-bench` |
| Bench engine STT | `.work/stt-engine-scan/` | `bench2/` Whisper vs Qwen3-ASR (2026-06, qualitativo); `bench3-it/` Whisper vs Confucius4-R2T2 vs Qwen3-ASR-1.7B-hf su FLEURS it_it con WER (2026-09-21): Whisper resta, vedi `BENCH3-RESULT.md` §9 |
| Bench voci TTS | `.work/tts-bench/` | candidate kokoro/xtts/piper/edge-tts |

## Stack e vincoli
- Python 3.11+, poetry (`pyproject.toml`), venv locale in `venv/`
- `torch`/`torchaudio` pinned su CUDA 12.8 (`cu128`) — GPU NVIDIA richiesta per il path locale
- `faster-whisper` 1.0 + `ctranslate2` 4.0, modello `large-v3` float16
- Windows-only: `pywin32`, `pynput`, `pystray` (system tray)
- Cleanup LLM **attuale** (verificato in `config.py`): `google/gemini-2.5-flash` primario + `qwen/qwen-2.5-7b-instruct` fallback, via OpenRouter — la SPEC in `docs/design/` (2026-06-29 mattina) riporta ancora qwen come primario: è superata dalla decisione in `HISTORY.md` delle 18:05 dello stesso giorno (garble fonetici severi, serve gemini). Fidarsi di `config.py` + `HISTORY.md`, non della SPEC da sola.

## Come si avvia
- `.\VoiceBridge.bat` oppure `.\venv\Scripts\python.exe -m src.voice_bridge`
- Test: `.\venv\Scripts\python.exe -m pytest` (113 test raccolti; `tests/test_stt_cleanup.py` è il core del cleanup LLM; `tests/conftest.py` impedisce ai test di troncare `voicebridge.log` del bridge live)

## Convenzioni
- Output operativi mai dentro `.claude/`: `rollouts/<topic>/` per bench, `.work/<topic>/` per workspace ad-hoc/handoff, `docs/design/` per SPEC+BACKLOG
- Bench LLM sempre via skill `llm-model-bench`; il ledger `rollouts/llm-bench/HISTORY.md` è append-only e la fonte di verità sul modello "current" (non fidarsi di doc statici più vecchi)
- Pattern matching/normalization: LLM-first con whitelist groundata (`stt_cleanup_glossary` in `tts_config.json`), mai alias dictionary hardcoded

## Riferimenti
- `docs/design/2026-06-29-stt-cleanup-SPEC.md` — SPEC lean, parzialmente superata (vedi Stack)
- `docs/design/stt-cleanup-BACKLOG.md` — deferred: glossario auto-apprendente human-in-the-loop, allowlist lingue, latenza live, re-bench engine STT
- `.work/handoffs/HANDOFF-2026-06-30-0352-stt-cleanup-v1-0.md` — ultimo handoff sessione (stt-cleanup v1.0 live)
