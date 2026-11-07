"""Make NVIDIA's pip-installed CUDA 12 libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12, ...) findable.

Both Kokoro (onnxruntime-gpu 1.26, CUDA 12) and Whisper (CTranslate2 4.x, needs cublas64_12.dll and
cudnn64_9.dll) load the same DLLs from .venv/Lib/site-packages/nvidia/*/bin. Windows doesn't look
there by itself, so add those folders to the DLL search path and PATH before either loads.
Without the libraries this does nothing and both run on the CPU.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_done = False


def cuda_dirs() -> list[Path]:
    dirs = []
    for sp in sys.path:
        root = Path(sp) / "nvidia"
        if root.is_dir():
            dirs += [d for d in root.glob("*/bin") if d.is_dir()]
    return dirs


def add_cuda_dlls() -> list[Path]:
    global _done
    if _done or os.name != "nt":
        return []
    _done = True
    dirs = cuda_dirs()
    for d in dirs:
        os.add_dll_directory(str(d))
    if dirs:
        # CTranslate2 loads cuBLAS by name at runtime, which only searches PATH.
        os.environ["PATH"] = os.pathsep.join([str(d) for d in dirs] + [os.environ.get("PATH", "")])
    return dirs
