"""Installer selection contract; fake roots/configs only, no user config access."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_choices_test", REPO / "installer/vb_bootstrap.py")
boot = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = boot
spec.loader.exec_module(boot)


class Reporter:
    def info(self, text):
        pass


@pytest.mark.parametrize("claude,codex", [(False, False), (True, False), (False, True), (True, True)])
def test_each_checkbox_controls_only_its_agent(tmp_path, monkeypatch, claude, codex):
    connected, synced = [], []
    monkeypatch.setattr(boot.voice_hooks, "config_path", lambda agent: tmp_path / f"{agent}.json")
    monkeypatch.setattr(boot.voice_hooks, "connect", lambda agent, root: (
        connected.append(agent) or boot.voice_hooks.HookResult(agent, "connect", True)))
    monkeypatch.setattr(boot, "sync_tts_hook", lambda *args: synced.append(args[2:]) or [])
    boot.configure_voice_hooks(tmp_path, Reporter(), {"claude": claude, "codex": codex})
    assert connected == [a for a, selected in (("claude", claude), ("codex", codex)) if selected]
    assert synced == ([(tmp_path / "claude.json" if claude else None,
                        tmp_path / "codex.json" if codex else None)] if claude or codex else [])


def test_explicit_upgrade_choices_override_saved_selection(tmp_path):
    (tmp_path / boot.STATE_FILE).write_text(json.dumps({"voice_hooks": {"claude": True, "codex": False}}))
    args = argparse.Namespace(claude_hooks="no", codex_hooks="yes")
    assert boot.resolve_hook_choices(args, tmp_path) == {"claude": False, "codex": True}


def test_repair_keeps_saved_deselection(tmp_path):
    (tmp_path / boot.STATE_FILE).write_text(json.dumps({"voice_hooks": {"claude": False, "codex": True}}))
    assert boot.resolve_hook_choices(argparse.Namespace(), tmp_path) == {"claude": False, "codex": True}


@pytest.mark.parametrize("state", [None, "bad json", '{"voice_hooks": []}', '{"voice_hooks": {"claude": "yes"}}'])
def test_legacy_or_invalid_state_never_auto_connects(tmp_path, state):
    if state is not None:
        (tmp_path / boot.STATE_FILE).write_text(state)
    assert boot.resolve_hook_choices(argparse.Namespace(), tmp_path) == {"claude": None, "codex": None}


def test_real_connector_is_idempotent_preserves_unselected_and_unrelated_config(tmp_path, monkeypatch):
    claude = tmp_path / "claude" / "settings.json"
    codex = tmp_path / "codex" / "hooks.json"
    claude.parent.mkdir()
    codex.parent.mkdir()
    existing = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]}}
    claude.write_text(json.dumps(existing))
    codex.write_text('{"keep": "unchanged"}')
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude.parent))
    monkeypatch.setenv("CODEX_HOME", str(codex.parent))
    monkeypatch.setattr(boot, "sync_tts_hook", lambda *args: [])
    boot.configure_voice_hooks(tmp_path, Reporter(), {"claude": True, "codex": False})
    first = claude.read_bytes()
    boot.configure_voice_hooks(tmp_path, Reporter(), {"claude": True, "codex": False})
    assert claude.read_bytes() == first
    assert len(list(claude.parent.glob("settings.json.bak-*"))) == 1
    assert json.loads(first)["hooks"]["Stop"][0] == existing["hooks"]["Stop"][0]
    assert json.loads(first)["theme"] == "dark"
    assert codex.read_text() == '{"keep": "unchanged"}'


@pytest.mark.parametrize("selected", ["claude", "codex"])
def test_selected_only_sync_never_probes_or_updates_other_existing_hook(tmp_path, monkeypatch, selected):
    app_root = tmp_path / "app"
    src = app_root / "scripts" / "notify-tts.py"
    src.parent.mkdir(parents=True)
    src.write_text("new hook")
    script_files = {}
    for agent in ("claude", "codex"):
        config_dir = tmp_path / agent
        config_dir.mkdir()
        script = config_dir / "notify-tts.py"
        script.write_text(f"old {agent}")
        script_files[agent] = script
        command = f'"{agent}-python" "{script}"'
        config = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}
        path = config_dir / ("settings.json" if agent == "claude" else "hooks.json")
        path.write_text(json.dumps(config))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(boot.voice_hooks, "connect", lambda agent, root: boot.voice_hooks.HookResult(agent, "connect", True))
    probed = []
    monkeypatch.setattr(boot, "resolve_interpreter", lambda tokens: list(tokens))
    monkeypatch.setattr(boot, "installed_edge_tts", lambda interp: probed.append(interp) or "7.3.1")
    boot.configure_voice_hooks(app_root, Reporter(), {agent: agent == selected for agent in ("claude", "codex")})
    other = "codex" if selected == "claude" else "claude"
    assert probed == [[f"{selected}-python"]]
    assert script_files[selected].read_text() == "new hook"
    assert script_files[other].read_text() == f"old {other}"
    assert not list(script_files[other].parent.glob("*.bak-*"))


def test_test_build_does_not_sync_existing_production_scripts_or_interpreters(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(boot.voice_hooks, "connect", lambda agent, root: (
        called.append(agent) or boot.voice_hooks.HookResult(agent, "connect", True)))
    monkeypatch.setattr(boot, "sync_tts_hook", lambda *args: pytest.fail("test build must not rewrite production scripts"))
    boot.configure_voice_hooks(tmp_path, Reporter(), {"claude": True, "codex": True}, maintain_existing=False)
    assert called == ["claude", "codex"]


def test_installer_defaults_and_explicit_yes_no_forwarding():
    script = (REPO / "installer/VoiceBridge.iss").read_text()
    for task, arg in (("claudehooks", "claude"), ("codexhooks", "codex")):
        line = next(line for line in script.splitlines() if line.startswith(f'Name: "{task}";'))
        assert "unchecked" not in line
        assert f"WizardIsTaskSelected('{task}')" in script
        assert f" --{arg}-hooks yes" in script
        assert f" --{arg}-hooks no" in script
    suffix = "-Test" if boot.IS_TEST_BUILD else ""
    assert f"DefaultDirName={{localappdata}}\\Programs\\VoiceBridge{suffix}" in script
    assert "[InstallDelete]" not in script


@pytest.mark.parametrize("kind", ["cpu", "cloud", "gpu"])
def test_setup_prefetches_the_local_model_except_for_cloud(tmp_path, monkeypatch, kind):
    payload = tmp_path / "src" / "voice_bridge" / "__main__.py"
    payload.parent.mkdir(parents=True)
    payload.write_text("")
    (tmp_path / boot.LOCK_DIR).mkdir()
    for name in boot.LOCK_FILES.values():
        (tmp_path / boot.LOCK_DIR / name).write_text("")
    gpu = SimpleNamespace(name="test", vram_mb=8000, driver_version="test")
    profile = SimpleNamespace(device="cuda", model="small", compute_type="float16", reason="test")
    commands = []
    monkeypatch.setattr(boot, "detect_profile", lambda *args: (gpu, profile))
    monkeypatch.setattr(boot, "find_uv", lambda *args: "uv")
    monkeypatch.setattr(boot, "venv_action", lambda *args: boot.VENV_REUSE)
    monkeypatch.setattr(boot, "run_streamed", lambda cmd, *args, **kwargs: commands.append(cmd))
    args = argparse.Namespace(profile=kind, dry_run=False, skip_model=False, skip_hook=True)
    assert boot.setup(args, tmp_path, None) == boot.EXIT_OK
    prefetch = [cmd for cmd in commands if "-c" in cmd]
    assert len(prefetch) == (0 if kind == "cloud" else 1)
    if kind == "cpu":  # CPU forced on a GPU host: the CPU model, not the GPU one
        assert "device='cpu'" in prefetch[0][-1] and "'small'" in prefetch[0][-1]
