"""notify-tts.py names the REAL project: main repo root via git, also from a
subfolder or a linked worktree; plain basename when git cannot answer."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "notify_tts_repo_name_under_test", REPO / "scripts" / "notify-tts.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nt = _load()

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "progetto-vero"
    (root / "src" / "deep").mkdir(parents=True)
    _git("init", "-q", cwd=root)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init", cwd=root)
    return root


@needs_git
def test_repo_root_from_root(repo: Path):
    assert nt.extract_repo_name({"cwd": str(repo)}) == "progetto-vero"


@needs_git
def test_subfolder_names_the_repo_not_the_folder(repo: Path):
    assert nt.extract_repo_name({"cwd": str(repo / "src" / "deep")}) == "progetto-vero"


@needs_git
def test_worktree_names_the_main_repo(repo: Path, tmp_path: Path):
    wt = tmp_path / "wt-feature-x"
    _git("worktree", "add", "-q", str(wt), "-b", "feature-x", cwd=repo)
    (wt / "sub").mkdir()
    assert nt.extract_repo_name({"cwd": str(wt)}) == "progetto-vero"
    assert nt.extract_repo_name({"cwd": str(wt / "sub")}) == "progetto-vero"


@needs_git
def test_not_a_repo_falls_back_to_basename(tmp_path: Path):
    plain = tmp_path / "cartella-libera"
    plain.mkdir()
    assert nt.git_main_repo_root(str(plain)) is None
    assert nt.extract_repo_name({"cwd": str(plain)}) == "cartella-libera"


def test_git_missing_falls_back_to_basename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(nt, "GIT_EXE", "git-that-does-not-exist-xyz")
    d = tmp_path / "senza-git"
    d.mkdir()
    assert nt.extract_repo_name({"cwd": str(d)}) == "senza-git"


def test_git_timeout_falls_back(tmp_path: Path, monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=nt.GIT_TIMEOUT_S)

    monkeypatch.setattr(nt.subprocess, "run", slow)
    d = tmp_path / "lento"
    d.mkdir()
    assert nt.extract_repo_name({"cwd": str(d)}) == "lento"


def test_missing_cwd_dir_does_not_call_git(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(nt.subprocess, "run", lambda *a, **k: pytest.fail("git called"))
    assert nt.extract_repo_name({"cwd": str(tmp_path / "sparita")}) == "sparita"
