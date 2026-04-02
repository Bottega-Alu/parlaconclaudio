"""
Voice Bridge - Main entry point and state machine.

Push-to-talk flow:
    IDLE --(hotkey press)--> RECORDING --(hotkey release)--> TRANSCRIBING --> OUTPUT --> IDLE

Usage:
    cd C:\\PROJECTS\\RTSTT && .\\venv\\Scripts\\activate
    python -m src.voice_bridge.bridge
"""

import logging
import signal
import sys
import threading
import time
from enum import Enum

from .config import VoiceBridgeConfig
from .sounds import beep_start, beep_stop, beep_output
from .audio_recorder import AudioRecorder
from .transcriber import Transcriber
from .output_handler import OutputHandler
from .hotkey_listener import HotkeyListener
from .tray_icon import TrayIcon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("voice_bridge")


class BridgeState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    OUTPUT = "output"


class VoiceBridge:
    """Main voice bridge controller with push-to-talk state machine."""

    def __init__(self, config: VoiceBridgeConfig | None = None):
        self.config = config or VoiceBridgeConfig()
        self._state = BridgeState.IDLE
        self._running = False
        self._lock = threading.Lock()

        # Read mic device from shared config
        self._mic_device_id = self._read_mic_device()

        # Components (lazy init for heavy ones like Whisper)
        self._recorder = AudioRecorder(
            sample_rate=self.config.sample_rate,
            channels=self.config.channels,
            chunk_size=self.config.chunk_size,
            device_id=self._mic_device_id,
        )
        self._transcriber = Transcriber(
            model_name=self.config.whisper_model,
            device=self.config.whisper_device,
            compute_type=self.config.whisper_compute_type,
            language=self.config.whisper_language,
        )
        self._output = OutputHandler(
            mode=self.config.output_mode,
            auto_submit=self.config.auto_submit,
        )
        self._hotkey = HotkeyListener(
            hotkey_str=self.config.hotkey,
            on_press=self._on_hotkey_press,
            on_release=self._on_hotkey_release,
            mode=self.config.hotkey_mode,
        )
        self._tray = TrayIcon(
            on_exit=self.stop,
            on_purge_vram=self._purge_vram,
            on_stt_mode_changed=self._on_stt_mode_changed,
            on_mic_changed=self._on_mic_changed,
            transcriber=self._transcriber,
            recorder=self._recorder,
        )

    @property
    def state(self) -> BridgeState:
        return self._state

    def _set_state(self, new_state: BridgeState) -> None:
        old = self._state
        self._state = new_state
        self._tray.set_state(new_state.value)
        logger.info(f"State: {old.value} -> {new_state.value}")

    def _on_hotkey_press(self) -> None:
        """Called when push-to-talk hotkey is pressed."""
        with self._lock:
            if self._state != BridgeState.IDLE:
                return
            self._set_state(BridgeState.RECORDING)

        if self.config.sound_on_start:
            beep_start(self.config.sound_start_freq, self.config.sound_start_duration)

        self._recorder.start()

    def _on_hotkey_release(self) -> None:
        """Called when push-to-talk hotkey is released."""
        with self._lock:
            if self._state != BridgeState.RECORDING:
                return
            self._set_state(BridgeState.TRANSCRIBING)

        if self.config.sound_on_stop:
            beep_stop(self.config.sound_stop_freq, self.config.sound_stop_duration)

        # Stop recording and get audio
        audio = self._recorder.stop()

        # Transcribe in background thread to not block hotkey listener
        threading.Thread(target=self._transcribe_and_output, args=(audio,), daemon=True).start()

    def _transcribe_and_output(self, audio) -> None:
        """Transcribe audio and deliver output."""
        try:
            text = self._transcriber.transcribe(audio)

            if text:
                with self._lock:
                    self._set_state(BridgeState.OUTPUT)

                self._output.deliver(text)

                if self.config.sound_on_output:
                    beep_output(self.config.sound_output_freq, self.config.sound_output_duration)
            else:
                logger.info("No speech detected")
        except Exception as e:
            logger.error(f"Transcription pipeline error: {e}")
        finally:
            with self._lock:
                self._set_state(BridgeState.IDLE)
            self._hotkey.reset_toggle()

    @staticmethod
    def _install_sound_packs() -> None:
        """Copy bundled sound packs to ~/.claude/cache/tts/sounds/ if not present."""
        import shutil
        from pathlib import Path
        src = Path(__file__).resolve().parent.parent.parent / "sounds"
        dst = Path.home() / ".claude" / "cache" / "tts" / "sounds"
        if not src.is_dir():
            return
        dst.mkdir(parents=True, exist_ok=True)
        for pack_dir in src.iterdir():
            if pack_dir.is_dir():
                target = dst / pack_dir.name
                if not target.is_dir():
                    shutil.copytree(pack_dir, target)
                    logger.info(f"Installed sound pack: {pack_dir.name}")

    def start(self) -> None:
        """Start the voice bridge."""
        self._install_sound_packs()
        self._running = True
        logger.info("=" * 50)
        logger.info("Voice Bridge starting...")
        logger.info(f"  Hotkey: {self.config.hotkey}")
        logger.info(f"  Mode: {self.config.mode}")
        logger.info(f"  Model: {self.config.whisper_model} ({self.config.whisper_device})")
        logger.info(f"  STT Mode: {self.config.stt_mode}")
        logger.info(f"  Output: {self.config.output_mode}")
        logger.info("=" * 50)

        # Start tray first so user sees loading animation
        self._tray.start()
        self._tray.set_state("loading")

        # Pre-load STT engine at startup (may skip for cloud engines)
        logger.info("Initializing STT engine...")
        try:
            self._transcriber._ensure_engine()
            logger.info(f"STT engine ready: {self._transcriber.active_engine_name()}")
        except Exception as e:
            logger.warning(f"STT engine pre-load failed: {e} — will retry on first transcription")

        # Start hotkey listener
        self._hotkey.start()
        self._set_state(BridgeState.IDLE)

        # Rebuild menu now that engine is loaded (fixes "Not Loaded" status)
        self._tray._rebuild_menu()

        logger.info("Voice Bridge ready! Hold hotkey to dictate.")
        logger.info("Press Ctrl+C to exit.")

    @staticmethod
    def _read_mic_device() -> int | None:
        """Read mic_device_id from tts_config.json."""
        import json
        from pathlib import Path
        try:
            config_path = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"
            if config_path.is_file():
                config = json.loads(config_path.read_text(encoding="utf-8"))
                val = config.get("mic_device_id")
                return int(val) if val is not None else None
        except Exception:
            pass
        return None

    def _on_mic_changed(self, device_id: int | None) -> None:
        """Handle microphone change from tray menu."""
        if self._state != BridgeState.IDLE:
            logger.warning("Cannot switch microphone while recording")
            return
        self._recorder.set_device(device_id)
        logger.info(f"Microphone switched to device {device_id}")

    def _on_stt_mode_changed(self, mode: str) -> None:
        """Handle STT mode switch from tray menu."""
        if self._state != BridgeState.IDLE:
            logger.warning("Cannot switch STT mode while recording/transcribing")
            return
        logger.info(f"STT mode change requested: {mode}")
        self._transcriber.switch_engine(mode)

    def _purge_vram(self) -> None:
        """Purge VRAM: unload Whisper model, clear CUDA cache, reload."""
        if self._state != BridgeState.IDLE:
            logger.warning("Cannot purge VRAM while recording/transcribing")
            return
        logger.info("VRAM purge requested via menu")
        self._transcriber.purge_vram()

    def stop(self) -> None:
        """Stop the voice bridge and clean up."""
        self._running = False
        logger.info("Shutting down voice bridge...")
        self._hotkey.stop()
        self._recorder.cleanup()
        self._transcriber.cleanup()
        self._tray.stop()
        logger.info("Voice bridge stopped.")

    def run_forever(self) -> None:
        """Start and block until interrupted."""
        self.start()
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main():
    config = VoiceBridgeConfig()
    bridge = VoiceBridge(config)

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    bridge.run_forever()


if __name__ == "__main__":
    main()
