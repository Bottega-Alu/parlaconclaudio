"""
Audio Recorder - Captures microphone audio using PortAudioDriver from RTSTT core.

Collects raw PCM bytes while recording is active, returns a numpy array
suitable for WhisperRTX transcription.
"""

import logging
import threading
import time
import numpy as np

from ..core.audio_capture.audio_capture_base import AudioCaptureConfig
from ..core.audio_capture.drivers.portaudio_driver import PortAudioDriver

logger = logging.getLogger(__name__)

MAX_RECORDING_SECONDS = 120


class AudioRecorder:
    """Records microphone audio and returns numpy arrays for transcription."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1, chunk_size: int = 1024, device_id: int | None = None):
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size = chunk_size
        self._device_id = device_id
        self._config = AudioCaptureConfig(
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
            device_id=device_id,
        )
        self._driver: PortAudioDriver | None = None
        self._chunks: list[bytes] = []
        self._lock = threading.Lock()
        self._recording = False
        self._record_start: float = 0.0

    def _ensure_driver(self) -> None:
        """Lazily initialize the PortAudio driver."""
        if self._driver is None:
            self._driver = PortAudioDriver(self._config)
            logger.info("PortAudioDriver initialized for voice bridge")

    def start(self) -> None:
        """Start recording from microphone."""
        self._ensure_driver()
        with self._lock:
            self._chunks.clear()
            self._recording = True
            self._record_start = time.monotonic()

        max_bytes = self._sample_rate * self._channels * 2 * MAX_RECORDING_SECONDS

        def on_audio(data: bytes) -> None:
            with self._lock:
                if not self._recording:
                    return
                self._chunks.append(data)
                total = sum(len(c) for c in self._chunks)
                if total >= max_bytes:
                    logger.warning(f"Recording capped at {MAX_RECORDING_SECONDS}s of audio data")
                    self._recording = False

        self._driver.start(callback=on_audio)
        logger.info("Recording started")

    def stop(self) -> np.ndarray:
        """Stop recording and return audio as float32 numpy array."""
        wall_clock = time.monotonic() - self._record_start if self._record_start else 0

        with self._lock:
            self._recording = False

        if self._driver:
            self._driver.stop()

        with self._lock:
            if not self._chunks:
                logger.warning("No audio chunks captured")
                return np.array([], dtype=np.float32)

            chunk_count = len(self._chunks)
            raw = b"".join(self._chunks)
            self._chunks.clear()

        audio_int16 = np.frombuffer(raw, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0

        raw_duration = len(audio_float32) / self._sample_rate
        logger.info(
            f"Recording stopped: wall={wall_clock:.2f}s, "
            f"audio={raw_duration:.2f}s, {len(audio_float32)} samples, {chunk_count} chunks"
        )

        # Guard against device data flooding: if audio duration vastly exceeds
        # wall-clock time, trim to wall-clock + generous margin
        expected_samples = int((wall_clock + 1.0) * self._sample_rate)
        if len(audio_float32) > expected_samples * 2:
            logger.warning(
                f"Device flooding detected: {raw_duration:.1f}s audio from {wall_clock:.1f}s recording. "
                f"Trimming to first {wall_clock + 1.0:.1f}s"
            )
            audio_float32 = audio_float32[:expected_samples]

        return audio_float32

    def set_device(self, device_id: int | None) -> None:
        """Switch microphone device. Takes effect on next start()."""
        self._device_id = device_id
        self._config = AudioCaptureConfig(
            sample_rate=self._sample_rate,
            channels=self._channels,
            chunk_size=self._chunk_size,
            device_id=device_id,
        )
        # Force driver re-init on next recording
        if self._driver:
            try:
                self._driver.stop()
            except Exception:
                pass
            self._driver = None
        logger.info(f"Microphone device set to: {device_id}")

    def list_devices(self) -> list:
        """List available input devices via PortAudio."""
        self._ensure_driver()
        return self._driver.list_devices()

    def cleanup(self) -> None:
        """Release audio resources."""
        if self._driver:
            try:
                self._driver.stop()
            except Exception:
                pass
            self._driver = None
