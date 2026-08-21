"""Post-install bootstrapper: pick a GPU profile, then build the runtime for it.

Run by the Inno Setup installer after the payload is copied, and re-runnable by
hand for repair. Everything heavy is downloaded HERE, on the target host, so the
installer itself stays small and never ships CUDA kernels a machine cannot use.

    vb_bootstrap.exe --root "C:\\Program Files\\VoiceBridge"
    vb_bootstrap.exe --root . --profile cloud     # force, skip detection
    vb_bootstrap.exe --root . --dry-run           # print the plan, install nothing
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PYTHON_VERSION = "3.11"
UV_INSTALL_URL = "https://astral.sh/uv/install.ps1"
STATE_FILE = "install-state.json"

# Base runtime: everything that works with or without a GPU.
BASE_DEPS = [
    "numpy>=1.26,<2",
    "pyaudio>=0.2.14",
    "pynput>=1.7.6",
    "pyperclip>=1.8.2",
    "pystray>=0.19.5",
    "Pillow>=10",
    "faster-whisper>=1.2",
    "ctranslate2>=4.0,<5",
    "pywin32>=306",
    "edge-tts>=6.1",
]

# CUDA kernels. ctranslate2 does NOT declare these but cannot run on GPU without
# them: the cudnn64_9.dll inside its wheel is only a dispatcher stub.
GPU_DEPS = [
    "nvidia-cublas-cu12>=12.9",
    "nvidia-cudnn-cu12>=9.20",
]


class Reporter:
    """Plain sequential output - the installer shows this console to the user."""

    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def step(self, msg: str) -> None:
        print(f"\n[{time.perf_counter() - self.t0:6.1f}s] === {msg}", flush=True)

    def info(self, msg: str) -> None:
        print(f"          {msg}", flush=True)

    def fail(self, msg: str) -> None:
        print(f"\n*** {msg}", file=sys.stderr, flush=True)


def find_uv(root: Path, rep: Reporter) -> str:
    """Prefer the uv shipped next to the installer, else PATH, else fetch it."""
    bundled = root / "bin" / "uv.exe"
    if bundled.is_file():
        rep.info(f"uv: bundled ({bundled})")
        return str(bundled)

    on_path = shutil.which("uv")
    if on_path:
        rep.info(f"uv: found on PATH ({on_path})")
        return on_path

    rep.info("uv: not found, downloading from astral.sh ...")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f"irm {UV_INSTALL_URL} | iex",
        ],
        check=True,
    )
    fetched = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv.exe")
    if not Path(fetched).is_file():
        raise RuntimeError("uv install completed but uv.exe was not found")
    return fetched


def _load_probe():
    """Import gpu_probe from the frozen bundle first, from the payload second.

    --probe-json runs during the wizard, BEFORE the payload is extracted, so the
    copy compiled into this exe is the only one available then. After install the
    on-disk payload is authoritative, which keeps repair runs honest if the app
    was updated without rebuilding this bootstrapper.
    """
    try:
        from src.core.gpu_probe import probe_gpu, select_profile

        return probe_gpu, select_profile
    except ImportError:
        return None, None


def detect_profile(root: Path, rep: Reporter):
    """Ask the shared probe. Works with zero dependencies installed."""
    probe_gpu, select_profile = _load_probe()
    if probe_gpu is None:
        sys.path.insert(0, str(root))
        from src.core.gpu_probe import probe_gpu, select_profile  # noqa: F811

    gpu = probe_gpu()
    profile = select_profile(gpu)
    rep.info(f"GPU     : {gpu.name}")
    rep.info(f"VRAM    : {gpu.vram_mb} MB")
    rep.info(f"Driver  : {gpu.driver_version or 'n/a'}")
    rep.info(f"Decision: {profile.reason}")
    rep.info(
        f"Runtime : device={profile.device} model={profile.model} "
        f"compute={profile.compute_type}"
    )
    return gpu, profile


def run_streamed(cmd: list[str], rep: Reporter, cwd: Path | None = None) -> None:
    """Run a child process, streaming its output; raise with context on failure."""
    rep.info(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed (exit {proc.returncode}): {' '.join(cmd)}")


def build_model_prefetch_code(root: Path, profile) -> str:
    """Code run inside the fresh venv to pull the model into the HF cache."""
    return "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(root)!r})",
            "from src.core.gpu_probe import ensure_cuda_dll_path",
            "ensure_cuda_dll_path()",
            "from faster_whisper import WhisperModel",
            f"WhisperModel({profile.model!r}, device={profile.device!r}, "
            f"compute_type={profile.compute_type!r})",
            "print('model cached')",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the VoiceBridge runtime for this host"
    )
    ap.add_argument("--root", required=True, type=Path, help="installation directory")
    ap.add_argument(
        "--profile", choices=["auto", "gpu", "cpu", "cloud"], default="auto"
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan, change nothing"
    )
    ap.add_argument(
        "--skip-model", action="store_true", help="do not pre-fetch the model"
    )
    ap.add_argument(
        "--probe-json",
        type=Path,
        metavar="FILE",
        help="write the detected profile as JSON and exit (used by the installer wizard)",
    )
    args = ap.parse_args()

    if args.probe_json is not None:
        # Silent mode for the Inno Setup wizard: no banner, machine-readable only.
        quiet = Reporter()
        quiet.info = lambda _msg: None  # type: ignore[method-assign]
        try:
            gpu, profile = detect_profile(args.root.resolve(), quiet)
            payload = {
                "ok": True,
                "recommended": "gpu" if profile.device == "cuda" else "cpu",
                "gpu_name": gpu.name,
                "vram_mb": gpu.vram_mb,
                "driver_version": gpu.driver_version,
                "model": profile.model,
                "compute_type": profile.compute_type,
                "reason": profile.reason,
            }
        except Exception as exc:
            payload = {"ok": False, "recommended": "cpu", "reason": f"probe failed: {exc}"}
        args.probe_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload))
        return 0

    rep = Reporter()
    root: Path = args.root.resolve()
    venv = root / "venv"

    print("=" * 68)
    print(" VoiceBridge - runtime setup")
    print("=" * 68)
    rep.info(f"target: {root}")

    if not (root / "src" / "voice_bridge" / "__main__.py").is_file():
        rep.fail(f"payload not found under {root} (expected src/voice_bridge/)")
        return 2

    try:
        rep.step("Detecting hardware")
        gpu, profile = detect_profile(root, rep)

        if args.profile == "gpu":
            deps, kind = BASE_DEPS + GPU_DEPS, "gpu"
        elif args.profile in ("cpu", "cloud"):
            deps, kind = BASE_DEPS, args.profile
        else:
            wants_gpu = profile.device == "cuda"
            deps = BASE_DEPS + GPU_DEPS if wants_gpu else BASE_DEPS
            kind = "gpu" if wants_gpu else "cpu"

        rep.info(f"profile selected: {kind} ({len(deps)} packages)")
        if kind == "gpu":
            rep.info("includes ~1.6 GB of CUDA kernels (cuBLAS + cuDNN)")

        if args.dry_run:
            rep.step("DRY RUN - nothing will be installed")
            for dep in deps:
                print(f"          - {dep}")
            print(f"\n          venv    : {venv}")
            print(f"          model   : {profile.model} ({profile.compute_type})")
            return 0

        rep.step("Locating uv")
        uv = find_uv(root, rep)

        rep.step(f"Installing Python {PYTHON_VERSION}")
        run_streamed([uv, "python", "install", PYTHON_VERSION], rep)

        rep.step("Creating virtual environment")
        run_streamed([uv, "venv", str(venv), "--python", PYTHON_VERSION], rep)

        rep.step(f"Installing {len(deps)} packages")
        env_python = venv / "Scripts" / "python.exe"
        run_streamed([uv, "pip", "install", "--python", str(env_python), *deps], rep)

        if not args.skip_model and kind != "cloud":
            rep.step(f"Pre-fetching Whisper model '{profile.model}'")
            rep.info("first download only; later starts read it from cache")
            run_streamed(
                [str(env_python), "-c", build_model_prefetch_code(root, profile)],
                rep,
                cwd=root,
            )

        rep.step("Writing install state")
        state = {
            "profile": kind,
            "device": profile.device,
            "model": profile.model,
            "compute_type": profile.compute_type,
            "reason": profile.reason,
            "gpu_name": gpu.name,
            "vram_mb": gpu.vram_mb,
            "driver_version": gpu.driver_version,
            "python_version": PYTHON_VERSION,
        }
        (root / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")
        rep.info(str(root / STATE_FILE))

        rep.step("Done")
        print(f"\n  VoiceBridge is ready ({kind} profile).")
        print(f"  Launch it with {root / 'VoiceBridge.exe'}\n")
        return 0

    except Exception as exc:
        rep.fail(f"setup failed: {exc}")
        print(
            "\nThe installation is incomplete. Re-run this bootstrapper to retry:",
            file=sys.stderr,
        )
        print(f'  vb_bootstrap.exe --root "{root}"', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
