"""
System Tray Icon - Voice Bridge control panel.

Animated marble sphere icon that shifts colors:
- Idle: slow mystical rainbow shift
- Recording: pulsing red/magenta
- Transcribing: fast golden shimmer

Right-click menu:
- Voice selector (single speaker for all events)
- Whisper Language selector
- Sound Pack + Volume
- Preview sounds on click
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

logger = logging.getLogger(__name__)

try:
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

VOLUME_LEVELS = [25, 50, 75, 100, 125, 150, 200, 250, 300]

TTS_MODES = {
    "Full": "full",              # Chime + voice always
    "Semi-silent": "semi-silent", # Chime always, voice only on Stop
    "Silent": "silent",           # Chime only, no voice
}

WHISPER_LANGUAGES = {
    "Auto-detect": None,
    "Italiano": "it",
    "English": "en",
    "Portugues": "pt",
    "Espanol": "es",
    "Francais": "fr",
    "Deutsch": "de",
    "Japanese": "ja",
}

# === MENU EMOJI ===
E_VOICE = "🎙️"
E_PRESETS = "⚡"
E_BROWSE = "📂"
E_LANG = "🌐"
E_TTS = "🔔"
E_VOLUME = "🔊"
E_PACK = "🎵"
E_PREVIEW = "🎧"
E_EXIT = "🚪"
E_SELECTED = "🔘"
E_UNSELECTED = "⚫"
E_FEMALE = "👩"
E_MALE = "👨"

LANG_FLAGS = {
    "Italiano": "🇮🇹",
    "English US": "🇺🇸",
    "English GB": "🇬🇧",
    "Portugues BR": "🇧🇷",
    "Espanol": "🇪🇸",
    "Francais": "🇫🇷",
    "Deutsch": "🇩🇪",
    "Japanese": "🇯🇵",
    "Auto-detect": "🌍",
    "English": "🇺🇸",
    "Portugues": "🇧🇷",
}

PACK_EMOJI = {
    "r2d2": "🤖",
    "south-park": "🎭",
    "south-park-ita": "🎭",
    "star-wars": "⚔️",
    "dune": "🏜️",
    "american-dad": "🇺🇸",
    "horror-zombie": "🧟",
    "maccio-capatonda": "🤡",
}

MODE_EMOJI = {"full": "📢", "semi-silent": "🔉", "silent": "🔇"}

E_PURGE = "🧹"
E_REPO = "🔗"
E_ENGINE = "🧠"
E_KEY = "🔑"
E_STATUS = "📊"
E_TEST = "🔗"
REPO_URL = "https://github.com/fra-itc/parlaconclaudio"

# STT mode labels for menu display
STT_MODES = {
    "auto": "Auto (recommended)",
    "local": "Local GPU (NVIDIA CUDA)",
    "cloud_groq": "Cloud: Groq (free)",
    "cloud_deepgram": "Cloud: Deepgram",
}


def _load_config() -> dict:
    try:
        if TTS_CONFIG.is_file():
            return json.loads(TTS_CONFIG.read_text())
    except Exception:
        pass
    return {"sound_pack": "r2d2", "whisper_language": None}


def _save_config(config: dict) -> None:
    try:
        TTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        TTS_CONFIG.write_text(json.dumps(config, indent=2))
    except Exception as e:
        logger.error(f"Failed to save config: {e}")


def _play_sound(filepath: str) -> None:
    """Play a sound file for preview at configured volume."""
    config = _load_config()
    volume = str(config.get("volume", 200))
    try:
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
             "-volume", volume, filepath],
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════
# ANIMATED MARBLE SPHERE ICON (original)
# ══════════════════════════════════════════════════

def _marble_noise(x: float, y: float, t: float) -> float:
    """Smooth marble noise — soft veins, no grain."""
    v = math.sin(x * 0.12 + y * 0.08 + t * 0.6)
    v += 0.5 * math.cos(x * 0.09 - y * 0.15 + t * 0.45)
    v += 0.3 * math.sin((x + y) * 0.14 + t * 0.8)
    return v


def _generate_marble_sphere(
    size: int,
    hue_offset: float,
    hue_range: float = 0.3,
    saturation: float = 0.75,
    brightness: float = 0.95,
    time_val: float = 0.0,
) -> "Image.Image":
    """Generate a single frame of the animated marble sphere.

    Rich liquid-ink effect with deep shimmer and smooth color flow.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = img.load()
    center = size / 2.0
    radius = (size - 6) / 2.0

    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            dist = math.sqrt(dx * dx + dy * dy)

            if dist > radius:
                continue

            norm_dist = dist / radius
            nx = dx / radius
            ny = dy / radius
            nz = math.sqrt(max(0, 1 - nx * nx - ny * ny))

            # 3D sphere lighting
            light_dot = nx * (-0.3) + ny * (-0.4) + nz * 0.8
            light_factor = max(0.15, min(1.0, 0.5 + light_dot * 0.6))

            # Marble veins — rotate sampling point around Y axis (like Earth)
            rot_offset = time_val * 12.0  # horizontal scroll speed in pixels
            marble = _marble_noise(x + rot_offset, y, 0.0)

            # Hue: uniform base + very subtle vein variation
            h = (hue_offset + marble * hue_range * 0.08) % 1.0
            # Saturation: smooth, uniform
            s = saturation * (1.0 - norm_dist * 0.10)
            # Brightness: base + lighting (no pulse/shimmer in idle)
            v = brightness * light_factor

            # Main specular highlight (glass)
            spec_dist = math.sqrt((nx + 0.35) ** 2 + (ny + 0.35) ** 2)
            if spec_dist < 0.38:
                spec = (1.0 - spec_dist / 0.38) ** 2.5 * 0.65
                v = min(1.0, v + spec)
                s = max(0.0, s - spec * 0.8)

            # Pinpoint specular
            spec2_dist = math.sqrt((nx + 0.25) ** 2 + (ny + 0.50) ** 2)
            if spec2_dist < 0.12:
                spec2 = (1.0 - spec2_dist / 0.12) ** 4 * 0.35
                v = min(1.0, v + spec2)
                s = max(0.0, s - spec2)

            # Rim lighting
            if norm_dist > 0.65:
                rim = (norm_dist - 0.65) / 0.35
                v = min(1.0, v + rim ** 1.5 * 0.25)

            r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))

            alpha = 255
            if norm_dist > 0.85:
                alpha = int(255 * (1.0 - (norm_dist - 0.85) / 0.15))

            pixels[x, y] = (int(r * 255), int(g * 255), int(b * 255), alpha)

    glow = img.filter(ImageFilter.GaussianBlur(radius=2.0))
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
        "interval": 0.05,
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

ICON_SIZE = 192  # Crisp + fast enough for on-the-fly generation (~90ms/frame)


class _IconAnimator:
    """Background thread that animates the tray icon.

    ALL states are generated on-the-fly for smooth, fluid animation.
    """

    def __init__(self):
        self._state = "idle"
        self._running = False
        self._thread: threading.Thread | None = None
        self._icon_ref = None
        self._time_val = 0.0
        self._base_hue = 0.0

    def set_icon(self, icon):
        self._icon_ref = icon

    def set_state(self, state: str):
        self._state = state

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._animation_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _animation_loop(self):
        """Main animation loop — all states generated on-the-fly."""
        while self._running:
            preset = _ANIM_PRESETS.get(self._state, _ANIM_PRESETS["idle"])
            interval = preset["interval"]

            try:
                self._time_val += preset["time_speed"]
                if "base_hue" in preset:
                    self._base_hue = preset["base_hue"] + preset["hue_speed"] * self._time_val
                else:
                    self._base_hue += preset["hue_speed"]
                hue = self._base_hue % 1.0

                # Clamp to allowed hue range (e.g. skip red zone for idle)
                hue_min = preset.get("hue_min")
                hue_max = preset.get("hue_max")
                if hue_min is not None and hue_max is not None:
                    hue = hue_min + (hue % 1.0) * (hue_max - hue_min)

                brightness = preset["brightness"]
                if preset.get("pulse"):
                    pulse = 0.5 + 0.5 * math.sin(self._time_val * 3.0)
                    brightness = 0.60 + pulse * 0.40

                frame = _generate_marble_sphere(
                    ICON_SIZE, hue,
                    hue_range=preset["hue_range"],
                    saturation=preset["saturation"],
                    brightness=brightness,
                    time_val=self._time_val,
                )

                if self._icon_ref:
                    self._icon_ref.icon = frame

            except Exception as e:
                logger.debug(f"Animation frame error: {e}")

            time.sleep(interval)


class TrayIcon:
    """System tray icon with animated marble sphere and settings menu."""

    def __init__(
        self,
        on_exit: Callable[[], None] | None = None,
        on_purge_vram: Callable[[], None] | None = None,
        on_stt_mode_changed: Callable[[str], None] | None = None,
        on_mic_changed: Callable[[int | None], None] | None = None,
        transcriber=None,
        recorder=None,
    ):
        self._on_exit = on_exit
        self._on_purge_vram = on_purge_vram
        self._on_stt_mode_changed = on_stt_mode_changed
        self._on_mic_changed = on_mic_changed
        self._transcriber = transcriber
        self._recorder = recorder
        self._icon: "pystray.Icon | None" = None
        self._thread: threading.Thread | None = None
        self._animator = _IconAnimator()

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

    def _build_voice_submenu(self, current_voice_id: str) -> "pystray.Menu":
        """Build voice selection submenu organized by language."""
        lang_submenus = []
        for lang_name, voices in EDGE_VOICES.items():
            flag = LANG_FLAGS.get(lang_name, "")
            voice_items = []
            for v in voices:
                is_current = (v["id"] == current_voice_id)
                gender = E_FEMALE if v["gender"] == "F" else E_MALE
                sel = E_SELECTED if is_current else E_UNSELECTED
                label = f"{sel} {gender} {v['name']}"
                voice_items.append(pystray.MenuItem(
                    label,
                    self._make_set_voice(v["id"]),
                ))
            lang_submenus.append(pystray.MenuItem(
                f"{flag} {lang_name}",
                pystray.Menu(*voice_items),
            ))
        return pystray.Menu(*lang_submenus)

    def _build_menu(self) -> "pystray.Menu":
        config = _load_config()
        current_pack = config.get("sound_pack", "r2d2")
        current_lang = config.get("whisper_language")
        current_voice = config.get("voice", {"voice": "it-IT-IsabellaNeural"})
        current_voice_id = current_voice.get("voice", "it-IT-IsabellaNeural")

        # --- Quick Presets submenu ---
        preset_items = []
        for preset_name, preset_data in VOICE_PRESETS.items():
            is_current = (preset_data["voice"] == current_voice_id)
            sel = E_SELECTED if is_current else E_UNSELECTED
            preset_items.append(pystray.MenuItem(
                f"{sel} {preset_name}",
                self._make_set_voice_preset(preset_name),
            ))

        # --- Full voice browser by language ---
        voice_display = self._voice_display_name(current_voice_id)
        voice_browser = self._build_voice_submenu(current_voice_id)

        # --- Whisper Language submenu ---
        lang_items = []
        for label, code in WHISPER_LANGUAGES.items():
            checked = (current_lang == code)
            sel = E_SELECTED if checked else E_UNSELECTED
            flag = LANG_FLAGS.get(label, "")
            lang_items.append(pystray.MenuItem(
                f"{sel} {flag} {label}",
                self._make_set_language(code),
            ))

        # --- Sound Pack submenu ---
        pack_items = []
        if SOUNDS_DIR.is_dir():
            for pack_dir in sorted(SOUNDS_DIR.iterdir()):
                if pack_dir.is_dir():
                    pack_name = pack_dir.name
                    count = len(list(pack_dir.glob("*.mp3")))
                    checked = (pack_name == current_pack)
                    sel = E_SELECTED if checked else E_UNSELECTED
                    pe = PACK_EMOJI.get(pack_name, "📦")
                    pack_items.append(pystray.MenuItem(
                        f"{sel} {pe} {pack_name} ({count})",
                        self._make_set_sound_pack(pack_name),
                    ))

        # --- TTS Mode submenu ---
        current_mode = config.get("tts_mode", "full")
        mode_items = []
        for mode_label, mode_value in TTS_MODES.items():
            checked = (current_mode == mode_value)
            sel = E_SELECTED if checked else E_UNSELECTED
            me = MODE_EMOJI.get(mode_value, "")
            mode_items.append(pystray.MenuItem(
                f"{sel} {me} {mode_label}",
                self._make_set_tts_mode(mode_value),
            ))

        # --- Volume submenu ---
        current_vol = config.get("volume", 200)
        vol_items = []
        for level in VOLUME_LEVELS:
            checked = (level == current_vol)
            sel = E_SELECTED if checked else E_UNSELECTED
            bar = "█" * (level // 50)
            vol_items.append(pystray.MenuItem(
                f"{sel} {level}% {bar}",
                self._make_set_volume(level),
            ))

        # --- Preview Sounds submenu ---
        preview_items = []
        pack_dir = SOUNDS_DIR / current_pack
        if pack_dir.is_dir():
            for mp3 in sorted(pack_dir.glob("*.mp3"))[:15]:
                preview_items.append(pystray.MenuItem(
                    f"  ▶️ {mp3.stem}",
                    self._make_preview_sound(str(mp3)),
                ))

        # === Resolve status labels for top-level display ===
        stt_status = self._get_stt_status(config)
        stt_engine_item = self._build_stt_engine_menu(config)
        mic_item = self._build_mic_menu(config)

        # Language display
        lang_display = "Auto"
        if current_lang:
            for label, code in WHISPER_LANGUAGES.items():
                if code == current_lang:
                    flag = LANG_FLAGS.get(label, "")
                    lang_display = f"{flag} {label}"
                    break

        # Mic display
        mic_device = config.get("mic_device_id")
        mic_display = "Default"
        if self._recorder and mic_device is not None:
            try:
                for dev in self._recorder.list_devices():
                    if dev.device_id == mic_device:
                        mic_display = dev.name[:25]
                        break
            except Exception:
                mic_display = f"Device {mic_device}"

        # Engine display
        engine_display = stt_status["active_label"]

        # === Build Settings submenu ===
        settings_menu = pystray.Menu(
            # STT Engine (cruscotto)
            stt_engine_item,
            pystray.Menu.SEPARATOR,
            # Microphone
            mic_item,
            pystray.Menu.SEPARATOR,
            # TTS Voice
            pystray.MenuItem(f"{E_VOICE} TTS Voice [{voice_display}]", pystray.Menu(
                pystray.MenuItem(f"{E_PRESETS} Quick Presets", pystray.Menu(*preset_items)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(f"{E_BROWSE} Browse All", voice_browser),
            )),
            # STT Language
            pystray.MenuItem(f"{E_LANG} STT Language [{lang_display}]", pystray.Menu(*lang_items)),
            pystray.Menu.SEPARATOR,
            # System
            pystray.MenuItem(f"{E_PURGE} Purge VRAM", self._purge_vram_clicked),
            pystray.Menu.SEPARATOR,
            # Links
            pystray.MenuItem(f"{E_REPO} GitHub Repo", self._open_repo),
            pystray.MenuItem(f"{E_KEY} Get Groq API Key (free)", self._open_groq_console),
            pystray.MenuItem(f"{E_KEY} Get Deepgram API Key", self._open_deepgram_console),
        )

        # === Mode emoji for status ===
        mode_icon = MODE_EMOJI.get(current_mode, "🔔")
        pack_icon = PACK_EMOJI.get(current_pack, "📦")

        return pystray.Menu(
            pystray.MenuItem("✨ parlaconclaudio v0.9.9.0426 ✨", None, enabled=False),
            pystray.Menu.SEPARATOR,
            # --- Status dashboard (always visible, read-only) ---
            pystray.MenuItem(f"  🧠 {engine_display}  │  {lang_display}  │  🎤 {mic_display}", None, enabled=False),
            pystray.MenuItem(f"  {mode_icon} {current_mode}  │  🔊 {current_vol}%  │  {pack_icon} {current_pack}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            # --- Quick access ---
            pystray.MenuItem(f"🔔 Mode [{current_mode}]", pystray.Menu(*mode_items)),
            pystray.MenuItem(f"🔊 Volume [{current_vol}%]", pystray.Menu(*vol_items)),
            pystray.MenuItem(f"🎵 Sound Pack [{current_pack}]", pystray.Menu(*pack_items)),
            pystray.MenuItem(f"🎧 Preview [{current_pack}]", pystray.Menu(*preview_items) if preview_items else None),
            pystray.Menu.SEPARATOR,
            # --- Settings & Info ---
            pystray.MenuItem("⚙️ Settings & Info", settings_menu),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🚪 Exit", self._exit_clicked),
        )

    # ------------------------------------------------------------------
    # Microphone selector
    # ------------------------------------------------------------------

    def _build_mic_menu(self, config: dict) -> "pystray.MenuItem":
        """Build microphone selection submenu."""
        current_device = config.get("mic_device_id")  # None = default

        mic_items = []
        if self._recorder:
            try:
                devices = self._recorder.list_devices()
                for dev in devices:
                    is_current = (current_device == dev.device_id) or (current_device is None and dev.is_default)
                    sel = E_SELECTED if is_current else E_UNSELECTED
                    default_mark = " (default)" if dev.is_default else ""
                    label = f"{sel} [{dev.device_id}] {dev.name}{default_mark}"
                    mic_items.append(pystray.MenuItem(
                        label,
                        self._make_set_mic(dev.device_id),
                    ))
            except Exception as e:
                logger.warning(f"Failed to list mic devices: {e}")

        if not mic_items:
            mic_items.append(pystray.MenuItem("  No devices found", None, enabled=False))

        current_name = "Default"
        if self._recorder and current_device is not None:
            try:
                for dev in self._recorder.list_devices():
                    if dev.device_id == current_device:
                        current_name = dev.name[:20]
                        break
            except Exception:
                current_name = f"Device {current_device}"

        return pystray.MenuItem(
            f"🎤 Microphone [{current_name}]",
            pystray.Menu(*mic_items),
        )

    def _make_set_mic(self, device_id: int):
        def handler(icon, item):
            config = _load_config()
            config["mic_device_id"] = device_id
            _save_config(config)
            if self._on_mic_changed:
                self._on_mic_changed(device_id)
            logger.info(f"Microphone set to device {device_id}")
            self._rebuild_menu()
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
            gpu_state = "loading"
        else:
            gpu_state = "cloud"  # GPU present but using cloud

        # Determine display label for active backend
        _labels = {
            "whisper_local": "Local GPU",
            "groq": "Groq",
            "deepgram": "Deepgram",
            "disabled": "Disabled",
            "not_loaded": "Loading...",
        }
        active_label = _labels.get(active, active)

        return {
            "mode": stt_mode,
            "active": active,
            "active_label": active_label,
            "gpu": gpu,
            "gpu_state": gpu_state,
        }

    def _build_stt_engine_menu(self, config: dict) -> "pystray.MenuItem":
        """Build the STT Engine cruscotto submenu."""
        status = self._get_stt_status(config)
        stt_mode = status["mode"]

        # Title shows resolved backend: [Auto → Groq] or [Local GPU]
        if stt_mode == "auto":
            title_suffix = f"Auto → {status['active_label']}"
        else:
            title_suffix = status["active_label"]

        # Mode selector items
        mode_items = []
        for mode_key, mode_label in STT_MODES.items():
            is_current = (stt_mode == mode_key)
            sel = E_SELECTED if is_current else E_UNSELECTED
            mode_items.append(pystray.MenuItem(
                f"{sel} {mode_label}",
                self._make_set_stt_mode(mode_key),
            ))

        # Status dashboard (read-only)
        gpu_state = status["gpu_state"]
        gpu_icons = {"loaded": "✅", "loading": "⏳", "not_detected": "❌", "cloud": "☁️"}
        gpu_mark = gpu_icons.get(gpu_state, "❓")

        from ..core.stt_engine.key_manager import KeyManager
        has_groq = bool(KeyManager.get_key("groq"))
        has_deepgram = bool(KeyManager.get_key("deepgram"))
        groq_mark = "✅" if has_groq else "❌"
        deepgram_mark = "✅" if has_deepgram else "❌"

        status_items = [
            pystray.MenuItem(f"  Provider: {status['active_label']}", None, enabled=False),
            pystray.MenuItem(f"  GPU: {gpu_mark} {status['gpu']}", None, enabled=False),
            pystray.MenuItem(f"  Groq key: {groq_mark}", None, enabled=False),
            pystray.MenuItem(f"  Deepgram key: {deepgram_mark}", None, enabled=False),
        ]

        # API key items
        key_items = [
            pystray.MenuItem(f"{E_KEY} Set Groq API Key...", self._ask_api_key_groq),
            pystray.MenuItem(f"{E_KEY} Set Deepgram API Key...", self._ask_api_key_deepgram),
        ]

        # Test connection
        test_item = pystray.MenuItem(f"{E_TEST} Test Connection", self._test_cloud_connection)

        # Warning if disabled
        warn_items = []
        if status["active"] == "disabled":
            warn_items.append(pystray.MenuItem("⚠️ Configure cloud STT below", None, enabled=False))

        return pystray.MenuItem(
            f"{E_ENGINE} STT Engine [{title_suffix}]",
            pystray.Menu(
                *warn_items,
                *mode_items,
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(f"{E_STATUS} Status", pystray.Menu(*status_items)),
                pystray.Menu.SEPARATOR,
                *key_items,
                test_item,
            ),
        )

    def _make_set_stt_mode(self, mode: str):
        def handler(icon, item):
            if self._on_stt_mode_changed:
                self._on_stt_mode_changed(mode)
            else:
                # Direct config write if no callback
                config = _load_config()
                config["stt_mode"] = mode
                _save_config(config)
            logger.info(f"STT mode set to: {mode}")
            self._rebuild_menu()
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
                    self._rebuild_menu()
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
            self._rebuild_menu()
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
            self._rebuild_menu()
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
            self._rebuild_menu()
        return handler

    def _make_set_volume(self, level: int):
        def handler(icon, item):
            config = _load_config()
            config["volume"] = level
            _save_config(config)
            logger.info(f"Volume set to: {level}%")
            self._rebuild_menu()
        return handler

    def _make_set_tts_mode(self, mode_value: str):
        def handler(icon, item):
            config = _load_config()
            config["tts_mode"] = mode_value
            _save_config(config)
            logger.info(f"TTS mode set to: {mode_value}")
            self._rebuild_menu()
        return handler

    def _make_set_sound_pack(self, pack_name):
        def handler(icon, item):
            config = _load_config()
            config["sound_pack"] = pack_name
            _save_config(config)
            logger.info(f"Sound pack set to: {pack_name}")
            self._rebuild_menu()
        return handler

    def _make_preview_sound(self, filepath):
        def handler(icon, item):
            _play_sound(filepath)
        return handler

    def _rebuild_menu(self):
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.update_menu()

    def start(self) -> None:
        if not TRAY_AVAILABLE:
            logger.warning("Tray icon not available (install pystray Pillow)")
            return

        # Create initial static icon (animator will replace it immediately)
        initial_img = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(initial_img)
        draw.ellipse([4, 4, ICON_SIZE - 4, ICON_SIZE - 4], fill=(0, 200, 100, 255))

        self._icon = pystray.Icon(
            "voice_bridge",
            initial_img,
            "parlaconclaudio - Ready",
            self._build_menu(),
        )

        # Start animator
        self._animator.set_icon(self._icon)
        self._animator.set_state("idle")
        self._animator.start()

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Tray icon started with animated marble sphere")

    def set_state(self, state: str) -> None:
        if not self._icon:
            return
        title_map = {
            "loading": "parlaconclaudio - Loading STT engine...",
            "idle": "parlaconclaudio - Ready",
            "recording": "parlaconclaudio - Recording...",
            "transcribing": "parlaconclaudio - Transcribing...",
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

    def _exit_clicked(self, icon, item):
        if self._on_exit:
            self._on_exit()
        self.stop()

    def stop(self) -> None:
        self._animator.stop()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
