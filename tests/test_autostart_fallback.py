"""Autostart fallback: HKCU Run value when the logon task cannot be registered.

schtasks is a recording runner and the Run value an in-memory fake
(tests/conftest.FakeRunKey); the one real-registry test uses a throwaway key
HKCU\\Software\\parlaconclaudio-test-<uuid>, deleted afterwards. The real
...\\CurrentVersion\\Run key is never written.
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from src.voice_bridge import autostart
from tests.conftest import FakeRunKey

DENIED = "ERRORE: Accesso negato. (0x80070005)"
REPO = Path(autostart.__file__).resolve().parents[2]


class _Runner:
    """schtasks stand-in: per-subcommand return code (or exception), records calls."""

    def __init__(self, create=0, query=1, delete=0, stderr=""):
        self.codes = {"/Create": create, "/Query": query, "/Delete": delete}
        self.stderr, self.calls = stderr, []

    def __call__(self, cmd, **kw):
        assert kw["creationflags"] & autostart.CREATE_NO_WINDOW
        self.calls.append(cmd[1])
        code = self.codes[cmd[1]]
        if isinstance(code, BaseException):
            raise code
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr=self.stderr)


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    return tmp_path


def test_task_ok_uses_task_and_removes_stale_run_value(root):
    rk = FakeRunKey(value="old")
    res = autostart.enable(root, runner=_Runner(create=0), user_id="u", run_key=rk)
    assert res.ok and res.mechanism == "task" and rk.value is None
    assert "stale Run value removed" in res.message
    assert res.summary() == "Avvio automatico attivato (Utilita' di pianificazione)"


def test_access_denied_falls_back_to_run_key(root):
    rk = FakeRunKey()
    res = autostart.enable(root, runner=_Runner(create=1, stderr=DENIED), user_id="u", run_key=rk)
    assert res.ok and res.mechanism == "run_key"
    assert res.task_error is autostart.AutostartErrorKind.ACCESS_DENIED
    assert rk.value == autostart.run_command(root.resolve())
    assert "ripiego: registro HKCU Run" in res.summary() and "access_denied" in res.summary()
    assert "mechanism=run_key" in res.log_line() and "task_error=access_denied" in res.log_line()


@pytest.mark.parametrize("create", [2, FileNotFoundError("schtasks")])
def test_any_task_failure_falls_back(root, create):
    rk = FakeRunKey()
    res = autostart.enable(root, runner=_Runner(create=create), user_id="u", run_key=rk)
    assert res.ok and res.mechanism == "run_key" and rk.value


def test_not_installed_never_falls_back(tmp_path):
    rk = FakeRunKey()
    res = autostart.enable(tmp_path, runner=_Runner(create=1), run_key=rk)
    assert res.error_kind is autostart.AutostartErrorKind.NOT_INSTALLED and rk.value is None


def test_run_command_targets_supervisor_start_script(root):
    cmd = autostart.run_command(root)
    py = root / "venv" / "Scripts" / "pythonw.exe"
    assert cmd == f'"{py}" "{root / "scripts" / "start_voice_bridge.py"}"'
    assert (REPO / autostart.START_SCRIPT).is_file()  # shipped with scripts/*


@pytest.mark.parametrize("task,run,expected", [
    (0, None, True), (1, "cmd", True), (1, None, False), (0, "cmd", True)])
def test_enabled_is_task_or_run_value(task, run, expected):
    assert autostart.is_enabled(runner=_Runner(query=task), run_key=FakeRunKey(value=run)) is expected


def test_mechanisms_lists_both():
    assert autostart.mechanisms(_Runner(query=0), FakeRunKey("c")) == ["task", "run_key"]
    assert autostart.mechanisms(_Runner(query=1), FakeRunKey(None)) == []


def test_disable_removes_both():
    rk, runner = FakeRunKey("cmd"), _Runner(query=0, delete=0)
    res = autostart.disable(runner=runner, run_key=rk)
    assert res.ok and rk.value is None and runner.calls == ["/Query", "/Delete"]
    assert "task" in res.message and "Run value deleted" in res.message


def test_disable_run_only():
    rk, runner = FakeRunKey("cmd"), _Runner(query=1)
    res = autostart.disable(runner=runner, run_key=rk)
    assert res.ok and rk.value is None and runner.calls == ["/Query"]


def test_disable_run_key_failure_is_structured():
    res = autostart.disable(runner=_Runner(query=1), run_key=FakeRunKey("cmd", fail="delete"))
    assert not res.ok and res.error_kind is autostart.AutostartErrorKind.RUN_KEY_FAILED


def test_disable_task_failure_still_removes_run_value():
    rk = FakeRunKey("cmd")
    res = autostart.disable(runner=_Runner(query=0, delete=1, stderr=DENIED), run_key=rk)
    assert not res.ok and res.error_kind is autostart.AutostartErrorKind.ACCESS_DENIED
    assert rk.value is None


def test_log_result_appends_mechanism(tmp_path, root):
    res = autostart.enable(root, runner=_Runner(create=1, stderr=DENIED), user_id="u",
                           run_key=FakeRunKey())
    log = tmp_path / "vb.log"
    autostart.log_result(res, log)
    line = log.read_text(encoding="utf-8")
    assert "[voice_bridge.autostart] INFO: Autostart enable: ok=True mechanism=run_key" in line


def test_default_run_key_is_faked_in_tests(_never_touch_real_run_key):
    assert autostart._default_run_key() is _never_touch_real_run_key


def test_installer_uninstall_deletes_run_value():
    iss = (REPO / "installer" / "VoiceBridge.iss").read_text(encoding="utf-8")
    assert ('delete ""HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"" '
            '/v ""{#AutostartTask}"" /f') in iss
    assert autostart.RUN_VALUE_NAME == autostart.TASK_NAME
    assert autostart.RUN_KEY_PATH == r"Software\Microsoft\Windows\CurrentVersion\Run"


def test_winreg_run_key_on_throwaway_hkcu_key():
    """Real winreg round trip on HKCU\\Software\\parlaconclaudio-test-<uuid>."""
    import winreg

    subkey = f"Software\\parlaconclaudio-test-{uuid.uuid4().hex}"
    rk = autostart.WinRegRunKey(subkey=subkey, name="vb-test")
    command = '"C:\\a b\\pythonw.exe" "C:\\a b\\scripts\\start_voice_bridge.py"'
    try:
        assert rk.get() is None and rk.delete() is False
        rk.set(command)
        assert rk.get() == command
        assert rk.delete() is True and rk.get() is None
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except FileNotFoundError:
            pass
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey)


def test_start_script_moves_to_root_and_runs_the_entry_module(tmp_path):
    """A Run value has no working dir: from an unrelated cwd the start script must
    chdir to the root and run src.voice_bridge as __main__ (run_module stubbed:
    no bridge, no mutex, no log write)."""
    script = REPO / "scripts" / "start_voice_bridge.py"
    code = (
        "import os, runpy, sys; seen = []; "
        "runpy.run_module = lambda name, **kw: seen.append((name, kw)); "
        f"runpy.run_path(r'{script}', run_name='__main__'); "
        f"assert os.path.samefile(os.getcwd(), r'{REPO}'), os.getcwd(); "
        "assert seen == [('src.voice_bridge', {'run_name': '__main__', 'alter_sys': True})], seen"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
