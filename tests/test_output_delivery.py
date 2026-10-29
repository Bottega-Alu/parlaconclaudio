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
    monkeypatch.setattr(output, "_copy", copied.append)
    monkeypatch.setattr(output.time, "sleep", lambda _: None)
    monkeypatch.setattr(output, "_send_key", lambda *a, **k: None)
    monkeypatch.setattr(output, "_send_ctrl_v", lambda: (_ for _ in ()).throw(OSError("blocked")))
    assert output.OutputHandler().deliver("testo recuperabile") is False
    assert copied == ["testo recuperabile"]


class FakeClipboard:
    """win32clipboard stand-in: records what a copy publishes, never touches the real clipboard."""
    CF_UNICODETEXT = 13

    def __init__(self, fail_open=0):
        self.data, self.formats, self.fail_open, self.closed = {}, {}, fail_open, True

    def OpenClipboard(self, hwnd=None):
        if self.fail_open:
            self.fail_open -= 1
            raise OSError("busy")
        self.closed = False

    def EmptyClipboard(self):
        self.data.clear()

    def RegisterClipboardFormat(self, name):
        return self.formats.setdefault(name, 0xC000 + len(self.formats))

    def SetClipboardData(self, fmt, value):
        self.data[fmt] = value

    def CloseClipboard(self):
        self.closed = True


def test_dictated_text_stays_out_of_clipboard_history_and_cloud(monkeypatch):
    clip = FakeClipboard(fail_open=2)  # another app holds the clipboard for a moment
    monkeypatch.setattr(output, "_win32clipboard", lambda: clip)
    monkeypatch.setattr(output.time, "sleep", lambda _: None)
    output._copy(" testo privato")
    assert clip.data[clip.CF_UNICODETEXT] == " testo privato"
    for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
        assert clip.data[clip.formats[name]] == b"\0\0\0\0"
    assert clip.closed


def test_copy_falls_back_to_pyperclip_when_the_clipboard_stays_locked(monkeypatch):
    copied = []
    monkeypatch.setattr(output, "_win32clipboard", lambda: FakeClipboard(fail_open=99))
    monkeypatch.setattr(output.time, "sleep", lambda _: None)
    monkeypatch.setattr(output.pyperclip, "copy", copied.append)
    output._copy("testo")
    assert copied == ["testo"]
