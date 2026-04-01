"""
Transcriber - Multi-backend STT orchestration for voice bridge.

Handles lazy engine loading, automatic fallback, and runtime switching.
Reads stt_mode and whisper_language from shared tts_config.json.

Decision tree (mode → engine):
  auto         → local GPU if available, else Groq if key, else Deepgram if key, else disabled
  local        → local GPU if available, else cloud fallback if auto_fallback, else disabled
  cloud_groq   → Groq if key, else disabled
  cloud_deepgram → Deepgram if key, else disabled
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"


class Transcriber:
    """Multi-backend STT orchestrator with push-to-talk interface."""

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
        self._gpu_available: bool | None = None  # cached after first check

    # ------------------------------------------------------------------
    # GPU detection
    # ------------------------------------------------------------------

    def _detect_gpu(self) -> bool:
        """Check CUDA availability. Result is cached."""
        if self._gpu_available is not None:
            return self._gpu_available
        try:
            import torch
            self._gpu_available = torch.cuda.is_available()
        except Exception:
            self._gpu_available = False
        if self._gpu_available:
            try:
                import torch
                gpu_name = torch.cuda.get_device_name(0)
                logger.info(f"GPU detected: {gpu_name}")
            except Exception:
                pass
        else:
            logger.info("No CUDA GPU detected")
        return self._gpu_available

    def gpu_name(self) -> str:
        """Return GPU name or 'Not detected'."""
        if not self._detect_gpu():
            return "Not detected"
        try:
            import torch
            return torch.cuda.get_device_name(0)
        except Exception:
            return "Unknown GPU"

    # ------------------------------------------------------------------
    # Config reading
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            if TTS_CONFIG.is_file():
                return json.loads(TTS_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _get_active_language(self) -> str | None:
        """Read whisper_language from tts_config.json.

        Falls back to OS locale so both local Whisper and cloud engines
        start with the user's system language instead of blind auto-detect.
        """
        lang = self._load_config().get("whisper_language", self._language)
        if not lang:
            lang = self._get_system_language()
        return lang

    def _get_active_mode(self) -> str:
        """Read stt_mode from tts_config.json."""
        return self._load_config().get("stt_mode", "auto")

    @staticmethod
    def _get_system_language() -> str:
        """Derive a Whisper-compatible language code from the OS locale."""
        import locale
        try:
            loc = locale.getdefaultlocale()[0]  # e.g. "it_IT", "en_US"
            if loc:
                return loc.split("_")[0]  # "it", "en"
        except Exception:
            pass
        return "it"  # safe default

    def _get_auto_fallback(self) -> bool:
        return self._load_config().get("stt_auto_fallback", True)

    # ------------------------------------------------------------------
    # Engine resolution (the decision tree)
    # ------------------------------------------------------------------

    def _resolve_engine(self):
        """Resolve which engine to use based on mode, GPU, and available keys."""
        from ..core.stt_engine.base import NullSTTEngine
        from ..core.stt_engine.key_manager import KeyManager

        mode = self._get_active_mode()
        gpu_ok = self._detect_gpu()

        if mode == "auto":
            if gpu_ok:
                return self._make_local_engine()
            groq_key = KeyManager.get_key("groq")
            if groq_key:
                logger.info("Auto mode: GPU unavailable, using Groq cloud STT")
                return self._make_groq_engine(groq_key)
            deepgram_key = KeyManager.get_key("deepgram")
            if deepgram_key:
                logger.info("Auto mode: GPU unavailable, using Deepgram cloud STT")
                return self._make_deepgram_engine(deepgram_key)
            logger.warning("Auto mode: no GPU and no cloud API keys — STT disabled")
            return NullSTTEngine()

        if mode == "local":
            if gpu_ok:
                return self._make_local_engine()
            if self._get_auto_fallback():
                groq_key = KeyManager.get_key("groq")
                if groq_key:
                    logger.info("Local mode: GPU unavailable, auto-fallback to Groq")
                    return self._make_groq_engine(groq_key)
                deepgram_key = KeyManager.get_key("deepgram")
                if deepgram_key:
                    logger.info("Local mode: GPU unavailable, auto-fallback to Deepgram")
                    return self._make_deepgram_engine(deepgram_key)
            logger.warning("Local mode: GPU unavailable and no fallback — STT disabled")
            return NullSTTEngine()

        if mode == "cloud_groq":
            groq_key = KeyManager.get_key("groq")
            if groq_key:
                return self._make_groq_engine(groq_key)
            logger.warning("Cloud Groq mode: no API key configured — STT disabled")
            return NullSTTEngine()

        if mode == "cloud_deepgram":
            deepgram_key = KeyManager.get_key("deepgram")
            if deepgram_key:
                return self._make_deepgram_engine(deepgram_key)
            logger.warning("Cloud Deepgram mode: no API key configured — STT disabled")
            return NullSTTEngine()

        logger.warning(f"Unknown stt_mode '{mode}' — STT disabled")
        return NullSTTEngine()

    def _make_local_engine(self):
        from ..core.stt_engine.whisper_rtx import WhisperRTXEngine
        logger.info(f"Loading local Whisper: {self._model_name} on {self._device}")
        return WhisperRTXEngine(
            model_name=self._model_name,
            device=self._device,
            compute_type=self._compute_type,
            language=self._language,
            vad_filter=True,
        )

    def _make_groq_engine(self, api_key: str):
        from ..core.stt_engine.groq_stt import GroqSTTEngine
        return GroqSTTEngine(api_key=api_key)

    def _make_deepgram_engine(self, api_key: str):
        from ..core.stt_engine.deepgram_stt import DeepgramSTTEngine
        return DeepgramSTTEngine(api_key=api_key)

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> None:
        """Lazily resolve and load the STT engine."""
        if self._engine is not None:
            return
        self._engine = self._resolve_engine()
        logger.info(f"STT engine ready: {self._engine.name}")

    def active_engine_name(self) -> str:
        """Return the name of the currently loaded engine (or 'not_loaded')."""
        return self._engine.name if self._engine else "not_loaded"

    def switch_engine(self, mode: str) -> None:
        """Switch STT mode at runtime. Cleans up current engine first."""
        logger.info(f"Switching STT mode to: {mode}")
        self.cleanup()
        # Persist mode to config
        config = self._load_config()
        config["stt_mode"] = mode
        TTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        TTS_CONFIG.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        # Engine will be resolved lazily on next transcribe()

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array to text. Returns empty string on failure or no speech."""
        if audio.size == 0:
            return ""

        self._ensure_engine()

        language = self._get_active_language()

        try:
            if self._engine.is_local:
                result = self._engine.transcribe(
                    audio,
                    language=language,
                    beam_size=5,
                    vad_filter=True,
                    word_timestamps=False,
                )
            else:
                result = self._engine.transcribe(audio, language=language)

            text = result.text.strip()

            if result.error:
                logger.warning(f"STT [{result.provider}] error: {result.error}")
            else:
                latency = f", {result.latency_ms}ms" if result.latency_ms else ""
                logger.info(
                    f"Transcription [{result.provider}]: '{text}' "
                    f"(lang={result.language}, requested={language or 'auto'}{latency})"
                )

            return text

        except Exception as e:
            logger.error(f"Transcription failed [{self._engine.name}]: {e}")
            return ""

    # ------------------------------------------------------------------
    # VRAM management
    # ------------------------------------------------------------------

    def purge_vram(self) -> None:
        """Purge VRAM — only meaningful for local GPU engine."""
        if self._engine is None:
            logger.info("No engine loaded, nothing to purge")
            return
        if not self._engine.is_local:
            logger.info("Cloud engine active — VRAM purge not applicable")
            return
        self._engine.purge_vram()
        logger.info("VRAM purge complete — model reloaded")

    def cleanup(self) -> None:
        """Release engine resources."""
        if self._engine:
            self._engine.cleanup()
            del self._engine
            self._engine = None
