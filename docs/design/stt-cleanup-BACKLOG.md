# stt-cleanup — Backlog

Idee deferred, in ordine. Lo slice shippato (`feat/stt-cleanup`) copre cleanup
file-drop + live-mic con glossario **statico** (hand-seeded). Questi sono i passi
successivi.

## 1. Glossario ad apprendimento attivo (human-in-the-loop) ⭐ [richiesta Frisco, 2026-06-29]

**Idea.** Quando il cleanup **non capisce** un garble (bassa confidenza / mapping
ambiguo), invece di tirare a indovinare o lasciarlo sporco, **CHIEDE all'utente**
("hai detto *di tab* → intendevi **GitHub**?") e **IMPARA**: la risposta entra nel
glossario / in uno store di correzioni, così la volta dopo è risolta da sola. Il
glossario **cresce dall'uso reale** — LLM-first grounding che si auto-alimenta, e i
garble severi (di tab→GitHub) si risolvono **una volta sola, per sempre**.

**Pezzi da decidere (capability matrix):**

| pezzo | opzioni |
|---|---|
| **rilevare l'incertezza** | (a) structured output: il modello ritorna `{cleaned, unresolved:[{token, candidates, confidence}]}`; (b) post-pass che flagga token non-dizionario "garble-shaped"; (c) due modelli → disaccordo = incerto |
| **chiedere all'utente** | (a) non-bloccante: notifica tray "rivedi 1 termine"; (b) batch review delle ultime N trascrizioni; (c) inline nel flusso (blocca la dettatura → costoso, evitare) |
| **store appreso** | `corrections.json` append-only `{garble → termine}` (o estende `stt_cleanup_glossary`), reiniettato nel system prompt |
| **loop** | ogni conferma utente → glossario aggiornato → cleanup migliora **senza toccare codice** |

**Perché conta.** È l'evoluzione del glossario statico → glossario che si costruisce
da solo. Cura proprio i garble severi che oggi nessun modello prende senza grounding.

**Non-banale.** UX del "chiedere" senza interrompere la dettatura; soglia di confidenza
(non chiedere troppo); de-dup delle domande; privacy (i termini personali restano locali).
Va prototipato sul flusso reale, non specificato a tavolino.

## 2. Altri deferred (raccolti in questa sessione)

- **Multi-speaker / diarization** — fuori scope (dettatura single-voice). Se servirà:
  cloud free-tier (Gladia 10h/mese, AssemblyAI) o stack locale Qwen3-ASR + NVIDIA
  Sortformer (plumbing, satura i 16GB) — vedi `.work/stt-engine-scan/`.
- **Routing modello per-lingua** — già config-driven (PT→gemini si setta a mano);
  auto-route per `detected_language` è il passo dopo.
- **Lingue preferite / allowlist per l'auto-detect** [Frisco, 2026-06-30] — l'utente
  dichiara le sue lingue (es. EN + IT); l'auto-detect viene **vincolato a quel set**:
  se Whisper rileva fuori dall'allowlist (o con bassa `language_probability`), si
  ripiega sulla più probabile **tra le consentite**. È la versione principled del
  "fallback su lingua primaria" — un'**allowlist groundata** (regola LLM-first: passa
  il set valido e fai scegliere), non una word-list hardcoded. Misurare prima il
  multilingua puro: si costruisce solo se le clip IT brevi si misdetectano davvero.
- **Latenza live** — cleanup async/streaming: mostra il grezzo subito, poi corregge
  in-place; oppure primo passo con modello locale a bassa latenza.
- **Re-bench engine STT periodico** — oggi Whisper batte Qwen3-ASR sul reale, ma le
  versioni nuove vanno ri-testate; ledger + harness già pronti (`.work/stt-engine-scan/bench2/`).
- **Inserimento key OpenRouter da UI** [Frisco, 2026-06-30] — estendere il menu tray
  "Set API keys" (che già gestisce Groq/Deepgram con input mascherato) per includere la
  key OpenRouter del **cleanup LLM (affinamento output STT)**, così si imposta dall'app
  invece che a mano in `tts_config.json`/env. Riusa `key_manager` (entry `openrouter`
  già presente) + il pattern di input mascherato esistente nel tray.
