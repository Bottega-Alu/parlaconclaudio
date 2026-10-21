"""Native ABI and failure reporting, without sending actual keyboard input."""
import ctypes
from types import SimpleNamespace

import pytest

from src.voice_bridge import output_handler as output


def test_input_matches_windows_sdk_layout():
    assert ctypes.sizeof(output.INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_rejected_key_is_reported(monkeypatch):
    class Send:
        def __call__(self, *args):
            return 0

    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: SimpleNamespace(SendInput=Send()))
    with pytest.raises(OSError, match="Windows"):
        output._send_key(output.VK_V)


def test_paste_releases_control_when_v_fails(monkeypatch):
    events = []

    def send(vk, up=False):
        events.append((vk, up))
        if vk == output.VK_V and not up:
            raise OSError("blocked")

    monkeypatch.setattr(output, "_send_key", send)
    with pytest.raises(OSError):
        output._send_ctrl_v()
    assert events[-1] == (output.VK_CONTROL, True)


def test_delivery_failure_keeps_text_copied_and_returns_false(monkeypatch):
    copied = []
    monkeypatch.setattr(output.pyperclip, "copy", copied.append)
    monkeypatch.setattr(output.time, "sleep", lambda _: None)
    monkeypatch.setattr(output, "_send_key", lambda *a, **k: None)
    monkeypatch.setattr(output, "_send_ctrl_v", lambda: (_ for _ in ()).throw(OSError("blocked")))
    assert output.OutputHandler().deliver("testo recuperabile") is False
    assert copied == ["testo recuperabile"]
