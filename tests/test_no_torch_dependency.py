"""Regression guard: the runtime must never depend on torch again.

torch used to be pulled in for a device check it did not own (4.25 GB of wheels).
It was replaced by src/core/gpu_probe.py, which asks ctranslate2 and nvidia-smi.
These tests keep it out - reintroducing `import torch` anywhere under src/ would
quadruple the install size and silently break every torch-free installation.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"


def _imported_modules(path: Path) -> set[str]:
    """Top-level module names imported by a file, via AST (not text search)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


@pytest.mark.parametrize(
    "py_file",
    sorted(SRC.rglob("*.py")),
    ids=lambda p: str(p.relative_to(SRC)) if isinstance(p, Path) else str(p),
)
def test_source_file_does_not_import_torch(py_file: Path) -> None:
    banned = {"torch", "torchaudio"} & _imported_modules(py_file)
    assert not banned, (
        f"{py_file.relative_to(SRC)} imports {sorted(banned)}. "
        "Use src.core.gpu_probe instead - see its module docstring."
    )


def test_gpu_probe_works_without_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """probe_gpu() must return a verdict even when torch cannot be imported."""

    class TorchBlocker:
        def find_spec(self, name, path=None, target=None):
            if name == "torch" or name.startswith("torch."):
                raise ImportError(f"blocked: {name}")
            return None

    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.delitem(sys.modules, "torch")
    blocker = TorchBlocker()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])

    from src.core.gpu_probe import probe_gpu, select_profile

    gpu = probe_gpu()
    profile = select_profile(gpu)

    # No assertion on the value: CI may have no GPU. What matters is that the
    # probe answers instead of raising, and that the answer is self-consistent.
    assert profile.device in {"cuda", "cpu"}
    assert profile.reason
    if profile.device == "cuda":
        assert gpu.present and gpu.cuda_usable
    if not gpu.present:
        assert profile.device == "cpu"
