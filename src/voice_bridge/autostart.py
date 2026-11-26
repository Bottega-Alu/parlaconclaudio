"""Start the bridge at sign-in through a per-user Task Scheduler task.

Why a scheduled task and not a Windows Service: a service runs in session 0,
where there is no global hotkey, no clipboard/paste into the user's apps, no
tray and no per-user microphone. A logon task runs in the user's session with
the user's token and still gets service-like behaviour:

  trigger    at logon of THIS user (+10 s, audio devices settle)  -> no admin needed
  principal  InteractiveToken, LeastPrivilege                     -> no UAC
  settings   no execution time limit, restart on failure 3x / 1 min,
             runs on battery, IgnoreNew if already running
  action     <base pythonw.exe> -m src.voice_bridge  (no console),
             working dir <root>

The interpreter comes from gui_python.resolve(<root>\\venv): the venv's BASE
pythonw.exe (pyvenv.cfg `home`), not venv\\Scripts\\pythonw.exe - under a uv
venv that file is a console-subsystem trampoline, and a task that starts a
console program opens a console window. A task cannot set __PYVENV_LAUNCHER__,
so the supervisor runs outside the venv (it is stdlib-only) and starts the
bridge child inside it. Without a base pythonw.exe it falls back to
venv\\Scripts\\pythonw.exe. The Run value below uses the same interpreter.

The action runs pythonw directly, not the VoiceBridge.exe stub: the stub exits
at once (the task would end immediately) and it is an unsigned exe that Smart
App Control may block.

Fallback (enable decision tree):
  1. pythonw missing under root          -> NOT_INSTALLED, nothing registered
  2. task registered                     -> mechanism "task"; a stale Run value
                                            is removed (no double launch)
  3. task failed (access denied, any schtasks/COM error, schtasks missing)
                                         -> per-user Run key (RUN_KEY_PATH under
                                            HKCU), value TASK_NAME,
                                            mechanism "run_key"
  4. both failed                         -> RUN_KEY_FAILED with both reasons
A Run value has no working directory, so it runs scripts/start_voice_bridge.py,
which chdirs to the root and runs the same `-m src.voice_bridge` (the supervisor).
On / off: enabled = task present OR Run value present; disable removes both.

Administrator mode (opt-in, app window > Personalize > System): the task's
RunLevel is HighestAvailable, so the bridge runs elevated. Why: Windows gives a
normal process no keys while an elevated window (an Administrator terminal) is
in front (UIPI) - neither a keyboard hook nor RegisterHotKey - and blocks its
paste there. The task's RunLevel is the only record of the choice (no config
key): elevated = task present with HighestAvailable.
  enable(elevated=None)  keeps the current level. An installer upgrade runs it
                         unelevated: a HighestAvailable task whose action is
                         already right is left alone; one that must change is
                         re-created through a UAC prompt (runas), and if that is
                         declined the task drops back to a normal one.
  enable(elevated=True/False)  the switch; without admin rights the task is
                         written by an elevated copy of this module (UAC).
  relaunch               waits for the running bridge to exit, then starts the
                         task, so the bridge comes back at the new level.
The VoiceBridge.exe stub starts an elevated task with schtasks /Run instead of
starting pythonw itself, which would run unelevated.

One place: the tray toggle, the installer ([Code] after the bootstrap) and the
CLI below all go through enable()/disable()/is_enabled(). The uninstaller
deletes TASK_NAME with schtasks and the Run value with reg.exe directly (the
venv may already be gone).

    pythonw -m src.voice_bridge.autostart enable|disable|status [--root DIR]
"""

from __future__ import annotations

import argparse
import ctypes
import enum
import getpass
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from xml.sax.saxutils import escape

from . import gui_python
from .build_info import IS_TEST_BUILD

TASK_NAME = "parlaconclaudio-voice-bridge" + ("-test" if IS_TEST_BUILD else "")
APP_ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = 0x08000000

LOGON_DELAY = "PT10S"
RESTART_INTERVAL = "PT1M"
RESTART_COUNT = 3

# Fallback when the task cannot be registered. Value name = TASK_NAME; keep both
# in sync with installer/VoiceBridge.iss ([UninstallRun] reg delete).
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
RUN_LEVEL_NORMAL = "LeastPrivilege"
RUN_LEVEL_ELEVATED = "HighestAvailable"
RELAUNCH_WAIT_S = 30.0

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = TASK_NAME
START_SCRIPT = Path("scripts") / "start_voice_bridge.py"

MECHANISM_TASK = "task"
MECHANISM_RUN_KEY = "run_key"
MECHANISM_LABEL = {
    MECHANISM_TASK: "Utilita' di pianificazione",
    MECHANISM_RUN_KEY: "registro HKCU Run",
}

Runner = Callable[..., subprocess.CompletedProcess]


class RunKey(Protocol):
    """One string value under a per-user Run key. Errors raise OSError."""

    def get(self) -> str | None: ...

    def set(self, command: str) -> None: ...

    def delete(self) -> bool: ...  # True = removed, False = was absent


class WinRegRunKey:
    """HKCU\\<subkey>, value <name>. The subkey is a parameter so the tests can
    point it at a throwaway key under HKCU\\Software instead of the real Run."""

    def __init__(self, subkey: str = RUN_KEY_PATH, name: str = RUN_VALUE_NAME) -> None:
        self.subkey, self.name = subkey, name

    def get(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.subkey) as key:
                value, _ = winreg.QueryValueEx(key, self.name)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def set(self, command: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, self.name, 0, winreg.REG_SZ, command)

    def delete(self) -> bool:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.subkey, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, self.name)
        except FileNotFoundError:
            return False
        return True


def _default_run_key() -> RunKey:
    """Indirection point: tests/conftest.py swaps it for an in-memory fake, so
    no test can ever write the real Run value."""
    return WinRegRunKey()


class AutostartErrorKind(enum.Enum):
    ACCESS_DENIED = "access_denied"
    SCHTASKS_FAILED = "schtasks_failed"
    NOT_INSTALLED = "not_installed"  # venv/pythonw missing under root
    LAUNCH_FAILED = "launch_failed"  # schtasks.exe could not be started
    RUN_KEY_FAILED = "run_key_failed"  # registry Run value could not be written/removed


@dataclass(frozen=True)
class AutostartResult:
    action: str  # enable | disable
    ok: bool
    error_kind: AutostartErrorKind | None = None
    message: str = ""
    mechanism: str | None = None  # enable ok: "task" | "run_key"
    task_error: AutostartErrorKind | None = None  # why the task was not used (fallback)

    def summary(self) -> str:
        """One line for the tray notification."""
        if self.ok and self.action == "enable":
            how = MECHANISM_LABEL.get(self.mechanism or "", self.mechanism or "?")
            if self.mechanism == MECHANISM_RUN_KEY:
                why = self.task_error.value if self.task_error else "error"
                return f"Avvio automatico attivato (ripiego: {how}; task non registrato: {why})"
            return f"Avvio automatico attivato ({how})"
        if self.ok:
            return "Avvio automatico disattivato"
        kind = self.error_kind.value if self.error_kind else "error"
        return f"Avvio automatico: errore ({kind}) - {self.message}"

    def log_line(self) -> str:
        """One structured line for voicebridge.log (tray and CLI)."""
        return (f"Autostart {self.action}: ok={self.ok} mechanism={self.mechanism or '-'} "
                f"kind={self.error_kind.value if self.error_kind else '-'} "
                f"task_error={self.task_error.value if self.task_error else '-'} {self.message}")


def current_user_id(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    user = env.get("USERNAME") or getpass.getuser()
    domain = env.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def pythonw_path(root: Path) -> Path:
    """The venv's launcher: its presence is the "installed" check."""
    return gui_python.venv_launcher(root / "venv")


def launch_exe(root: Path) -> Path:
    """GUI-subsystem interpreter for the task / Run value (see module doc)."""
    return gui_python.resolve(root / "venv").exe


def build_task_xml(root: Path, user_id: str, elevated: bool = False) -> str:
    """Task Scheduler 1.2 definition. Pure: no I/O, asserted by the tests."""
    run_level = RUN_LEVEL_ELEVATED if elevated else RUN_LEVEL_NORMAL
    exe = escape(str(launch_exe(root)))
    workdir = escape(str(root))
    user = escape(user_id)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>{user}</Author>
    <Description>parlaconclaudio - voice-bridge: push-to-talk dictation (tray), started at sign-in.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>{LOGON_DELAY}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>{run_level}</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure>
      <Interval>{RESTART_INTERVAL}</Interval>
      <Count>{RESTART_COUNT}</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>-m src.voice_bridge</Arguments>
      <WorkingDirectory>{workdir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def run_command(root: Path) -> str:
    """Run-key command line: same supervisor as the task, via the start script
    (a Run value has no working directory for `-m src.voice_bridge`)."""
    return f'"{launch_exe(root)}" "{root / START_SCRIPT}"'


def create_command(xml_path: Path) -> list[str]:
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]


def delete_command() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def query_command() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME]


def query_xml_command() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/XML"]


def run_task_command() -> list[str]:
    return ["schtasks", "/Run", "/TN", TASK_NAME]


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


@dataclass(frozen=True)
class TaskDefinition:
    """What matters of the registered task, read back from Windows."""
    run_level: str
    command: str
    arguments: str
    workdir: str

    @property
    def elevated(self) -> bool:
        return self.run_level == RUN_LEVEL_ELEVATED

    def runs(self, root: Path) -> bool:
        """Same action as a task built now for root (the interpreter can move on upgrade)."""
        return (_same_path(self.command, str(launch_exe(root)))
                and self.arguments.strip() == "-m src.voice_bridge"
                and _same_path(self.workdir, str(root)))


def parse_task_xml(text: str) -> TaskDefinition | None:
    """Parser, not string search. None if it is not a task definition."""
    body = text.split("?>", 1)[1] if text.lstrip().startswith("<?xml") else text
    try:
        node = ET.fromstring(body.strip())
    except ET.ParseError:
        return None

    def find(path: str) -> str:
        return (node.findtext(path, default="", namespaces=TASK_NS) or "").strip()
    if node.tag != "{%s}Task" % TASK_NS["t"]:
        return None
    return TaskDefinition(run_level=find("./t:Principals/t:Principal/t:RunLevel") or RUN_LEVEL_NORMAL,
                          command=find("./t:Actions/t:Exec/t:Command"),
                          arguments=find("./t:Actions/t:Exec/t:Arguments"),
                          workdir=find("./t:Actions/t:Exec/t:WorkingDirectory"))


def task_definition(runner: Runner = subprocess.run) -> TaskDefinition | None:
    """The registered task, or None (absent, or schtasks unusable)."""
    try:
        proc = runner(query_xml_command(), capture_output=True, timeout=30,
                      stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    raw = proc.stdout
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            raw = raw.decode("mbcs", errors="replace")
    return parse_task_xml(raw)


def is_elevated_mode(runner: Runner = subprocess.run) -> bool:
    """Administrator mode on = the logon task runs with HighestAvailable."""
    task = task_definition(runner)
    return bool(task and task.elevated)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong), ("hwnd", ctypes.c_void_p),
                ("lpVerb", ctypes.c_wchar_p), ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int), ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                ("dwHotKey", ctypes.c_ulong), ("hIcon", ctypes.c_void_p), ("hProcess", ctypes.c_void_p)]


def elevated_command(root: Path, elevated: bool) -> tuple[str, str]:
    """(exe, parameters) for the UAC copy of this module. The code goes in -c with
    an explicit sys.path: an elevated process may not keep the working directory."""
    level = "on" if elevated else "off"
    code = (f"import sys; sys.path.insert(0, {str(root)!r}); "
            "from src.voice_bridge.autostart import main; "
            f"sys.exit(main(['enable', '--elevated', {level!r}, '--root', {str(root)!r}]))")
    return str(launch_exe(root)), subprocess.list2cmdline(["-c", code])


def run_as_admin(exe: str, params: str, workdir: str, timeout_s: float = 120.0) -> int | None:
    """Start exe elevated (UAC prompt) and wait. Exit code, or None when the
    prompt was declined or the process could not be started."""
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb, info.lpFile, info.lpParameters, info.lpDirectory = "runas", exe, params, workdir
    info.nShow = 0
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None  # ERROR_CANCELLED when the user says no
    kernel32 = ctypes.windll.kernel32
    try:
        kernel32.WaitForSingleObject(ctypes.c_void_p(info.hProcess), int(timeout_s * 1000))
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(info.hProcess), ctypes.byref(code)):
            return None
        return code.value
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(info.hProcess))


def _run(cmd: list[str], runner: Runner) -> subprocess.CompletedProcess:
    # CREATE_NO_WINDOW: called from pythonw, a console child would flash a window.
    return runner(cmd, capture_output=True, text=True, timeout=30,
                  stdin=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)


def _failure(action: str, proc: subprocess.CompletedProcess) -> AutostartResult:
    """schtasks prints localized prose; only the "access denied" case is
    told apart, by the Win32 code it embeds (0x80070005) - never by wording."""
    text = ((proc.stderr or "") + (proc.stdout or "")).strip()
    kind = (AutostartErrorKind.ACCESS_DENIED if "0x80070005" in text.lower()
            else AutostartErrorKind.SCHTASKS_FAILED)
    return AutostartResult(action, False, kind, f"schtasks exit {proc.returncode}: {text[-300:]}")


def task_present(runner: Runner = subprocess.run) -> bool:
    try:
        return _run(query_command(), runner).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_value_present(run_key: RunKey | None = None) -> bool:
    try:
        return (run_key or _default_run_key()).get() is not None
    except OSError:
        return False


def is_enabled(runner: Runner = subprocess.run, run_key: RunKey | None = None) -> bool:
    """Tray checked state: the task OR the Run-key fallback is registered."""
    return task_present(runner) or run_value_present(run_key)


def mechanisms(runner: Runner = subprocess.run, run_key: RunKey | None = None) -> list[str]:
    found = [MECHANISM_TASK] if task_present(runner) else []
    if run_value_present(run_key):
        found.append(MECHANISM_RUN_KEY)
    return found


def _enable_task(root: Path, runner: Runner, user_id: str | None,
                 elevated: bool = False) -> AutostartResult:
    xml = build_task_xml(root, user_id or current_user_id(), elevated)
    fd, tmp = tempfile.mkstemp(prefix="vb-task-", suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")  # BOM + UTF-16LE, as schtasks expects
        proc = _run(create_command(Path(tmp)), runner)
    except (OSError, subprocess.SubprocessError) as exc:
        return AutostartResult("enable", False, AutostartErrorKind.LAUNCH_FAILED, str(exc))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    if proc.returncode != 0:
        return _failure("enable", proc)
    level = RUN_LEVEL_ELEVATED if elevated else RUN_LEVEL_NORMAL
    return AutostartResult("enable", True, message=f"task {TASK_NAME} ({level})", mechanism=MECHANISM_TASK)


def _enable_with_uac(root: Path, elevated: bool, elevate: Callable[..., int | None]) -> AutostartResult:
    exe, params = elevated_command(root, elevated)
    code = elevate(exe, params, str(root))
    if code == 0:
        level = RUN_LEVEL_ELEVATED if elevated else RUN_LEVEL_NORMAL
        return AutostartResult("enable", True, message=f"task {TASK_NAME} ({level}, via UAC)",
                               mechanism=MECHANISM_TASK)
    why = "UAC declined or not started" if code is None else f"elevated enable exit {code}"
    return AutostartResult("enable", False, AutostartErrorKind.ACCESS_DENIED, why)


def enable(root: Path | None = None, runner: Runner = subprocess.run,
           user_id: str | None = None, run_key: RunKey | None = None,
           elevated: bool | None = None, admin: Callable[[], bool] = is_admin,
           elevate: Callable[..., int | None] = run_as_admin) -> AutostartResult:
    """elevated None keeps the task's current level (installer, autostart switch)."""
    root = (root or APP_ROOT).resolve()
    if not pythonw_path(root).is_file():
        return AutostartResult("enable", False, AutostartErrorKind.NOT_INSTALLED,
                               f"{pythonw_path(root)} not found")
    run_key = run_key or _default_run_key()
    current = task_definition(runner)
    keep_level = elevated is None
    want = elevated if elevated is not None else bool(current and current.elevated)
    if want and keep_level and current and current.elevated and current.runs(root):
        return AutostartResult("enable", True, message=f"task {TASK_NAME} ({RUN_LEVEL_ELEVATED}) kept",
                               mechanism=MECHANISM_TASK)
    # Writing or replacing an elevated task needs admin rights: ask Windows (UAC).
    if (want or (current is not None and current.elevated)) and not admin():
        task = _enable_with_uac(root, want, elevate)
        if task.ok:
            try:
                run_key.delete()
            except OSError:
                pass
            return task
        if not keep_level:
            return task
        # Upgrade with the prompt declined: a working normal task beats none.
        want = False
    task = _enable_task(root, runner, user_id, want)
    if task.ok:
        note = ""
        try:
            if run_key.delete():  # a stale fallback would start a second launcher
                note = "; stale Run value removed"
        except OSError as exc:
            note = f"; stale Run value NOT removed: {exc}"
        return AutostartResult("enable", True, message=task.message + note, mechanism=MECHANISM_TASK)

    command = run_command(root)
    try:
        run_key.set(command)
        if run_key.get() != command:
            raise OSError("Run value differs after write")
    except OSError as exc:
        return AutostartResult("enable", False, AutostartErrorKind.RUN_KEY_FAILED,
                               f"task: {task.message}; run key: {exc}", task_error=task.error_kind)
    return AutostartResult(
        "enable", True,
        message=f"HKCU\\{RUN_KEY_PATH}\\{RUN_VALUE_NAME} = {command}; task: {task.message}",
        mechanism=MECHANISM_RUN_KEY, task_error=task.error_kind)


def disable(runner: Runner = subprocess.run, run_key: RunKey | None = None) -> AutostartResult:
    """Removes the task AND the Run value. Idempotent: absent = already off."""
    run_key = run_key or _default_run_key()
    removed: list[str] = []
    errors: list[str] = []
    kind: AutostartErrorKind | None = None
    try:
        if task_present(runner):
            proc = _run(delete_command(), runner)
            if proc.returncode != 0:
                failed = _failure("disable", proc)
                kind = failed.error_kind
                errors.append(f"task: {failed.message}")
            else:
                removed.append(f"task {TASK_NAME} deleted")
    except (OSError, subprocess.SubprocessError) as exc:
        kind = AutostartErrorKind.LAUNCH_FAILED
        errors.append(f"task: {exc}")
    try:
        if run_key.delete():
            removed.append("Run value deleted")
    except OSError as exc:
        kind = kind or AutostartErrorKind.RUN_KEY_FAILED
        errors.append(f"run key: {exc}")
    if errors:
        return AutostartResult("disable", False, kind, "; ".join(errors + removed))
    return AutostartResult("disable", True, message="; ".join(removed) or "not registered")


def log_result(result: AutostartResult, log_path: Path | None = None) -> None:
    """CLI / installer path: append the outcome (mechanism included) to
    voicebridge.log, as the tray does through its logger."""
    path = log_path or (APP_ROOT / "voicebridge.log")
    level = "INFO" if result.ok else "ERROR"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} [voice_bridge.autostart] {level}: {result.log_line()}\n")
    except OSError:
        pass


def _process_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def relaunch(wait_pid: int, runner: Runner = subprocess.run,
             alive: Callable[[int], bool] = _process_alive, sleep: Callable[[float], None] = time.sleep,
             wait_s: float = RELAUNCH_WAIT_S) -> bool:
    """After the bridge (wait_pid) exits, start it again through the task: it
    comes back at the task's level. False if it did not exit or the run failed."""
    deadline = time.monotonic() + wait_s
    while alive(wait_pid):
        if time.monotonic() > deadline:
            return False
        sleep(0.2)
    sleep(0.5)  # the mutex goes with the process; leave Windows a moment
    try:
        return _run(run_task_command(), runner).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Voice Bridge autostart (Task Scheduler)")
    ap.add_argument("action", choices=["enable", "disable", "status", "relaunch"])
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--elevated", choices=["on", "off"], default=None,
                    help="enable: run the bridge as administrator (on) or not (off); default keeps it")
    ap.add_argument("--wait-pid", type=int, default=0, help="relaunch: bridge process to wait for")
    args = ap.parse_args(argv)
    if args.action == "relaunch":
        return 0 if relaunch(args.wait_pid) else 3
    if args.action == "status":
        found = mechanisms()
        if sys.stdout is not None:
            print(f"enabled ({'+'.join(found)})" if found else "disabled")
        return 0 if found else 1
    elevated = None if args.elevated is None else args.elevated == "on"
    result = enable(args.root, elevated=elevated) if args.action == "enable" else disable()
    log_result(result, (args.root or APP_ROOT).resolve() / "voicebridge.log")
    if sys.stdout is not None:
        print(result.summary())
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
