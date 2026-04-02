"""
PortAudio Universal Driver

Cross-platform audio capture driver using PortAudio.
Works on Windows, Linux, and macOS as a universal fallback.
"""

import time
import logging
import threading
from typing import Optional, Callable, List
from queue import Queue, Empty

try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False

from ..audio_capture_base import (
    AudioCaptureBase,
    AudioCaptureConfig,
    AudioCaptureState,
    AudioDevice,
    AudioFormat
)


logger = logging.getLogger(__name__)


class PortAudioDriver(AudioCaptureBase):
    """
    Universal PortAudio driver for cross-platform audio capture.

    Uses PyAudio (PortAudio wrapper) for maximum compatibility.
    Works on Windows, Linux, and macOS.

    Features:
    - Cross-platform compatibility
    - Automatic device detection
    - Configurable sample rate and channels
    - Thread-safe operation
    - Automatic fallback driver

    Note: Identical implementation to PulseAudio driver but named differently
    for clarity. On Linux, this will use PulseAudio backend if available.
    """

    def __init__(self, config: Optional[AudioCaptureConfig] = None):
        """
        Initialize PortAudio driver.

        Args:
            config: Audio configuration

        Raises:
            ImportError: If PyAudio is not installed
            RuntimeError: If PortAudio is not available
        """
        if not PYAUDIO_AVAILABLE:
            raise ImportError(
                "PyAudio required for PortAudio driver. "
                "Install with: pip install pyaudio"
            )

        super().__init__(config)

        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._audio_queue: Queue = Queue(maxsize=100)

        # Initialize PyAudio
        try:
            self._pa = pyaudio.PyAudio()
            logger.info(f"PortAudio driver initialized (API: {self._get_api_name()})")
        except Exception as e:
            raise RuntimeError(f"Failed to initialize PortAudio: {e}")

    def __del__(self):
        """Cleanup on destruction."""
        if self._pa:
            try:
                self._pa.terminate()
            except:
                pass

    def _get_api_name(self) -> str:
        """Get the underlying audio API name."""
        try:
            if self._pa:
                # Get default host API
                api_index = self._pa.get_default_host_api_info()['index']
                api_info = self._pa.get_host_api_info_by_index(api_index)
                return api_info['name']
        except:
            pass
        return "Unknown"

    def list_devices(self) -> List[AudioDevice]:
        """
        List available audio input devices.

        Returns:
            List of AudioDevice objects
        """
        if not self._pa:
            return []

        devices = []
        default_input = None

        try:
            # Get default input device
            try:
                default_info = self._pa.get_default_input_device_info()
                default_input = default_info['index']
            except:
                pass

            # Enumerate all devices
            for i in range(self._pa.get_device_count()):
                try:
                    info = self._pa.get_device_info_by_index(i)

                    # Only include input devices
                    if info['maxInputChannels'] > 0:
                        devices.append(AudioDevice(
                            device_id=i,
                            name=info['name'],
                            is_default=(i == default_input),
                            max_channels=info['maxInputChannels'],
                            supported_sample_rates=self._get_supported_rates(i)
                        ))
                except Exception as e:
                    logger.warning(f"Error querying device {i}: {e}")

        except Exception as e:
            logger.error(f"Error listing audio devices: {e}")

        return devices

    def _get_supported_rates(self, device_id: int) -> List[int]:
        """
        Get supported sample rates for a device.

        Args:
            device_id: Device index

        Returns:
            List of supported sample rates
        """
        standard_rates = [8000, 16000, 22050, 44100, 48000]
        supported = []

        for rate in standard_rates:
            try:
                # Try to check if format is supported
                if self._pa.is_format_supported(
                    rate,
                    input_device=device_id,
                    input_channels=1,
                    input_format=pyaudio.paInt16
                ):
                    supported.append(rate)
            except:
                pass

        return supported if supported else standard_rates

    def get_default_device(self) -> Optional[AudioDevice]:
        """
        Get default audio input device.

        Returns:
            AudioDevice or None if not found
        """
        devices = self.list_devices()
        for device in devices:
            if device.is_default:
                return device

        # Return first device if no default
        return devices[0] if devices else None

    def start(self, callback: Optional[Callable[[bytes], None]] = None) -> None:
        """
        Start audio capture.

        Args:
            callback: Optional callback for audio chunks
        """
        if self.state == AudioCaptureState.RUNNING:
            logger.warning("PortAudio driver already running")
            return

        if not self._pa:
            raise RuntimeError("PyAudio not initialized")

        self.state = AudioCaptureState.STARTING
        self._callback = callback
        self._stop_event.clear()

        try:
            # Determine device ID
            device_id = self.config.device_id
            if device_id is None:
                default_device = self.get_default_device()
                device_id = default_device.device_id if default_device else None

            # Open audio stream
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self.config.channels,
                rate=self.config.sample_rate,
                input=True,
                input_device_index=device_id,
                frames_per_buffer=self.config.chunk_size,
                stream_callback=None  # We'll use blocking mode
            )

            # Start capture thread
            self._capture_thread = threading.Thread(
                target=self._capture_loop,
                name="PortAudioCapture",
                daemon=True
            )
            self._capture_thread.start()

            self.state = AudioCaptureState.RUNNING
            self.metrics.start_time = time.time()

            device_name = f"device {device_id}" if device_id is not None else "default device"
            logger.info(f"PortAudio capture started on {device_name}")

        except Exception as e:
            self.state = AudioCaptureState.ERROR
            logger.error(f"Failed to start PortAudio capture: {e}")
            raise

    def stop(self) -> None:
        """Stop audio capture."""
        if self.state != AudioCaptureState.RUNNING:
            return

        self.state = AudioCaptureState.STOPPING
        self._stop_event.set()

        # Wait for capture thread
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)

        # Close stream
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except:
                pass
            self._stream = None

        self.state = AudioCaptureState.STOPPED
        logger.info("PortAudio capture stopped")

    def read_chunk(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        Read one chunk of audio data.

        Args:
            timeout: Read timeout in seconds

        Returns:
            Audio bytes or None
        """
        if self.state != AudioCaptureState.RUNNING:
            return None

        try:
            return self._audio_queue.get(timeout=timeout or 1.0)
        except Empty:
            return None

    def is_capturing(self) -> bool:
        """
        Check if currently capturing.

        Returns:
            True if capturing
        """
        return self.state == AudioCaptureState.RUNNING

    def _capture_loop(self) -> None:
        """Main audio capture loop."""
        logger.info(f"PortAudio capture loop started: {self.config.chunk_size} frames @ {self.config.sample_rate}Hz")

        try:
            while not self._stop_event.is_set():
                try:
                    # Read audio chunk (blocking)
                    audio_data = self._stream.read(
                        self.config.chunk_size,
                        exception_on_overflow=False
                    )

                    # Update metrics
                    self.metrics.chunks_captured += 1
                    self.metrics.bytes_captured += len(audio_data)

                    # Deliver audio
                    if self._callback:
                        try:
                            self._callback(audio_data)
                        except Exception as e:
                            logger.error(f"Error in callback: {e}")
                            self.metrics.errors += 1
                    else:
                        # Queue for read_chunk()
                        try:
                            self._audio_queue.put_nowait(audio_data)
                        except:
                            self.metrics.buffer_overruns += 1

                except Exception as e:
                    if not self._stop_event.is_set():
                        logger.error(f"Error reading audio: {e}")
                        self.metrics.errors += 1
                        time.sleep(0.1)  # Avoid tight loop on error

        except Exception as e:
            logger.error(f"Fatal error in capture loop: {e}")
            self.metrics.errors += 1
            self.state = AudioCaptureState.ERROR

        logger.info("PortAudio capture loop stopped")

    def get_format_info(self) -> AudioFormat:
        """
        Get current audio format information.

        Returns:
            AudioFormat object
        """
        return AudioFormat(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            sample_width=2,  # 16-bit = 2 bytes
            format_name="PCM16"
        )

    def set_volume(self, volume: float) -> None:
        """
        Set capture volume (not supported by PyAudio).

        Args:
            volume: Volume level (0.0 to 1.0)
        """
        logger.warning("Volume control not supported by PortAudio driver")

    def get_volume(self) -> float:
        """
        Get capture volume (not supported by PyAudio).

        Returns:
            1.0 (fixed)
        """
        return 1.0
