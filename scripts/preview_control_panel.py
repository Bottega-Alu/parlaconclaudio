"""Render the actual native control window without loading models or user settings.

Run: venv/Scripts/python.exe scripts/preview_control_panel.py --seconds 120
All callbacks are disabled; no audio, hooks or user configuration is modified.
"""
import argparse
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.voice_bridge import tray_icon
from src.voice_bridge.control_panel import ControlPanel
from src.core import voice_hooks

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=int, default=120)
args = parser.parse_args()
tray_icon._load_config = lambda: {'stt_mode': 'auto', 'mic_device_id': None}
voice_hooks.status = lambda agent: False
tray = tray_icon.TrayIcon()
tray._transcribe_files_clicked = lambda *args: None
tray._open_repo = lambda *args: None
tray._purge_vram_clicked = lambda *args: None
tray._make_set_mic = lambda *args: lambda *args: None
tray._make_set_stt_mode = lambda *args: lambda *args: None
tray._apply_voice_hooks = lambda *args: None
panel = ControlPanel(tray)
panel._build()
panel.root.title('parlaconclaudio • ANTEPRIMA SICURA • test')
panel.files.configure(state='disabled')
panel.root.after(max(1, args.seconds) * 1000, panel.root.destroy)
panel.root.mainloop()
