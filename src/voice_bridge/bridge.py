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
from .sounds import beep_start, beep_stop, beep_output, beep_ready
from .audio_recorder import AudioRecorder
from .transcriber import Transcriber
from .output_handler import OutputHandler
from .hotkey_listener import HotkeyListener
from .tray_icon import TrayIcon
from .gesture_listener import GestureListener

from pathlib import Path as _Path
_log_path = _Path(__file__).resolve().parent.parent.parent / "voicebridge.log"
_fh = logging.FileHandler(_log_path, mode="w", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), _fh],
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
        self._toggle_lock = threading.Lock()
        self._last_toggle_ts = 0.0

        # Read initial config from shared tts_config.json
        self._load_shared_config()
        self._mic_device_id = self.config.mic_device_id

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
            on_volume_changed=self._on_volume_changed,
            on_muted_changed=self._on_muted_changed,
            transcriber=self._transcriber,
            recorder=self._recorder,
        )

        self._gesture: GestureListener | None = None
        if self.config.gesture_enabled:
            self._gesture = GestureListener(
                on_toggle_rec=self._gesture_toggle_recording,
                mouse_long_press_ms=self.config.gesture_mouse_long_press_ms,
                caps_long_press_ms=self.config.gesture_caps_long_press_ms,
                double_click_ms=self.config.gesture_double_click_ms,
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

        if self.config.sound_on_start and not self.config.muted and self.config.volume > 0:
            beep_start(self.config.sound_start_freq, self.config.sound_start_duration)

        self._recorder.start()

        # Start recording watchdog
        threading.Thread(
            target=self._recording_watchdog,
            daemon=True,
        ).start()

    def _recording_watchdog(self) -> None:
        """Auto-stop recording after timeout. Prevents stuck recording sessions."""
        timeout = self.config.recording_timeout
        poll_interval = 5.0
        elapsed = 0.0
        while elapsed < timeout:
            time.sleep(poll_interval)
            elapsed += poll_interval
            with self._lock:
                if self._state != BridgeState.RECORDING:
                    return  # recording ended normally
        # Still recording after timeout — force stop
        with self._lock:
            if self._state != BridgeState.RECORDING:
                return
        logger.warning(f"Recording timeout ({timeout}s) — auto-stopping")
        beep_stop(self.config.sound_stop_freq, self.config.sound_stop_duration)
        self._on_hotkey_release()

    def _on_hotkey_release(self) -> None:
        """Called when push-to-talk hotkey is released."""
        with self._lock:
            if self._state != BridgeState.RECORDING:
                return
            self._set_state(BridgeState.TRANSCRIBING)

        if self.config.sound_on_stop and not self.config.muted and self.config.volume > 0:
            beep_stop(self.config.sound_stop_freq, self.config.sound_stop_duration)

        # Stop recording and get audio
        audio = self._recorder.stop()

        # Transcribe in background thread to not block hotkey listener
        threading.Thread(target=self._transcribe_and_output, args=(audio,), daemon=True).start()

    def _gesture_toggle_recording(self) -> None:
        """Toggle REC from gesture trigger (mouse long-press, double-click,
        or Caps Lock long-press). Same effect as a hotkey toggle press.

        Debounced + locked to prevent double-fires when multiple sources
        (e.g. hotkey + gesture) trigger nearly simultaneously.
        """
        with self._toggle_lock:
            now = time.monotonic()
            debounce_s = self.config.gesture_debounce_ms / 1000
            if now - self._last_toggle_ts < debounce_s:
                logger.debug("Gesture toggle debounced")
                return
            self._last_toggle_ts = now

        if self._state == BridgeState.IDLE:
            self._on_hotkey_press()
        elif self._state == BridgeState.RECORDING:
            self._on_hotkey_release()
        else:
            logger.debug(f"Gesture toggle ignored (state={self._state.value})")

    def _transcribe_and_output(self, audio) -> None:
        """Transcribe audio and deliver output."""
        try:
            text = self._transcriber.transcribe(audio)

            if text:
                with self._lock:
                    self._set_state(BridgeState.OUTPUT)

                self._output.deliver(text)

                if self.config.sound_on_output and not self.config.muted and self.config.volume > 0:
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

        # Mic health check — detect flooding/broken devices before user tries to record
        logger.info(f"Mic health check (device {self._mic_device_id})...")
        hc = self._recorder.health_check(test_duration=0.5)
        if hc["ok"]:
            logger.info(
                f"Mic OK: {hc['audio_sec']:.2f}s audio from {hc['wall_sec']:.2f}s "
                f"(ratio {hc['ratio']:.2f}x)"
            )
        else:
            logger.warning(f"Mic FAILED: {hc['error']}")
            # Try default device as fallback
            if self._mic_device_id is not None:
                logger.info("Trying default system microphone as fallback...")
                self._recorder.set_device(None)
                hc2 = self._recorder.health_check(test_duration=0.5)
                if hc2["ok"]:
                    logger.info(
                        f"Default mic OK: {hc2['audio_sec']:.2f}s audio "
                        f"(ratio {hc2['ratio']:.2f}x) — using default device"
                    )
                else:
                    logger.error(
                        f"Default mic also failed: {hc2['error']}. "
                        "Recording may not work — check audio devices."
                    )
                    # Restore original device so user can fix via tray menu
                    self._recorder.set_device(self._mic_device_id)

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

        # Start gesture listener (mouse on tray + Caps Lock long-press)
        if self._gesture:
            self._gesture.start()

        self._set_state(BridgeState.IDLE)

        # Rebuild menu now that engine is loaded (fixes "Not Loaded" status)
        self._tray._rebuild_menu()

        logger.info("Voice Bridge ready! Hold hotkey to dictate.")
        logger.info("Press Ctrl+C to exit.")
        beep_ready()

    def _load_shared_config(self) -> None:
        """Read settings from shared tts_config.json into self.config."""
        import json
        from pathlib import Path
        try:
            config_path = Path.home() / ".claude" / "cache" / "tts" / "tts_config.json"
            if config_path.is_file():
                shared = json.loads(config_path.read_text(encoding="utf-8"))

                # Microphone
                mic_id = shared.get("mic_device_id")
                self.config.mic_device_id = int(mic_id) if mic_id is not None else None

                # Volume & Mute
                self.config.volume = int(shared.get("volume", 200))
                self.config.muted = bool(shared.get("muted", False))

                # STT Language
                self.config.whisper_language = shared.get("whisper_language")

                # STT Mode
                self.config.stt_mode = shared.get("stt_mode", "auto")

                logger.info(f"Loaded shared config: volume={self.config.volume}, muted={self.config.muted}, language={self.config.whisper_language}")
        except Exception as e:
            logger.warning(f"Failed to load shared config: {e}")

    @staticmethod
    def _read_mic_device() -> int | None:
        """DEPRECATED: Use _load_shared_config instead."""
        return None

    def _on_mic_changed(self, device_id: int | None) -> None:
        """Handle microphone change from tray menu."""
        if self._state != BridgeState.IDLE:
            logger.warning("Cannot switch microphone while recording")
            return
        self._recorder.set_device(device_id)
        logger.info(f"Microphone switched to device {device_id}")

    def _on_volume_changed(self, level: int) -> None:
        """Handle volume change from tray menu."""
        self.config.volume = level
        logger.info(f"Bridge volume updated to {level}%")

    def _on_muted_changed(self, muted: bool) -> None:
        """Handle mute toggle from tray menu."""
        self.config.muted = muted
        logger.info(f"Bridge mute updated to {muted}")

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
        if self._gesture:
            self._gesture.stop()
        self._recorder.cleanup()
        self._transcriber.cleanup()
        self._tray.stop()
        # Remove PID lockfile
        from pathlib import Path
        pid_file = Path.home() / ".claude" / "cache" / "tts" / "voicebridge.pid"
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
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


def _kill_orphan_instances() -> None:
    """Find and kill stale VoiceBridge processes from previous runs.

    Uses a PID lockfile at ~/.claude/cache/tts/voicebridge.pid.
    Also scans for any python process running voice_bridge module.
    """
    import os
    import subprocess
    from pathlib import Path

    pid_file = Path.home() / ".claude" / "cache" / "tts" / "voicebridge.pid"
    my_pid = os.getpid()
    # Also get parent PID to avoid killing the shell that launched us
    my_ppid = os.getppid()
    safe_pids = {my_pid, my_ppid}

    logger.debug(f"Orphan scan: my PID={my_pid}, parent PID={my_ppid}")

    # 1. Check lockfile from previous run
    if pid_file.is_file():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid not in safe_pids and old_pid > 0:
                _try_kill_pid(old_pid, "lockfile")
        except (ValueError, OSError):
            pass

    # 2. Scan for any python processes running voice_bridge
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
             "| Where-Object { $_.CommandLine -like '*voice_bridge*' } "
             "| Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            try:
                pid = int(line.strip())
                if pid in safe_pids:
                    logger.debug(f"Skipping own process PID {pid}")
                    continue
                _try_kill_pid(pid, "process scan")
            except ValueError:
                continue
    except Exception as e:
        logger.debug(f"Orphan scan via powershell failed: {e}")

    # 3. Write our PID
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(my_pid))
    logger.info(f"PID lockfile written: {my_pid}")


def _try_kill_pid(pid: int, source: str) -> None:
    """Attempt to terminate a process by PID using taskkill (Windows).

    No pre-check: os.kill(pid, 0) on Windows maps to TerminateProcess and
    is destructive (can crash with WinError 87 / SystemError). taskkill
    /F is idempotent — exit code 128 if PID not found, no-op otherwise.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            logger.warning(f"Killed orphan VoiceBridge (PID {pid}, found via {source})")
            time.sleep(0.3)
        # else: PID not found or access denied — silent no-op
    except Exception as e:
        logger.warning(f"Failed to kill PID {pid}: {e}")


def main():
    _kill_orphan_instances()
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
