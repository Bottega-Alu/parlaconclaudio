# Storico bench LLM (append-only)

## run · stt_cleanup · stage 1 · 2026-06-29T13:33:46Z
- eval_set: stt-cleanup-itaeng-v1 (`n/a`)
- judge: `claude-opus-4-8 (inline)`  runs/model: 5
- catalog: openrouter-live-2026-06-29
- results:
  - `qwen/qwen-2.5-7b-instruct` mean=4.6 var=0.1 det_pass=True cost=$2.1e-05 p50=936ms
  - `amazon/nova-micro-v1` mean=4.4 var=0.1 det_pass=True cost=$1.9e-05 p50=601ms
  - `google/gemma-4-26b-a4b-it` mean=4.3 var=0.1 det_pass=True cost=$3.5e-05 p50=1022ms
  - `ibm-granite/granite-4.1-8b` mean=4.0 var=0.2 det_pass=True cost=$2.7e-05 p50=516ms
  - `amazon/nova-lite-v1` mean=3.9 var=0.2 det_pass=True cost=$3.2e-05 p50=648ms
  - `inclusionai/ling-2.6-flash` mean=3.8 var=0.2 det_pass=True cost=$6e-06 p50=1016ms
  - `ibm-granite/granite-4.0-h-micro` mean=3.3 var=0.3 det_pass=False cost=$1e-05 p50=1250ms
  - `nvidia/nemotron-3-super-120b-a12b` mean=2.8 var=0.4 det_pass=False cost=$0.000125 p50=2507ms
  - `nvidia/nemotron-3-nano-30b-a3b` mean=2.5 var=0.5 det_pass=False cost=$8.4e-05 p50=2265ms
  - `google/gemma-3-4b-it` mean=2.0 var=0.5 det_pass=False cost=$2.5e-05 p50=1187ms
  - `mistralai/mistral-nemo` mean=2.0 var=0.6 det_pass=False cost=$1e-05 p50=2077ms
  - `mistralai/mistral-small-24b-instruct-2501` mean=1.8 var=0.6 det_pass=False cost=$2.5e-05 p50=1391ms

## decision · stt_cleanup · 2026-06-29T13:33:46Z
- chosen: `qwen/qwen-2.5-7b-instruct`
- fallback: `amazon/nova-micro-v1`
- rationale: Language-preservation #1: unico 5/5 a NON flippare IT->EN sul langflip-trap e EN preservato su D; piu fedele (no content-add che invece nova-micro fa su E). 0 errori, consistente. Fallback nova-micro: quasi pari, 601ms e cheap. Squalificati: gemma-3-4b (non processa input), mistral-nemo (allucina/traduce), mistral-small (31/50 err), nemotron nano/super (leak CoT), granite-micro (traduce+aggiunge+droppa). Judge: Claude inline.
- run_ref: run-2026-06-29T13-33-46Z
- supersedes: (prima decisione)

## decision · stt_cleanup · 2026-06-29T18:05:09Z
- chosen: `google/gemini-2.5-flash`
- fallback: `qwen/qwen-2.5-7b-instruct`
- rationale: Con glossario LLM-first + garble fonetici SEVERI (di tab->GitHub), gemini-2.5-flash e l UNICO che recupera in modo affidabile (4/5 verificato anti-culo). ~15 modelli provati: cheap/mid (qwen/ling/llama/mistral/nova/lfm) si fermano ai medio-severi (Nextcloud), NON craccano GitHub; qwen3-235b-thinking solo Nextcloud (non e questione di taglia); arcee-trinity-mini 1/5 = culo; gpt-5-nano/gpt-oss reasoning-model -> content vuoto (budget) o errato (di tab->Git). Glossario sempre attivo. qwen-2.5-7b resta fallback cheap per outage. Supersede la decisione no-glossario su garble moderati.
- run_ref: severe-garble-verify-2026-06-29
- supersedes: decision-2026-06-29T13-33-46Z
