"""
Audio Capture Base Interface

Abstract base class defining the interface for all audio capture drivers.
Platform-specific implementations (WASAPI, PulseAudio, ALSA, PortAudio)
must inherit from this class and implement all abstract methods.
"""

from abc import ABC, abstractmethod
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
import numpy as np


class AudioFormat(Enum):
    """Supported audio formats"""
    PCM_16 = "pcm_16"  # 16-bit PCM (most common)
    PCM_24 = "pcm_24"  # 24-bit PCM
    PCM_32 = "pcm_32"  # 32-bit PCM
    FLOAT_32 = "float_32"  # 32-bit float


class AudioCaptureState(Enum):
    """Audio capture states"""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class AudioDevice:
    """Represents an audio input device"""

    def __init__(
        self,
        device_id: int,
        name: str,
        is_default: bool = False,
        max_channels: int = 2,
        supported_sample_rates: Optional[List[int]] = None
    ):
        self.device_id = device_id
        self.name = name
        self.is_default = is_default
        self.max_channels = max_channels
        self.supported_sample_rates = supported_sample_rates or [16000, 44100, 48000]

    def __repr__(self) -> str:
        default = " (default)" if self.is_default else ""
        return f"AudioDevice({self.device_id}: {self.name}{default})"


class AudioCaptureConfig:
    """Configuration for audio capture"""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_size: int = 1024,
        format: AudioFormat = AudioFormat.PCM_16,
        device_id: Optional[int] = None,
        loopback: bool = False
    ):
        """
        Initialize audio capture configuration.

        Args:
            sample_rate: Audio sample rate in Hz (default: 16000 for speech)
            channels: Number of audio channels (1=mono, 2=stereo)
            chunk_size: Number of frames per buffer/chunk
            format: Audio sample format
            device_id: Specific device to use (None = default device)
            loopback: Capture system audio instead of microphone
        """
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self.format = format
        self.device_id = device_id
        self.loopback = loopback

    def validate(self) -> None:
        """Validate configuration parameters"""
        if self.sample_rate not in [8000, 16000, 22050, 44100, 48000]:
            raise ValueError(f"Unsupported sample rate: {self.sample_rate}")

        if self.channels not in [1, 2]:
            raise ValueError(f"Unsupported channel count: {self.channels}")

        if self.chunk_size < 128 or self.chunk_size > 8192:
            raise ValueError(f"Chunk size must be between 128 and 8192: {self.chunk_size}")


class AudioCaptureMetrics:
    """Metrics for monitoring audio capture performance"""

    def __init__(self):
        self.chunks_captured = 0
        self.bytes_captured = 0
        self.buffer_overruns = 0
        self.buffer_underruns = 0
        self.errors = 0
        self.start_time: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary"""
        import time
        elapsed = (time.time() - self.start_time) if self.start_time else 0

        return {
            "chunks_captured": self.chunks_captured,
            "bytes_captured": self.bytes_captured,
            "buffer_overruns": self.buffer_overruns,
            "buffer_underruns": self.buffer_underruns,
            "errors": self.errors,
            "elapsed_time": elapsed,
            "chunks_per_second": self.chunks_captured / elapsed if elapsed > 0 else 0
        }


class AudioCaptureBase(ABC):
    """
    Abstract base class for audio capture drivers.

    All platform-specific drivers must implement this interface.
    This ensures consistent behavior across Windows (WASAPI),
    Linux (PulseAudio/ALSA), and cross-platform (PortAudio) implementations.
    """

    def __init__(self, config: Optional[AudioCaptureConfig] = None):
        """
        Initialize audio capture driver.

        Args:
            config: Audio capture configuration
        """
        self.config = config or AudioCaptureConfig()
        self.config.validate()

        self.state = AudioCaptureState.STOPPED
        self.metrics = AudioCaptureMetrics()
        self._callback: Optional[Callable[[bytes], None]] = None

    @abstractmethod
    def list_devices(self) -> List[AudioDevice]:
        """
        List all available audio input devices.

        Returns:
            List of AudioDevice objects
        """
        pass

    @abstractmethod
    def get_default_device(self) -> Optional[AudioDevice]:
        """
        Get the default audio input device.

        Returns:
            Default AudioDevice or None if not available
        """
        pass

    @abstractmethod
    def start(self, callback: Optional[Callable[[bytes], None]] = None) -> None:
        """
        Start audio capture.

        Args:
            callback: Optional callback function that receives audio chunks (bytes)
                     If not provided, use read_chunk() to get audio data

        Raises:
            RuntimeError: If capture fails to start
        """
        pass

    @abstractmethod
    def stop(self) -> None:
        """
        Stop audio capture.

        Raises:
            RuntimeError: If capture fails to stop cleanly
        """
        pass

    @abstractmethod
    def read_chunk(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        Read one chunk of audio data (blocking).

        Only use this if no callback was provided to start().

        Args:
            timeout: Maximum time to wait for data (None = block indefinitely)

        Returns:
            Audio data as bytes, or None if timeout/stopped
        """
        pass

    @abstractmethod
    def is_capturing(self) -> bool:
        """
        Check if currently capturing audio.

        Returns:
            True if capturing, False otherwise
        """
        pass

    def get_state(self) -> AudioCaptureState:
        """Get current capture state"""
        return self.state

    def get_metrics(self) -> Dict[str, Any]:
        """Get current capture metrics"""
        return self.metrics.to_dict()

    def get_config(self) -> AudioCaptureConfig:
        """Get current configuration"""
        return self.config

    @abstractmethod
    def set_volume(self, volume: float) -> None:
        """
        Set capture volume (0.0 to 1.0).

        Args:
            volume: Volume level (0.0 = mute, 1.0 = max)
        """
        pass

    @abstractmethod
    def get_volume(self) -> float:
        """
        Get current capture volume.

        Returns:
            Volume level (0.0 to 1.0)
        """
        pass

    def __enter__(self):
        """Context manager support"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager cleanup"""
        self.stop()
        return False  # Don't suppress exceptions
