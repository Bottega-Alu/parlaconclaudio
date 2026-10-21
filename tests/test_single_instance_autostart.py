"""Feature A: single instance (named mutex) and the logon task definition.

No task is ever registered here: schtasks is replaced by a recording runner.
The mutex tests use a unique name, so a bridge running on this machine is
never affected.
"""
from __future__ import annotations

import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.voice_bridge import autostart, single_instance

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


# --------------------------------------------------------------------------- single instance


def test_real_mutex_second_acquire_sees_first():
    name = f"Local\\vb-test-{uuid.uuid4().hex}"
    h1, already1 = single_instance.acquire_mutex(name)
    try:
        assert h1 is not None and already1 is False
        h2, already2 = single_instance.acquire_mutex(name)
        assert already2 is True
        single_instance.release_mutex(h2)
    finally:
        single_instance.release_mutex(h1)
    h3, already3 = single_instance.acquire_mutex(name)  # released -> free again
    assert already3 is False
    single_instance.release_mutex(h3)


def test_second_instance_exits_without_scanning(monkeypatch):
    monkeypatch.setattr(single_instance, "release_mutex", lambda h: None)
    scanned = []
    ok, reason = single_instance.ensure_single_instance(
        acquire=lambda: (object(), True),
        scan=lambda excl: scanned.append(excl) or [],
    )
    assert ok is False and "another instance" in reason
    assert scanned == []  # never looks for (let alone kills) other processes


def test_first_instance_runs_when_no_legacy_bridge(monkeypatch):
    monkeypatch.setattr(single_instance, "_HANDLE", None)
    handle = object()
    ok, _ = single_instance.ensure_single_instance(acquire=lambda: (handle, False), scan=lambda excl: [])
    assert ok is True
    assert single_instance._HANDLE is handle  # kept alive for the process lifetime


def test_legacy_bridge_without_mutex_blocks_new_one(monkeypatch):
    monkeypatch.setattr(single_instance, "release_mutex", lambda h: None)
    ok, reason = single_instance.ensure_single_instance(
        acquire=lambda: (object(), False), scan=lambda excl: [4242]
    )
    assert ok is False and "4242" in reason


def test_scan_excludes_self_and_parses_pids():
    def fake_run(cmd, **kw):
        assert kw["creationflags"] & single_instance.CREATE_NO_WINDOW
        return subprocess.CompletedProcess(cmd, 0, stdout="111\r\n222\r\n\r\n", stderr="")

    assert single_instance.find_legacy_instances({111}, run=fake_run) == [222]


def test_scan_failure_is_not_fatal():
    def boom(cmd, **kw):
        raise OSError("powershell missing")

    assert single_instance.find_legacy_instances(set(), run=boom) == []


def test_scan_only_counts_older_processes():
    q = single_instance.legacy_query(1234)
    assert "ProcessId=1234" in q and "CreationDate -lt $me.CreationDate" in q


def test_main_entry_checks_mutex_before_importing_bridge():
    src = (Path(single_instance.__file__).parent / "__main__.py").read_text(encoding="utf-8")
    assert src.index("ensure_single_instance()") < src.index("from .bridge import main")


# --------------------------------------------------------------------------- autostart task


def _xml(root: Path) -> ET.Element:
    return ET.fromstring(autostart.build_task_xml(root, "PC\\frisco").replace('encoding="UTF-16"', ""))


def test_task_xml_logon_trigger_for_current_user_only(tmp_path):
    t = _xml(tmp_path)
    trig = t.find("t:Triggers/t:LogonTrigger", NS)
    assert trig.find("t:UserId", NS).text == "PC\\frisco"
    p = t.find("t:Principals/t:Principal", NS)
    assert p.find("t:LogonType", NS).text == "InteractiveToken"
    assert p.find("t:RunLevel", NS).text == "LeastPrivilege"


def test_task_xml_service_like_settings(tmp_path):
    s = _xml(tmp_path).find("t:Settings", NS)
    assert s.find("t:ExecutionTimeLimit", NS).text == "PT0S"  # no duration limit
    assert s.find("t:RestartOnFailure/t:Count", NS).text == "3"
    assert s.find("t:RestartOnFailure/t:Interval", NS).text == "PT1M"
    assert s.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    assert s.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    assert s.find("t:StopIfGoingOnBatteries", NS).text == "false"


def test_task_xml_runs_pythonw_module_from_root(tmp_path):
    root = tmp_path / "Program & Co"  # XML-escaped, not broken
    ex = _xml(root).find("t:Actions/t:Exec", NS)
    assert ex.find("t:Command", NS).text == str(root / "venv" / "Scripts" / "pythonw.exe")
    assert ex.find("t:Arguments", NS).text == "-m src.voice_bridge"
    assert ex.find("t:WorkingDirectory", NS).text == str(root)


def test_schtasks_commands():
    assert autostart.create_command(Path("C:/t.xml")) == [
        "schtasks", "/Create", "/TN", autostart.TASK_NAME, "/XML", str(Path("C:/t.xml")), "/F"]
    assert autostart.delete_command() == ["schtasks", "/Delete", "/TN", autostart.TASK_NAME, "/F"]
    assert autostart.query_command() == ["schtasks", "/Query", "/TN", autostart.TASK_NAME]


def test_task_name_matches_installer():
    iss = (Path(autostart.__file__).resolve().parents[2] / "installer" / "VoiceBridge.iss").read_text(encoding="utf-8")
    assert f'#define AutostartTask  "{autostart.TASK_NAME}"' in iss


class _Runner:
    def __init__(self, *codes, stderr=""):
        self.codes = list(codes)
        self.calls: list[list[str]] = []
        self.xml_seen: str | None = None
        self.stderr = stderr

    def __call__(self, cmd, **kw):
        assert kw["creationflags"] & autostart.CREATE_NO_WINDOW
        self.calls.append(cmd)
        if "/XML" in cmd:
            self.xml_seen = Path(cmd[cmd.index("/XML") + 1]).read_text(encoding="utf-16")
        return subprocess.CompletedProcess(cmd, self.codes.pop(0), stdout="", stderr=self.stderr)


def _fake_root(tmp_path: Path) -> Path:
    (tmp_path / "venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    return tmp_path


def test_enable_writes_utf16_xml_and_cleans_temp(tmp_path):
    runner = _Runner(0)
    res = autostart.enable(_fake_root(tmp_path), runner=runner, user_id="PC\\frisco")
    assert res.ok and runner.calls[0][:2] == ["schtasks", "/Create"]
    assert "<UserId>PC\\frisco</UserId>" in runner.xml_seen
    assert not Path(runner.calls[0][5]).exists()  # temp xml removed


def test_enable_without_venv_is_structured_error(tmp_path):
    res = autostart.enable(tmp_path, runner=_Runner())
    assert not res.ok and res.error_kind is autostart.AutostartErrorKind.NOT_INSTALLED


def test_enable_access_denied_classified_by_code(tmp_path):
    runner = _Runner(1, stderr="ERRORE: Accesso negato. (0x80070005)")
    from tests.conftest import FakeRunKey

    res = autostart.enable(_fake_root(tmp_path), runner=runner, user_id="u",
                           run_key=FakeRunKey(fail="set"))  # fallback fails too
    assert res.error_kind is autostart.AutostartErrorKind.RUN_KEY_FAILED
    assert res.task_error is autostart.AutostartErrorKind.ACCESS_DENIED
    assert "errore" in res.summary()


def test_disable_is_idempotent():
    runner = _Runner(1)  # /Query: not registered
    assert autostart.disable(runner=runner).ok
    assert [c[1] for c in runner.calls] == ["/Query"]  # no /Delete attempted


def test_disable_deletes_registered_task():
    runner = _Runner(0, 0)
    assert autostart.disable(runner=runner).ok
    assert [c[1] for c in runner.calls] == ["/Query", "/Delete"]


def test_schtasks_missing_is_not_enabled():
    def boom(cmd, **kw):
        raise FileNotFoundError("schtasks")

    assert autostart.is_enabled(runner=boom) is False
    res = autostart.disable(runner=boom)
    assert res.ok  # nothing registered -> nothing to remove


def test_pythonw_none_streams_are_made_safe():
    """Under pythonw sys.stdout/stderr are None. Importing the entry module must
    make print() and stderr writes harmless (no console behind them). Simulated
    in a child (a test runner always hands the child real std handles)."""
    import sys

    code = (
        "import sys; sys.stdout = None; sys.stderr = None; "
        "import src.voice_bridge.__main__; "
        "print('x'); sys.stderr.write('y'); sys.stderr.flush(); "
        "sys.exit(0 if sys.stdout is not None and sys.stderr is not None else 3)"
    )
    repo = Path(single_instance.__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo, timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""  # went to devnull, not to the inherited handle
