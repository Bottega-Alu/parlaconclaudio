"""Actual quick-controls renderer with in-memory settings and inert actions."""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.voice_bridge import tray_icon
from src.voice_bridge import tray_flyout
from src.voice_bridge.menu_dpi import native_menu_dpi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--dark", action="store_true")
    parser.add_argument("--capture", action="store_true", help="Expose preview in window inventory")
    args = parser.parse_args()
    config = {"volume": 125, "whisper_language": "it", "stt_mode": "auto", "mic_device_id": 1}
    tray_icon._load_config = lambda: dict(config)
    tray_icon._save_config = lambda value: config.update(value)
    tray_flyout._dark_mode = lambda: args.dark
    recorder = SimpleNamespace(list_devices=lambda: [SimpleNamespace(device_id=1, name="Microfono predefinito", is_default=True)])
    transcriber = SimpleNamespace(active_engine_name=lambda: "whisper_local", gpu_name=lambda: "NVIDIA GPU")
    tray = tray_icon.TrayIcon(recorder=recorder, transcriber=transcriber)
    tray._on_toggle_recording = lambda: setattr(tray, "_state", "idle" if tray._state == "recording" else "recording")
    tray._transcribe_files_clicked = lambda *a: None
    tray._open_panel = lambda *a: None
    tray._show_full_menu = lambda: None
    tray._on_stt_mode_changed = lambda mode: (config.update(stt_mode=mode) or True)
    flyout = tray_flyout.TrayFlyout(tray)
    # Safe preview never dismisses while Computer Use inspects the window.
    flyout._dismiss_if_outside = lambda: None
    with native_menu_dpi():
        flyout._build()
        flyout.root.title("Anteprima sicura - Comandi rapidi parlaconclaudio")
        flyout._show()
        if args.capture:
            flyout.root.overrideredirect(False)
            import ctypes
            hwnd = tray_flyout.flyout_window.window_handle(flyout.root)
            user = ctypes.WinDLL("user32")
            user.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
            user.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user.SetWindowLongPtrW(hwnd, -20, (user.GetWindowLongPtrW(hwnd, -20) & ~0x80) | 0x40000)
        print("Preview:", flyout.root.state(), flyout.root.geometry(), "DPI", flyout._dpi, flush=True)
        flyout.root.after(args.seconds * 1000, flyout.root.destroy)
        flyout.root.mainloop()


if __name__ == "__main__":
    main()
