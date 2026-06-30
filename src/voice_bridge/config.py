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
    mic_device_id: int | None = None

    # Sound feedback
    sound_on_start: bool = True
    sound_on_stop: bool = True
    sound_on_output: bool = True
    volume: int = 200
    muted: bool = False

    # STT engine orchestration
    stt_mode: str = "auto"            # "auto" | "local" | "cloud_groq" | "cloud_deepgram"
    stt_auto_fallback: bool = True    # in mode "local", fallback to cloud if GPU unavailable

    # STT cleanup agent (language-preserving LLM post-processing via OpenRouter).
    # Mirrors keys in tts_config.json; AudioFileProcessor reads those at runtime.
    # gemini-2.5-flash is the only model that recovers severe garbles ("di tab"
    # -> "GitHub", 4/5); qwen stays as the cheap fallback.
    enable_stt_cleanup: bool = True
    stt_cleanup_model: str = "google/gemini-2.5-flash"
    stt_cleanup_fallback: str = "qwen/qwen-2.5-7b-instruct"
    # Optional speaker-specific terms (list or csv string) grounded into the
    # cleanup prompt so severe phonetic garbles map to the intended term.
    stt_cleanup_glossary: list[str] = field(default_factory=list)

    # Audio-file pipeline: extra-output gating.
    # - transcribe_translations: produce EN/IT/PT translations alongside the
    #   source-language transcription.
    # - transcribe_tts_dub: synthesize spoken TTS dubs for the translations.
    # - transcribe_extras_max_chars: above this length only the (cleaned)
    #   transcription is produced — translations + dubs are skipped (0 = no cap).
    transcribe_translations: bool = True
    transcribe_tts_dub: bool = True
    transcribe_extras_max_chars: int = 2500

    # Recording timeout: auto-stop if recording exceeds this (seconds)
    recording_timeout: int = 180  # 3 minutes

    # Sound frequencies (Hz) and durations (ms)
    sound_start_freq: int = 800
    sound_start_duration: int = 150
    sound_stop_freq: int = 600
    sound_stop_duration: int = 150
    sound_output_freq: int = 1000
    sound_output_duration: int = 100

    # Gesture triggers (mouse on tray sphere + Caps Lock long-press)
    gesture_enabled: bool = True
    gesture_mouse_long_press_ms: int = 480
    gesture_caps_long_press_ms: int = 660
    gesture_double_click_ms: int = 350
    gesture_debounce_ms: int = 200
