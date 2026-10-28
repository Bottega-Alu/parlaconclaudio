"""A cloned repo can contain git.exe or ffmpeg.exe: the hook runs inside that folder,
so a bare-name launch must never pick a program from the current directory."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FAKE_SOURCE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "hostname.exe"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not FAKE_SOURCE.is_file(), reason="needs git and hostname.exe")

PROBE = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("nt_probe", sys.argv[1])
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)
print(nt._git(".", "--version"))
"""


def test_the_hook_ignores_a_git_exe_planted_in_the_project(tmp_path):
    shutil.copy(FAKE_SOURCE, tmp_path / "git.exe")
    env = {k: v for k, v in os.environ.items() if k.lower() != "nodefaultcurrentdirectoryinexepath"}
    out = subprocess.run([sys.executable, "-c", PROBE, str(REPO / "scripts" / "notify-tts.py")],
                         cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().startswith("git version"), out.stdout


def test_the_bridge_entry_point_opts_out_of_the_current_directory():
    source = (REPO / "src" / "voice_bridge" / "__main__.py").read_text(encoding="utf-8")
    assert 'os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"' in source
