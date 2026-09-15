"""The CUDA runtime DLL directories a GPU transcription needs.

faster-whisper runs on the GPU through CTranslate2, which loads cuBLAS from its
own C++ code with a plain LoadLibrary. That searches PATH and ignores the
directories registered with os.add_dll_directory, so the wheels have to go on
both: with only add_dll_directory the DLLs load fine from Python and
CTranslate2 still reports "cublas64_12.dll is not found or cannot be loaded"
and silently falls back to the CPU.
"""

import os
import sys
import types

import pytest

from core import video_importer as vi


@pytest.fixture
def fake_wheels(tmp_path, monkeypatch):
    """A site-packages/nvidia tree shaped like the nvidia-*-cu12 wheels."""
    root = tmp_path / "nvidia"
    for pkg, dll in (("cublas", "cublas64_12.dll"), ("cuda_runtime", "cudart64_12.dll"),
                     ("cudnn", "cudnn64_9.dll"), ("cuda_nvrtc", "nvrtc64_120_0.dll")):
        d = root / pkg / "bin"
        d.mkdir(parents=True)
        (d / dll).write_bytes(b"MZ")
    (root / "cuda_runtime" / "include").mkdir()          # not a bin dir: ignored

    added = []
    monkeypatch.setattr(os, "add_dll_directory", lambda p: added.append(p) or object())
    monkeypatch.setitem(sys.modules, "nvidia", types.SimpleNamespace(__path__=[str(root)]))
    monkeypatch.setattr(vi, "_cuda_dll_dirs_added", False)
    monkeypatch.setattr(vi, "_cuda_dll_handles", [])
    monkeypatch.setenv("PATH", r"C:\existing")
    return root, added


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL search only")
def test_every_wheels_bin_dir_goes_on_the_dll_path_and_PATH(fake_wheels):
    root, added = fake_wheels

    vi._add_cuda_dll_dirs()

    names = sorted(os.path.basename(os.path.dirname(p)) for p in added)
    assert names == ["cublas", "cuda_nvrtc", "cuda_runtime", "cudnn"], names
    on_path = os.environ["PATH"].split(os.pathsep)
    for p in added:
        assert p in on_path, f"{p} was registered but never put on PATH"
    assert on_path[-1] == r"C:\existing", "the existing PATH must be kept, after ours"


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL search only")
def test_the_cuda_runtime_is_not_left_out(fake_wheels):
    """cuBLAS cannot load without cudart64_12.dll beside it, and CTranslate2
    reports that as the cuBLAS library being missing."""
    root, added = fake_wheels

    vi._add_cuda_dll_dirs()

    assert any("cuda_runtime" in p for p in added), added


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL search only")
def test_it_runs_once_and_does_not_duplicate_path_entries(fake_wheels):
    root, added = fake_wheels

    vi._add_cuda_dll_dirs()
    first = os.environ["PATH"]
    vi._add_cuda_dll_dirs()

    assert os.environ["PATH"] == first, "a second call must not grow PATH"
    assert len(added) == 4


def test_missing_wheels_are_not_an_error(monkeypatch):
    """No GPU wheels installed is the normal case: transcription runs on the CPU."""
    monkeypatch.setattr(vi, "_cuda_dll_dirs_added", False)
    monkeypatch.setitem(sys.modules, "nvidia", types.SimpleNamespace(__path__=[]))
    vi._add_cuda_dll_dirs()   # must not raise
