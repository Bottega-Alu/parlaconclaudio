"""
STT (Speech-to-Text) Engine module.

Multi-backend STT orchestration: local GPU (Whisper/FasterWhisper),
cloud providers (Groq, Deepgram), with automatic fallback.
"""

from .base import (
    STTEngine,
    NullSTTEngine,
    TranscriptionSegment,
    TranscriptionResult,
    ndarray_to_wav_bytes,
)
from .whisper_rtx import WhisperRTXEngine, create_engine
from .groq_stt import GroqSTTEngine
from .deepgram_stt import DeepgramSTTEngine
from .key_manager import KeyManager

__all__ = [
    # Base
    "STTEngine",
    "NullSTTEngine",
    "TranscriptionSegment",
    "TranscriptionResult",
    "ndarray_to_wav_bytes",
    # Engines
    "WhisperRTXEngine",
    "GroqSTTEngine",
    "DeepgramSTTEngine",
    "create_engine",
    # Key management
    "KeyManager",
]
