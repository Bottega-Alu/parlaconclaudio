"""
Standalone launcher for Voice Bridge.
Used by PyInstaller as the entry point.
"""

import os
import sys
from pathlib import Path


def setup_nvidia_dlls():
    """Add NVIDIA DLL paths before any CUDA imports."""
    # When running from exe, DLLs are in the same directory
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else Path(sys.executable).parent
        os.add_dll_directory(str(exe_dir))
        os.environ["PATH"] = str(exe_dir) + os.pathsep + os.environ.get("PATH", "")
    else:
        # When running from source, find nvidia packages in venv
        for pkg in ("cudnn", "cublas", "cuda_runtime"):
            nvidia_bin = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / pkg / "bin"
            if nvidia_bin.is_dir():
                os.environ["PATH"] = str(nvidia_bin) + os.pathsep + os.environ.get("PATH", "")
                os.add_dll_directory(str(nvidia_bin))


def main():
    setup_nvidia_dlls()

    # Ensure src package is importable
    rtstt_root = Path(__file__).resolve().parent.parent.parent
    if str(rtstt_root) not in sys.path:
        sys.path.insert(0, str(rtstt_root))

    from src.voice_bridge.bridge import main as bridge_main
    bridge_main()


if __name__ == "__main__":
    main()
