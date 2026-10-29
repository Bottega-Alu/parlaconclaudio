"""Hashed dependency locks: in sync with BASE_DEPS / GPU_DEPS, used by the
bootstrapper, shipped by the installer, and no uv download fallback left.

Pure file checks plus monkeypatched setup(): nothing is installed or downloaded.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


boot = _load("vb_bootstrap_locks_test", "installer/vb_bootstrap.py")
lock_deps = _load("lock_deps_test", "scripts/lock_deps.py")

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pins(text: str) -> dict[str, str]:
    return {_canon(m.group(1)): m.group(2) for m in map(_PIN.match, text.splitlines()) if m}


def _lock_text(kind: str) -> str:
    return (REPO / boot.LOCK_DIR / boot.LOCK_FILES[kind]).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- drift


@pytest.mark.parametrize("name", sorted(lock_deps.lock_inputs(boot)))
def test_lock_was_generated_from_the_current_requirements(name):
    """Fails when BASE_DEPS / GPU_DEPS change without `python scripts/lock_deps.py`."""
    text = (REPO / "installer" / name).read_text(encoding="utf-8")
    assert lock_deps.recorded_inputs(text) == lock_deps.lock_inputs(boot)[name], (
        f"installer/{name} is stale: run python scripts/lock_deps.py and commit the result"
    )


def test_check_mode_agrees():
    assert lock_deps.main(["--check"]) == 0


@pytest.mark.parametrize("kind", ["base", "gpu"])
def test_every_input_is_pinned_and_every_pin_is_hashed(kind):
    text = _lock_text(kind)
    pins = _pins(text)
    reqs = boot.BASE_DEPS + (boot.GPU_DEPS if kind == "gpu" else [])
    for req in reqs:
        name = _canon(re.match(r"[A-Za-z0-9._-]+", req).group(0))
        assert name in pins, f"{req} missing from {boot.LOCK_FILES[kind]}"
    # Each `name==ver \` block must be followed by at least one --hash line.
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    assert sum(1 for b in blocks if _PIN.match(b)) == len(pins)
    for block in blocks:
        if _PIN.match(block):
            assert "--hash=sha256:" in block, block.splitlines()[0]


@pytest.mark.parametrize("kind", ["base", "gpu"])
def test_locked_edge_tts_honours_the_floor(kind):
    """The 403 fix (EDGE_TTS_MIN) must survive into what is actually installed."""
    pinned = _pins(_lock_text(kind))["edge-tts"]
    assert boot.version_tuple(pinned) >= boot.version_tuple(boot.EDGE_TTS_MIN)


def test_gpu_lock_adds_the_cuda_kernels_and_base_lock_does_not():
    base, gpu = _pins(_lock_text("base")), _pins(_lock_text("gpu"))
    for req in boot.GPU_DEPS:
        name = _canon(req.split(">")[0])
        assert name in gpu and name not in base
    assert "torch" not in gpu and "torch" not in base


# --------------------------------------------------------------------------- bootstrap


class _Rep:
    def info(self, _text):
        pass

    step = info


def _payload_root(tmp_path: Path, uv: bool = True, locks: bool = True) -> Path:
    main = tmp_path / "src" / "voice_bridge" / "__main__.py"
    main.parent.mkdir(parents=True)
    main.write_text("")
    if uv:
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "uv.exe").write_bytes(b"")
    if locks:
        (tmp_path / boot.LOCK_DIR).mkdir()
        for name in boot.LOCK_FILES.values():
            (tmp_path / boot.LOCK_DIR / name).write_text("# lock\n")
    return tmp_path


def test_find_uv_uses_only_the_bundled_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(boot.shutil, "which", lambda *_: r"C:\elsewhere\uv.exe")
    with pytest.raises(boot.PayloadMissing) as err:
        boot.find_uv(tmp_path, _Rep())
    assert "uv.exe" in str(err.value)
    _payload_root(tmp_path, locks=False)
    assert boot.find_uv(tmp_path, _Rep()) == str(tmp_path / "bin" / "uv.exe")


def test_find_uv_accepts_the_source_checkout_layout(tmp_path):
    (tmp_path / "installer" / "bin").mkdir(parents=True)
    (tmp_path / "installer" / "bin" / "uv.exe").write_bytes(b"")
    assert boot.find_uv(tmp_path, _Rep()) == str(tmp_path / "installer" / "bin" / "uv.exe")


def test_no_network_uv_fallback_left():
    source = (REPO / "installer" / "vb_bootstrap.py").read_text(encoding="utf-8")
    assert "astral.sh" not in source and "| iex" not in source


@pytest.mark.parametrize("kind,lock", [("gpu", "gpu"), ("cpu", "base"), ("cloud", "base")])
def test_find_lock_per_profile(tmp_path, kind, lock):
    _payload_root(tmp_path, uv=False)
    assert boot.find_lock(tmp_path, kind) == tmp_path / "installer" / boot.LOCK_FILES[lock]


def test_install_command_requires_hashes(tmp_path):
    cmd = boot.build_install_cmd("uv", tmp_path / "python.exe", tmp_path / "x.lock")
    assert cmd[:3] == ["uv", "pip", "install"]
    assert "--require-hashes" in cmd
    assert cmd[cmd.index("-r") + 1] == str(tmp_path / "x.lock")


def _run_setup(tmp_path, monkeypatch, profile="cpu"):
    gpu = SimpleNamespace(name="test", vram_mb=0, driver_version=None)
    prof = SimpleNamespace(device="cpu", model="small", compute_type="int8", reason="test")
    commands = []
    monkeypatch.setattr(boot, "detect_profile", lambda *args: (gpu, prof))
    monkeypatch.setattr(boot, "venv_action", lambda *args: boot.VENV_REUSE)
    monkeypatch.setattr(boot, "run_streamed", lambda cmd, *a, **k: commands.append(cmd))
    args = argparse.Namespace(profile=profile, dry_run=False, skip_model=True, skip_hook=True)
    return boot.setup(args, tmp_path, None), commands


@pytest.mark.parametrize("profile,lock", [("gpu", "gpu"), ("cpu", "base")])
def test_setup_installs_from_the_profile_lock(tmp_path, monkeypatch, profile, lock):
    root = _payload_root(tmp_path)
    code, commands = _run_setup(root, monkeypatch, profile)
    assert code == boot.EXIT_OK
    installs = [c for c in commands if c[1:3] == ["pip", "install"]]
    assert installs == [boot.build_install_cmd(
        str(root / "bin" / "uv.exe"), root / "venv" / "Scripts" / "python.exe",
        root / "installer" / boot.LOCK_FILES[lock])]


@pytest.mark.parametrize("missing", ["uv", "locks"])
def test_setup_missing_bundled_file_is_a_structured_payload_failure(tmp_path, monkeypatch, capsys, missing):
    root = _payload_root(tmp_path, uv=missing != "uv", locks=missing != "locks")
    code, commands = _run_setup(root, monkeypatch)
    assert code == boot.EXIT_NO_PAYLOAD
    assert not any(c[1:3] == ["pip", "install"] for c in commands)
    err = capsys.readouterr().err
    assert "setup failed: PayloadMissing" in err
    assert "Run the installer again" in err


def test_installer_ships_uv_and_locks_unconditionally():
    iss = (REPO / "installer" / "VoiceBridge.iss").read_text(encoding="utf-8")
    uv_line = next(line for line in iss.splitlines() if line.startswith('Source: "bin\\uv.exe"'))
    assert "skipifsourcedoesntexist" not in uv_line
    for name in boot.LOCK_FILES.values():
        line = next(line for line in iss.splitlines() if f'Source: "{name}"' in line)
        assert 'DestDir: "{app}\\installer"' in line


def test_build_script_pins_and_verifies_uv():
    ps1 = (REPO / "scripts" / "build_exe.ps1").read_text(encoding="utf-8")
    assert re.search(r'\$UvVersion\s*=\s*"\d+\.\d+\.\d+"', ps1)
    assert re.search(r'\$UvZipSha256\s*=\s*"[0-9a-f]{64}"', ps1)
    assert "Get-FileHash" in ps1
