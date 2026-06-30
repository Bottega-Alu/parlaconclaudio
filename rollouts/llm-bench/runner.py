"""Bench runner per il task stt_cleanup (OpenRouter, OpenAI-compatible).

Pipeline per ogni (modello x item x run):
  prompt cleanup -> candidate call -> gate deterministico -> judge call -> score.

Concorrenza (pattern Frisco): parti con N_init chiamate su modelli DIVERSI, poi ogni
`ramp_interval` secondi aggiungi `ramp_step` permessi in parallelo, ricicli finche'
non hai >= runs per modello. Cap a `max_concurrency`. Robustezza per-call: qualunque
errore -> item a score 0 (esce dalla top-k) invece di abortire il funnel.

Prezzi: letti LIVE dal catalogo OpenRouter a runtime (mai hardcoded).
Key: env OPENROUTER_API_KEY. Mai scritta su disco/log.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

CATALOG_URL = "https://openrouter.ai/api/v1/models"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def resolve_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("ERRORE: OPENROUTER_API_KEY non in env.")
    return key


def fetch_price_map() -> dict[str, tuple[float, float]]:
    req = urllib.request.Request(CATALOG_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))["data"]
    out: dict[str, tuple[float, float]] = {}
    for e in data:
        pid = e.get("id")
        pr = e.get("pricing", {}) or {}
        try:
            out[pid] = (float(pr.get("prompt", 0) or 0), float(pr.get("completion", 0) or 0))
        except (TypeError, ValueError):
            out[pid] = (0.0, 0.0)
    return out


def call_chat(key: str, model: str, messages: list[dict], *, temperature: float,
              max_tokens: int, retries: int = 1) -> dict:
    """Ritorna {text, prompt_tokens, completion_tokens, latency_ms}. Solleva su errore."""
    body = json.dumps({
        "model": model, "messages": messages,
        "temperature": temperature, "max_tokens": max_tokens,
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/parlaconclaudio",
        "X-Title": "stt-cleanup-bench",
    }
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(CHAT_URL, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            latency = int((time.monotonic() - t0) * 1000)
            choices = payload.get("choices") or []
            if not choices:
                raise ValueError(f"no choices: {str(payload)[:200]}")
            text = (choices[0].get("message") or {}).get("content")
            if text is None:
                raise ValueError("content:null")
            usage = payload.get("usage") or {}
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            return {"text": text, "prompt_tokens": pt, "completion_tokens": ct, "latency_ms": latency}
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_exc  # type: ignore[misc]


def cleanup_messages(system_prompt: str, item: dict) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": item["input"]},
    ]


JUDGE_SYS = (
    "You are an impartial evaluator of speech-to-text (STT) transcript CLEANUP. "
    "You are NOT one of the models being judged. Score the CANDIDATE strictly, 1-5 per dimension.\n"
    "Dimensions:\n"
    "- language_preservation: candidate is in the EXPECTED language with NO translation "
    "(a translation to the other language = 1).\n"
    "- term_restoration: garbled/misheard technical terms correctly restored; REQUIRED_TERMS present and correct.\n"
    "- fidelity: meaning preserved, NO paraphrase, NO added/removed content, minimal edits.\n"
    "- mechanics: punctuation, capitalization, spacing, natural written form.\n"
    'Return STRICT JSON only: {"language_preservation":n,"term_restoration":n,"fidelity":n,'
    '"mechanics":n,"evidence":{"language_preservation":"...","term_restoration":"...",'
    '"fidelity":"...","mechanics":"..."}}'
)


def judge_messages(item: dict, candidate: str) -> list[dict]:
    user = (
        f"EXPECTED_LANGUAGE: {item['expected_lang']}\n"
        f"REQUIRED_TERMS: {item.get('must_contain', [])}\n"
        f"RAW_INPUT: {item['input']}\n"
        f"REFERENCE: {item.get('reference', '')}\n"
        f"CANDIDATE: {candidate}"
    )
    return [{"role": "system", "content": JUDGE_SYS}, {"role": "user", "content": user}]


def defence_json(text: str) -> dict:
    """De-imbusta l'oggetto {...} da code-fence/prosa, poi json.loads."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no json object in judge output")
    return json.loads(m.group(0))


def det_gate(item: dict, output: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    out = output.strip()
    low = out.lower()
    if not out:
        return False, ["empty"]
    n_in, n_out = len(item["input"]), len(out)
    if not (0.4 * n_in <= n_out <= 2.5 * n_in):
        reasons.append(f"length_out_of_bound({n_out}vs{n_in})")
    for term in item.get("must_contain", []):
        if term.lower() not in low:
            reasons.append(f"missing:{term}")
    for term in item.get("must_not_contain", []):
        if term.lower() in low:
            reasons.append(f"forbidden:{term}")
    return (len(reasons) == 0), reasons


def quality(dims: dict) -> float:
    keys = ["language_preservation", "term_restoration", "fidelity", "mechanics"]
    vals = [float(dims[k]) for k in keys if k in dims]
    return sum(vals) / len(vals) if vals else 0.0


class RampLimiter:
    """Semaforo che parte a `init` permessi e ne aggiunge `step` ogni `interval` s fino a `cap`."""
    def __init__(self, init: int, step: int, interval: float, cap: int):
        self._sem = threading.Semaphore(init)
        self._added, self._target = 0, max(0, cap - init)
        self._step, self._interval = step, interval
        self._stop = threading.Event()
        if self._target > 0:
            threading.Thread(target=self._ramp, daemon=True).start()

    def _ramp(self) -> None:
        while self._added < self._target and not self._stop.is_set():
            time.sleep(self._interval)
            n = min(self._step, self._target - self._added)
            self._sem.release(n)
            self._added += n

    def acquire(self) -> None:
        self._sem.acquire()

    def release(self) -> None:
        self._sem.release()

    def stop(self) -> None:
        self._stop.set()


def build_worklist(models: list[str], items: list[dict], runs: int) -> list[tuple]:
    """Round-robin per modello (chiamate iniziali su modelli diversi)."""
    work = []
    for r in range(runs):
        for it in items:
            for m in models:
                work.append((m, it, r))
    # interleave: ordina per (run, item, model) gia' alterna i modelli a ogni step
    return work


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=Path, required=True)
    ap.add_argument("--eval-set", type=Path, required=True)
    ap.add_argument("--prompt", type=Path, required=True)
    ap.add_argument("--task", default="stt_cleanup")
    ap.add_argument("--stage", type=int, default=1)
    ap.add_argument("--judge", default="inline",
                    help="id giudice OpenRouter; 'inline' = giudica Claude fuori dal runner (judge cost 0)")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--tier", default="base", choices=["base", "hard", "all"])
    ap.add_argument("--only", default="", help="csv di id modello da tenere (substring match)")
    ap.add_argument("--init-concurrency", type=int, default=4)
    ap.add_argument("--ramp-step", type=int, default=2)
    ap.add_argument("--ramp-interval", type=float, default=1.5)
    ap.add_argument("--max-concurrency", type=int, default=12)
    ap.add_argument("--limit-usd", type=float, default=1.0)
    ap.add_argument("--smoke", type=int, default=0, help="esegui solo N work-item, verbose, poi stima")
    ap.add_argument("--out", type=Path, default=None, help="dove scrivere il run record json")
    args = ap.parse_args(argv)

    key = resolve_key()
    system_prompt = args.prompt.read_text(encoding="utf-8")
    prices = fetch_price_map()
    log(f"[catalog] prezzi live per {len(prices)} modelli")

    cand = json.loads(args.candidates.read_text(encoding="utf-8"))
    models = [c["id"] for c in cand["candidates"]]
    if args.only:
        subs = [s.strip() for s in args.only.split(",") if s.strip()]
        models = [m for m in models if any(s in m for s in subs)]
    eval_set = json.loads(args.eval_set.read_text(encoding="utf-8"))
    items = [it for it in eval_set["items"]
             if args.tier == "all" or it.get("tier") == args.tier]
    if not models or not items:
        raise SystemExit(f"ERRORE: models={len(models)} items={len(items)} — niente da fare.")

    work = build_worklist(models, items, args.runs)
    if args.smoke:
        work = work[: args.smoke]
    log(f"[plan] {len(models)} modelli x {len(items)} item x {args.runs} run = {len(work)} candidate-call "
        f"(judge: {args.judge}) | tier={args.tier} | smoke={args.smoke or 'off'}")

    cost_lock = threading.Lock()
    state = {"cost": 0.0, "stopped": False}
    results: dict[str, list] = {m: [] for m in models}

    def price(model: str) -> tuple[float, float]:
        return prices.get(model, (0.0, 0.0))

    def run_one(model: str, item: dict, run_idx: int) -> dict:
        with cost_lock:
            if state["stopped"]:
                return {"model": model, "skipped": True}
        rec = {"model": model, "item": item["id"], "run": run_idx,
               "q": 0.0, "det_pass": False, "cost": 0.0, "latency_ms": 0, "error": None}
        try:
            c = call_chat(key, model, cleanup_messages(system_prompt, item),
                          temperature=args.temperature, max_tokens=512)
            pp, pc = price(model)
            ccost = c["prompt_tokens"] * pp + c["completion_tokens"] * pc
            rec["latency_ms"] = c["latency_ms"]
            rec["output"] = c["text"].strip()
            rec["cand_pt"], rec["cand_ct"] = c["prompt_tokens"], c["completion_tokens"]
            passed, reasons = det_gate(item, c["text"])
            rec["det_pass"], rec["det_reasons"] = passed, reasons
            jp, jc = price(args.judge)
            jcost = 0.0
            if passed and args.judge not in ("inline", "none", ""):
                j = call_chat(key, args.judge, judge_messages(item, c["text"]),
                              temperature=0.0, max_tokens=300)
                jcost = j["prompt_tokens"] * jp + j["completion_tokens"] * jc
                rec["judge_pt"], rec["judge_ct"] = j["prompt_tokens"], j["completion_tokens"]
                dims = defence_json(j["text"])
                rec["dims"] = dims
                rec["q"] = quality(dims)
            rec["cost"] = ccost + jcost
        except Exception as exc:  # noqa: BLE001 — robustezza: errore -> score 0
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        with cost_lock:
            state["cost"] += rec["cost"]
            if state["cost"] >= args.limit_usd:
                state["stopped"] = True
        return rec

    # ---- SMOKE: sequenziale, verbose, poi stima full ----
    if args.smoke:
        recs = []
        for (m, it, r) in work:
            log(f"[smoke] {m} / {it['id']} ...")
            rec = run_one(m, it, r)
            recs.append(rec)
            print(json.dumps(rec, ensure_ascii=False, indent=2))
        return _smoke_estimate(recs, models, items, args, prices)

    # ---- FULL: ramp scheduler ----
    limiter = RampLimiter(args.init_concurrency, args.ramp_step, args.ramp_interval, args.max_concurrency)
    done = 0

    def task(m, it, r):
        limiter.acquire()
        try:
            return run_one(m, it, r)
        finally:
            limiter.release()

    with ThreadPoolExecutor(max_workers=args.max_concurrency) as ex:
        futs = [ex.submit(task, m, it, r) for (m, it, r) in work]
        for f in as_completed(futs):
            rec = f.result()
            if rec.get("skipped"):
                continue
            results[rec["model"]].append(rec)
            done += 1
            if done % 5 == 0 or done == len(work):
                log(f"[progress] {done}/{len(work)} | cost=${state['cost']:.4f}")
    limiter.stop()

    if state["stopped"]:
        log(f"[STOP] cost cap ${args.limit_usd} raggiunto a ${state['cost']:.4f} — run parziale.")

    # judge inline -> q resta 0; il valore vero e' negli output grezzi che giudico io.
    raw = {"task": args.task, "stage": args.stage, "runs_per_model": args.runs,
           "tier": args.tier, "temperature": args.temperature,
           "records": {m: results[m] for m in models}}
    view = build_judging_view(results, items)
    summary = aggregate(results, models, prices, args)
    for row in summary["results"]:
        log(f"  {row['model']:44s} det={row['det_pass_rate']:.2f} err={row['errors']:>2} "
            f"${row['cost_usd_per_run']:.6f}/run p50={row['latency_ms_p50']}ms")
    log(f"[cost] totale run = ${state['cost']:.4f}")
    if args.out:
        args.out.write_text(json.dumps(
            {"meta": {k: raw[k] for k in ('task', 'stage', 'runs_per_model', 'tier', 'temperature')},
             "summary": summary["results"], "view": view}, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_path = args.out.with_name(args.out.stem + "_raw.json")
        raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"[out] judging-view -> {args.out}  | raw -> {raw_path}")
    return 0


def _smoke_estimate(work, models, items, args, prices) -> int:
    # ri-esegue 1 chiamata gia' fatta? No: stima da token osservati nello smoke gia' stampato.
    # Qui forniamo solo la formula con prezzi live (la spesa reale dello smoke e' gia' a video).
    full_calls = len(models) * len(items) * args.runs
    jp, jc = prices.get(args.judge, (0.0, 0.0))
    log("\n[estimate] vedi i token reali nello smoke sopra; full Stage-1:")
    log(f"  candidate-call = {len(models)} x {len(items)} x {args.runs} = {full_calls}")
    log(f"  judge-call    <= {full_calls} (solo gli item che passano il gate)")
    log(f"  judge price    = in ${jp*1e6:.3f}/M  out ${jc*1e6:.3f}/M")
    log("  costo full ~= full_calls x (token_cand x prezzo_modello + token_judge x prezzo_judge)")
    return 0


def aggregate(results, models, prices, args) -> dict:
    rows = []
    for m in models:
        rs = results.get(m, [])
        qs = [r["q"] for r in rs]
        costs = [r["cost"] for r in rs if not r.get("error")]
        lats = [r["latency_ms"] for r in rs if r.get("latency_ms")]
        det = [r for r in rs if r.get("det_pass")]
        errs = [r for r in rs if r.get("error")]
        mean = statistics.fmean(qs) if qs else 0.0
        var = statistics.pvariance(qs) if len(qs) > 1 else 0.0
        cost_run = statistics.fmean(costs) if costs else 0.0
        rows.append({
            "model": m,
            "score_mean": round(mean, 3),
            "score_var": round(var, 3),
            "det_pass_rate": round(len(det) / len(rs), 2) if rs else 0.0,
            "errors": len(errs),
            "cost_usd_per_run": round(cost_run, 6),
            "quality_per_usd": round(mean / cost_run, 1) if cost_run > 0 else None,
            "latency_ms_p50": int(statistics.median(lats)) if lats else 0,
            "runs": len(rs),
        })
    rows.sort(key=lambda r: (-(r["quality_per_usd"] or 0), r["score_var"]))
    return {"task": args.task, "stage": args.stage, "judge": args.judge,
            "runs_per_model": args.runs, "tier": args.tier, "results": rows}


def build_judging_view(results: dict, items: list[dict]) -> dict:
    """Per ogni model x item: output UNICI (+conteggio) e det-pass sui run.

    Collassa i 5 run identici cosi' il giudice (Claude) legge ~120 celle, non 600 righe.
    """
    order = [it["id"] for it in items]
    view: dict = {}
    for m, recs in results.items():
        by_item: dict = {}
        for r in recs:
            by_item.setdefault(r["item"], []).append(r)
        cells = {}
        for iid in order:
            rs = by_item.get(iid, [])
            ok = [r for r in rs if not r.get("error")]
            outs: dict = {}
            for r in ok:
                o = r.get("output", "")
                outs[o] = outs.get(o, 0) + 1
            cells[iid] = {
                "runs": len(rs),
                "errors": sum(1 for r in rs if r.get("error")),
                "det_pass": sum(1 for r in rs if r.get("det_pass")),
                "unique_outputs": [{"text": k, "n": v}
                                   for k, v in sorted(outs.items(), key=lambda x: -x[1])],
            }
        view[m] = cells
    return view


if __name__ == "__main__":
    sys.exit(main())
