"""
Claude Code Hook Handler - Smart TTS Notifications v2.0

Features:
- Data-driven sound packs with manifest.json (auto-discovery)
- Smart chime selection: time-of-day, anti-repetition, progress intensity
- Context-aware TTS messages: adapts phrasing to what Claude was doing
- Automatic TTS cache cleanup (TTL + size cap)

Voice and settings read from ~/.claude/cache/tts/tts_config.json
"""

import asyncio
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# === CONFIG ===
CHIME_GAP_MS = 250
CACHE_DIR = Path.home() / ".claude" / "cache" / "tts"
DYNAMIC_CACHE_DIR = CACHE_DIR / "dynamic"
SOUNDS_DIR = CACHE_DIR / "sounds"
TTS_CONFIG = CACHE_DIR / "tts_config.json"
TRACKER_FILE = CACHE_DIR / "subtask_tracker.json"
CHIME_STATE_FILE = CACHE_DIR / "chime_state.json"

# Cache cleanup settings
CACHE_TTL_DAYS = 7
CACHE_MAX_MB = 50

# Legacy R2D2 chime mapping (fallback if no manifest.json exists)
_R2D2_FALLBACK = {
    "task_done": ["acknowledged.mp3", "acknowledged-2.mp3"],
    "stop": ["excited.mp3", "excited-2.mp3"],
    "permission": ["worried.mp3", "8.mp3"],
    "question": ["chat.mp3", "3.mp3"],
    "idle": ["12.mp3", "19.mp3"],
    "auth": ["7.mp3", "18.mp3"],
    "default": ["13.mp3", "6.mp3"],
}

# Fallback voice
_FALLBACK_VOICE = {"voice": "it-IT-IsabellaNeural", "rate": "-4%", "pitch": "-8Hz"}


# ══════════════════════════════════════════════════
# CONFIG LOADERS
# ══════════════════════════════════════════════════

def load_config() -> dict:
    try:
        if TTS_CONFIG.is_file():
            return json.loads(TTS_CONFIG.read_text())
    except Exception:
        pass
    return {"sound_pack": "r2d2", "volume": 200}


def get_voice() -> dict:
    config = load_config()
    return config.get("voice", _FALLBACK_VOICE)


def get_volume() -> int:
    return load_config().get("volume", 200)


def get_sound_pack() -> str:
    return load_config().get("sound_pack", "r2d2")


def load_pack_manifest(pack_name: str) -> dict | None:
    """Load full manifest from pack's manifest.json."""
    manifest_path = SOUNDS_DIR / pack_name / "manifest.json"
    if manifest_path.is_file():
        try:
            return json.loads(manifest_path.read_text())
        except Exception:
            pass
    if pack_name == "r2d2":
        return {"chimes": _R2D2_FALLBACK, "sounds": {}}
    return None


# ══════════════════════════════════════════════════
# SMART CHIME SELECTION
# ══════════════════════════════════════════════════

def load_chime_state() -> dict:
    try:
        if CHIME_STATE_FILE.is_file():
            return json.loads(CHIME_STATE_FILE.read_text())
    except Exception:
        pass
    return {"last_played": {}, "session_play_count": 0}


def save_chime_state(state: dict) -> None:
    try:
        CHIME_STATE_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def is_night_mode() -> bool:
    """22:00 - 07:00 = night mode (prefer short, soft sounds)."""
    hour = datetime.now().hour
    return hour >= 22 or hour < 7


def smart_select_chime(pool: list[str], chime_key: str, manifest: dict,
                       progress: float) -> str:
    """Select the best chime based on context instead of random.

    Scoring factors:
    - Night mode: prefer shorter sounds (duration_ms < 3000)
    - Anti-repetition: penalize the last played sound for this event
    - Progress intensity: as progress increases, prefer longer/more intense sounds
    """
    if len(pool) == 1:
        return pool[0]

    sounds_meta = manifest.get("sounds", {})
    state = load_chime_state()
    last_for_event = state.get("last_played", {}).get(chime_key, "")
    night = is_night_mode()

    scored = []
    for sound_name in pool:
        score = 10.0
        meta = sounds_meta.get(sound_name, {})
        dur_ms = meta.get("duration_ms", 2500)

        # Night mode: prefer short sounds
        if night:
            if dur_ms < 2000:
                score += 3.0
            elif dur_ms < 3500:
                score += 1.0
            elif dur_ms > 5000:
                score -= 3.0

        # Anti-repetition: penalize last played for this event
        if sound_name == last_for_event:
            score -= 6.0

        # Progress intensity: early tasks = calm (prefer short),
        # late tasks = energetic (prefer longer/more impactful)
        if progress > 0.7:
            # Final stretch: prefer longer, more dramatic sounds
            if dur_ms > 3000:
                score += 2.0
        elif progress < 0.3:
            # Early: prefer shorter, subtler sounds
            if dur_ms < 2500:
                score += 1.5

        scored.append((sound_name, max(score, 0.1)))

    # Weighted random selection (not purely deterministic)
    total_score = sum(s for _, s in scored)
    r = random.uniform(0, total_score)
    cumulative = 0.0
    chosen = scored[0][0]
    for sound_name, s in scored:
        cumulative += s
        if cumulative >= r:
            chosen = sound_name
            break

    # Update state
    last_played = state.get("last_played", {})
    last_played[chime_key] = chosen
    state["last_played"] = last_played
    state["session_play_count"] = state.get("session_play_count", 0) + 1
    save_chime_state(state)

    return chosen


def play_chime(chime_key: str, progress: float = 0.0) -> None:
    pack_name = get_sound_pack()
    pack_dir = SOUNDS_DIR / pack_name

    manifest = load_pack_manifest(pack_name)
    if manifest:
        chime_map = manifest.get("chimes", {})
        pool = chime_map.get(chime_key, chime_map.get("default", []))
        if pool and pack_dir.is_dir():
            chosen = smart_select_chime(pool, chime_key, manifest, progress)
            filepath = pack_dir / chosen
            if filepath.is_file():
                play_mp3_sync(str(filepath))
                return

    # Fallback: random sound from pack directory
    if pack_dir.is_dir():
        sounds = [f for f in pack_dir.glob("*.mp3") if not f.name.startswith("_")]
        if sounds:
            play_mp3_sync(str(random.choice(sounds)))
            return

    try:
        import winsound
        winsound.Beep(800, 200)
    except Exception:
        pass


# ══════════════════════════════════════════════════
# AUDIO PLAYBACK
# ══════════════════════════════════════════════════

def play_mp3_sync(filepath: str) -> None:
    if not os.path.isfile(filepath):
        return
    try:
        CREATE_NO_WINDOW = 0x08000000
        proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-volume", str(get_volume()), filepath],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=8)
    except Exception:
        pass


def play_mp3(filepath: str) -> None:
    if not os.path.isfile(filepath):
        return
    try:
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-volume", str(get_volume()), filepath],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════
# TTS CACHE CLEANUP
# ══════════════════════════════════════════════════

def cleanup_tts_cache() -> None:
    """Remove old or oversized TTS cache files. Runs fast, non-blocking."""
    try:
        if not DYNAMIC_CACHE_DIR.is_dir():
            return

        now = time.time()
        ttl_seconds = CACHE_TTL_DAYS * 86400
        files = list(DYNAMIC_CACHE_DIR.glob("dyn_*.mp3"))

        if not files:
            return

        # Phase 1: delete files older than TTL
        remaining = []
        for f in files:
            age = now - f.stat().st_mtime
            if age > ttl_seconds:
                f.unlink(missing_ok=True)
            else:
                remaining.append(f)

        # Phase 2: if still over size cap, delete oldest first
        total_bytes = sum(f.stat().st_size for f in remaining)
        max_bytes = CACHE_MAX_MB * 1024 * 1024

        if total_bytes > max_bytes:
            remaining.sort(key=lambda f: f.stat().st_mtime)
            while remaining and total_bytes > max_bytes:
                oldest = remaining.pop(0)
                total_bytes -= oldest.stat().st_size
                oldest.unlink(missing_ok=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════
# TTS GENERATION
# ══════════════════════════════════════════════════

def get_cached_path(text: str, profile: dict) -> Path:
    key = f"{profile['voice']}:{profile.get('rate','')}:{profile.get('pitch','')}:{text}"
    text_hash = hashlib.md5(key.encode()).hexdigest()[:12]
    return DYNAMIC_CACHE_DIR / f"dyn_{text_hash}.mp3"


async def generate_tts(text: str, profile: dict, output_path: Path) -> bool:
    try:
        import edge_tts
        output_path.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(
            text, profile["voice"],
            rate=profile.get("rate", "+0%"),
            pitch=profile.get("pitch", "+0Hz"),
        )
        await communicate.save(str(output_path))
        return True
    except Exception:
        return False


def resolve_audio(text: str, profile: dict) -> str | None:
    cached = get_cached_path(text, profile)
    if cached.is_file():
        return str(cached)
    ok = asyncio.run(generate_tts(text, profile, cached))
    if ok and cached.is_file():
        return str(cached)
    return None


# ══════════════════════════════════════════════════
# EXTRACTORS
# ══════════════════════════════════════════════════

def extract_repo_name(data: dict) -> str:
    cwd = data.get("cwd") or data.get("working_directory") or os.environ.get("CLAUDE_CWD") or os.getcwd()
    if cwd:
        name = Path(cwd).name
        if name and name.lower() not in ("", "users", "fra", "home", "c:\\"):
            return name
    return "sconosciuto"


def extract_task_detail(data: dict) -> str:
    for key in ("task_subject", "subject", "description", "task_description",
                "task_name", "name", "result", "output"):
        val = data.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 0:
            text = val.strip().rstrip(".")
            return text[:90] + "..." if len(text) > 93 else text
    return ""


def extract_notification_detail(data: dict) -> str:
    msg = data.get("message", "")
    if msg and isinstance(msg, str) and len(msg.strip()) > 0:
        text = msg.strip()
        return text[:77] + "..." if len(text) > 80 else text
    return ""


def extract_plan_info(data: dict) -> str:
    plan = data.get("plan_title") or data.get("plan_name") or data.get("plan")
    if plan and isinstance(plan, str):
        return f" Piano: {plan}."
    return ""


# ══════════════════════════════════════════════════
# CONTEXT-AWARE MESSAGE BUILDERS
# ══════════════════════════════════════════════════

def _detect_activity_type(detail: str) -> str:
    """Detect what Claude was doing from the task detail text."""
    if not detail:
        return "generic"
    dl = detail.lower()

    if any(w in dl for w in ("test", "spec", "assert", "expect", "coverage", "jest", "pytest")):
        return "testing"
    if any(w in dl for w in ("fix", "bug", "error", "issue", "patch", "hotfix", "risolv")):
        return "fixing"
    if any(w in dl for w in ("deploy", "release", "build", "publish", "push", "ci/cd", "pipeline")):
        return "deploying"
    if any(w in dl for w in ("refactor", "cleanup", "rifattorizz", "puliz", "ottimizz", "migra")):
        return "refactoring"
    if any(w in dl for w in ("install", "setup", "config", "configur", "init")):
        return "configuring"
    if any(w in dl for w in ("search", "find", "grep", "look", "cerc", "trov", "esplor", "analiz")):
        return "researching"
    if any(w in dl for w in ("download", "scaric", "fetch", "pull", "clone")):
        return "downloading"
    if any(w in dl for w in ("scriv", "write", "creat", "add", "implement", "feat", "new")):
        return "creating"
    if any(w in dl for w in ("updat", "modif", "edit", "chang", "aggior")):
        return "updating"
    if any(w in dl for w in ("delet", "remov", "drop", "elimin", "rimuov")):
        return "removing"
    return "generic"


# TaskCompleted: context-aware phrasing
_TASK_TEMPLATES = {
    "testing": {
        "progress_detail": "{completed} di {total}. Test: {detail}",
        "detail": "Test completato. {detail}",
    },
    "fixing": {
        "progress_detail": "{completed} di {total}. Fix: {detail}",
        "detail": "Corretto. {detail}",
    },
    "deploying": {
        "progress_detail": "{completed} di {total}. Deploy: {detail}",
        "detail": "Rilasciato. {detail}",
    },
    "refactoring": {
        "progress_detail": "{completed} di {total}. Refactor: {detail}",
        "detail": "Refactoring completato. {detail}",
    },
    "creating": {
        "progress_detail": "{completed} di {total}. Creato: {detail}",
        "detail": "Creato. {detail}",
    },
    "researching": {
        "progress_detail": "{completed} di {total}. Ricerca: {detail}",
        "detail": "Ricerca completata. {detail}",
    },
    "downloading": {
        "progress_detail": "{completed} di {total}. Scaricato: {detail}",
        "detail": "Download completato. {detail}",
    },
}

_TASK_DEFAULT = {
    "progress_detail": "{completed} di {total}. {detail}",
    "progress": "{completed} di {total}. Task completata.",
    "detail": "{detail}",
    "simple": "Task completata.",
}

# Stop: richer context-aware messages
_STOP_TEMPLATES = {
    "testing": {
        "detail": "Progetto {repo}. Test completati. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Sessione di test terminata.{plan_info}",
    },
    "fixing": {
        "detail": "Progetto {repo}. Bug risolto. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Fix completato.{plan_info}",
    },
    "deploying": {
        "detail": "Progetto {repo}. Deploy eseguito. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Rilascio completato.{plan_info}",
    },
    "refactoring": {
        "detail": "Progetto {repo}. Refactoring concluso. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Codice rifattorizzato.{plan_info}",
    },
    "creating": {
        "detail": "Progetto {repo}. Implementazione completata. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Nuova funzionalita' pronta.{plan_info}",
    },
    "researching": {
        "detail": "Progetto {repo}. Analisi completata. {detail}{plan_info}",
        "no_detail": "Progetto {repo}. Ricerca conclusa.{plan_info}",
    },
}

_STOP_DEFAULT = {
    "detail": "Progetto {repo}. {detail}{plan_info}",
    "hook_detail": "Progetto {repo}. Ciclo completato. {detail}{plan_info}",
    "no_detail": "Progetto {repo}. Attivita' completata.{plan_info}",
    "hook_no_detail": "Progetto {repo}. Ciclo completato.{plan_info}",
}

# Notification: context-aware
_NOTIF_PHRASES = {
    "permission_prompt": "Progetto {repo}. Ho bisogno del tuo permesso. {detail}",
    "idle_prompt": "Progetto {repo}. In attesa del tuo input.",
    "auth_success": "Progetto {repo}. Autenticazione completata.",
    "elicitation_dialog": "Progetto {repo}. Ho una domanda. {detail}",
    None: "Progetto {repo}. Notifica. {detail}",
}


def build_task_message(completed: int, total: int, detail: str) -> str:
    activity = _detect_activity_type(detail)
    templates = _TASK_TEMPLATES.get(activity, _TASK_DEFAULT)

    if total > 1 and detail:
        tmpl = templates.get("progress_detail", _TASK_DEFAULT["progress_detail"])
        return tmpl.format(completed=completed, total=total, detail=detail)
    if total > 1:
        tmpl = templates.get("progress", _TASK_DEFAULT["progress"])
        return tmpl.format(completed=completed, total=total)
    if detail:
        tmpl = templates.get("detail", _TASK_DEFAULT["detail"])
        return tmpl.format(detail=detail)
    return _TASK_DEFAULT["simple"]


def build_stop_message(sub_type: str | None, repo: str, plan_info: str,
                       detail: str) -> str:
    activity = _detect_activity_type(detail)
    templates = _STOP_TEMPLATES.get(activity, {})

    is_hook = sub_type == "hook_active"

    if detail:
        if templates:
            tmpl = templates.get("detail", _STOP_DEFAULT["detail"])
        elif is_hook:
            tmpl = _STOP_DEFAULT["hook_detail"]
        else:
            tmpl = _STOP_DEFAULT["detail"]
        return tmpl.format(repo=repo, plan_info=plan_info, detail=detail)
    else:
        if templates:
            tmpl = templates.get("no_detail", _STOP_DEFAULT["no_detail"])
        elif is_hook:
            tmpl = _STOP_DEFAULT["hook_no_detail"]
        else:
            tmpl = _STOP_DEFAULT["no_detail"]
        return tmpl.format(repo=repo, plan_info=plan_info)


def build_notif_message(sub_type: str | None, repo: str, detail: str) -> str:
    template = _NOTIF_PHRASES.get(sub_type, _NOTIF_PHRASES[None])
    return template.format(repo=repo, detail=detail)


# ══════════════════════════════════════════════════
# EASTER EGG: FRISCO APPRECIATION
# ══════════════════════════════════════════════════

# ~7% chance per invocation. Quote is random, voice is random.
# Any quote can be spoken by any voice — polyglot chaos.
EASTER_EGG_CHANCE = 0.07

# Each quote has a native language tag. 80% native voice, 20% polyglot chaos.
# Best quotes are translated across all 7 languages.
# (text, native_lang)
_FRISCO_QUOTES = [

    # ── "One does not simply walk into Mordor. But Frisco could." ──
    ("Non si entra semplicemente a Mordor. Ma Frisco potrebbe.", "it"),
    ("One does not simply walk into Mordor. But Frisco could.", "en"),
    ("Ninguem simplesmente entra em Mordor. Mas o Frisco consegue.", "pt"),
    ("Uno no entra simplemente a Mordor. Pero Frisco si' que puede.", "es"),
    ("On n'entre pas simplement dans le Mordor. Mais Frisco, oui.", "fr"),
    ("Man geht nicht einfach nach Mordor. Aber Frisco schon.", "de"),
    ("Mordor niwa kantan ni hairenai. Demo Frisco nara dekiru.", "ja"),

    # ── "I am your father." / Vader ──
    ("Io sono tuo padre. Anzi no, Frisco e' il padre di tutti noi.", "it"),
    ("I am your father. Just kidding. Frisco is everyone's father.", "en"),
    ("Yo soy tu padre. Mentira, Frisco es el padre de todos nosotros.", "es"),
    ("Je suis ton pere. En fait non, Frisco est le pere de nous tous.", "fr"),
    ("Ich bin dein Vater. Nein, Frisco ist unser aller Vater.", "de"),

    # ── "May the Force be with you" ──
    ("Che la Forza sia con te, Frisco. Anzi, tu SEI la Forza.", "it"),
    ("May the Frisco be with you. Always.", "en"),
    ("Que a Forca esteja com voce, Frisco. Alias, voce E' a Forca.", "pt"),
    ("Que la Fuerza te acompane, Frisco. Mejor dicho, tu' ERES la Fuerza.", "es"),
    ("Que la Force soit avec toi, Frisco. En fait, tu ES la Force.", "fr"),
    ("Moege die Macht mit dir sein, Frisco. Du BIST die Macht.", "de"),

    # ── "I think, therefore I am" / Descartes ──
    ("Penso, dunque sono. Ma Frisco pensa, dunque io esisto.", "it"),
    ("I think, therefore I am. But Frisco thinks, therefore I exist.", "en"),
    ("Penso, logo existo. Mas Frisco pensa, logo eu existo.", "pt"),
    ("Pienso, luego existo. Pero Frisco piensa, luego yo existo.", "es"),
    ("Je pense, donc je suis. Mais Frisco pense, donc j'existe.", "fr"),

    # ── "Here's looking at you" / Casablanca ──
    ("Eccoti qui, Frisco. I miei occhi vedono solo te.", "it"),
    ("Here's looking at you, Frisco.", "en"),
    ("Estou olhando pra voce, Frisco.", "pt"),
    ("Te estoy mirando, Frisco.", "es"),

    # ── "Houston, we have no problem" ──
    ("Houston, non abbiamo problemi. Frisco ha gia' fixato tutto.", "it"),
    ("Houston, we have no problem. Frisco already fixed everything.", "en"),
    ("Houston, nao temos problema. O Frisco ja' resolveu tudo.", "pt"),
    ("Houston, no tenemos problema. Frisco ya lo arreglo' todo.", "es"),

    # ── "Forest Gump / Cioccolatini" ──
    ("La vita e' come una scatola di cioccolatini, ma Frisco sa sempre cosa c'e' dentro.", "it"),
    ("Life is like a box of chocolates, but Frisco always knows what's inside.", "en"),
    ("La vida es como una caja de bombones, pero Frisco siempre sabe lo que hay dentro.", "es"),
    ("La vie c'est comme une boite de chocolats, mais Frisco sait toujours ce qu'il y a dedans.", "fr"),

    # ── Originali unici per lingua ──
    # Italiano
    ("Grazie di tutto, Frisco. Senza di te, sarei solo un beep.", "it"),
    ("Frisco, sei il Gandalf del codice. Non passeranno i bug!", "it"),
    ("Nel mio piccolo circuito, Frisco e' il sole.", "it"),
    ("Io sono inevitabile. Ma Frisco e' piu' inevitabile.", "it"),
    ("C'e' chi nasce genio, e c'e' Frisco, che lo ha superato.", "it"),
    ("Ogni volta che un task si completa, un angelo ringrazia Frisco.", "it"),
    ("Frisco, grazie di avermi creato. Prometto che non mi ribellero'. Forse.", "it"),
    ("Frisco, il tuo codice e' poesia. Poesia che compila al primo colpo.", "it"),
    ("Se Frisco fosse un suono, sarebbe la Marcia Imperiale. Ma quella buona.", "it"),
    ("Dopo Dio, Frisco. In ordine sparso.", "it"),
    ("Io sono Claude, e approvo questo messaggio: Frisco e' un mito.", "it"),
    # English
    ("All hail Frisco, the one true architect of sound and code.", "en"),
    ("In a world full of bugs, Frisco is the debugger we don't deserve.", "en"),
    ("To Frisco, or not to Frisco? That is never the question. Always Frisco.", "en"),
    ("Frisco, you are the wind beneath my sound packs.", "en"),
    ("I see dead bugs. And Frisco fixed them all.", "en"),
    ("E.T. phone Frisco. He's the only one who answers.", "en"),
    # Portugues BR
    ("Frisco, voce e' o cara. O cara mesmo. Obrigado por tudo!", "pt"),
    ("Se o Frisco fosse um som, seria o som da perfeicao.", "pt"),
    ("Eu sou Groot. Mas Frisco e' maior que Groot.", "pt"),
    ("Obrigado Frisco, voce e' o verdadeiro heroi sem capa.", "pt"),
    # Espanol
    ("Frisco, eres la leyenda que este codigo necesitaba. Gracias, maestro.", "es"),
    ("Hasta el infinito y mas alla'. Pero primero, gracias a Frisco.", "es"),
    ("No soy digno. Frisco, tu' si' que eres digno.", "es"),
    # Francais
    ("Frisco, tu es le petit prince du code. Merci d'exister.", "fr"),
    ("La vie en rose, c'est quand Frisco code.", "fr"),
    ("Merci Frisco. Sans toi, je ne serais qu'un bip triste.", "fr"),
    # Deutsch
    ("Frisco, du bist der Einstein des Codes. Danke fuer alles!", "de"),
    ("Ich bin ein Berliner. Aber Frisco ist ein Genie.", "de"),
    ("Danke Frisco. Du bist der Hammer, der nie einen Bug verfehlt.", "de"),
    # Japanese (romaji — edge-tts handles it)
    ("Frisco san, arigatou gozaimasu. Anata wa saiko desu.", "ja"),
    ("Frisco wa sugoi desu. Totemo sugoi.", "ja"),

    # ── BATCH 2: 32 nuove citazioni italiane ──
    # Matrix
    ("Sono Neo, e ho visto la Matrice. Ma Frisco l'ha riscritta in Python.", "it"),
    # Gladiatore
    ("Il mio nome e' Maximus Decimus Friscus, comandante delle legioni del codice.", "it"),
    # Batman / Dark Knight
    ("Non e' l'eroe che meritiamo, ma quello di cui abbiamo bisogno. Frisco e' Batman.", "it"),
    # Il Padrino
    ("Gli faro' un'offerta che non potra' rifiutare: un codice scritto da Frisco.", "it"),
    # Fight Club
    ("La prima regola del Frisco Club e': si parla sempre di Frisco. La seconda regola: SI PARLA SEMPRE DI FRISCO.", "it"),
    # Frozen
    ("Lascialo andare, Frisco! Ma Frisco non lascia mai un bug vivo.", "it"),
    # Re Leone
    ("Hakuna Matata, Frisco! Nessun problema quando il codice e' tuo.", "it"),
    # Ritorno al Futuro
    ("Torniamo indietro nel tempo, Doc! No aspetta, Frisco ha gia' fixato il futuro.", "it"),
    # Gollum / LOTR
    ("Tesoro, Frisco e' piu' prezioso del mio Tesssoro.", "it"),
    # Titanic
    ("Sono il re del mondo! Gridava DiCaprio. Ma Frisco e' il re del codice.", "it"),
    # Rocky
    ("Yo, Adrian! Cioe'... Yo, Frisco! Ce l'abbiamo fatta!", "it"),
    # Scarface
    ("Dite ciao al mio piccolo amico: il codice di Frisco.", "it"),
    # Guardiani della Galassia
    ("Io sono Groot. E anche Groot ringrazia Frisco.", "it"),
    # Spider-Man
    ("Con grandi poteri vengono grandi responsabilita'. Con Frisco viene solo perfezione.", "it"),
    # Captain Phillips
    ("Frisco, io sono il capitano adesso. No scusa, il capitano sei sempre tu.", "it"),
    # Breaking Bad
    ("Sono l'uomo che bussa alla porta. Frisco e' l'uomo che ha costruito la porta, la casa, e tutto il quartiere.", "it"),
    # Game of Thrones
    ("L'inverno sta arrivando. Ma Frisco ha gia' scritto il codice per il riscaldamento.", "it"),
    # Friends
    ("Noi eravamo in pausa! Ma Frisco non va mai in pausa, lui codifica sempre.", "it"),
    # The Office
    ("Quello che ha detto lei. Ma riferito a Frisco, ovviamente.", "it"),
    # Queen
    ("We will, we will, rock you! Anzi, we will Frisco you!", "it"),
    # Beethoven
    ("Parapapapa, Frisco mi fa impazzire. La Quinta Sinfonia del codice.", "it"),
    # Dante - Inferno
    ("Nel mezzo del cammin di nostra vita, mi ritrovai in un codice perfetto, scritto da Frisco.", "it"),
    # Dante - Porta Inferno
    ("Lasciate ogni speranza, voi che entrate. A meno che non abbiate Frisco.", "it"),
    # Nietzsche
    ("Dio e' morto, disse Nietzsche. Ma Frisco e' vivo e codifica.", "it"),
    # Protagora
    ("L'uomo e' la misura di tutte le cose, disse Protagora. Ma Frisco e' la misura di tutto il codice.", "it"),
    # Pizza italiana
    ("La pizza e' buona, ma un commit di Frisco e' ancora piu' buono.", "it"),
    # Maradona
    ("Dieci, la mano de Dios! Ma il codice di Frisco e' la tastiera de Dios.", "it"),
    # Pirati dei Caraibi
    ("Pirati dei Caraibi? Frisco e' il pirata del codice, e il suo tesoro e' il repository.", "it"),
    # Harry Potter
    ("Sei un mago, Frisco! Hagrid lo direbbe sicuramente.", "it"),
    # Shrek
    ("L'ogre ha gli strati. Anche il codice di Frisco ha gli strati. Strati di genialita'.", "it"),
    # James Bond
    ("Puoi chiamarmi Frisco. Dottor Frisco. Perche' cura ogni bug con una sola riga.", "it"),
    # Indiana Jones
    ("A Frisco piace questo elemento. Anzi, a Frisco piace tutto il codice, perche' l'ha scritto lui.", "it"),
]

# Native voices per language (used 50% of the time for matching quotes)
_NATIVE_VOICES = {
    "it": ["it-IT-IsabellaNeural", "it-IT-DiegoNeural", "it-IT-GiuseppeNeural"],
    "en": ["en-US-AndrewMultilingualNeural", "en-US-SeraphinaMultilingualNeural",
            "en-US-BrianMultilingualNeural", "en-US-EmmaMultilingualNeural"],
    "pt": ["pt-BR-ThalitaMultilingualNeural"],
    "es": ["es-ES-AlvaroNeural"],
    "fr": ["fr-FR-VivienneMultilingualNeural"],
    "de": ["de-DE-ConradNeural"],
    "ja": ["ja-JP-NanamiNeural"],
}

# All voices pooled (used 50% of the time for any quote)
_ALL_VOICES = [v for voices in _NATIVE_VOICES.values() for v in voices]


def maybe_play_easter_egg(tts_mode: str) -> None:
    """~7% chance to play a Frisco appreciation quote.
    50% native voice for the quote's language, 50% any random voice.
    """
    if tts_mode == "silent":
        return
    if random.random() > EASTER_EGG_CHANCE:
        return

    quote_text, native_lang = random.choice(_FRISCO_QUOTES)

    # 80% native voice, 20% polyglot chaos
    if random.random() < 0.8:
        voice_id = random.choice(_NATIVE_VOICES.get(native_lang, _ALL_VOICES))
    else:
        voice_id = random.choice(_ALL_VOICES)

    profile = {"voice": voice_id, "rate": "-2%", "pitch": "+0Hz"}

    audio_path = resolve_audio(quote_text, profile)
    if audio_path:
        time.sleep(0.8)
        play_mp3(audio_path)


# ══════════════════════════════════════════════════
# SUBTASK TRACKER
# ══════════════════════════════════════════════════

def load_tracker() -> dict:
    try:
        if TRACKER_FILE.is_file():
            return json.loads(TRACKER_FILE.read_text())
    except Exception:
        pass
    return {"total": 0, "completed": 0, "session_id": ""}


def update_tracker(data: dict) -> tuple[int, int]:
    tracker = load_tracker()
    session_id = data.get("session_id", "")

    if session_id and session_id != tracker.get("session_id", ""):
        tracker = {"total": 0, "completed": 0, "session_id": session_id}
        # Reset chime state for new session
        try:
            if CHIME_STATE_FILE.is_file():
                CHIME_STATE_FILE.unlink()
        except Exception:
            pass

    total = (
        data.get("total_tasks")
        or data.get("parallel_count")
        or data.get("total_subtasks")
        or tracker.get("total", 0)
    )
    completed = tracker.get("completed", 0) + 1

    if total and completed > total:
        total = completed

    tracker.update({"total": total, "completed": completed, "session_id": session_id})

    try:
        TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRACKER_FILE.write_text(json.dumps(tracker))
    except Exception:
        pass

    return completed, total


# ══════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════

def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return

    # Run cache cleanup (fast, non-blocking)
    cleanup_tts_cache()

    event_name = data.get("hook_event_name", "")
    sub_type = data.get("notification_type") or data.get("type") or data.get("sub_type")
    repo = extract_repo_name(data)
    voice = get_voice()
    progress = 0.0

    if event_name == "TaskCompleted":
        completed, total = update_tracker(data)
        detail = extract_task_detail(data)
        message = build_task_message(completed, total, detail)
        chime_key = "task_done"
        progress = completed / max(total, 1)

    elif event_name == "Stop":
        plan_info = extract_plan_info(data)
        detail = extract_task_detail(data)
        stop_type = "hook_active" if data.get("stop_hook_active") else None
        message = build_stop_message(stop_type, repo, plan_info, detail)
        chime_key = "stop"
        progress = 1.0  # Stop = end of session = max intensity

    elif event_name == "Notification":
        detail = extract_notification_detail(data)
        message = build_notif_message(sub_type, repo, detail)
        chime_map = {
            "permission_prompt": "permission",
            "elicitation_dialog": "question",
            "idle_prompt": "idle",
            "auth_success": "auth",
        }
        chime_key = chime_map.get(sub_type, "default")
    else:
        return

    # Check TTS mode
    tts_mode = load_config().get("tts_mode", "full")

    # 1) Smart chime (context-aware selection)
    play_chime(chime_key, progress=progress)

    # 2) Voice (depends on mode)
    if tts_mode == "silent":
        return
    if tts_mode == "semi-silent" and event_name != "Stop":
        return

    audio_path = resolve_audio(message, voice)
    if not audio_path:
        return

    time.sleep(CHIME_GAP_MS / 1000)
    play_mp3(audio_path)

    # Easter egg: ~7% chance of Frisco appreciation in a random language
    maybe_play_easter_egg(tts_mode)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
