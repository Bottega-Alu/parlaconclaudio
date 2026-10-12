"""Bootstrap failure visibility: run log (tee), error classification, pause decision,
and the launcher's "setup incomplete" message.

installer/ and scripts/ are not packages, so both modules are loaded by path.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


boot = _load("vb_bootstrap_under_test", "installer/vb_bootstrap.py")
launcher = _load("vb_launcher_under_test", "scripts/vb_launcher.py")


class _Tty(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# --------------------------------------------------------------------------- tee / log


def test_tee_writes_to_console_and_log():
    console, log = io.StringIO(), io.StringIO()
    tee = boot.Tee(console, log)
    tee.write("hello\n")
    tee.flush()
    assert console.getvalue() == "hello\n"
    assert log.getvalue() == "hello\n"


def test_tee_survives_console_that_cannot_encode():
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", newline="\n")  # strict errors
    log = io.StringIO()
    boot.Tee(console, log).write("ok ✓\n")  # check mark not in cp1252
    console.flush()
    assert log.getvalue() == "ok ✓\n"
    assert raw.getvalue() == b"ok ?\n"


def test_tee_without_console_still_logs():
    log = io.StringIO()
    tee = boot.Tee(None, log)
    tee.write("x")
    tee.flush()
    assert log.getvalue() == "x"
    assert tee.isatty() is False


def test_run_log_appends_with_header_per_run(tmp_path):
    for argv in (["vb", "--root", "a"], ["vb", "--root", "b"]):
        handle, path = boot.open_run_log(tmp_path, argv)
        handle.write("body\n")
        handle.close()
    assert path == tmp_path / boot.LOG_FILE
    text = path.read_text(encoding="utf-8")
    assert text.count("===== vb_bootstrap run ") == 2
    assert "'--root', 'a'" in text and "'--root', 'b'" in text


def test_run_log_falls_back_to_temp_when_root_missing(tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    handle, path = boot.open_run_log(tmp_path / "does-not-exist", ["vb"], temp_dir=temp)
    handle.close()
    assert path == temp / boot.FALLBACK_LOG_FILE
    assert path.is_file()


def test_run_log_null_object_when_nothing_writable(tmp_path):
    handle, path = boot.open_run_log(tmp_path / "nope", ["vb"], temp_dir=tmp_path / "nope2")
    assert path is None
    assert isinstance(handle, boot.NullLog)
    assert handle.write("abc") == 3
    handle.flush()
    handle.close()


def test_run_streamed_tees_child_output_and_raises_with_tail():
    out = io.StringIO()
    rep = boot.Reporter()
    boot.run_streamed([sys.executable, "-c", "print('child-ok')"], rep, out=out)
    assert "child-ok" in out.getvalue()

    out = io.StringIO()
    code = "import sys; sys.stderr.write('boom [WinError 4551] x\\n'); sys.exit(7)"
    with pytest.raises(boot.CommandFailed) as info:
        boot.run_streamed([sys.executable, "-c", code], rep, out=out)
    assert info.value.returncode == 7
    assert "WinError 4551" in info.value.tail
    assert "WinError 4551" in out.getvalue()  # stderr merged into the tee


# --------------------------------------------------------------------------- classification


def _oserror(winerror: int) -> OSError:
    return OSError(None, "msg", None, winerror)


@pytest.mark.parametrize(
    "exc, kind",
    [
        (_oserror(4551), "BLOCKED"),  # Smart App Control / App Control policy
        (_oserror(1260), "BLOCKED"),  # blocked by group policy
        (_oserror(225), "BLOCKED"),  # Defender
        (_oserror(5), "PERMISSION"),  # access denied
        (_oserror(32), "PERMISSION"),  # sharing violation
        (PermissionError(13, "denied"), "PERMISSION"),
        (FileNotFoundError(2, "missing"), "FAILED"),
        (RuntimeError("uv install completed but uv.exe was not found"), "FAILED"),
        (boot.CommandFailed(["uv"], 2, "error: ... (os error 4551)"), "BLOCKED"),
        (boot.CommandFailed(["uv"], 2, "Access is denied. (os error 5)"), "PERMISSION"),
        (boot.CommandFailed(["uv"], 1, "[WinError 2] x\n(os error 5)"), "PERMISSION"),
        (boot.CommandFailed(["uv"], 1, "No solution found for numpy"), "FAILED"),
        # Codes are matched, phrasing is not: prose alone does not classify.
        (boot.CommandFailed(["uv"], 1, "blocked by policy"), "FAILED"),
    ],
)
def test_classify_error(exc, kind):
    assert boot.classify_error(exc) is boot.ErrorKind[kind]


def test_describe_failure_maps_exit_code_and_hint():
    failure = boot.describe_failure(_oserror(4551))
    assert failure.exit_code == boot.EXIT_BLOCKED == 4
    assert "Smart App Control" in failure.hint
    assert boot.describe_failure(PermissionError(13, "x")).exit_code == boot.EXIT_PERMISSION
    assert boot.describe_failure(RuntimeError("x")).exit_code == boot.EXIT_FAILED


# --------------------------------------------------------------------------- pause decision


@pytest.mark.parametrize(
    "no_pause, stdin, expected",
    [
        (False, _Tty(True), True),
        (True, _Tty(True), False),
        (False, _Tty(False), False),
        (False, None, False),
    ],
)
def test_should_pause(no_pause, stdin, expected):
    assert boot.should_pause(no_pause, stdin) is expected


def test_should_pause_closed_stdin():
    stdin = _Tty(True)
    stdin.close()
    stdin.isatty = lambda: (_ for _ in ()).throw(ValueError("closed"))  # type: ignore
    assert boot.should_pause(False, stdin) is False


def test_pause_for_user_swallows_eof():
    def prompt(_msg):
        raise EOFError

    assert boot.pause_for_user(False, _Tty(True), prompt=prompt) is True
    assert boot.pause_for_user(True, _Tty(True), prompt=prompt) is False


# --------------------------------------------------------------------------- main end-to-end


def test_main_missing_payload_logs_and_exits_2(tmp_path, capsys):
    code = boot.main(["--root", str(tmp_path), "--no-pause"])
    assert code == boot.EXIT_NO_PAYLOAD
    log = (tmp_path / boot.LOG_FILE).read_text(encoding="utf-8")
    assert "payload not found" in log
    assert "Repair runtime" in log
    assert str(tmp_path / boot.LOG_FILE) in log
    assert "===== exit 2 =====" in log
    assert sys.stdout is not None and not isinstance(sys.stdout, boot.Tee)


# --------------------------------------------------------------------------- launcher


def test_launcher_message_has_no_control_chars(tmp_path):
    msg = launcher._not_found_message([tmp_path])
    assert all(ch in "\n" or ord(ch) >= 32 for ch in msg), repr(msg)
    assert "voice_bridge" in msg
    assert "Setup incomplete" not in msg


def test_launcher_message_points_at_bootstrap_log(tmp_path):
    (tmp_path / "bootstrap.log").write_text("x", encoding="utf-8")
    msg = launcher._not_found_message([tmp_path])
    assert "Setup incomplete" in msg
    assert str(tmp_path / "bootstrap.log") in msg
    assert "Repair runtime" in msg
