"""Supervisor: the bridge runs as a child and is relaunched after a crash.

Unit tests drive supervise() with fake children and a fake clock; the
integration tests spawn REAL child processes (a tiny python -c script that
crashes N times, tracked through a counter file), never the bridge.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.voice_bridge import __main__ as entry
from src.voice_bridge import supervisor as sup


class _Child:
    def __init__(self, pid: int, code: int, clock: "_Clock", runtime: float):
        self.pid, self._code, self._clock, self._runtime = pid, code, clock, runtime

    def wait(self) -> int:
        self._clock.t += self._runtime
        return self._code


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _Spawner:
    """Children exit with the given codes in order, each after `runtime` s."""

    def __init__(self, codes, clock, runtime=1.0):
        self.codes, self.clock, self.runtime = list(codes), clock, runtime
        self.calls: list[bool] = []

    def __call__(self, append_log: bool):
        self.calls.append(append_log)
        return _Child(100 + len(self.calls), self.codes.pop(0), self.clock, self.runtime)


def _run(codes, runtime=1.0, **kw):
    clock, lines, sleeps = _Clock(), [], []

    def sleep(s):
        sleeps.append(s)
        clock.t += s

    spawner = _Spawner(codes, clock, runtime)
    out = sup.supervise(spawn=spawner, log=lines.append, clock=clock, sleep=sleep, **kw)
    return out, spawner, lines, sleeps


def _payload(line: str) -> dict:
    return json.loads(line[line.index("{"):])


def test_voluntary_exit_is_not_relaunched():
    out, spawner, lines, sleeps = _run([0])
    assert (out.exit_code, out.reason) == (0, "voluntary_exit")
    assert spawner.calls == [False] and lines == [] and sleeps == []


def test_crash_then_ok_relaunches_once_with_append_log():
    out, spawner, lines, sleeps = _run([1, 0])
    assert out.exit_code == 0 and out.relaunches == 1
    assert spawner.calls == [False, True]  # relaunch keeps the crash log
    assert "supervisor_child_crashed" in lines[0] and _payload(lines[0])["exit_code"] == 1
    assert sleeps == [sup.BACKOFF_S]


def test_three_relaunches_then_gives_up_with_structured_error():
    out, spawner, lines, _ = _run([1, 2, 3, 0xC0000005, 0])
    assert (out.exit_code, out.reason) == (sup.EXIT_GAVE_UP, "crash_loop")
    assert len(spawner.calls) == 4  # first start + 3 relaunches, then stop
    assert spawner.codes == [0]      # the 5th child was never started
    last = lines[-1]
    assert "ERROR: supervisor_gave_up" in last
    p = _payload(last)
    assert p["kind"] == "crash_loop" and p["relaunches_in_window"] == 3
    assert p["exit_codes"] == [1, 2, 3, 0xC0000005]


def test_crashes_spread_beyond_the_window_keep_relaunching():
    # each child lives 200 s: at most 2 relaunches fall inside any 300 s window
    out, spawner, _, _ = _run([1, 1, 1, 1, 1, 0], runtime=200.0)
    assert out.exit_code == 0 and len(spawner.calls) == 6


def test_spawn_failure_is_structured():
    lines = []

    def boom(append_log):
        raise FileNotFoundError("pythonw.exe")

    out = sup.supervise(spawn=boom, log=lines.append)
    assert out.exit_code == sup.EXIT_SPAWN_FAILED
    assert _payload(lines[0])["kind"] == "spawn_failed"


def test_child_command_is_module_child_flag():
    assert sup.child_command(False, "py.exe") == ["py.exe", "-m", "src.voice_bridge", "--child"]
    assert sup.child_command(True, "py.exe")[-1] == "--append-log"


# --------------------------------------------------------------------------- entry routing


def test_entry_routes_child_and_supervisor(monkeypatch):
    seen = []
    monkeypatch.setattr(entry, "run_child", lambda: seen.append("child") or 0)
    monkeypatch.setattr(entry, "run_supervisor", lambda: seen.append("sup") or 0)
    entry.run(["--child", "--append-log"])
    entry.run([])
    assert seen == ["child", "sup"]


def test_second_launch_exits_without_supervising(monkeypatch):
    monkeypatch.setattr(entry, "ensure_single_instance", lambda: (False, "held"))
    monkeypatch.setattr(entry, "_note", lambda line: None)
    monkeypatch.setattr(entry, "supervise", lambda: pytest.fail("must not supervise"))
    assert entry.run_supervisor() == 0


def test_supervisor_holds_the_mutex_before_supervising(monkeypatch):
    order = []
    monkeypatch.setattr(entry, "ensure_single_instance", lambda: order.append("mutex") or (True, "first"))
    monkeypatch.setattr(entry, "supervise",
                        lambda: order.append("supervise") or sup.SupervisorOutcome(0, "voluntary_exit"))
    assert entry.run_supervisor() == 0 and order == ["mutex", "supervise"]


def test_supervisor_path_never_imports_bridge():
    """bridge.py truncates voicebridge.log at import: the supervisor (and a
    second launch) must not import it. Checked in a clean interpreter."""
    code = (
        "import sys; import src.voice_bridge.__main__ as m; "
        "sys.exit(1 if 'src.voice_bridge.bridge' in sys.modules else 0)"
    )
    repo = Path(sup.__file__).resolve().parents[2]
    proc = subprocess.run([sys.executable, "-c", code], cwd=repo, timeout=60,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------- real processes

_FAKE_CHILD = (
    "import sys, pathlib; p = pathlib.Path(sys.argv[1]); "
    "n = int(p.read_text()) if p.exists() else 0; p.write_text(str(n + 1)); "
    "codes = [int(c) for c in sys.argv[2].split(',')]; "
    "sys.exit(codes[min(n, len(codes) - 1)])"
)


def _real_spawner(counter: Path, codes: str, seen: list):
    def spawn(append_log: bool):
        seen.append(append_log)
        return subprocess.Popen([sys.executable, "-c", _FAKE_CHILD, str(counter), codes],
                                stdin=subprocess.DEVNULL)
    return spawn


def test_real_child_crashing_twice_then_exiting_zero(tmp_path):
    counter, seen, log = tmp_path / "n.txt", [], tmp_path / "vb.log"
    out = sup.supervise(spawn=_real_spawner(counter, "7,7,0", seen),
                        log=lambda line: sup.append_log(line, log), sleep=lambda s: None)
    assert out.exit_code == 0 and out.exit_codes == [7, 7, 0]
    assert counter.read_text() == "3" and seen == [False, True, True]
    assert log.read_text(encoding="utf-8").count("supervisor_child_crashed") == 2


def test_real_child_crash_loop_stops_after_three_relaunches(tmp_path):
    counter, seen, log = tmp_path / "n.txt", [], tmp_path / "vb.log"
    out = sup.supervise(spawn=_real_spawner(counter, "9", seen),
                        log=lambda line: sup.append_log(line, log), sleep=lambda s: None)
    assert out.exit_code == sup.EXIT_GAVE_UP
    assert counter.read_text() == "4"  # 1 start + 3 relaunches
    assert "supervisor_gave_up" in log.read_text(encoding="utf-8")
