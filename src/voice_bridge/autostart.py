"""Start the bridge at sign-in through a per-user Task Scheduler task.

Why a scheduled task and not a Windows Service: a service runs in session 0,
where there is no global hotkey, no clipboard/paste into the user's apps, no
tray and no per-user microphone. A logon task runs in the user's session with
the user's token and still gets service-like behaviour:

  trigger    at logon of THIS user (+10 s, audio devices settle)  -> no admin needed
  principal  InteractiveToken, LeastPrivilege                     -> no UAC
  settings   no execution time limit, restart on failure 3x / 1 min,
             runs on battery, IgnoreNew if already running
  action     <root>\\venv\\Scripts\\pythonw.exe -m src.voice_bridge  (no console),
             working dir <root>

The action runs pythonw directly, not the VoiceBridge.exe stub: the stub exits
at once (the task would end immediately) and it is an unsigned exe that Smart
App Control may block.

One place: the tray toggle, the installer ([Code] after the bootstrap) and the
CLI below all go through enable()/disable()/is_enabled(). The uninstaller
deletes TASK_NAME with schtasks directly (the venv may already be gone).

    pythonw -m src.voice_bridge.autostart enable|disable|status [--root DIR]
"""

from __future__ import annotations

import argparse
import enum
import getpass
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

TASK_NAME = "parlaconclaudio-voice-bridge"  # keep in sync with installer/VoiceBridge.iss
APP_ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = 0x08000000

LOGON_DELAY = "PT10S"
RESTART_INTERVAL = "PT1M"
RESTART_COUNT = 3

Runner = Callable[..., subprocess.CompletedProcess]


class AutostartErrorKind(enum.Enum):
    ACCESS_DENIED = "access_denied"
    SCHTASKS_FAILED = "schtasks_failed"
    NOT_INSTALLED = "not_installed"  # venv/pythonw missing under root
    LAUNCH_FAILED = "launch_failed"  # schtasks.exe could not be started


@dataclass(frozen=True)
class AutostartResult:
    action: str  # enable | disable
    ok: bool
    error_kind: AutostartErrorKind | None = None
    message: str = ""

    def summary(self) -> str:
        if self.ok:
            return ("Avvio automatico attivato" if self.action == "enable"
                    else "Avvio automatico disattivato")
        kind = self.error_kind.value if self.error_kind else "error"
        return f"Avvio automatico: errore ({kind}) - {self.message}"


def current_user_id(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    user = env.get("USERNAME") or getpass.getuser()
    domain = env.get("USERDOMAIN")
    return f"{domain}\\{user}" if domain else user


def pythonw_path(root: Path) -> Path:
    return root / "venv" / "Scripts" / "pythonw.exe"


def build_task_xml(root: Path, user_id: str) -> str:
    """Task Scheduler 1.2 definition. Pure: no I/O, asserted by the tests."""
    exe = escape(str(pythonw_path(root)))
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
      <RunLevel>LeastPrivilege</RunLevel>
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


def create_command(xml_path: Path) -> list[str]:
    return ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]


def delete_command() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def query_command() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME]


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


def is_enabled(runner: Runner = subprocess.run) -> bool:
    try:
        return _run(query_command(), runner).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def enable(root: Path | None = None, runner: Runner = subprocess.run,
           user_id: str | None = None) -> AutostartResult:
    root = (root or APP_ROOT).resolve()
    if not pythonw_path(root).is_file():
        return AutostartResult("enable", False, AutostartErrorKind.NOT_INSTALLED,
                               f"{pythonw_path(root)} not found")
    xml = build_task_xml(root, user_id or current_user_id())
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
    return AutostartResult("enable", True, message=f"task {TASK_NAME}")


def disable(runner: Runner = subprocess.run) -> AutostartResult:
    """Idempotent: a task that does not exist is already disabled."""
    try:
        if not is_enabled(runner):
            return AutostartResult("disable", True, message="not registered")
        proc = _run(delete_command(), runner)
    except (OSError, subprocess.SubprocessError) as exc:
        return AutostartResult("disable", False, AutostartErrorKind.LAUNCH_FAILED, str(exc))
    if proc.returncode != 0:
        return _failure("disable", proc)
    return AutostartResult("disable", True, message=f"task {TASK_NAME} deleted")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Voice Bridge autostart (Task Scheduler)")
    ap.add_argument("action", choices=["enable", "disable", "status"])
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.action == "status":
        on = is_enabled()
        if sys.stdout is not None:
            print("enabled" if on else "disabled")
        return 0 if on else 1
    result = enable(args.root) if args.action == "enable" else disable()
    if sys.stdout is not None:
        print(result.summary())
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
