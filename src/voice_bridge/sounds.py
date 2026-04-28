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
