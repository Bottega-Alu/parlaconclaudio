"""
Output Handler - Delivers transcribed text to the active window.

Modes:
- clipboard_paste: Copy to clipboard + simulate Ctrl+V (default)
- clipboard_only: Copy to clipboard only
- type_keys: Simulate individual key presses (slower but more compatible)
"""

import ctypes
import ctypes.wintypes
import logging
import time

import pyperclip

logger = logging.getLogger(__name__)

# Win32 constants
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT),
    ]


def _send_key(vk: int, up: bool = False) -> None:
    """Send a single key event via Win32 SendInput."""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp._input.ki.time = 0
    inp._input.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _send_ctrl_v() -> None:
    """Simulate Ctrl+V key press."""
    _send_key(VK_CONTROL)
    _send_key(VK_V)
    time.sleep(0.02)
    _send_key(VK_V, up=True)
    _send_key(VK_CONTROL, up=True)


def _send_enter() -> None:
    """Simulate Enter key press."""
    _send_key(VK_RETURN)
    time.sleep(0.02)
    _send_key(VK_RETURN, up=True)


class OutputHandler:
    """Delivers transcription text to the active window."""

    def __init__(self, mode: str = "clipboard_paste", auto_submit: bool = False):
        self._mode = mode
        self._auto_submit = auto_submit

    def deliver(self, text: str) -> bool:
        """
        Deliver text to the user.

        Args:
            text: Transcribed text to deliver

        Returns:
            True if delivered successfully
        """
        if not text:
            return False

        try:
            if self._mode == "clipboard_only":
                pyperclip.copy(text)
                logger.info(f"Text copied to clipboard ({len(text)} chars)")
                return True

            elif self._mode == "clipboard_paste":
                pyperclip.copy(text)
                time.sleep(0.3)  # Wait for clipboard + window focus to settle
                # Release any lingering modifier keys before pasting
                _send_key(0x11, up=True)  # VK_CONTROL up
                _send_key(0x12, up=True)  # VK_MENU (Alt) up
                _send_key(0x10, up=True)  # VK_SHIFT up
                time.sleep(0.05)
                _send_ctrl_v()
                logger.info(f"Text pasted ({len(text)} chars)")
                if self._auto_submit:
                    time.sleep(0.1)
                    _send_enter()
                    logger.info("Auto-submit: Enter pressed")
                return True

            elif self._mode == "type_keys":
                pyperclip.copy(text)
                time.sleep(0.05)
                _send_ctrl_v()
                logger.info(f"Text typed via paste ({len(text)} chars)")
                if self._auto_submit:
                    time.sleep(0.1)
                    _send_enter()
                return True

            else:
                logger.error(f"Unknown output mode: {self._mode}")
                return False

        except Exception as e:
            logger.error(f"Output delivery failed: {e}")
            return False
