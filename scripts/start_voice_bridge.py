"""Start the voice bridge from a command line that has no working directory.

Used by the autostart fallback (HKCU Run value, src/voice_bridge/autostart.py):
a Run value cannot set a working directory, so `pythonw -m src.voice_bridge`
would not find the package. This script sits in <root>/scripts, moves to <root>
and runs exactly that module - the supervisor, same as the logon task.

    <base pythonw.exe> <root>\\scripts\\start_voice_bridge.py

The interpreter is the venv's BASE pythonw.exe (gui_python.resolve), so this
runs outside the venv: the supervisor path is stdlib-only and starts the bridge
child inside the venv.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    runpy.run_module("src.voice_bridge", run_name="__main__", alter_sys=True)
