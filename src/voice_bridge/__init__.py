"""
Voice Bridge - Push-to-talk dictation for Claude Code.

Captures microphone audio via hotkey, transcribes with WhisperRTX,
and pastes the result into the active terminal window.
"""

import os
import sys
from pathlib import Path

# Ensure NVIDIA DLLs (cuDNN, cuBLAS) are on PATH before ctranslate2 import.
# Must happen before any faster-whisper / ctranslate2 import.
for _pkg in ("cudnn", "cublas", "cuda_runtime"):
    _nvidia_bin = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia" / _pkg / "bin"
    if _nvidia_bin.is_dir():
        os.environ["PATH"] = str(_nvidia_bin) + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(str(_nvidia_bin))

__version__ = "0.9.9.0426"
