"""GPU capability probe and STT profile selection.

Single source of truth for "what can this machine run?", used by TWO callers:

  1. the running app  (transcriber.py: cuda or cpu? which compute type?)
  2. the installer    (which wheels to download, which model to pre-fetch)

Because the installer runs BEFORE any dependency exists, this module must work
with nothing but `nvidia-smi` on PATH. When ctranslate2 IS importable it is used
as the authoritative confirmation, since it is the library that actually runs
inference - torch was only ever consulted here for a device check it does not own.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000  # keep nvidia-smi from flashing a console under pythonw

# ctranslate2 4.x links cuDNN 9 / CUDA 12; on Windows that needs a >=525 driver.
MIN_CUDA12_DRIVER_MAJOR = 525

# VRAM thresholds in MB, generous enough to leave room for the OS compositor.
VRAM_LARGE_FP16 = 6000
VRAM_LARGE_INT8 = 3500
VRAM_MEDIUM_INT8 = 2000

_dll_dirs_registered: list[str] | None = None


@dataclass(frozen=True)
class GpuInfo:
    """What nvidia-smi (and ctranslate2, when available) report about the host."""

    present: bool
    name: str = "Not detected"
    vram_mb: int = 0
    driver_version: str = ""
    cuda_usable: bool = False       # ctranslate2 confirmed it can open the device
    confirmed_by_ct2: bool = False  # False => inferred from nvidia-smi alone


@dataclass(frozen=True)
class SttProfile:
    """The engine configuration chosen for this host."""

    device: str        # "cuda" | "cpu"
    model: str         # faster-whisper model id
    compute_type: str  # "float16" | "int8_float16" | "int8"
    reason: str        # human-readable justification, logged and shown by the installer
    beam_size: int = 5  # 1 (greedy) on CPU, where each extra beam costs real seconds


def _driver_major(driver_version: str) -> int:
    """'576.88' -> 576. Returns 0 when unparsable."""
    head = driver_version.split(".", 1)[0].strip()
    return int(head) if head.isdigit() else 0


def _query_nvidia_smi() -> tuple[str, int, str] | None:
    """Return (name, vram_mb, driver_version) for GPU 0, or None if unavailable."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"nvidia-smi failed: {exc}")
        return None

    if out.returncode != 0 or not out.stdout.strip():
        logger.debug(f"nvidia-smi returned {out.returncode}: {out.stderr.strip()}")
        return None

    first_gpu = out.stdout.strip().splitlines()[0]
    fields = [f.strip() for f in first_gpu.split(",")]
    if len(fields) < 3:
        logger.debug(f"unexpected nvidia-smi output: {first_gpu!r}")
        return None

    name, vram_raw, driver = fields[0], fields[1], fields[2]
    return name, int(vram_raw) if vram_raw.isdigit() else 0, driver


def ensure_cuda_dll_path() -> list[str]:
    """Register the NVIDIA wheel DLL folders with the Windows loader.

    ctranslate2 ships only a 0.3 MB cudnn dispatcher stub; the real cuBLAS and
    cuDNN payloads live in the nvidia-cublas-cu12 / nvidia-cudnn-cu12 wheels.
    torch used to add those folders to the DLL search path as an import side
    effect - with torch gone we must do it ourselves, or ctranslate2 fails at
    inference time with "Library cublas64_12.dll is not found or cannot be loaded".

    Returns the directories actually registered. Idempotent.
    """
    global _dll_dirs_registered
    if _dll_dirs_registered is not None:
        return _dll_dirs_registered

    registered: list[str] = []
    try:
        import nvidia
    except ImportError:
        logger.debug("nvidia wheels not installed; relying on system CUDA")
        _dll_dirs_registered = registered
        return registered

    for pkg_root in nvidia.__path__:
        for component in ("cublas", "cudnn"):
            bin_dir = Path(pkg_root) / component / "bin"
            if not bin_dir.is_dir():
                continue
            try:
                os.add_dll_directory(str(bin_dir))
            except OSError as exc:
                logger.warning(f"could not register {bin_dir}: {exc}")
                continue
            # PATH too: some loaders resolve dependent DLLs the old way.
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            registered.append(str(bin_dir))

    if registered:
        logger.info(f"CUDA DLL directories registered: {len(registered)}")
    _dll_dirs_registered = registered
    return registered


def _ct2_device_count() -> int | None:
    """CUDA devices per ctranslate2, or None when ctranslate2 is not installed yet."""
    try:
        import ctranslate2
    except ImportError:
        return None
    ensure_cuda_dll_path()
    try:
        return ctranslate2.get_cuda_device_count()
    except Exception as exc:
        logger.debug(f"ctranslate2 CUDA probe failed: {exc}")
        return 0


def probe_gpu() -> GpuInfo:
    """Inspect the host. Never raises - a missing GPU is a valid answer."""
    smi = _query_nvidia_smi()
    ct2_count = _ct2_device_count()

    if smi is None:
        # No NVIDIA driver. ctranslate2 may still exist but has nothing to open.
        return GpuInfo(present=False, confirmed_by_ct2=ct2_count is not None)

    name, vram_mb, driver = smi
    driver_ok = _driver_major(driver) >= MIN_CUDA12_DRIVER_MAJOR

    if ct2_count is None:
        # Installer path: ctranslate2 not installed yet, trust the driver version.
        return GpuInfo(
            present=True,
            name=name,
            vram_mb=vram_mb,
            driver_version=driver,
            cuda_usable=driver_ok,
            confirmed_by_ct2=False,
        )

    return GpuInfo(
        present=True,
        name=name,
        vram_mb=vram_mb,
        driver_version=driver,
        cuda_usable=ct2_count > 0,
        confirmed_by_ct2=True,
    )


def _cpu_profile(reason: str) -> SttProfile:
    """Whisper small, int8, greedy: on 20 FLEURS it clips (.work/perf-bench/) it runs at
    0.09x real time on a 16-thread CPU (medium: 0.26x, too slow on a 4-core laptop),
    with 4.8% of words differing from large-v3 on GPU (medium: 2.8%)."""
    return SttProfile("cpu", "small", "int8", reason, beam_size=1)


def select_profile(gpu: GpuInfo | None = None) -> SttProfile:
    """Pick model + compute type for this host. Explicit tree, no hidden heuristics."""
    gpu = probe_gpu() if gpu is None else gpu

    if not gpu.present:
        return _cpu_profile("No NVIDIA GPU detected")

    if not gpu.cuda_usable:
        detail = (
            f"driver {gpu.driver_version} is older than {MIN_CUDA12_DRIVER_MAJOR} "
            "required by cuDNN 9"
            if not gpu.confirmed_by_ct2
            else "ctranslate2 could not open the CUDA device"
        )
        return _cpu_profile(f"{gpu.name} unusable: {detail}")

    if gpu.vram_mb >= VRAM_LARGE_FP16:
        return SttProfile(
            "cuda", "large-v3", "float16", f"{gpu.name}, {gpu.vram_mb} MB VRAM"
        )
    if gpu.vram_mb >= VRAM_LARGE_INT8:
        return SttProfile(
            "cuda",
            "large-v3",
            "int8_float16",
            f"{gpu.name}, {gpu.vram_mb} MB VRAM - quantized to fit",
        )
    if gpu.vram_mb >= VRAM_MEDIUM_INT8:
        return SttProfile(
            "cuda", "medium", "int8", f"{gpu.name}, only {gpu.vram_mb} MB VRAM"
        )

    return _cpu_profile(f"{gpu.name} has too little VRAM ({gpu.vram_mb} MB)")


if __name__ == "__main__":  # python -m src.core.gpu_probe
    info = probe_gpu()
    profile = select_profile(info)
    print(f"GPU        : {info.name}")
    print(f"VRAM       : {info.vram_mb} MB")
    print(f"Driver     : {info.driver_version}")
    print(f"CUDA usable: {info.cuda_usable} (ctranslate2-confirmed: {info.confirmed_by_ct2})")
    print(f"-> device={profile.device} model={profile.model} compute={profile.compute_type}")
    print(f"-> reason: {profile.reason}")
