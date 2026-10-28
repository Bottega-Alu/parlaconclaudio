"""
Claude Code + Codex Hook Handler - Smart TTS Notifications v2.1

Features:
- Data-driven sound packs with manifest.json (auto-discovery)
- Smart chime selection: time-of-day, anti-repetition, progress intensity
- Context-aware TTS messages: adapts phrasing to what Claude was doing
- Automatic TTS cache cleanup (TTL + size cap)

Voice and settings read from ~/.claude/cache/tts/tts_config.json

Input (decision tree in read_event):
  1. a JSON object as a command-line argument -> legacy Codex `notify` program
     ({"type": "agent-turn-complete", "cwd": ..., ...})
  2. JSON on stdin + `--source codex`         -> Codex hooks engine (Stop,
     PermissionRequest), translated to the Claude-shaped event below
  3. JSON on stdin                            -> Claude Code hook (Stop,
     Notification, TaskCompleted, PreToolUse with matcher AskUserQuestion)
Nothing is ever written to stdout: a Codex Stop hook must print JSON or nothing,
and a PreToolUse hook's stdout JSON could be read as a permission decision.
The process always exits 0 (never blocks a tool).
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
NOTIFY_BURST_FILE = CACHE_DIR / "notify_burst.txt"
ERROR_LOG_FILE = CACHE_DIR / "notify-tts.log"
ERROR_BEEP_FILE = CACHE_DIR / "error_beep.txt"

# Synthesis-failure diagnostics: the log is truncated to its newest half
# once it passes the cap; the audible beep is rate-limited so a persistent
# outage (e.g. edge-tts 403) does not beep on every hook.
ERROR_LOG_MAX_BYTES = 256 * 1024
ERROR_LOG_SNIPPET_CHARS = 80
ERROR_BEEP_MIN_INTERVAL_S = 30
ERROR_BEEP_TONES = ((330, 120), (220, 120), (150, 250))  # (Hz, ms) descending

# Burst suppression: when several hooks fire in quick succession only
# the last invocation actually plays audio. Each invocation writes its
# UUID, sleeps a short wait, and proceeds only if its UUID is still the
# latest on disk.
BURST_WAIT_MS = 300

# Live playback: the detached player re-reads the sliders every block, so a
# volume change reaches the voice already speaking. ffplay is the fallback.
MAX_VOLUME = 125
DEFAULT_VOLUME = 100
LIVE_RATE = 48000
LIVE_CHANNELS = 2
LIVE_BLOCK_FRAMES = 2048  # ~43 ms at 48 kHz

# Subagent (TaskCompleted) gets a longer window so a cluster of
# subagents finishing within a couple of seconds collapses to a single
# voice line instead of overlapping into cacophony.
SUBAGENT_BURST_WAIT_MS = 1500

# "Claudio ha bisogno di una tua scelta" (PreToolUse/AskUserQuestion) vs the Notification
# Claude Code may send for the same pending prompt about six seconds later (its
# "needs your input" gate): the burst file above is last-wins over 300 ms and
# cannot span that gap, so the question leaves a marker {session_id, ts} and a
# Notification of the SAME session within QUESTION_DEDUP_S stays silent.
# auth_success is never suppressed (it is not about the question).
QUESTION_MARK_FILE = CACHE_DIR / "notify_question.json"
QUESTION_DEDUP_S = 20
ASK_USER_QUESTION = "AskUserQuestion"
QUESTION_PHRASE = "ha bisogno di una tua scelta."
SUBAGENT_PHRASE = "Subagente terminato."

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
    return {"sound_pack": "r2d2", "volume": DEFAULT_VOLUME}


def _level(config: dict, voice: bool) -> int:
    """Slider level in 0..MAX_VOLUME; voices follow the sound volume until set on their own.
    Older configs may hold up to 300 from the previous range: clamped here."""
    level = config.get("volume", DEFAULT_VOLUME)
    if voice:
        level = config.get("voice_volume", level)
    try:
        return max(0, min(MAX_VOLUME, int(level)))
    except (TypeError, ValueError):
        return DEFAULT_VOLUME


def get_voice() -> dict:
    config = load_config()
    return config.get("voice", _FALLBACK_VOICE)


def get_volume() -> int:
    return _level(load_config(), voice=False)


def get_voice_volume() -> int:
    """Spoken announcements have their own level; older configs fall back to the sound volume."""
    return _level(load_config(), voice=True)


def get_muted() -> bool:
    return load_config().get("muted", False)


def get_sound_pack() -> str:
    """Return the configured sound pack name as stored in config.

    May return the literal "random" — callers that want a real pack
    must resolve that themselves (see play_chime cross-pack logic).
    """
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


def _pick_cross_pack_chime(chime_key: str) -> Path | None:
    """Pick a single chime sample uniformly across all installed packs.

    Used when sound_pack is set to "random". Aggregates the chime_key
    pool across every pack that has a manifest, then picks one sample
    file at random. Returns None if no candidates were found anywhere.
    """
    if not SOUNDS_DIR.is_dir():
        return None
    all_candidates: list[Path] = []
    for pack_dir in SOUNDS_DIR.iterdir():
        if not pack_dir.is_dir() or pack_dir.name.startswith("_"):
            continue
        manifest = load_pack_manifest(pack_dir.name)
        if not manifest:
            continue
        chime_map = manifest.get("chimes", {})
        pool = chime_map.get(chime_key, chime_map.get("default", []))
        for sound in pool:
            fpath = pack_dir / sound
            if fpath.is_file():
                all_candidates.append(fpath)
    if not all_candidates:
        return None
    return random.choice(all_candidates)


def play_chime(chime_key: str, progress: float = 0.0) -> None:
    if get_muted() or get_volume() <= 0:
        return
    pack_name = get_sound_pack()

    # "random" means: pick one chime uniformly across every installed
    # pack, not "pick one pack and use it for the whole event".
    if pack_name == "random":
        chosen_path = _pick_cross_pack_chime(chime_key)
        if chosen_path is not None:
            play_mp3_sync(str(chosen_path))
            return
        # No candidates anywhere — fall back to r2d2 as a last resort
        pack_name = "r2d2"

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

    # Fallback: random MP3 from the chosen pack directory
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
    if not os.path.isfile(filepath) or get_muted() or get_volume() <= 0:
        return
    try:
        CREATE_NO_WINDOW = 0x08000000
        proc = subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-af", f"volume={get_volume()}/100", filepath],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=8)
    except Exception:
        pass


def _play_ffplay(filepath: str, volume: int) -> None:
    """Fixed level for the whole clip: the fallback when live playback is unavailable."""
    try:
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-af", f"volume={volume}/100", filepath],
            creationflags=_CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def play_mp3(filepath: str, voice: bool = False) -> None:
    """Start playback and return at once. Decision tree:
    1. detached player (--play): follows the sliders while the clip plays
    2. ffplay at the level read now, if the player cannot be spawned"""
    volume = get_voice_volume() if voice else get_volume()
    if not os.path.isfile(filepath) or get_muted() or volume <= 0:
        return
    if not _spawn_hidden([PLAY_FLAG, "voice" if voice else "sound", filepath]):
        _play_ffplay(filepath, volume)


class _LiveLevel:
    """Slider gain, re-read from tts_config.json only when the file changes."""

    def __init__(self, voice: bool):
        self.voice = voice
        self.mtime = None
        self.gain = None

    def read(self) -> float:
        try:
            mtime = TTS_CONFIG.stat().st_mtime_ns
        except OSError:
            mtime = None
        if self.gain is None or mtime != self.mtime:
            self.mtime = mtime
            config = load_config()
            self.gain = 0.0 if config.get("muted") else _level(config, self.voice) / 100
        return self.gain


def play_live(filepath: str, voice: bool) -> bool:
    """Decode with ffmpeg and play through PyAudio, re-reading the level every
    block (~43 ms) so a slider change reaches the clip already playing.
    False = live playback unavailable (numpy, PyAudio, ffmpeg or the output
    device missing) before any audio was played: the caller uses ffplay."""
    try:
        import numpy as np
        import pyaudio
    except ImportError:
        return False
    try:
        decoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "quiet", "-i", filepath,
             "-f", "s16le", "-ac", str(LIVE_CHANNELS), "-ar", str(LIVE_RATE), "-"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
    except OSError:
        return False
    audio = stream = None
    try:
        try:
            audio = pyaudio.PyAudio()
            stream = audio.open(format=pyaudio.paInt16, channels=LIVE_CHANNELS,
                                rate=LIVE_RATE, output=True)
        except Exception:
            return False
        level = _LiveLevel(voice)
        gain = level.read()
        frame_bytes = 2 * LIVE_CHANNELS
        while True:
            data = decoder.stdout.read(LIVE_BLOCK_FRAMES * frame_bytes)
            data = data[:len(data) - len(data) % frame_bytes]
            if not data:
                break
            target = level.read()
            samples = np.frombuffer(data, dtype=np.int16).reshape(-1, LIVE_CHANNELS)
            # Ramp across the block: a jump in gain would click.
            ramp = np.linspace(gain, target, len(samples), dtype=np.float32)[:, None]
            out = np.clip(samples * ramp, -32768, 32767).astype(np.int16)
            stream.write(out.tobytes())
            gain = target
        return True
    finally:
        if decoder.poll() is None:
            decoder.kill()
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if audio is not None:
            audio.terminate()


def play_command(args: list[str]) -> None:
    """Entry of the detached player: --play voice|sound <file>."""
    if len(args) != 2:
        return
    voice = args[0] == "voice"
    if not play_live(args[1], voice):
        volume = get_voice_volume() if voice else get_volume()
        if not get_muted() and volume > 0:
            _play_ffplay(args[1], volume)


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


# Minimum size for a valid TTS MP3 — anything smaller is treated as a
# silent failure (edge-tts can save zero-byte files on transient errors).
_MIN_TTS_MP3_BYTES = 512


def log_tts_error(kind: str, text: str, profile: dict, exc: BaseException | None = None) -> None:
    """Append one structured line to ERROR_LOG_FILE. Best-effort, never raises."""
    try:
        ERROR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        snippet = " ".join(text.split())[:ERROR_LOG_SNIPPET_CHARS]
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "exc_type": type(exc).__name__ if exc else None,
            "message": str(exc) if exc else None,
            "voice": profile.get("voice"),
            "text": snippet,
        }
        with ERROR_LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if ERROR_LOG_FILE.stat().st_size > ERROR_LOG_MAX_BYTES:
            # Keep the newest half, aligned to a line boundary.
            tail = ERROR_LOG_FILE.read_bytes()[-ERROR_LOG_MAX_BYTES // 2:]
            tail = tail.split(b"\n", 1)[-1]
            ERROR_LOG_FILE.write_bytes(tail)
    except Exception:
        pass


def play_error_beep() -> None:
    """Descending low tones, unlike the soundpack MP3s. Rate-limited, best-effort."""
    try:
        if get_muted() or get_volume() <= 0:
            return
        if (ERROR_BEEP_FILE.is_file()
                and time.time() - ERROR_BEEP_FILE.stat().st_mtime < ERROR_BEEP_MIN_INTERVAL_S):
            return
        ERROR_BEEP_FILE.parent.mkdir(parents=True, exist_ok=True)
        ERROR_BEEP_FILE.write_text(str(time.time()), encoding="utf-8")
        import winsound
        for freq, dur in ERROR_BEEP_TONES:
            winsound.Beep(freq, dur)
    except Exception:
        pass


def report_tts_failure(kind: str, text: str, profile: dict, exc: BaseException | None = None) -> None:
    log_tts_error(kind, text, profile, exc)
    play_error_beep()


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
        # Edge-tts may exit cleanly while writing 0 bytes on rate limits or
        # transient network errors. Treat that as failure and clean up so
        # the next call retries instead of caching a silent file forever.
        if output_path.is_file() and output_path.stat().st_size < _MIN_TTS_MP3_BYTES:
            output_path.unlink(missing_ok=True)
            report_tts_failure("empty_output", text, profile)
            return False
        return True
    except Exception as exc:
        # If the file was partially written, drop it so we don't cache garbage.
        try:
            if output_path.is_file() and output_path.stat().st_size < _MIN_TTS_MP3_BYTES:
                output_path.unlink(missing_ok=True)
        except Exception:
            pass
        report_tts_failure("synthesis_error", text, profile, exc)
        return False


def resolve_audio(text: str, profile: dict) -> str | None:
    cached = get_cached_path(text, profile)
    # Treat empty/tiny cached files as a miss — they cause silent playback.
    if cached.is_file() and cached.stat().st_size >= _MIN_TTS_MP3_BYTES:
        return str(cached)
    if cached.is_file():
        cached.unlink(missing_ok=True)  # remove the bad cached file
    ok = asyncio.run(generate_tts(text, profile, cached))
    if ok and cached.is_file() and cached.stat().st_size >= _MIN_TTS_MP3_BYTES:
        return str(cached)
    return None


# ══════════════════════════════════════════════════
# EXTRACTORS
# ══════════════════════════════════════════════════

GIT_EXE = "git"
GIT_TIMEOUT_S = 1.5


def _git(cwd: str, *args: str) -> str | None:
    """stdout of `git -C <cwd> <args>`, or None on any failure (no git, not a
    repo, timeout, dubious ownership...). Never raises: the hook must not fail."""
    try:
        out = subprocess.run(
            [GIT_EXE, "-C", cwd, *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
            stdin=subprocess.DEVNULL, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    value = out.stdout.strip() if out.returncode == 0 else ""
    return value or None


def git_main_repo_root(cwd: str) -> Path | None:
    """Root of the MAIN repository, also from a subfolder or a linked worktree.

    --git-common-dir is the main repo's .git even inside a worktree, so its
    parent is the main checkout. When the common dir is not a ".git" folder
    (bare repo, submodule under .git/modules/) fall back to --show-toplevel.
    """
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common and Path(common).name.lower() == ".git":
        return Path(common).parent
    top = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(top) if top else None


UNKNOWN_REPO = "sconosciuto"


def extract_repo_name(data: dict) -> str:
    cwd = data.get("cwd") or data.get("working_directory") or os.environ.get("CLAUDE_CWD") or os.getcwd()
    if cwd:
        root = git_main_repo_root(cwd) if os.path.isdir(cwd) else None
        name = (root or Path(cwd)).name
        if name and name.lower() not in ("", "users", "fra", "home", "c:\\"):
            return name
    return UNKNOWN_REPO


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

# Who is speaking. One sentence shape for every announcement, both agents:
#   "Progetto <repo>, <who> <phrase>[ <extra>]"   e.g. "Progetto alpha, Claudio ha finito."
# Unknown project (or one named like an agent) -> just "<who> <phrase>".
SPEAKER_CLAUDE = "Claudio"   # Claude Code (name chosen by Frisco)
SPEAKER_CODEX = "Codex"
_NOT_A_PROJECT = {UNKNOWN_REPO, "claudio", "claude", "codex"}


def _is_project_name(repo: str) -> bool:
    name = (repo or "").strip().lower().lstrip(".")
    return bool(name) and name not in _NOT_A_PROJECT


def announce(repo: str, who: str, phrase: str, extra: str = "") -> str:
    lead = f"Progetto {repo}, {who}" if _is_project_name(repo) else who
    text = f"{lead} {phrase}"
    extra = (extra or "").strip()
    return f"{text} {extra}" if extra else text


# Stop: "<who> ha finito." + (activity label + task detail, when the payload has one)
_STOP_ACTIVITY_LABEL = {
    "testing": "Test completati.",
    "fixing": "Bug risolto.",
    "deploying": "Deploy eseguito.",
    "refactoring": "Refactoring concluso.",
    "creating": "Implementazione completata.",
    "researching": "Analisi completata.",
}
STOP_PHRASE = "ha finito."
STOP_HOOK_PHRASE = "ha finito un ciclo."

# Notification: (phrase, keeps the detail?)
_NOTIF_PHRASES = {
    "permission_prompt": ("ha bisogno del tuo permesso.", True),
    "idle_prompt": ("ha bisogno del tuo aiuto.", False),
    "elicitation_dialog": ("ha bisogno di una tua scelta.", True),
    "auth_success": ("ha completato l'autenticazione.", False),
    None: ("ti manda una notifica.", True),
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
                       detail: str, who: str = SPEAKER_CLAUDE) -> str:
    phrase = STOP_HOOK_PHRASE if sub_type == "hook_active" else STOP_PHRASE
    extra = ""
    if detail:
        label = _STOP_ACTIVITY_LABEL.get(_detect_activity_type(detail), "")
        extra = f"{label} {detail}".strip()
    return announce(repo, who, phrase, extra) + (plan_info or "")


def build_notif_message(sub_type: str | None, repo: str, detail: str,
                        who: str = SPEAKER_CLAUDE) -> str:
    phrase, keeps_detail = _NOTIF_PHRASES.get(sub_type, _NOTIF_PHRASES[None])
    return announce(repo, who, phrase, detail if keeps_detail else "")


# ══════════════════════════════════════════════════
# EASTER EGG: FRISCO APPRECIATION
# ══════════════════════════════════════════════════

# ~14% chance per invocation. Quote is random, voice is random.
# Any quote can be spoken by any voice — polyglot chaos.
EASTER_EGG_CHANCE = 0.14

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
        play_mp3(audio_path, voice=True)


def play_subagent_voice() -> None:
    """Speak the fixed SUBAGENT_PHRASE with a randomly-picked voice from
    the multilingual pool.

    Used for TaskCompleted (subagent termination) to keep the audio cue
    short and varied without the full chime + contextual TTS +
    easter-egg fanfara that would overlap when several subagents finish
    close together.
    """
    voice_id = random.choice(_ALL_VOICES)
    profile = {"voice": voice_id, "rate": "+0%", "pitch": "+0Hz"}
    audio_path = resolve_audio(SUBAGENT_PHRASE, profile)
    if audio_path:
        play_mp3(audio_path, voice=True)


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

def burst_check_should_play(wait_ms: int = BURST_WAIT_MS) -> bool:
    """Last-wins burst suppression.

    Each invocation writes a fresh UUID to NOTIFY_BURST_FILE, sleeps a
    short window, and only proceeds to play audio if its UUID is still
    the latest one on disk. When several hooks fire in quick succession
    (parallel subtasks finishing together) only the last one survives —
    the earlier ones see the file overwritten and exit silently.
    """
    import uuid
    my_id = uuid.uuid4().hex
    try:
        NOTIFY_BURST_FILE.parent.mkdir(parents=True, exist_ok=True)
        NOTIFY_BURST_FILE.write_text(my_id, encoding="utf-8")
    except Exception:
        return True  # if we can't write, just play
    time.sleep(wait_ms / 1000)
    try:
        latest = NOTIFY_BURST_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return True
    return latest == my_id


def mark_question(session_id: str | None, now: float | None = None) -> None:
    if not session_id:
        return
    try:
        QUESTION_MARK_FILE.parent.mkdir(parents=True, exist_ok=True)
        QUESTION_MARK_FILE.write_text(
            json.dumps({"session_id": session_id, "ts": time.time() if now is None else now}),
            encoding="utf-8")
    except OSError:
        pass


def question_just_announced(session_id: str | None, now: float | None = None) -> bool:
    """True when this session's question was announced < QUESTION_DEDUP_S ago."""
    if not session_id:
        return False
    try:
        mark = json.loads(QUESTION_MARK_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(mark, dict) or mark.get("session_id") != session_id:
        return False
    ts = mark.get("ts")
    now = time.time() if now is None else now
    return isinstance(ts, (int, float)) and 0 <= now - ts < QUESTION_DEDUP_S


# ══════════════════════════════════════════════════
# INPUT: Claude Code hooks, Codex hooks, legacy Codex notify
# ══════════════════════════════════════════════════

SOURCE_CLAUDE = "claude"
SOURCE_CODEX = "codex"

def _source_from_argv(argv: list[str]) -> str:
    """`--source codex` (as written by the tray / voice_hooks); default claude."""
    for i, arg in enumerate(argv):
        if arg == "--source" and i + 1 < len(argv):
            return argv[i + 1].strip().lower()
        if arg.startswith("--source="):
            return arg.split("=", 1)[1].strip().lower()
    return SOURCE_CLAUDE


def _json_arg(argv: list[str]) -> dict | None:
    """Legacy Codex `notify`: the event is a JSON object in the last argument."""
    for arg in reversed(argv):
        s = arg.strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                return None
            return obj if isinstance(obj, dict) else None
    return None


def _short(text: object, limit: int = 80) -> str:
    if not isinstance(text, str):
        return ""
    t = " ".join(text.split()).rstrip(".")
    return t[: limit - 3] + "..." if len(t) > limit else t


def codex_to_event(data: dict) -> dict | None:
    """Codex payload -> Claude-shaped event (hook_event_name Stop/Notification),
    tagged agent=codex. Unknown/irrelevant events -> None (stay silent).

      hooks  Stop               -> Stop        ("Codex ha finito")
      hooks  PermissionRequest  -> Notification permission_prompt
      notify agent-turn-complete-> Stop        (legacy `notify` program)
    """
    event = data.get("hook_event_name") or data.get("type") or ""
    base = {"agent": SOURCE_CODEX, "cwd": data.get("cwd"), "session_id": data.get("session_id")}
    if event in ("Stop", "agent-turn-complete"):
        return {**base, "hook_event_name": "Stop", "stop_hook_active": bool(data.get("stop_hook_active"))}
    if event == "PermissionRequest":
        tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        detail = _short(tool_input.get("description")) or _short(data.get("tool_name"), 40)
        return {**base, "hook_event_name": "Notification",
                "notification_type": "permission_prompt", "message": detail}
    return None


def read_event(argv: list[str], read_stdin) -> tuple[str, dict] | None:
    """(source, Claude-shaped event) or None. Decision tree:
      1. JSON object in argv          -> legacy Codex notify
      2. stdin JSON, --source codex   -> Codex hooks engine
      3. stdin JSON                   -> Claude Code
    """
    legacy = _json_arg(argv)
    if legacy is not None:
        ev = codex_to_event(legacy)
        return (SOURCE_CODEX, ev) if ev else None
    try:
        raw = read_stdin()
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if _source_from_argv(argv) == SOURCE_CODEX:
        ev = codex_to_event(data)
        return (SOURCE_CODEX, ev) if ev else None
    return SOURCE_CLAUDE, data


# Codex runs its hooks synchronously (see voice_hooks._HANDLER_OPTS): the hook
# process hands the payload to a detached worker and exits at once, so the
# turn is not held for synthesis + playback and `codex exec` shutting down
# cannot kill the voice line.
WORKER_FLAG = "--worker"
PLAY_FLAG = "--play"
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _worker_python() -> tuple[str, dict[str, str]]:
    """Keep this script standalone: installed hooks may live outside the app.

    uv's venv pythonw.exe can itself be a console launcher. Prefer the base
    GUI interpreter and tell it which venv to use, as the app launcher does.
    CREATE_NO_WINDOW also protects the fallback console interpreter.
    """
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    env["PYTHONIOENCODING"] = "utf-8"
    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    if pythonw.is_file():
        launcher = Path(sys.prefix) / "Scripts" / "pythonw.exe"
        if sys.prefix != sys.base_prefix and launcher.is_file():
            env["__PYVENV_LAUNCHER__"] = str(launcher)
        return str(pythonw), env
    return sys.executable, env


def _spawn_hidden(args: list[str], raw: str | None = None) -> bool:
    """Re-run this script without a console, optional payload on its stdin.
    Breakaway from the caller's job first (Codex's job allows it); if refused,
    plain no-window (Codex preserves descendants of a hook that completed).
    Never combine DETACHED_PROCESS with CREATE_NO_WINDOW: Windows ignores the
    no-window flag in that combination, and a uv child can create a console.
    False = could not spawn."""
    python, env = _worker_python()
    cmd = [python, os.path.abspath(__file__), *args]
    base = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    for flags in (base | _CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, creationflags=flags,
                                    env=env, close_fds=True)
        except (OSError, ValueError):
            continue
        try:
            proc.stdin.write((raw or "").encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass
        return True
    return False


def spawn_detached_worker(argv: list[str], raw: str | None) -> bool:
    """Hand a Codex event to a detached worker. False = could not spawn: the
    caller handles the event inline."""
    return _spawn_hidden([*argv, WORKER_FLAG], raw)


def main(argv: list[str] | None = None, read_stdin=None, detach=None) -> None:
    """detach: callable(argv, raw) -> bool, used for Codex events only (the
    __main__ entry passes spawn_detached_worker; tests leave it None)."""
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == [PLAY_FLAG]:
        play_command(argv[1:])
        return
    if read_stdin is None:
        read_stdin = (lambda: sys.stdin.read()) if sys.stdin is not None else (lambda: "")
    captured: dict[str, str] = {}

    def _read() -> str:
        captured["raw"] = read_stdin()
        return captured["raw"]

    got = read_event(argv, _read)
    if got is None:
        return
    source, data = got
    if (source == SOURCE_CODEX and detach is not None and WORKER_FLAG not in argv
            and detach(argv, captured.get("raw"))):
        return

    event_name = data.get("hook_event_name", "")

    # PreToolUse: only AskUserQuestion speaks (a wider matcher stays silent).
    # The marker is written before the burst check, so the dedup below works
    # even if this line itself is swallowed by a burst.
    if event_name == "PreToolUse":
        if data.get("tool_name") != ASK_USER_QUESTION:
            return
        mark_question(data.get("session_id"))
    elif (event_name == "Notification" and source == SOURCE_CLAUDE
          and data.get("notification_type") != "auth_success"
          and question_just_announced(data.get("session_id"))):
        return

    # Per-event burst window: subagents (TaskCompleted) get the longer
    # window so clusters collapse to one voice line; Stop and
    # Notification stay reactive on the default 300 ms window.
    burst_wait = SUBAGENT_BURST_WAIT_MS if event_name == "TaskCompleted" else BURST_WAIT_MS
    if not burst_check_should_play(burst_wait):
        return

    # Run cache cleanup (fast, non-blocking)
    cleanup_tts_cache()

    # Subagent terminated → fixed short phrase, random voice, no chime,
    # no contextual TTS, no easter egg. Tracker is still updated so any
    # downstream progress logic stays consistent.
    if event_name == "TaskCompleted":
        update_tracker(data)
        tts_mode = load_config().get("tts_mode", "full")
        if tts_mode in ("silent", "semi-silent"):
            return
        play_subagent_voice()
        return

    sub_type = data.get("notification_type") or data.get("type") or data.get("sub_type")
    repo = extract_repo_name(data)
    who = SPEAKER_CODEX if source == SOURCE_CODEX else SPEAKER_CLAUDE
    voice = get_voice()
    progress = 0.0

    if event_name == "Stop":
        plan_info = extract_plan_info(data)
        detail = extract_task_detail(data)
        stop_type = "hook_active" if data.get("stop_hook_active") else None
        message = build_stop_message(stop_type, repo, plan_info, detail, who)
        chime_key = "stop"
        progress = 1.0  # Stop = end of session = max intensity

    elif event_name == "PreToolUse":  # AskUserQuestion (filtered above)
        message = announce(repo, who, QUESTION_PHRASE)
        chime_key = "question"

    elif event_name == "Notification":
        detail = extract_notification_detail(data)
        message = build_notif_message(sub_type, repo, detail, who)
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
    play_mp3(audio_path, voice=True)

    # Easter egg: ~7% chance of Frisco appreciation in a random language
    maybe_play_easter_egg(tts_mode)


if __name__ == "__main__":
    try:
        main(detach=spawn_detached_worker)
    except Exception:
        pass
    sys.exit(0)
