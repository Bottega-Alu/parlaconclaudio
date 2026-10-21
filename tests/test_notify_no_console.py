"""Codex audio workers must preserve stdin/venv without opening a console."""
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest


def load_notify():
    spec = importlib.util.spec_from_file_location(
        "notify_without_console", Path(__file__).resolve().parents[1] / "scripts" / "notify-tts.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_prefers_base_gui_python_and_preserves_venv(monkeypatch, tmp_path):
    nt = load_notify()
    base, venv = tmp_path / "base python", tmp_path / "app venv"
    base.mkdir()
    (base / "pythonw.exe").touch()
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "pythonw.exe").touch()
    monkeypatch.setattr(sys, "base_prefix", str(base))
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "stale")
    exe, env = nt._worker_python()
    assert exe == str(base / "pythonw.exe")
    assert env["__PYVENV_LAUNCHER__"] == str(venv / "Scripts" / "pythonw.exe")
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_worker_fallback_clears_stale_venv(monkeypatch, tmp_path):
    nt = load_notify()
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path))
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "stale")
    exe, env = nt._worker_python()
    assert exe == sys.executable
    assert "__PYVENV_LAUNCHER__" not in env


def test_worker_breakaway_retry_always_keeps_no_window(monkeypatch):
    nt = load_notify()
    calls, payload = [], []
    stdin = SimpleNamespace(write=payload.append, close=lambda: None)

    def popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(calls) == 1:
            raise OSError("breakaway denied")
        return SimpleNamespace(stdin=stdin)

    monkeypatch.setattr(nt.subprocess, "Popen", popen)
    assert nt.spawn_detached_worker(["--source", "codex"], '{"test":"attività"}')
    assert len(calls) == 2
    for cmd, kwargs in calls:
        assert kwargs["creationflags"] & nt._CREATE_NO_WINDOW
        assert not kwargs["creationflags"] & 0x00000008  # DETACHED_PROCESS overrides NO_WINDOW
        assert cmd[-1] == nt.WORKER_FLAG
        assert kwargs["stdout"] is kwargs["stderr"] is nt.subprocess.DEVNULL
        assert kwargs["close_fds"]
    assert calls[0][1]["creationflags"] & nt._CREATE_BREAKAWAY_FROM_JOB
    assert not calls[1][1]["creationflags"] & nt._CREATE_BREAKAWAY_FROM_JOB
    assert payload == ['{"test":"attività"}'.encode("utf-8")]


def test_failed_worker_spawn_reports_failure(monkeypatch):
    nt = load_notify()

    def fail(*args, **kwargs):
        raise OSError("cannot start")

    monkeypatch.setattr(nt.subprocess, "Popen", fail)
    assert nt.spawn_detached_worker([], "{}") is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console and venv regression")
def test_actual_worker_has_no_console_and_receives_utf8(monkeypatch, tmp_path):
    nt = load_notify()
    output = tmp_path / "worker.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import ctypes,json,os,sys\n"
        "from pathlib import Path\n"
        "raw = sys.stdin.read()\n"
        "data = dict(console=ctypes.windll.kernel32.GetConsoleWindow(), prefix=sys.prefix, raw=raw)\n"
        "Path(os.environ['VB_PROBE_OUTPUT']).write_text(json.dumps(data), encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nt, "__file__", str(probe))
    monkeypatch.setenv("VB_PROBE_OUTPUT", str(output))
    payload = json.dumps({"message": "attività completata"}, ensure_ascii=False)
    assert nt.spawn_detached_worker(["--source", "codex"], payload)
    deadline = time.monotonic() + 10
    while not output.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert output.exists(), "worker did not receive payload or could not start"
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["console"] == 0
    assert Path(data["prefix"]).resolve() == Path(sys.prefix).resolve()
    assert data["raw"] == payload
