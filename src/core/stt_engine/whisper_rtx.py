"""
WhisperRTXEngine - Optimized Whisper inference for NVIDIA RTX GPUs.

This module provides a high-performance speech-to-text engine using
Whisper Large V3 with FasterWhisper, optimized for RTX 5080 GPUs.
"""

import logging
from typing import List, Dict, Optional, Union, Tuple
from pathlib import Path
import numpy as np

from .base import STTEngine, TranscriptionSegment, TranscriptionResult  # noqa: F401 — re-exported for backward compat

logger = logging.getLogger(__name__)


class WhisperRTXEngine(STTEngine):
    """
    High-performance Whisper STT engine optimized for NVIDIA RTX GPUs.

    Features:
    - FasterWhisper with CTranslate2 backend
    - Float16 precision for RTX 5080 optimization
    - Batch processing support
    - Timestamp-accurate transcription
    - VAD (Voice Activity Detection) filtering
    """

    @property
    def name(self) -> str:
        return "whisper_local"

    @property
    def is_local(self) -> bool:
        return True

    @property
    def supports_word_timestamps(self) -> bool:
        return True

    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "cuda",
        compute_type: str = "float16",
        num_workers: int = 1,
        download_root: Optional[str] = None,
        language: Optional[str] = None,
        beam_size: int = 5,
        vad_filter: bool = True,
        vad_parameters: Optional[Dict] = None
    ):
        """
        Initialize WhisperRTXEngine.

        Args:
            model_name: Whisper model size (default: "large-v3")
            device: Device to use - "cuda" or "cpu" (default: "cuda")
            compute_type: Precision - "float16", "int8", "int8_float16" (default: "float16")
            num_workers: Number of CPU workers for preprocessing (default: 1)
            download_root: Custom model cache directory
            language: Target language code (e.g., "it", "en"). None for auto-detect
            beam_size: Beam search size (default: 5, higher = more accurate but slower)
            vad_filter: Enable Voice Activity Detection filtering (default: True)
            vad_parameters: Custom VAD parameters dict
        """
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.num_workers = num_workers
        self.download_root = download_root
        self.language = language
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.vad_parameters = vad_parameters or {}
        self.model = None

        logger.info(f"Initializing WhisperRTXEngine...")
        logger.info(f"Model: {model_name}, Device: {device}, Compute: {compute_type}")
        self._load_model()

    def _load_model(self):
        """Load the Whisper model into memory."""
        try:
            from faster_whisper import WhisperModel

            logger.info("Loading Whisper model...")

            self.model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                num_workers=self.num_workers,
                download_root=self.download_root
            )

            logger.info("Model loaded successfully!")

        except ImportError as e:
            logger.error("faster-whisper not installed. Run: pip install faster-whisper")
            raise RuntimeError(f"Failed to import faster-whisper: {e}")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def transcribe(
        self,
        audio: Union[str, Path, np.ndarray],
        language: Optional[str] = None,
        task: str = "transcribe",
        beam_size: Optional[int] = None,
        vad_filter: Optional[bool] = None,
        word_timestamps: bool = True,
        initial_prompt: Optional[str] = None,
        temperature: Union[float, List[float]] = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    ) -> TranscriptionResult:
        """
        Transcribe audio to text with timestamps.

        Args:
            audio: Audio file path or numpy array (shape: [samples] or [channels, samples])
            language: Override default language (e.g., "it", "en")
            task: "transcribe" or "translate" (default: "transcribe")
            beam_size: Override default beam size
            vad_filter: Override default VAD filter setting
            word_timestamps: Return word-level timestamps (default: True)
            initial_prompt: Optional prompt to guide the model
            temperature: Temperature for sampling (single value or list for fallback)

        Returns:
            TranscriptionResult: Object containing full text, segments with timestamps, and metadata
        """
        if self.model is None:
            raise RuntimeError("Model not loaded. Call _load_model() first.")

        # Use instance defaults if not specified
        language = language or self.language
        beam_size = beam_size or self.beam_size
        vad_filter = vad_filter if vad_filter is not None else self.vad_filter

        logger.info(f"Transcribing audio...")
        logger.info(f"Language: {language or 'auto'}, Task: {task}, Beam size: {beam_size}")

        try:
            # Transcribe with faster-whisper
            segments, info = self.model.transcribe(
                audio,
                language=language,
                task=task,
                beam_size=beam_size,
                vad_filter=vad_filter,
                vad_parameters=self.vad_parameters if vad_filter else None,
                word_timestamps=word_timestamps,
                initial_prompt=initial_prompt,
                temperature=temperature,
            )

            # Convert generator to list and build result
            result_segments = []
            full_text_parts = []

            for segment in segments:
                result_segments.append(TranscriptionSegment(
                    text=segment.text,
                    start=segment.start,
                    end=segment.end,
                    confidence=segment.avg_logprob if hasattr(segment, 'avg_logprob') else None,
                    tokens=segment.tokens if hasattr(segment, 'tokens') else None
                ))
                full_text_parts.append(segment.text)

            full_text = " ".join(full_text_parts).strip()

            result = TranscriptionResult(
                text=full_text,
                segments=result_segments,
                language=info.language,
                duration=info.duration if hasattr(info, 'duration') else None,
                provider=self.name,
                model=self.model_name,
            )

            logger.info(f"Transcription completed: {len(result_segments)} segments")
            logger.info(f"Detected language: {result.language}")

            return result

        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            raise

    def transcribe_batch(
        self,
        audio_files: List[Union[str, Path]],
        **kwargs
    ) -> List[TranscriptionResult]:
        """
        Transcribe multiple audio files.

        Args:
            audio_files: List of audio file paths
            **kwargs: Additional arguments passed to transcribe()

        Returns:
            List[TranscriptionResult]: List of transcription results
        """
        logger.info(f"Batch transcribing {len(audio_files)} files...")

        results = []
        for i, audio_file in enumerate(audio_files):
            logger.info(f"Processing file {i+1}/{len(audio_files)}: {audio_file}")
            try:
                result = self.transcribe(audio_file, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to transcribe {audio_file}: {e}")
                # Add empty result as placeholder
                results.append(TranscriptionResult(
                    text="",
                    segments=[],
                    language="unknown",
                    duration=None
                ))

        logger.info(f"Batch transcription completed: {len(results)} results")
        return results

    def get_model_info(self) -> Dict:
        """
        Get information about the loaded model.

        Returns:
            Dict: Model information including name, device, compute type
        """
        return {
            "model_name": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "num_workers": self.num_workers,
            "language": self.language,
            "beam_size": self.beam_size,
            "vad_filter": self.vad_filter,
            "is_loaded": self.model is not None
        }

    def purge_vram(self) -> None:
        """Unload model, clear CUDA cache, and reload fresh into VRAM."""
        import gc
        logger.info("VRAM purge: unloading Whisper model...")
        if self.model is not None:
            del self.model
            self.model = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                logger.info("VRAM purge: CUDA cache cleared")
        except ImportError:
            pass
        logger.info("VRAM purge: reloading model...")
        self._load_model()
        logger.info("VRAM purge: model reloaded fresh")

    def __del__(self):
        """Cleanup on deletion."""
        if self.model is not None:
            logger.info("Unloading Whisper model...")
            del self.model
            self.model = None


def create_engine(
    model_name: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
    **kwargs
) -> WhisperRTXEngine:
    """
    Factory function to create a WhisperRTXEngine instance.

    Args:
        model_name: Whisper model size
        device: Device to use
        compute_type: Computation precision
        **kwargs: Additional arguments for WhisperRTXEngine

    Returns:
        WhisperRTXEngine: Initialized engine instance
    """
    return WhisperRTXEngine(
        model_name=model_name,
        device=device,
        compute_type=compute_type,
        **kwargs
    )
