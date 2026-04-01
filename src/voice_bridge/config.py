"""
Voice Bridge Configuration.
"""

from dataclasses import dataclass, field


@dataclass
class VoiceBridgeConfig:
    # Hotkey (pynput format)
    hotkey: str = "<ctrl>+<alt>+<space>"

    # Mode: push_to_talk | vad_continuous (P1)
    mode: str = "push_to_talk"

    # Hotkey mode: toggle (press once start, press again stop) | push_to_talk (hold to record)
    hotkey_mode: str = "toggle"

    # Whisper engine settings
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    whisper_language: str | None = None  # None = auto-detect

    # Output mode: clipboard_paste | clipboard_only | type_keys
    output_mode: str = "clipboard_paste"
    auto_submit: bool = False  # If True, press Enter after paste

    # Audio settings
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024

    # Sound feedback
    sound_on_start: bool = True
    sound_on_stop: bool = True
    sound_on_output: bool = True

    # STT engine orchestration
    stt_mode: str = "auto"            # "auto" | "local" | "cloud_groq" | "cloud_deepgram"
    stt_auto_fallback: bool = True    # in mode "local", fallback to cloud if GPU unavailable

    # Sound frequencies (Hz) and durations (ms)
    sound_start_freq: int = 800
    sound_start_duration: int = 150
    sound_stop_freq: int = 600
    sound_stop_duration: int = 150
    sound_output_freq: int = 1000
    sound_output_duration: int = 100
