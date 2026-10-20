"""Bootstrap upgrade path for the Claude Code TTS hook.

The hook runs with whatever interpreter settings.json names, not the venv, so an
upgrade must raise edge-tts THERE and refresh the script copy it points at.
Subprocess-free: the interpreter probe/upgrade are monkeypatched.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


boot = _load("vb_bootstrap_hook_under_test", "installer/vb_bootstrap.py")


class _Rep:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg: str) -> None:
        self.lines.append(msg)

    step = fail = info


def _settings(*commands: str) -> dict:
    return {
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": c} for c in commands]}],
            "Notification": [{"hooks": [{"type": "command", "command": commands[0]}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "other.sh"}]}],
        }
    }


# --------------------------------------------------------------------------- pins


def test_base_deps_pin_edge_tts_floor():
    assert "edge-tts>=7.3.1" in boot.BASE_DEPS
    assert not any(d.startswith("edge-tts") and d != "edge-tts>=7.3.1" for d in boot.BASE_DEPS)


def test_pyproject_pins_same_floor():
    import tomllib

    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["tool"]["poetry"]["dependencies"]["edge-tts"] == ">=7.3.1"


def test_version_compare():
    assert boot.edge_tts_outdated(None)
    assert boot.edge_tts_outdated("7.2.7")
    assert boot.edge_tts_outdated("6.1.12")
    assert not boot.edge_tts_outdated("7.3.1")
    assert not boot.edge_tts_outdated("7.10.0")
    assert not boot.edge_tts_outdated("8.0")


# --------------------------------------------------------------------------- parsing


def test_find_hook_commands_dedups_and_filters():
    cmd = "python C:/Users/x/.claude/scripts/notify-tts.py"
    assert boot.find_hook_commands(_settings(cmd)) == [cmd]
    assert boot.find_hook_commands({}) == []
    assert boot.find_hook_commands({"hooks": "garbage"}) == []


def test_parse_forward_slash_command():
    t = boot.parse_hook_command("python C:/Users/x/.claude/scripts/notify-tts.py")
    assert t.interpreter == ("python",)
    assert t.script == Path("C:/Users/x/.claude/scripts/notify-tts.py")


def test_parse_quoted_backslash_paths_and_launcher_args():
    t = boot.parse_hook_command(
        r'"C:\Program Files\Python311\python.exe" "C:\My Apps\VoiceBridge\scripts\notify-tts.py" --x'
    )
    assert t.interpreter == (r"C:\Program Files\Python311\python.exe",)
    assert t.script == Path(r"C:\My Apps\VoiceBridge\scripts\notify-tts.py")
    t2 = boot.parse_hook_command("py -3 C:/a/notify-tts.py")
    assert t2.interpreter == ("py", "-3")


def test_parse_rejects_script_only_and_unrelated():
    assert boot.parse_hook_command("C:/a/notify-tts.py") is None
    assert boot.parse_hook_command("python other.py") is None


# --------------------------------------------------------------------------- sync


def _setup(tmp_path: Path, hook_script: Path, interp: str = "python"):
    root = tmp_path / "app"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "notify-tts.py").write_text("NEW", encoding="utf-8")
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(_settings(f"{interp} {hook_script.as_posix()}")), encoding="utf-8")
    return root, settings


def _fake_python(monkeypatch, versions: list[str | None], upgrades: list):
    monkeypatch.setattr(boot, "resolve_interpreter", lambda toks: [f"/bin/{toks[0]}", *toks[1:]])
    seq = iter(versions)
    monkeypatch.setattr(boot, "installed_edge_tts", lambda interp: next(seq))
    monkeypatch.setattr(boot, "upgrade_edge_tts", lambda interp, rep: upgrades.append(interp))


def test_outdated_interpreter_upgraded_and_external_script_replaced(tmp_path, monkeypatch):
    ext = tmp_path / "claude" / "scripts" / "notify-tts.py"
    ext.parent.mkdir(parents=True)
    ext.write_text("OLD", encoding="utf-8")
    root, settings = _setup(tmp_path, ext)
    upgrades: list = []
    _fake_python(monkeypatch, ["7.2.7", "7.3.1"], upgrades)
    settings_before = settings.read_bytes()

    res = boot.sync_tts_hook(root, _Rep(), settings)

    assert upgrades == [["/bin/python"]]  # once, though two events share the command
    assert res[0] == "upgraded /bin/python edge-tts 7.2.7 -> 7.3.1"
    assert res[1].startswith("script-updated")
    assert ext.read_text(encoding="utf-8") == "NEW"
    backups = list(ext.parent.glob("notify-tts.py.bak-*"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == "OLD"
    assert settings.read_bytes() == settings_before  # settings.json is never written


def test_current_interpreter_and_identical_script_untouched(tmp_path, monkeypatch):
    ext = tmp_path / "notify-tts.py"
    ext.write_text("NEW", encoding="utf-8")
    root, settings = _setup(tmp_path, ext)
    upgrades: list = []
    _fake_python(monkeypatch, ["7.3.1"], upgrades)

    res = boot.sync_tts_hook(root, _Rep(), settings)

    assert upgrades == []
    assert res == ["ok /bin/python edge-tts 7.3.1", f"script-same {ext}"]
    assert not list(tmp_path.glob("*.bak-*"))


def test_script_inside_install_root_left_to_installer(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root, settings = _setup(tmp_path, root / "scripts" / "notify-tts.py")
    _fake_python(monkeypatch, ["7.3.1"], [])
    res = boot.sync_tts_hook(root, _Rep(), settings)
    assert res[1].startswith("script-in-root")


def test_failures_never_raise(tmp_path, monkeypatch):
    ext = tmp_path / "missing" / "notify-tts.py"
    root, settings = _setup(tmp_path, ext)

    def boom(interp):
        raise RuntimeError("probe failed")

    monkeypatch.setattr(boot, "resolve_interpreter", lambda toks: ["/bin/python"])
    monkeypatch.setattr(boot, "installed_edge_tts", boom)
    res = boot.sync_tts_hook(root, _Rep(), settings)
    assert res == ["error /bin/python RuntimeError", f"script-missing {ext}"]

    settings.write_text("{not json", encoding="utf-8")
    assert boot.sync_tts_hook(root, _Rep(), settings) == ["error JSONDecodeError"]
    assert boot.sync_tts_hook(root, _Rep(), tmp_path / "nope.json") == []


def test_interpreter_not_found(tmp_path, monkeypatch):
    ext = tmp_path / "notify-tts.py"
    ext.write_text("NEW", encoding="utf-8")
    root, settings = _setup(tmp_path, ext, interp="nosuchpython")
    monkeypatch.setattr(boot, "resolve_interpreter", lambda toks: None)
    res = boot.sync_tts_hook(root, _Rep(), settings)
    assert res[0] == "interpreter-not-found nosuchpython"


def test_claude_config_dir_env():
    assert boot.claude_config_dir({"CLAUDE_CONFIG_DIR": "D:/cc"}) == Path("D:/cc")
    assert boot.claude_config_dir({}) == Path.home() / ".claude"


def test_backup_name_is_timestamped(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    src = tmp_path / "src.py"
    src.write_text("NEW", encoding="utf-8")
    tgt = tmp_path / "notify-tts.py"
    tgt.write_text("OLD", encoding="utf-8")
    out = boot.sync_hook_script(src, tgt, root, _Rep(), now=dt.datetime(2026, 10, 20, 9, 8, 7))
    assert out == "updated"
    assert (tmp_path / "notify-tts.py.bak-20261020-090807").read_text(encoding="utf-8") == "OLD"


# --------------------------------------------------------------------------- venv on upgrade


def test_venv_action_decision_tree(tmp_path):
    venv = tmp_path / "venv"
    assert boot.venv_action(venv, runs=lambda p: True) == boot.VENV_CREATE
    venv.mkdir()
    assert boot.venv_action(venv, runs=lambda p: True) == boot.VENV_REUSE
    assert boot.venv_action(venv, runs=lambda p: False) == boot.VENV_RECREATE


def test_venv_python_runs_false_for_missing(tmp_path):
    assert boot.venv_python_runs(tmp_path / "nope" / "python.exe") is False


def test_venv_python_runs_true_for_this_interpreter():
    assert boot.venv_python_runs(Path(sys.executable)) is True
