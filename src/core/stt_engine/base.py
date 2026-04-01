"""
STT Engine base classes and shared types.

Abstract interface for all STT engines (local GPU and cloud providers).
"""

import io
import logging
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Union

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """A single transcription segment with timing information."""
    text: str
    start: float
    end: float
    confidence: Optional[float] = None
    tokens: Optional[list[int]] = None


@dataclass
class TranscriptionResult:
    """Complete transcription result with metadata."""
    text: str
    segments: list[TranscriptionSegment]
    language: str
    duration: Optional[float] = None
    provider: str = ""
    model: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class STTEngine(ABC):
    """Abstract interface for all STT engines."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Engine identifier (e.g. 'whisper_local', 'groq', 'deepgram')."""
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True if engine runs on local hardware (GPU/CPU)."""
        ...

    @property
    def supports_segments(self) -> bool:
        return True

    @property
    def supports_word_timestamps(self) -> bool:
        return False

    @abstractmethod
    def transcribe(
        self,
        audio: Union[np.ndarray, str],
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        ...

    def cleanup(self) -> None:
        """Release resources. Override in subclasses if needed."""
        pass


class NullSTTEngine(STTEngine):
    """Placeholder engine when no backend is available. Returns empty results, never crashes."""

    _warned = False

    @property
    def name(self) -> str:
        return "disabled"

    @property
    def is_local(self) -> bool:
        return False

    @property
    def supports_segments(self) -> bool:
        return False

    def transcribe(self, audio, language=None) -> TranscriptionResult:
        if not NullSTTEngine._warned:
            logger.warning("STT disabled: no GPU detected and no cloud API key configured")
            NullSTTEngine._warned = True
        return TranscriptionResult(
            text="",
            segments=[],
            language="",
            provider="disabled",
            error="disabled",
        )


def ndarray_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert a float32 numpy audio array to WAV bytes (PCM16, mono).

    Handles stereo fold, clipping, and empty arrays.
    """
    if audio.size == 0:
        return b""

    # Stereo → mono fold
    if audio.ndim == 2:
        audio = audio.mean(axis=0)

    # Ensure 1-D
    audio = audio.ravel()

    # Clip to [-1, 1] and convert to PCM16
    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()
