"""No console window anywhere in the launch chain.

Root cause (v1.0.8): `uv venv` writes the same console-subsystem trampoline as
venv\\Scripts\\python.exe AND pythonw.exe; it runs the base python.exe, so every
level started without an inherited console opened a console window. The fix
starts the base pythonw.exe with __PYVENV_LAUNCHER__ (src/voice_bridge/gui_python.py)
from every launch point. The tests below pin each launch point's command.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from src.voice_bridge import autostart, gui_python
from src.voice_bridge import supervisor as sup

REPO = Path(sup.__file__).resolve().parents[2]
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
DETACHED_PROCESS = 0x00000008


def _venv(root: Path, home: Path | None, base_pythonw: bool = True) -> Path:
    """Fake install: venv launcher + pyvenv.cfg (uv layout) + optional base pythonw."""
    venv = root / "venv"
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "pythonw.exe").write_bytes(b"")
    if home is not None:
        home.mkdir(parents=True, exist_ok=True)
        if base_pythonw:
            (home / "pythonw.exe").write_bytes(b"")
        (venv / "pyvenv.cfg").write_text(
            f"home = {home}\nimplementation = CPython\nuv = 0.11.3\n"
            "version_info = 3.11.15\ninclude-system-site-packages = false\n", encoding="utf-8")
    return venv


# --------------------------------------------------------------------------- gui_python


def test_resolve_prefers_base_pythonw_with_venv_launcher_env(tmp_path):
    home = tmp_path / "uv python"
    venv = _venv(tmp_path / "app", home)
    gui = gui_python.resolve(venv)
    assert gui.exe == home / "pythonw.exe" and gui.via == gui_python.VIA_BASE
    assert gui.env == {"__PYVENV_LAUNCHER__": str(venv / "Scripts" / "pythonw.exe")}


@pytest.mark.parametrize("home,base", [(None, False), ("h", False)])
def test_resolve_falls_back_to_venv_launcher(tmp_path, home, base):
    venv = _venv(tmp_path / "app", tmp_path / home if home else None, base_pythonw=base)
    gui = gui_python.resolve(venv)
    assert gui.exe == venv / "Scripts" / "pythonw.exe" and gui.env == {}
    assert gui.via == gui_python.VIA_VENV


def test_pyvenv_cfg_parsing_is_key_value(tmp_path):
    (tmp_path / "pyvenv.cfg").write_text("Home =  C:\\x y \nbroken line\nversion=3\n", encoding="utf-8")
    assert gui_python.read_pyvenv_cfg(tmp_path) == {"home": "C:\\x y", "version": "3"}
    assert gui_python.read_pyvenv_cfg(tmp_path / "missing") == {}


def test_base_pythonw_with_venv_launcher_env_runs_inside_the_venv(tmp_path):
    """Real interpreter: base pythonw.exe + __PYVENV_LAUNCHER__ -> sys.prefix is
    the venv. Uses the venv running these tests (skipped outside a venv)."""
    venv = Path(sys.prefix)
    gui = gui_python.resolve(venv)
    if sys.prefix == sys.base_prefix or gui.via != gui_python.VIA_BASE:
        pytest.skip("tests not running in a venv with a base pythonw.exe")
    out = tmp_path / "prefix.txt"
    env = {**os.environ, **gui.env}
    code = f"import sys; open({str(out)!r}, 'w').write(sys.prefix + '|' + sys.executable)"
    proc = subprocess.run([str(gui.exe), "-c", code], env=env, timeout=60)
    assert proc.returncode == 0
    prefix, executable = out.read_text().split("|")
    assert Path(prefix).resolve() == venv.resolve()
    assert Path(executable) == venv / "Scripts" / "pythonw.exe"


# --------------------------------------------------------------------------- supervisor -> child


def test_child_of_a_pythonw_supervisor_is_base_pythonw_without_window(tmp_path):
    home = tmp_path / "base"
    venv = _venv(tmp_path / "app", home)
    launch = sup.child_launch(True, executable=str(venv / "Scripts" / "pythonw.exe"), venv=venv)
    assert launch.cmd == [str(home / "pythonw.exe"), "-m", "src.voice_bridge", "--child", "--append-log"]
    assert launch.env_overrides == {"__PYVENV_LAUNCHER__": str(venv / "Scripts" / "pythonw.exe")}
    assert launch.creationflags & sup.CREATE_NO_WINDOW and not launch.creationflags & DETACHED_PROCESS
    assert launch.console is False


def test_child_of_a_base_pythonw_supervisor_outside_the_venv(tmp_path):
    """Logon task / Run value: the supervisor itself is the base pythonw.exe."""
    home = tmp_path / "base"
    venv = _venv(tmp_path / "app", home)
    launch = sup.child_launch(False, executable=str(home / "pythonw.exe"), venv=venv)
    assert launch.cmd[0] == str(home / "pythonw.exe") and launch.via == gui_python.VIA_BASE
    assert "__PYVENV_LAUNCHER__" in launch.env_overrides


def test_child_fallback_trampoline_still_gets_create_no_window(tmp_path):
    venv = _venv(tmp_path / "app", None)
    launch = sup.child_launch(False, executable=str(venv / "Scripts" / "pythonw.exe"), venv=venv)
    assert launch.cmd[0] == str(venv / "Scripts" / "pythonw.exe")
    assert launch.creationflags == sup.CREATE_NO_WINDOW


def test_console_run_keeps_sharing_its_console(tmp_path):
    launch = sup.child_launch(False, executable="C:\\v\\Scripts\\python.exe", venv=tmp_path)
    assert launch.cmd[0] == "C:\\v\\Scripts\\python.exe"
    assert launch.creationflags == 0 and launch.console and launch.env_overrides == {}


def test_spawn_child_streams_flags_and_env(monkeypatch, tmp_path):
    home = tmp_path / "base"
    venv = _venv(tmp_path / "app", home)
    seen = {}
    monkeypatch.setattr(sup, "VENV_DIR", venv)
    monkeypatch.setattr(sup.sys, "executable", str(home / "pythonw.exe"))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "C:\\stale\\pythonw.exe")
    monkeypatch.setattr(sup.subprocess, "Popen", lambda cmd, **kw: seen.update(cmd=cmd, **kw))
    sup.spawn_child(False)
    assert seen["cmd"][0] == str(home / "pythonw.exe")
    assert seen["creationflags"] == sup.CREATE_NO_WINDOW
    assert seen["stdin"] is seen["stdout"] is seen["stderr"] is subprocess.DEVNULL
    assert seen["env"]["__PYVENV_LAUNCHER__"] == str(venv / "Scripts" / "pythonw.exe")


def test_supervisor_path_is_stdlib_only():
    """The task / Run value run the supervisor with the base pythonw.exe, outside
    the venv: importing the entry module must work with no site-packages."""
    code = ("import sys; import src.voice_bridge.__main__; "
            "bad = [p for p in sys.path if 'site-packages' in p]; sys.exit(1 if bad else 0)")
    proc = subprocess.run([sys.executable, "-S", "-c", code], cwd=REPO, timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------- task / Run value


def test_task_and_run_value_use_base_pythonw(tmp_path):
    home = tmp_path / "base"
    root = tmp_path / "app"
    _venv(root, home)
    ex = ET.fromstring(autostart.build_task_xml(root, "PC\\u").replace(
        'encoding="UTF-16"', "")).find("t:Actions/t:Exec", NS)
    assert ex.find("t:Command", NS).text == str(home / "pythonw.exe")
    assert ex.find("t:Arguments", NS).text == "-m src.voice_bridge"
    assert ex.find("t:WorkingDirectory", NS).text == str(root)
    assert autostart.run_command(root) == (
        f'"{home / "pythonw.exe"}" "{root / "scripts" / "start_voice_bridge.py"}"')


# --------------------------------------------------------------------------- VoiceBridge.exe / .bat


def _load_launcher():
    spec = importlib.util.spec_from_file_location("vb_launcher", REPO / "scripts" / "vb_launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launcher_stub_never_detaches_and_uses_base_pythonw(tmp_path):
    vb = _load_launcher()
    home = tmp_path / "base"
    root = tmp_path / "app"
    venv = _venv(root, home)
    kw = vb.launch_kwargs(root, gui_python.resolve(venv), {"__PYVENV_LAUNCHER__": "stale", "X": "1"})
    assert kw["args"] == [str(home / "pythonw.exe"), "-m", "src.voice_bridge"]
    assert kw["creationflags"] & vb.CREATE_NO_WINDOW
    assert not kw["creationflags"] & DETACHED_PROCESS  # would override CREATE_NO_WINDOW
    assert kw["env"]["__PYVENV_LAUNCHER__"] == str(venv / "Scripts" / "pythonw.exe")
    assert kw["env"]["X"] == "1" and kw["cwd"] == str(root)


def test_bat_goes_through_the_launcher_not_venv_pythonw():
    bat = (REPO / "VoiceBridge.bat").read_text(encoding="utf-8")
    commands = [ln for ln in bat.splitlines() if ln.strip() and not ln.lower().startswith(("rem", "@echo"))]
    assert commands[-1].strip() == '"%~dp0venv\\Scripts\\python.exe" "%~dp0scripts\\vb_launcher.py"'
