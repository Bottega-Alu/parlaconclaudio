"""
System Tray Icon - Voice Bridge control panel.

Animated marble sphere icon that shifts colors:
- Idle: static teal marble (no animation wakeups)
- Recording: pulsing red/magenta
- Transcribing: fast golden shimmer

Right-click menu:
- Voice selector (single speaker for all events)
- Whisper Language selector
- Sound Pack + Volume
- Preview sounds on click
- Voice hooks for Claude Code / Codex (checked = wired in the real config files)
- Start at sign-in (Task Scheduler logon task, checked = registered)
- Exit
"""

import colorsys
import json
import logging
import math
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from .menu_dpi import native_menu_dpi

logger = logging.getLogger(__name__)

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    logger.info("pystray/Pillow not available - tray icon disabled")

# === PATHS ===
TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"
SOUNDS_DIR = Path.home() / ".claude" / "cache" / "tts" / "sounds"

# === QUICK PRESETS (single voice) ===
VOICE_PRESETS = {
    "Isabella (IT)": {"voice": "it-IT-IsabellaNeural", "rate": "-4%", "pitch": "-8Hz"},
    "Andrew ML (EN)": {"voice": "en-US-AndrewMultilingualNeural", "rate": "+0%", "pitch": "+0Hz"},
    "Thalita ML (BR)": {"voice": "pt-BR-ThalitaMultilingualNeural", "rate": "-2%", "pitch": "-4Hz"},
    "Seraphina ML": {"voice": "en-US-SeraphinaMultilingualNeural", "rate": "-5%", "pitch": "-10Hz"},
    "Brian ML (EN)": {"voice": "en-US-BrianMultilingualNeural", "rate": "-3%", "pitch": "-5Hz"},
    "Diego (IT)": {"voice": "it-IT-DiegoNeural", "rate": "+0%", "pitch": "-2Hz"},
    "Emma ML (EN)": {"voice": "en-US-EmmaMultilingualNeural", "rate": "+0%", "pitch": "+0Hz"},
    "Vivienne ML (FR)": {"voice": "fr-FR-VivienneMultilingualNeural", "rate": "+0%", "pitch": "+0Hz"},
}

# === EDGE-TTS VOICES BY LANGUAGE ===
# Organized for individual voice selection in tray menu
EDGE_VOICES = {
    "Italiano": [
        {"name": "Isabella", "id": "it-IT-IsabellaNeural", "gender": "F"},
        {"name": "Elsa", "id": "it-IT-ElsaNeural", "gender": "F"},
        {"name": "Diego", "id": "it-IT-DiegoNeural", "gender": "M"},
        {"name": "Giuseppe ML", "id": "it-IT-GiuseppeMultilingualNeural", "gender": "M"},
    ],
    "English US": [
        {"name": "Ava ML", "id": "en-US-AvaMultilingualNeural", "gender": "F"},
        {"name": "Emma ML", "id": "en-US-EmmaMultilingualNeural", "gender": "F"},
        {"name": "Aria", "id": "en-US-AriaNeural", "gender": "F"},
        {"name": "Jenny", "id": "en-US-JennyNeural", "gender": "F"},
        {"name": "Michelle", "id": "en-US-MichelleNeural", "gender": "F"},
        {"name": "Ana", "id": "en-US-AnaNeural", "gender": "F"},
        {"name": "Seraphina ML", "id": "en-US-SeraphinaMultilingualNeural", "gender": "F"},
        {"name": "Andrew ML", "id": "en-US-AndrewMultilingualNeural", "gender": "M"},
        {"name": "Brian ML", "id": "en-US-BrianMultilingualNeural", "gender": "M"},
        {"name": "Guy", "id": "en-US-GuyNeural", "gender": "M"},
        {"name": "Eric", "id": "en-US-EricNeural", "gender": "M"},
        {"name": "Roger", "id": "en-US-RogerNeural", "gender": "M"},
        {"name": "Steffan", "id": "en-US-SteffanNeural", "gender": "M"},
        {"name": "Christopher", "id": "en-US-ChristopherNeural", "gender": "M"},
    ],
    "English GB": [
        {"name": "Sonia", "id": "en-GB-SoniaNeural", "gender": "F"},
        {"name": "Libby", "id": "en-GB-LibbyNeural", "gender": "F"},
        {"name": "Maisie", "id": "en-GB-MaisieNeural", "gender": "F"},
        {"name": "Ryan", "id": "en-GB-RyanNeural", "gender": "M"},
        {"name": "Thomas", "id": "en-GB-ThomasNeural", "gender": "M"},
    ],
    "Portugues BR": [
        {"name": "Thalita ML", "id": "pt-BR-ThalitaMultilingualNeural", "gender": "F"},
        {"name": "Francisca", "id": "pt-BR-FranciscaNeural", "gender": "F"},
        {"name": "Antonio", "id": "pt-BR-AntonioNeural", "gender": "M"},
    ],
    "Espanol": [
        {"name": "Elvira (ES)", "id": "es-ES-ElviraNeural", "gender": "F"},
        {"name": "Ximena (ES)", "id": "es-ES-XimenaNeural", "gender": "F"},
        {"name": "Dalia (MX)", "id": "es-MX-DaliaNeural", "gender": "F"},
        {"name": "Alvaro (ES)", "id": "es-ES-AlvaroNeural", "gender": "M"},
        {"name": "Jorge (MX)", "id": "es-MX-JorgeNeural", "gender": "M"},
    ],
    "Francais": [
        {"name": "Vivienne ML", "id": "fr-FR-VivienneMultilingualNeural", "gender": "F"},
        {"name": "Denise", "id": "fr-FR-DeniseNeural", "gender": "F"},
        {"name": "Eloise", "id": "fr-FR-EloiseNeural", "gender": "F"},
        {"name": "Remy ML", "id": "fr-FR-RemyMultilingualNeural", "gender": "M"},
        {"name": "Henri", "id": "fr-FR-HenriNeural", "gender": "M"},
    ],
    "Deutsch": [
        {"name": "Seraphina ML", "id": "de-DE-SeraphinaMultilingualNeural", "gender": "F"},
        {"name": "Amala", "id": "de-DE-AmalaNeural", "gender": "F"},
        {"name": "Katja", "id": "de-DE-KatjaNeural", "gender": "F"},
        {"name": "Florian ML", "id": "de-DE-FlorianMultilingualNeural", "gender": "M"},
        {"name": "Conrad", "id": "de-DE-ConradNeural", "gender": "M"},
        {"name": "Killian", "id": "de-DE-KillianNeural", "gender": "M"},
    ],
    "Japanese": [
        {"name": "Nanami", "id": "ja-JP-NanamiNeural", "gender": "F"},
        {"name": "Keita", "id": "ja-JP-KeitaNeural", "gender": "M"},
    ],
}

VOLUME_LEVELS = [0, 25, 50, 75, 100, 125, 150, 200, 250, 300]

_VOICE_HOOKS_LOCK = threading.Lock()

TTS_MODES = {
    "Voce e suoni": "full",              # Chime + voice always
    "Voce solo al termine": "semi-silent", # Chime always, voice only on Stop
    "Solo suoni": "silent",           # Chime only, no voice
}

WHISPER_LANGUAGES = {
    "Rilevamento automatico": None,
    "Italiano": "it",
    "English": "en",
    "Portugues": "pt",
    "Espanol": "es",
    "Francais": "fr",
    "Deutsch": "de",
    "Japanese": "ja",
}

# === MENU EMOJI ===


REPO_URL = "https://github.com/fra-itc/parlaconclaudio"

# STT mode labels for menu display
STT_MODES = {
    "auto": "Automatico (consigliato)",
    "local": "GPU locale (NVIDIA CUDA)",
    "cloud_groq": "Cloud: Groq",
    "cloud_deepgram": "Cloud: Deepgram",
}


def _load_config() -> dict:
    try:
        if TTS_CONFIG.is_file():
            return json.loads(TTS_CONFIG.read_text())
    except Exception:
        pass
    return {"sound_pack": "r2d2", "whisper_language": None, "volume": 200, "muted": False}


def _save_config(config: dict) -> None:
    try:
        TTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        TTS_CONFIG.write_text(json.dumps(config, indent=2))
    except Exception as e:
        logger.error(f"Failed to save config: {e}")


def auto_paste_enabled(config: dict | None = None) -> bool:
    """Whether dictated text is pasted (Ctrl+V) or only copied to the clipboard."""
    return bool((config if config is not None else _load_config()).get("auto_paste", True))


def voice_volume(config: dict) -> int:
    """Volume of spoken announcements; follows the sound volume until set on its own."""
    return int(config.get("voice_volume", config.get("volume", 200)))


def output_mode_from_config(default: str) -> str:
    """Clipboard-only when the user turned auto paste off; otherwise the configured mode."""
    return default if auto_paste_enabled() else "clipboard_only"


def _play_sound(filepath: str) -> None:
    """Play a sound file for preview at configured volume."""
    config = _load_config()
    if config.get("muted"):
        return
    volume = config.get("volume", 200)
    if volume <= 0:
        return
    try:
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-af", f"volume={volume}/100", filepath],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def list_sound_packs() -> list[tuple[str, int]]:
    """Installed sound packs as (name, mp3 count); "_"-prefixed dirs are private."""
    if not SOUNDS_DIR.is_dir():
        return []
    return [(d.name, len(list(d.glob("*.mp3")))) for d in sorted(SOUNDS_DIR.iterdir())
            if d.is_dir() and not d.name.startswith("_")]


def preview_sound_pack(pack_name: str) -> bool:
    """Play one random sound of a pack ("random" picks any pack); False if empty."""
    import random
    packs = [name for name, count in list_sound_packs() if count]
    if pack_name == "random":
        pack_name = random.choice(packs) if packs else ""
    sounds = sorted((SOUNDS_DIR / pack_name).glob("*.mp3")) if pack_name else []
    if not sounds:
        return False
    _play_sound(str(random.choice(sounds)))
    return True


# ══════════════════════════════════════════════════
# ANIMATED MARBLE SPHERE ICON (original)
# ══════════════════════════════════════════════════

def _marble_noise(x: float, y: float, t: float) -> float:
    """Smooth marble noise — soft veins, no grain."""
    v = math.sin(x * 0.12 + y * 0.08 + t * 0.6)
    v += 0.5 * math.cos(x * 0.09 - y * 0.15 + t * 0.45)
    v += 0.3 * math.sin((x + y) * 0.14 + t * 0.8)
    return v


def _marble_noise_np(x, y, t: float):
    """numpy twin of _marble_noise (same formula, array-wise)."""
    v = np.sin(x * 0.12 + y * 0.08 + t * 0.6)
    v = v + 0.5 * np.cos(x * 0.09 - y * 0.15 + t * 0.45)
    v = v + 0.3 * np.sin((x + y) * 0.14 + t * 0.8)
    return v


def _hsv_to_rgb_np(h, s, v):
    """Array-wise colorsys.hsv_to_rgb (same branches, same float math)."""
    i = (h * 6.0).astype(np.int64)  # int() of a value >= 0
    f = (h * 6.0) - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i = i % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return r, g, b


def _generate_marble_sphere(
    size: int,
    hue_offset: float,
    hue_range: float = 0.3,
    saturation: float = 0.75,
    brightness: float = 0.95,
    time_val: float = 0.0,
    design_size: int | None = None,
) -> "Image.Image":
    """Generate a single frame of the animated marble sphere.

    Rich liquid-ink effect with deep shimmer and smooth color flow.

    The look is defined in the pixel space of ``design_size`` (vein frequency,
    3px margin, 2px glow). Rendering at a smaller ``size`` samples that same
    design scaled down; ``design_size=None`` means size == design (the original
    behaviour, kept pixel-identical).
    """
    # Vectorized (numpy) port of the original per-pixel loop: same formulas,
    # same float64 math, same int() truncation. The pure-Python loop took
    # ~90 ms per 192px frame holding the GIL and, since the idle animation
    # never stops, kept the bridge at ~50% of a core at rest.
    scale = (design_size or size) / size  # design px per output px
    center = size * scale / 2.0
    radius = (size * scale - 6) / 2.0
    coords = np.arange(size, dtype=np.float64) * scale  # design-space sample points
    x = coords[np.newaxis, :]
    y = coords[:, np.newaxis]
    dx = x - center
    dy = y - center
    dist = np.sqrt(dx * dx + dy * dy)
    inside = dist <= radius

    norm_dist = dist / radius
    nx = np.broadcast_to(dx / radius, dist.shape)
    ny = np.broadcast_to(dy / radius, dist.shape)
    nz = np.sqrt(np.maximum(0, 1 - nx * nx - ny * ny))

    # 3D sphere lighting
    light_dot = nx * (-0.3) + ny * (-0.4) + nz * 0.8
    light_factor = np.maximum(0.15, np.minimum(1.0, 0.5 + light_dot * 0.6))

    # Marble veins - rotate sampling point around Y axis (like Earth)
    rot_offset = time_val * 12.0  # horizontal scroll speed in pixels
    marble = _marble_noise_np(x + rot_offset, y, 0.0)

    # Hue: uniform base + very subtle vein variation
    h = (hue_offset + marble * hue_range * 0.08) % 1.0
    # Saturation: smooth, uniform
    s = saturation * (1.0 - norm_dist * 0.10)
    # Brightness: base + lighting (no pulse/shimmer in idle)
    v = brightness * light_factor

    # Main specular highlight (glass)
    spec_dist = np.sqrt((nx + 0.35) ** 2 + (ny + 0.35) ** 2)
    hit = spec_dist < 0.38
    spec = np.where(hit, np.clip(1.0 - spec_dist / 0.38, 0.0, None) ** 2.5 * 0.65, 0.0)
    v = np.where(hit, np.minimum(1.0, v + spec), v)
    s = np.where(hit, np.maximum(0.0, s - spec * 0.8), s)

    # Pinpoint specular
    spec2_dist = np.sqrt((nx + 0.25) ** 2 + (ny + 0.50) ** 2)
    hit = spec2_dist < 0.12
    spec2 = np.where(hit, np.clip(1.0 - spec2_dist / 0.12, 0.0, None) ** 4 * 0.35, 0.0)
    v = np.where(hit, np.minimum(1.0, v + spec2), v)
    s = np.where(hit, np.maximum(0.0, s - spec2), s)

    # Rim lighting
    rim_hit = norm_dist > 0.65
    rim = np.where(rim_hit, (norm_dist - 0.65) / 0.35, 0.0)
    v = np.where(rim_hit, np.minimum(1.0, v + rim ** 1.5 * 0.25), v)

    r, g, b = _hsv_to_rgb_np(h % 1.0, np.clip(s, 0, 1), np.clip(v, 0, 1))

    alpha = np.where(norm_dist > 0.85, 255 * (1.0 - (norm_dist - 0.85) / 0.15), 255.0)

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    for i, channel in enumerate((r * 255, g * 255, b * 255, alpha)):
        # int() truncates toward zero; every value here is >= 0 inside the disc
        rgba[..., i] = np.where(inside, np.trunc(channel), 0).astype(np.uint8)
    img = Image.fromarray(rgba, "RGBA")

    glow = img.filter(ImageFilter.GaussianBlur(radius=2.0 / scale))
    img = Image.blend(img, glow, alpha=0.15)

    return img


# Animation presets per state
_ANIM_PRESETS = {
    "loading": {
        "hue_speed": 0.0,
        "hue_range": 0.08,
        "saturation": 0.90,
        "brightness": 0.90,
        "time_speed": 1.5,
        "interval": 0.10,
        "base_hue": 0.13,         # Yellow/amber — caution
        "pulse": True,
    },
    "idle": {
        "hue_speed": 0.006,       # Slow drift through non-red spectrum
        "hue_range": 0.08,
        "saturation": 0.90,
        "brightness": 0.95,
        "time_speed": 0.4,        # Marble flow = rotation illusion
        # Conservative cadence if idle animation is ever enabled. The test
        # application keeps idle static and waits for a state change instead.
        "interval": 0.12,
        "hue_min": 0.08,          # Skip red zone
        "hue_max": 0.92,
    },
    "recording": {
        "hue_speed": 0.003,
        "hue_range": 0.10,
        "saturation": 0.92,
        "brightness": 0.95,
        "time_speed": 1.5,
        "interval": 0.08,
        "base_hue": 0.98,         # Red/magenta — recording
        "pulse": True,
    },
    "transcribing": {
        "hue_speed": 0.015,
        "hue_range": 0.10,
        "saturation": 0.80,
        "brightness": 0.95,
        "time_speed": 1.0,
        "interval": 0.10,
        "base_hue": 0.12,         # Golden — processing
    },
}

# The look is designed at 192px (MARBLE_DESIGN_SIZE) but the tray never shows
# more than SM_CXICON (32px at 100%, 64px at 200% DPI): pystray hands Windows an
# ICO with every Pillow size <= the image, and LoadImage(LR_DEFAULTSIZE) picks
# the SM_CXICON one. A 192px frame meant a 31 KB ICO temp file per frame
# (~250 KB/s at rest); 64px covers 200% DPI at a fraction of the bytes.
# Each frame is rendered at 2x (RENDER_SIZE) and downsampled for antialiasing.
MARBLE_DESIGN_SIZE = 192
ICON_SIZE = 64
RENDER_SIZE = ICON_SIZE * 2


def _render_icon_frame(hue: float, **kwargs) -> "Image.Image":
    """One tray frame: 2x supersampled render of the 192px design, ICON_SIZE out."""
    img = _generate_marble_sphere(RENDER_SIZE, hue, design_size=MARBLE_DESIGN_SIZE, **kwargs)
    return img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)


class _IconAnimator:
    """Static at rest, bounded reusable frames while work is in progress."""

    def __init__(self):
        self._state = "idle"
        self._running = False
        self._thread = None
        self._icon_ref = None
        self._wake = threading.Event()
        self._frames = {}

    def set_icon(self, icon):
        self._icon_ref = icon

    def set_state(self, state: str):
        if self._state != state:
            self._state = state
            self._wake.set()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._animation_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._wake.set()

    def _frame(self, state, index):
        state = state if state in _ANIM_PRESETS else "idle"
        index = 0 if state == "idle" else index % 12
        key = (state, index)
        if key not in self._frames:
            preset = _ANIM_PRESETS[state]
            phase = index * math.tau / 12
            hue = preset.get("base_hue", 0.45)
            brightness = 0.8 + 0.15 * math.sin(phase) if preset.get("pulse") else preset["brightness"]
            self._frames[key] = _render_icon_frame(
                hue, hue_range=preset["hue_range"],
                saturation=preset["saturation"], brightness=brightness,
                time_val=phase,
            )
        return self._frames[key]

    def _animation_loop(self):
        index = 0
        while self._running:
            self._wake.clear()
            state = self._state
            try:
                if self._icon_ref:
                    self._icon_ref.icon = self._frame(state, index)
            except Exception as exc:
                logger.debug("Animation frame error: %s", exc)
            index = (index + 1) % 12
            # No periodic rendering, shell updates or wakeups while idle.
            self._wake.wait(None if state == "idle" else 0.12)


class TrayIcon:
    """System tray icon with animated marble sphere and settings menu."""

    def __init__(
        self,
        on_exit: Callable[[], None] | None = None,
        on_purge_vram: Callable[[], None] | None = None,
        on_stt_mode_changed: Callable[[str], None] | None = None,
        on_mic_changed: Callable[[int | None], None] | None = None,
        on_volume_changed: Callable[[int], None] | None = None,
        on_muted_changed: Callable[[bool], None] | None = None,
        transcriber=None,
        recorder=None,
        on_toggle_recording=None,
        on_unload_vram=None,
        is_busy=None,
    ):
        self._on_toggle_recording = on_toggle_recording
        self._on_unload_vram = on_unload_vram
        self._is_busy = is_busy
        self._state = "idle"
        self._batch_lock = threading.Lock()
        self._panel = None
        self._on_exit = on_exit
        self._on_purge_vram = on_purge_vram
        self._on_stt_mode_changed = on_stt_mode_changed
        self._on_mic_changed = on_mic_changed
        self._on_volume_changed = on_volume_changed
        self._on_muted_changed = on_muted_changed
        self._transcriber = transcriber
        self._recorder = recorder
        self._icon: "pystray.Icon | None" = None
        self._thread: threading.Thread | None = None
        self._animator = _IconAnimator()
        self._flyout = None
        self._flyout_lock = threading.Lock()
        self._flyout_timer = None

    @property
    def batch_busy(self):
        return self._batch_lock.locked()

    def _voice_display_name(self, voice_id: str) -> str:
        """Get short display name for a voice ID."""
        for voices in EDGE_VOICES.values():
            for v in voices:
                if v["id"] == voice_id:
                    return v["name"]
        # Fallback: extract name from ID (e.g. "it-IT-IsabellaNeural" -> "Isabella")
        parts = voice_id.split("-")
        if len(parts) >= 3:
            return parts[2].replace("Neural", "").replace("Multilingual", " ML")
        return voice_id

    # ------------------------------------------------------------------
    # Microphone selector
    # ------------------------------------------------------------------

    def _list_mics(self):
        try:
            return self._recorder.list_devices() if self._recorder else []
        except Exception as exc:
            logger.warning("Failed to list microphones: %s", exc)
            return []

    def _make_set_mic(self, device_id: int):
        def handler(icon, item):
            if self._on_mic_changed and self._on_mic_changed(device_id) is False:
                self._notify("Microfono invariato: termina la registrazione prima di cambiarlo.")
                return
            config = _load_config()
            config["mic_device_id"] = device_id
            _save_config(config)
            logger.info(f"Microphone set to device {device_id}")
        return handler

    # ------------------------------------------------------------------
    # STT Engine cruscotto
    # ------------------------------------------------------------------

    def _get_stt_status(self, config: dict) -> dict:
        """Gather STT engine status info for menu display."""
        stt_mode = config.get("stt_mode", "auto")
        active = self._transcriber.active_engine_name() if self._transcriber else "not_loaded"
        gpu = self._transcriber.gpu_name() if self._transcriber else "Unknown"

        # GPU state: "loaded" | "loading" | "not_detected" | "error"
        if active == "whisper_local":
            gpu_state = "loaded"
        elif gpu == "Not detected":
            gpu_state = "not_detected"
        elif active == "not_loaded":
            gpu_state = "not_loaded"
        else:
            gpu_state = "cloud"  # GPU present but using cloud

        # Determine display label for active backend
        _labels = {
            "whisper_local": "GPU locale",
            "groq": "Groq",
            "deepgram": "Deepgram",
            "disabled": "Disabilitato",
            "not_loaded": "In attesa del primo utilizzo",
        }
        active_label = _labels.get(active, active)

        return {
            "mode": stt_mode,
            "active": active,
            "active_label": active_label,
            "gpu": gpu,
            "gpu_state": gpu_state,
        }

    def _make_set_stt_mode(self, mode: str):
        def handler(icon, item):
            if self._on_stt_mode_changed:
                if self._on_stt_mode_changed(mode) is False:
                    self._notify("Motore invariato: attendi la fine del lavoro in corso.")
                    return
            else:
                # Direct config write if no callback
                config = _load_config()
                config["stt_mode"] = mode
                _save_config(config)
            logger.info(f"STT mode set to: {mode}")
        return handler

    def _ask_api_key_groq(self, icon, item):
        self._ask_api_key("groq", "Groq API Key", "Enter your Groq API key:\n(Get one free at console.groq.com)")

    def _ask_api_key_deepgram(self, icon, item):
        self._ask_api_key("deepgram", "Deepgram API Key", "Enter your Deepgram API key:\n(Get $200 free at console.deepgram.com)")

    def _ask_api_key(self, provider: str, title: str, prompt: str):
        def _run():
            try:
                import tkinter as tk
                from tkinter import simpledialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                key = simpledialog.askstring(title, prompt, show="*", parent=root)
                root.destroy()
                if key and key.strip():
                    from ..core.stt_engine.key_manager import KeyManager
                    store = KeyManager.set_key(provider, key.strip())
                    logger.info(f"{provider} API key saved ({store})")
            except Exception as e:
                logger.error(f"API key dialog failed: {e}")
        threading.Thread(target=_run, daemon=True).start()

    def _test_cloud_connection(self, icon, item):
        """Quick validation of cloud STT connection (no actual transcription)."""
        def _run():
            from ..core.stt_engine.key_manager import KeyManager
            import urllib.request
            import urllib.error

            results = []
            groq_key = KeyManager.get_key("groq")
            if groq_key:
                try:
                    req = urllib.request.Request(
                        "https://api.groq.com/openai/v1/models",
                        headers={"Authorization": f"Bearer {groq_key}", "User-Agent": "parlaconclaudio/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=5):
                        results.append("Groq: ✓ Connected")
                except urllib.error.HTTPError as e:
                    results.append(f"Groq: ✗ HTTP {e.code}")
                except Exception as e:
                    results.append(f"Groq: ✗ {e}")
            else:
                results.append("Groq: no key")

            deepgram_key = KeyManager.get_key("deepgram")
            if deepgram_key:
                try:
                    req = urllib.request.Request(
                        "https://api.deepgram.com/v1/projects",
                        headers={"Authorization": f"Token {deepgram_key}", "User-Agent": "parlaconclaudio/1.0"},
                    )
                    with urllib.request.urlopen(req, timeout=5):
                        results.append("Deepgram: ✓ Connected")
                except urllib.error.HTTPError as e:
                    results.append(f"Deepgram: ✗ HTTP {e.code}")
                except Exception as e:
                    results.append(f"Deepgram: ✗ {e}")
            else:
                results.append("Deepgram: no key")

            msg = "\n".join(results)
            logger.info(f"Cloud connection test:\n{msg}")

            try:
                import tkinter as tk
                from tkinter import messagebox
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                messagebox.showinfo("STT Cloud Connection Test", msg, parent=root)
                root.destroy()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _make_set_language(self, lang_code):
        def handler(icon, item):
            config = _load_config()
            config["whisper_language"] = lang_code
            _save_config(config)
            logger.info(f"Whisper language set to: {lang_code or 'auto'}")
        return handler

    def _make_set_voice_preset(self, preset_name):
        def handler(icon, item):
            config = _load_config()
            config["voice"] = dict(VOICE_PRESETS[preset_name])
            # Clean up legacy keys
            config.pop("alessia", None)
            config.pop("claudio", None)
            _save_config(config)
            logger.info(f"Voice set to preset: {preset_name}")
        return handler

    def _make_set_voice(self, voice_id: str):
        """Set the single voice for all TTS events."""
        def handler(icon, item):
            config = _load_config()
            config["voice"] = {"voice": voice_id, "rate": "+0%", "pitch": "+0Hz"}
            # Clean up legacy keys
            config.pop("alessia", None)
            config.pop("claudio", None)
            _save_config(config)
            name = self._voice_display_name(voice_id)
            logger.info(f"Voice set to: {name} ({voice_id})")
        return handler

    def _make_set_volume(self, level: int):
        def handler(icon, item):
            config = _load_config()
            config["volume"] = level
            _save_config(config)
            if self._on_volume_changed:
                self._on_volume_changed(level)
            logger.info(f"Volume set to: {level}%")
        return handler

    def _make_set_voice_volume(self, level: int):
        def handler(icon, item):
            config = _load_config()
            config["voice_volume"] = level
            _save_config(config)
            logger.info(f"Voice volume set to: {level}%")
        return handler

    def _make_toggle_mute(self):
        def handler(icon, item):
            config = _load_config()
            new_state = not config.get("muted", False)
            config["muted"] = new_state
            _save_config(config)
            if self._on_muted_changed:
                self._on_muted_changed(new_state)
            logger.info(f"Mute toggled: {new_state}")
        return handler

    def _toggle_auto_paste_clicked(self, icon=None, item=None):
        config = _load_config()
        config["auto_paste"] = not auto_paste_enabled(config)
        _save_config(config)
        logger.info(f"Auto paste set to: {config['auto_paste']}")

    def _make_set_tts_mode(self, mode_value: str):
        def handler(icon, item):
            config = _load_config()
            config["tts_mode"] = mode_value
            _save_config(config)
            logger.info(f"TTS mode set to: {mode_value}")
        return handler

    def _make_set_sound_pack(self, pack_name):
        def handler(icon, item):
            config = _load_config()
            config["sound_pack"] = pack_name
            _save_config(config)
            logger.info(f"Sound pack set to: {pack_name}")
        return handler

    def _notify(self, message: str) -> None:
        """Balloon notification; logged too, so a failure is never silent."""
        if self._icon:
            try:
                self._icon.notify(message, "parlaconclaudio")
            except Exception as e:
                logger.debug(f"tray notify failed: {e}")

    # ------------------------------------------------------------------
    # Voice hooks (notify-tts.py) for Claude Code and Codex
    # ------------------------------------------------------------------

    def _apply_voice_hooks(self, plan: list[tuple[str, bool]]) -> None:
        """Run connect/disconnect per agent; log + one notification for all."""
        from ..core import voice_hooks as vh

        lines = []
        with _VOICE_HOOKS_LOCK:  # two quick clicks must not interleave read/modify/write
            results = [vh.connect(a) if want else vh.disconnect(a) for a, want in plan]
        for res in results:
            agent = res.agent
            log = logger.info if res.ok else logger.error
            log(f"Voice hook {agent} {res.action}: ok={res.ok} changed={res.changed} "
                f"kind={res.error_kind.value if res.error_kind else '-'} path={res.path} "
                f"backup={res.backup} {res.message}")
            lines.append(res.summary())
        self._notify("\n".join(lines)[:250])  # Windows balloon text limit ~256

    def _toggle_autostart_clicked(self, icon, item):
        def _run():
            from . import autostart
            result = autostart.disable() if autostart.is_enabled() else autostart.enable()
            log = logger.info if result.ok else logger.error
            log(result.log_line())  # includes the mechanism: task | run_key
            self._notify(result.summary())
        threading.Thread(target=_run, daemon=True).start()

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("Tray icon not available (install pystray Pillow)")
            return

        # Create initial static icon (animator will replace it immediately)
        initial_img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(initial_img)
        draw.ellipse([1, 1, ICON_SIZE - 1, ICON_SIZE - 1], fill=(0, 200, 100, 255))

        from .tray_popup import FlyoutIcon
        self._icon = FlyoutIcon(
            "voice_bridge",
            initial_img,
            "parlaconclaudio - Pronto",
            on_quick_controls=self._open_flyout,
            on_open_panel=self._open_panel,
        )

        # Start animator
        self._animator.set_icon(self._icon)
        self._animator.set_state("idle")
        self._animator.start()

        self._thread = threading.Thread(target=self._run_icon, daemon=True)
        self._thread.start()
        logger.info("Tray icon started with animated marble sphere")
        # The first click then only shows an already built window.
        prewarm = threading.Timer(3.0, self._prewarm_flyout)
        prewarm.daemon = True
        prewarm.start()

    def _quick_controls(self):
        from .tray_flyout import TrayFlyout
        with self._flyout_lock:
            if self._flyout is None:
                self._flyout = TrayFlyout(self)
            return self._flyout

    def _prewarm_flyout(self):
        try:
            self._quick_controls().prewarm()
        except Exception:
            logger.exception("Quick controls prewarm failed")

    def _run_icon(self):
        # Set DPI context before pystray creates the native owner windows.
        with native_menu_dpi():
            self._icon.run()

    def _open_flyout(self, left_click=False):
        if self._flyout_timer:
            self._flyout_timer.cancel()
        if left_click:
            # Leave double-click/long-press recording gestures time to fire.
            self._flyout_timer = threading.Timer(0.37, self._open_after_click)
            self._flyout_timer.daemon = True
            self._flyout_timer.start()
            return
        self._show_flyout()

    def _open_after_click(self):
        if self._state != "recording":
            self._show_flyout()

    def _show_flyout(self):
        self._quick_controls().show()

    def set_state(self, state: str) -> None:
        self._state = state
        if not self._icon:
            return
        title_map = {
            "loading": "parlaconclaudio - Caricamento motore…",
            "idle": "parlaconclaudio - Pronto",
            "recording": "parlaconclaudio - Registrazione…",
            "transcribing": "parlaconclaudio - Trascrizione…",
        }
        self._animator.set_state(state)
        self._icon.title = title_map.get(state, f"parlaconclaudio - {state}")

    def _open_repo(self, icon, item):
        """Open the GitHub repo in the default browser."""
        import webbrowser
        webbrowser.open(REPO_URL)

    def _open_groq_console(self, icon, item):
        import webbrowser
        webbrowser.open("https://console.groq.com/keys")

    def _open_deepgram_console(self, icon, item):
        import webbrowser
        webbrowser.open("https://console.deepgram.com/")

    def _purge_vram_clicked(self, icon, item):
        """Purge VRAM: unload model, clear CUDA cache, reload."""
        if self._on_purge_vram:
            threading.Thread(target=self._on_purge_vram, daemon=True).start()
            logger.info("VRAM purge triggered from menu")
        else:
            logger.warning("VRAM purge not available (no callback registered)")

    def _transcribe_files_clicked(self, icon, item):
        """Open a multi-select file dialog and run the audio file batch
        processor in a background thread.
        """
        if not self._transcriber:
            logger.warning("Transcribe-files: no transcriber available")
            return
        if self._is_busy and self._is_busy():
            self._notify("Termina la dettatura prima di trascrivere un file.")
            return
        if not self._batch_lock.acquire(blocking=False):
            self._notify("Una trascrizione file è già aperta o in corso.")
            return
        def run():
            try:
                self._transcribe_files_flow()
            finally:
                self._batch_lock.release()
        threading.Thread(target=run, daemon=True).start()

    def _transcribe_files_flow(self) -> None:
        """Background-thread flow: pick files, process, set clipboard,
        update tray title with progress + final summary.
        """
        from .audio_file_processor import AudioFileProcessor, SUPPORTED_EXTENSIONS

        # 1) Multi-select file dialog (Tk runs on its own root, off the
        # main tray thread — must withdraw or it'll show a stub window)
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            patterns = " ".join(f"*{e}" for e in SUPPORTED_EXTENSIONS)
            picked = filedialog.askopenfilenames(
                title="Seleziona file audio da trascrivere",
                filetypes=[
                    ("Audio files", patterns),
                    ("All files", "*.*"),
                ],
                parent=root,
            )
            root.destroy()
        except Exception as e:
            logger.error(f"File dialog failed: {e}")
            return

        if not picked:
            logger.info("Transcribe-files: no files selected")
            return

        from pathlib import Path
        paths = [Path(p) for p in picked]
        logger.info(f"Transcribe-files: processing {len(paths)} file(s)")

        # 2) Status while running
        original_title = None
        if self._icon:
            original_title = self._icon.title

        def on_progress(idx: int, total: int, p):
            if self._icon:
                self._icon.title = f"Trascrivendo {idx}/{total}: {p.name[:40]}"

        # 3) Run the pipeline
        processor = AudioFileProcessor(self._transcriber)
        try:
            with self._transcriber.operation():
                batch = processor.process_files(paths, on_progress=on_progress)
        except Exception as e:
            logger.error(f"Transcribe batch failed: {e}")
            self._notify("Trascrizione file non riuscita. Verifica il motore e riprova.")
            if self._icon and original_title is not None:
                self._icon.title = original_title
            return

        # 4) Clipboard with the aggregate report
        clipboard_text = AudioFileProcessor.build_clipboard_payload(batch)
        try:
            import pyperclip
            pyperclip.copy(clipboard_text)
            logger.info("Transcribe-files: clipboard updated with aggregate report")
        except Exception as e:
            logger.warning(f"Failed to write clipboard: {e}")

        # 5) Restore tray title with one-liner summary
        saved_total = sum(len(f.saved_files) for f in batch.files)
        dub_total = sum(len(f.dub_files) for f in batch.files)
        summary = (
            f"Trascrizione: {batch.ok_count}/{batch.total} file, "
            f"{saved_total} .txt + {dub_total} .mp3 salvati"
        )
        logger.info(summary)
        if self._icon:
            if original_title is not None:
                self._icon.title = original_title
            try:
                self._icon.notify(summary, "parlaconclaudio")
            except Exception:
                pass

    def _unload_vram_clicked(self, icon=None, item=None):
        if self._on_unload_vram:
            threading.Thread(target=self._on_unload_vram, daemon=True).start()

    def _open_panel(self, icon=None, item=None):
        from .control_panel import ControlPanel
        if self._panel is None:
            self._panel = ControlPanel(self)
        self._panel.show()

    def _exit_clicked(self, icon, item):
        if self._on_exit:
            self._on_exit()
        self.stop()

    def stop(self) -> None:
        self._animator.stop()
        if self._flyout_timer:
            self._flyout_timer.cancel()
        if self._flyout:
            self._flyout.stop()
        if self._panel:
            self._panel.stop()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
