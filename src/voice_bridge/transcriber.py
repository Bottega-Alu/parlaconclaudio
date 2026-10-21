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
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

TTS_CONFIG = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.operation():
            return method(self, *args, **kwargs)
    return wrapped


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
        self._gpu_info = None  # GpuInfo, populated by _detect_gpu()
        self._engine_lock = threading.RLock()
        self._operation_depth = 0
        self.last_error: str | None = None

    @property
    def busy(self) -> bool:
        """Whether inference or engine maintenance is currently in progress."""
        return self._operation_depth > 0

    @contextmanager
    def operation(self):
        """Own the engine for an operation, including a complete file batch.

        Reentrant so batch callers can safely invoke transcribe_file/cleanup.
        All lifecycle mutations share this lock with inference.
        """
        with self._engine_lock:
            self._operation_depth += 1
            try:
                yield
            finally:
                self._operation_depth -= 1

    # ------------------------------------------------------------------
    # GPU detection
    # ------------------------------------------------------------------

    def _detect_gpu(self) -> bool:
        """Check CUDA availability via ctranslate2 + nvidia-smi. Result is cached.

        ctranslate2 is what actually runs inference, so its device count is the
        authoritative answer; torch used to be imported here purely for the check.
        """
        if self._gpu_available is not None:
            return self._gpu_available
        from src.core.gpu_probe import probe_gpu
        self._gpu_info = probe_gpu()
        self._gpu_available = self._gpu_info.cuda_usable
        if self._gpu_available:
            logger.info(f"GPU detected: {self._gpu_info.name}")
        else:
            logger.info("No CUDA GPU detected")
        return self._gpu_available

    def gpu_name(self) -> str:
        """Return GPU name or 'Not detected'."""
        if not self._detect_gpu():
            return "Not detected"
        return self._gpu_info.name if self._gpu_info else "Unknown GPU"

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

    def _get_system_language(self) -> str | None:
        """Fallback language when no explicit ``whisper_language`` is set.

        Default is ``None`` → let Whisper auto-detect. The previous hardcoded
        ``"it"`` (and the OS-locale derivation that resolves to ``"it"`` on an
        Italian box) biased detection toward Italian and forced IT output even
        for English / mixed speech — the root of the language-flip bug.

        Opt back into OS-locale derivation by setting
        ``"whisper_locale_fallback": true`` in tts_config.json.
        """
        if not self._load_config().get("whisper_locale_fallback", False):
            return None  # auto-detect
        import locale
        try:
            loc = locale.getlocale()[0]  # e.g. "it_IT", "en_US"
            if loc:
                return loc.split("_")[0]  # "it", "en"
        except Exception:
            pass
        return None

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

    @_serialized
    def _ensure_engine(self) -> None:
        """Lazily resolve and load the STT engine."""
        if self._engine is not None:
            return
        self._engine = self._resolve_engine()
        logger.info(f"STT engine ready: {self._engine.name}")

    def active_engine_name(self) -> str:
        """Return the name of the currently loaded engine (or 'not_loaded')."""
        engine = self._engine
        return engine.name if engine else "not_loaded"

    @_serialized
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

    @_serialized
    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio array to text. Returns empty string on failure or no speech."""
        self.last_error = None
        if audio.size == 0:
            return ""

        language = self._get_active_language()
        try:
            self._ensure_engine()
            result = self._transcribe_with(self._engine, audio, language)

            text = result.text.strip()

            if result.error:
                logger.warning(f"STT [{result.provider}] error: {result.error}")
                self.last_error = str(result.error)
                return self._runtime_fallback(audio, language)
            else:
                latency = f", {result.latency_ms}ms" if result.latency_ms else ""
                logger.info(
                    f"Transcription [{result.provider}]: '{text}' "
                    f"(lang={result.language}, requested={language or 'auto'}{latency})"
                )

            return text

        except Exception as e:
            self.last_error = str(e)
            logger.error("Transcription failed: %s", e)
            return self._runtime_fallback(audio, language)

    @staticmethod
    def _transcribe_with(engine, audio, language):
        kwargs = {"language": language}
        if engine.is_local:
            kwargs.update(beam_size=5, vad_filter=True, word_timestamps=False)
        return engine.transcribe(audio, **kwargs)

    def _runtime_fallback(self, audio, language) -> str:
        """Try each configured alternate once; never retry a valid silence result."""
        if not self._get_auto_fallback():
            return ""
        from ..core.stt_engine.key_manager import KeyManager
        current = self.active_engine_name()
        for provider, factory in (("groq", self._make_groq_engine),
                                  ("deepgram", self._make_deepgram_engine)):
            if provider == current:
                continue
            key = KeyManager.get_key(provider)
            if not key:
                continue
            fallback = None
            try:
                fallback = factory(key)
                result = self._transcribe_with(fallback, audio, language)
                if not result.error:
                    self.last_error = None
                    logger.info("Runtime STT fallback succeeded: %s", provider)
                    return (result.text or "").strip()
                self.last_error = str(result.error)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("Runtime STT fallback [%s] failed: %s", provider, exc)
            finally:
                if fallback is not None:
                    try:
                        fallback.cleanup()
                    except Exception:
                        logger.warning("Fallback cleanup failed", exc_info=True)
        return ""

    @_serialized
    def transcribe_file(
        self,
        path: Path,
        language: str | None = None,
        task: str = "transcribe",
    ) -> tuple[str, str]:
        """Transcribe an audio file from disk.

        Returns (text, detected_language). Works only with the local
        Whisper engine (cloud engines don't get a path interface in
        this codebase). Caller should fall back to numpy buffer if
        the engine isn't local.

        Args:
            path: Path to audio file (.ogg, .mp3, .wav, .m4a, .opus, ...)
            language: ISO code to force (e.g. "it") or None to auto-detect.
            task: "transcribe" (output in source language) or "translate"
                  (Whisper-native translation to English).
        """
        self._ensure_engine()
        if not self._engine.is_local:
            raise RuntimeError(
                "transcribe_file requires the local Whisper engine; "
                f"current active engine is '{self._engine.name}'"
            )
        result = self._engine.transcribe(
            str(path),
            language=language,
            task=task,
            beam_size=5,
            vad_filter=True,
            word_timestamps=False,
        )
        if result.error:
            raise RuntimeError(f"STT file transcription failed: {result.error}")
        text = (result.text or "").strip()
        detected = result.language or "unknown"
        logger.info(
            f"transcribe_file [{path.name}] task={task} "
            f"lang={detected} ({len(text)} chars)"
        )
        return text, detected

    # ------------------------------------------------------------------
    # VRAM management
    # ------------------------------------------------------------------

    @_serialized
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

    @_serialized
    def unload_vram(self) -> None:
        """Release the local model until next use, without reloading it."""
        if self._engine is not None and self._engine.is_local:
            self.cleanup()

    @_serialized
    def cleanup(self) -> None:
        """Release engine resources."""
        if self._engine:
            self._engine.cleanup()
            self._engine = None
