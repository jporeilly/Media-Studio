"""The installer ships a git CLONE of this repo, so anything TRACKED ships to
every customer, and anything not ignored dirties the installed checkout (which
blocks its fast-forward self-update). Keep state and secrets out of the index
and runtime output out of the working tree - a standing rule for every project."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_NAMES = {".env", "config.json", ".storage_secret"}
FORBIDDEN_SUFFIXES = {".db", ".db-wal", ".db-shm", ".pem", ".key"}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def _tracked() -> list[str]:
    r = _git("ls-files", "-z")
    if r.returncode != 0:
        pytest.skip("not a git checkout (or git missing)")
    return [p for p in r.stdout.split("\0") if p]


def test_no_state_or_secret_files_are_tracked():
    bad = [p for p in _tracked()
           if Path(p).name in FORBIDDEN_NAMES or Path(p).suffix in FORBIDDEN_SUFFIXES]
    assert bad == [], f"state/secret files are tracked and would ship in the installer: {bad}"


@pytest.mark.parametrize(
    "rel",
    [
        "data/media_studio.db",       # accounts and sessions
        "data/config.json",           # lab settings, provider keys
        ".env",                       # provider keys (utils/config.py)
        "assets/finished/video.mp4",  # rendered output (utils/config.py default)
        "assets/temp/audio.wav",
        "bin/ffmpeg.exe",             # the stage-time overlay
        "boot.py",
    ],
)
def test_runtime_files_are_ignored(rel):
    _tracked()  # skips outside a checkout
    r = _git("check-ignore", "-q", rel)
    assert r.returncode == 0, f"{rel} is not git-ignored - it would ship, or dirty the installed checkout"
