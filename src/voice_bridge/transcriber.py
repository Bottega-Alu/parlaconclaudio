"""
Transcriber - Wrapper around WhisperRTXEngine for voice bridge.

Handles lazy model loading and provides a simple transcribe(audio) -> str interface.
Reads whisper_language from shared tts_config.json so tray menu changes take effect.
"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"


class Transcriber:
    """Thin wrapper around WhisperRTXEngine for push-to-talk transcription."""

    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        language: str | None = None,
    ):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._engine = None

    def _ensure_engine(self) -> None:
        """Lazily load WhisperRTX engine (first call takes a few seconds)."""
        if self._engine is not None:
            return
        from ..core.stt_engine.whisper_rtx import WhisperRTXEngine

        logger.info(f"Loading Whisper model: {self._model_name} on {self._device}...")
        self._engine = WhisperRTXEngine(
            model_name=self._model_name,
            device=self._device,
            compute_type=self._compute_type,
            language=self._language,
            vad_filter=True,
        )
        logger.info("Whisper model loaded and ready")

    def _get_active_language(self) -> str | None:
        """Read whisper_language from tts_config.json (tray menu setting)."""
        try:
            if TTS_CONFIG.is_file():
                config = json.loads(TTS_CONFIG.read_text())
                return config.get("whisper_language", self._language)
        except Exception:
            pass
        return self._language

    def transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe a numpy float32 audio array to text.

        Args:
            audio: float32 numpy array, mono, at the engine's expected sample rate

        Returns:
            Transcribed text string (empty string if nothing detected)
        """
        if audio.size == 0:
            return ""

        self._ensure_engine()

        # Read language from shared config (tray menu can change it)
        language = self._get_active_language()

        try:
            result = self._engine.transcribe(
                audio,
                language=language,
                beam_size=5,
                vad_filter=True,
                word_timestamps=False,
            )
            text = result.text.strip()
            logger.info(f"Transcription: '{text}' (lang={result.language}, requested={language or 'auto'})")
            return text
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    def purge_vram(self) -> None:
        """Purge VRAM: unload model, clear CUDA cache, reload fresh."""
        if self._engine is None:
            logger.info("No engine loaded, nothing to purge")
            return
        self._engine.purge_vram()
        logger.info("VRAM purge complete - model reloaded")

    def cleanup(self) -> None:
        """Release model resources."""
        if self._engine:
            del self._engine
            self._engine = None
