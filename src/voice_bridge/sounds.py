"""
Audio feedback sounds for voice bridge state transitions.
Uses winsound.Beep (Windows stdlib, zero dependencies).
"""

import threading


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
