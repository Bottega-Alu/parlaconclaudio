"""
Audio feedback sounds for voice bridge state transitions.
Uses winsound.Beep / winsound.PlaySound (Windows stdlib, zero dependencies).
"""

import io
import logging
import math
import struct
import threading
import wave

logger = logging.getLogger(__name__)


def _beep(freq: int, duration_ms: int) -> None:
    """Play a beep in a background thread (non-blocking)."""
    try:
        import winsound
        winsound.Beep(freq, duration_ms)
    except Exception:
        pass


def beep_async(freq: int, duration_ms: int) -> None:
    """Fire-and-forget beep."""
    t = threading.Thread(target=_beep, args=(freq, duration_ms), daemon=True)
    t.start()


def beep_start(freq: int = 800, duration: int = 150) -> None:
    """Recording started feedback."""
    beep_async(freq, duration)


def beep_stop(freq: int = 600, duration: int = 150) -> None:
    """Recording stopped / transcribing feedback."""
    beep_async(freq, duration)


def beep_output(freq: int = 1000, duration: int = 100) -> None:
    """Output delivered feedback."""
    beep_async(freq, duration)


def make_chirp_wav(
    f_start: float,
    f_end: float,
    duration_ms: int,
    sample_rate: int = 22050,
    amplitude: float = 0.5,
) -> bytes:
    """Synthesize a linear-frequency sweep (chirp) as mono 16-bit PCM WAV bytes.

    Pure function: no I/O, no playback, nothing stdlib-external beyond
    wave/struct/math/io -- directly testable without touching winsound.
    A short fade in/out (~8ms) avoids clicks at the edges.
    """
    if not (0 < amplitude <= 1.0):
        raise ValueError("amplitude must be in (0, 1]")

    duration_s = duration_ms / 1000.0
    n_samples = max(1, round(sample_rate * duration_s))
    fade_samples = max(1, min(n_samples // 2, round(sample_rate * 0.008)))
    freq_delta = f_end - f_start

    samples = []
    for i in range(n_samples):
        t = i / sample_rate
        # Instantaneous phase of a linear chirp: integral of 2*pi*f(t)dt
        # with f(t) = f_start + freq_delta * t / duration_s.
        phase = 2 * math.pi * (f_start * t + freq_delta * t * t / (2 * duration_s))
        value = math.sin(phase)

        if i < fade_samples:
            value *= i / fade_samples
        elif i >= n_samples - fade_samples:
            value *= (n_samples - 1 - i) / fade_samples

        value = max(-1.0, min(1.0, value * amplitude))
        samples.append(int(value * 32767))

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *samples))

    return buf.getvalue()


def _play_wav_bytes(data: bytes) -> None:
    """Play in-memory WAV bytes via winsound.PlaySound.

    SND_MEMORY synchronous, deliberately NOT combined with SND_ASYNC: real
    CPython raises RuntimeError("Cannot play asynchronously from memory")
    for that combination (S1 regression -- the chirp never actually played
    in production, it silently fell through to the Beep fallback below on
    every call). This runs on its own daemon thread (see beep_timeout), so
    blocking here for duration_ms does not stall the caller; `data` is a
    local reference kept alive for the whole synchronous call.

    Falls back to a short sequence of descending winsound.Beep calls if
    PlaySound is unavailable or fails (e.g. no audio device). Never raises.
    """
    try:
        import winsound
        winsound.PlaySound(data, winsound.SND_MEMORY)
    except Exception as e:
        logger.warning(f"beep_timeout: PlaySound failed, falling back to Beep sequence: {e}")
        try:
            import winsound
            for freq in (900, 700, 500, 350, 250):
                winsound.Beep(freq, 80)
        except Exception:
            pass


def beep_timeout(f_start: int = 900, f_end: int = 250, duration_ms: int = 700) -> None:
    """Recording auto-stopped by the watchdog timeout (not a manual release).

    Longer than beep_stop and a descending glissando, so the sound alone
    tells you whether you let go of the hotkey or the timeout cut you off.
    """
    data = make_chirp_wav(f_start, f_end, duration_ms)
    t = threading.Thread(target=_play_wav_bytes, args=(data,), daemon=True)
    t.start()


def beep_ready() -> None:
    """Startup jingle — ta-da-da da da! Plays in background thread."""
    def _jingle():
        try:
            import winsound
            # C5-E5-G5 C6 G5  — cheerful major arpeggio
            winsound.Beep(523, 100)   # C5
            winsound.Beep(659, 100)   # E5
            winsound.Beep(784, 100)   # G5
            winsound.Beep(1047, 180)  # C6 (hold)
            winsound.Beep(784, 120)   # G5 (resolve)
        except Exception:
            pass
    threading.Thread(target=_jingle, daemon=True).start()
