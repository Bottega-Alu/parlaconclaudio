"""Administrator mode: the logon task runs HighestAvailable so dictation works
over Administrator windows; upgrades keep it, the switch changes it via UAC."""
import subprocess
from pathlib import Path

from src.voice_bridge import autostart
from tests.conftest import FakeRunKey
from tests.test_single_instance_autostart import _Runner, _fake_root


def _registered(root: Path, level: str, command: str | None = None) -> str:
    xml = autostart.build_task_xml(root, "PC\\frisco", level == autostart.RUN_LEVEL_ELEVATED)
    if command:
        xml = xml.replace(f"<Command>{autostart.launch_exe(root)}</Command>", f"<Command>{command}</Command>")
    return xml


def test_task_xml_run_level_follows_the_choice(tmp_path):
    root = _fake_root(tmp_path)
    normal = autostart.parse_task_xml(autostart.build_task_xml(root, "u"))
    elevated = autostart.parse_task_xml(autostart.build_task_xml(root, "u", elevated=True))
    assert normal.run_level == "LeastPrivilege" and not normal.elevated
    assert elevated.run_level == "HighestAvailable" and elevated.elevated
    assert elevated.runs(root) and elevated.arguments == "-m src.voice_bridge"


def test_parse_task_xml_reads_what_schtasks_prints(tmp_path):
    root = _fake_root(tmp_path)
    printed = _registered(root, autostart.RUN_LEVEL_ELEVATED).replace("\n", "\r\r\n")
    task = autostart.parse_task_xml(printed)
    assert task.elevated and task.runs(root)
    assert autostart.parse_task_xml("not xml") is None
    assert autostart.parse_task_xml("<Other/>") is None


def test_is_elevated_mode_reads_the_registered_task(tmp_path):
    root = _fake_root(tmp_path)
    assert autostart.is_elevated_mode(_Runner(registered=_registered(root, autostart.RUN_LEVEL_ELEVATED)))
    assert not autostart.is_elevated_mode(_Runner(registered=_registered(root, autostart.RUN_LEVEL_NORMAL)))
    assert not autostart.is_elevated_mode(_Runner())


def _no_uac(*args):
    raise AssertionError("no UAC prompt expected")


def test_upgrade_keeps_a_correct_elevated_task_without_prompting(tmp_path):
    root = _fake_root(tmp_path)
    runner = _Runner(registered=_registered(root, autostart.RUN_LEVEL_ELEVATED))
    res = autostart.enable(root, runner=runner, user_id="u", admin=lambda: False, elevate=_no_uac)
    assert res.ok and "kept" in res.message and runner.calls == []  # nothing rewritten


def test_upgrade_with_a_moved_interpreter_renews_the_elevated_task_via_uac(tmp_path):
    root = _fake_root(tmp_path)
    runner = _Runner(registered=_registered(root, autostart.RUN_LEVEL_ELEVATED, command=r"C:\old\pythonw.exe"))
    asked = []
    res = autostart.enable(root, runner=runner, user_id="u", admin=lambda: False,
                           elevate=lambda exe, params, cwd: asked.append(params) or 0)
    assert res.ok and "via UAC" in res.message and runner.calls == []
    assert "'--elevated', 'on'" in asked[0]


def test_upgrade_with_uac_declined_falls_back_to_a_normal_task(tmp_path):
    root = _fake_root(tmp_path)
    runner = _Runner(0, registered=_registered(root, autostart.RUN_LEVEL_ELEVATED, command=r"C:\old\pythonw.exe"))
    res = autostart.enable(root, runner=runner, user_id="u", admin=lambda: False,
                           elevate=lambda *a: None)
    assert res.ok and "LeastPrivilege" in res.message
    assert "<RunLevel>LeastPrivilege</RunLevel>" in runner.xml_seen


def test_switch_on_without_admin_goes_through_uac_and_reports_a_refusal(tmp_path):
    root = _fake_root(tmp_path)
    run_key = FakeRunKey()
    run_key.set("stale")
    ok = autostart.enable(root, runner=_Runner(), user_id="u", run_key=run_key, elevated=True,
                          admin=lambda: False, elevate=lambda *a: 0)
    assert ok.ok and run_key.get() is None  # the old fallback would start a second, normal bridge
    refused = autostart.enable(root, runner=_Runner(), user_id="u", elevated=True,
                               admin=lambda: False, elevate=lambda *a: None)
    assert not refused.ok and refused.error_kind is autostart.AutostartErrorKind.ACCESS_DENIED


def test_switch_as_admin_writes_the_task_directly(tmp_path):
    root = _fake_root(tmp_path)
    on = _Runner(0)
    assert autostart.enable(root, runner=on, user_id="u", elevated=True, admin=lambda: True, elevate=_no_uac).ok
    assert "<RunLevel>HighestAvailable</RunLevel>" in on.xml_seen
    off = _Runner(0, registered=_registered(root, autostart.RUN_LEVEL_ELEVATED))
    assert autostart.enable(root, runner=off, user_id="u", elevated=False, admin=lambda: True, elevate=_no_uac).ok
    assert "<RunLevel>LeastPrivilege</RunLevel>" in off.xml_seen


def test_switch_off_of_an_elevated_task_without_admin_uses_uac(tmp_path):
    root = _fake_root(tmp_path)
    asked = []
    res = autostart.enable(root, runner=_Runner(registered=_registered(root, autostart.RUN_LEVEL_ELEVATED)),
                           user_id="u", elevated=False, admin=lambda: False,
                           elevate=lambda exe, params, cwd: asked.append(params) or 0)
    assert res.ok and "'--elevated', 'off'" in asked[0]


def test_elevated_command_runs_this_module_from_the_install_root(tmp_path):
    root = _fake_root(tmp_path)
    exe, params = autostart.elevated_command(root, True)
    assert exe == str(autostart.launch_exe(root))
    assert params.startswith("-c ") and repr(str(root)) in params and "main(['enable'" in params


def test_relaunch_waits_for_the_bridge_then_runs_the_task():
    states = [True, True, False]
    runner = _Runner(0)
    assert autostart.relaunch(1234, runner=runner, alive=lambda pid: states.pop(0), sleep=lambda s: None)
    assert runner.calls == [autostart.run_task_command()]


def test_relaunch_gives_up_if_the_bridge_never_exits():
    runner = _Runner()
    assert not autostart.relaunch(1234, runner=runner, alive=lambda pid: True, sleep=lambda s: None, wait_s=0)
    assert runner.calls == []


def test_cli_passes_the_choice(monkeypatch, tmp_path):
    seen = {}
    def fake_enable(root, elevated=None):
        seen["e"] = elevated
        return autostart.AutostartResult("enable", True, mechanism="task")
    monkeypatch.setattr(autostart, "enable", fake_enable)
    monkeypatch.setattr(autostart, "log_result", lambda *a: None)
    assert autostart.main(["enable", "--elevated", "on", "--root", str(tmp_path)]) == 0
    assert seen["e"] is True
    seen.clear()
    autostart.main(["enable", "--root", str(tmp_path)])
    assert seen["e"] is None  # installer: keep the current level


def test_real_schtasks_xml_query_is_parsed_or_absent():
    """Read-only call against this PC: either no task or a parsable one."""
    try:
        task = autostart.task_definition()
    except Exception as exc:  # pragma: no cover - must never raise
        raise AssertionError(exc)
    assert task is None or task.run_level in ("LeastPrivilege", "HighestAvailable")
    assert subprocess.run  # keep import used


def _launcher():
    import importlib.util
    repo = Path(autostart.__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("vb_launcher_elevated", repo / "scripts" / "vb_launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_start_menu_stub_starts_the_elevated_task_of_this_install(tmp_path):
    vb = _launcher()
    root = _fake_root(tmp_path)
    runner = _Runner(0, registered=_registered(root, autostart.RUN_LEVEL_ELEVATED))
    assert vb.start_elevated_task(root, runner=runner)
    assert runner.calls == [autostart.run_task_command()]


def test_start_menu_stub_starts_pythonw_itself_otherwise(tmp_path):
    vb = _launcher()
    root = _fake_root(tmp_path)
    for runner in (_Runner(), _Runner(registered=_registered(root, autostart.RUN_LEVEL_NORMAL)),
                   _Runner(registered=_registered(root, autostart.RUN_LEVEL_ELEVATED, command=r"C:\other\pythonw.exe"))):
        assert not vb.start_elevated_task(root, runner=runner)
        assert runner.calls == []
    failing = _Runner(1, registered=_registered(root, autostart.RUN_LEVEL_ELEVATED))
    assert not vb.start_elevated_task(root, runner=failing)  # schtasks /Run failed: normal start


def test_restart_via_task_spawns_relaunch_for_the_supervisor_then_exits(monkeypatch):
    import os
    import sys
    from types import SimpleNamespace
    from src.voice_bridge import tray_icon
    spawned, exited = [], []
    monkeypatch.setattr(tray_icon.subprocess, "Popen", lambda args, **kw: spawned.append((args, kw)))
    monkeypatch.setattr(sys, "argv", ["x", "--child"])
    tray = SimpleNamespace(_exit_clicked=lambda icon, item: exited.append(True))
    assert tray_icon.TrayIcon._restart_via_task(tray)
    args, kw = spawned[0]
    assert args[1:5] == ["-m", "src.voice_bridge.autostart", "relaunch", "--wait-pid"]
    assert args[5] == str(os.getppid())  # the supervisor holds the mutex, not the child
    assert kw["creationflags"] & 0x08000000 and exited == [True]


def test_restart_via_task_keeps_the_app_when_nothing_can_be_spawned(monkeypatch):
    from types import SimpleNamespace
    from src.voice_bridge import tray_icon

    def boom(*a, **k):
        raise OSError("denied")
    monkeypatch.setattr(tray_icon.subprocess, "Popen", boom)
    exited = []
    tray = SimpleNamespace(_exit_clicked=lambda icon, item: exited.append(True))
    assert not tray_icon.TrayIcon._restart_via_task(tray)
    assert exited == []


def test_panel_switch_restarts_only_when_windows_confirmed(monkeypatch):
    from types import SimpleNamespace
    from src.voice_bridge import control_panel
    calls = []
    tray = SimpleNamespace(_notify=lambda m: calls.append(("notify", m)),
                           _restart_via_task=lambda: calls.append(("restart",)))
    panel = SimpleNamespace(tray=tray, _elevated=False, _autostart=False, _work=lambda fn: fn())
    monkeypatch.setattr(autostart, "enable", lambda elevated=None: autostart.AutostartResult(
        "enable", False, autostart.AutostartErrorKind.ACCESS_DENIED, "UAC declined"))
    monkeypatch.setattr(autostart, "is_elevated_mode", lambda: False)
    control_panel.ControlPanel._toggle_elevated(panel, True)
    assert [c[0] for c in calls] == ["notify"] and panel._elevated is False
    calls.clear()
    monkeypatch.setattr(autostart, "enable", lambda elevated=None: autostart.AutostartResult(
        "enable", True, mechanism="task"))
    control_panel.ControlPanel._toggle_elevated(panel, True)
    assert [c[0] for c in calls] == ["notify", "restart"] and panel._elevated is True and panel._autostart
