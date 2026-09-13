"""Self-update from the Git repo: check upstream for new commits and pull in place.

The install is expected to be a git checkout of the app repo (the installer lays
it down that way). ``check_for_update`` fetches and compares HEAD with its
upstream; ``apply_update`` fast-forwards, reinstalls Python deps into the
running interpreter (the vendored Python in a packaged install, the venv in
dev), and reports that a restart is required. ``schedule_restart`` re-execs
the backend so the new code is served — no reinstall needed.

Both paths degrade with a clear message when the install is not a checkout or
``git`` is missing, rather than failing obscurely.
"""

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"


def _git(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run a git command in the repo root; never raises on a non-zero exit."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_checkout() -> bool:
    return (ROOT / ".git").exists()


def _precondition_error() -> str | None:
    if not git_available():
        return "git is not installed on this machine, so the app cannot self-update."
    if not is_git_checkout():
        return "This install is not a Git checkout, so the app cannot self-update."
    return None


def installed_state() -> dict:
    """Installed commit / branch (no network)."""
    err = _precondition_error()
    if err:
        return {"error": err, "commit": None, "branch": None}
    _, commit, _ = _git("rev-parse", "--short", "HEAD")
    _, branch, _ = _git("rev-parse", "--abbrev-ref", "HEAD")
    return {"error": None, "commit": commit or None, "branch": branch or None}


def check_for_update() -> dict:
    """Fetch upstream and report whether HEAD is behind it."""
    state = installed_state()
    if state["error"]:
        return {**state, "update_available": False, "behind": 0, "latest": None}

    rc, _, err = _git("fetch", "--quiet")
    if rc != 0:
        return {**state, "update_available": False, "behind": 0, "latest": None,
                "error": f"Could not reach the Git remote: {err or 'fetch failed'}"}

    rc, behind, err = _git("rev-list", "--count", "HEAD..@{u}")
    if rc != 0:
        return {**state, "update_available": False, "behind": 0, "latest": None,
                "error": f"No upstream branch to compare against: {err or 'rev-list failed'}"}
    _, latest, _ = _git("rev-parse", "--short", "@{u}")
    behind_n = int(behind or 0)
    return {**state, "update_available": behind_n > 0, "behind": behind_n, "latest": latest or None}


def apply_update(progress=None) -> dict:
    """Fast-forward to upstream and reinstall dependencies. Raises on failure."""
    err = _precondition_error()
    if err:
        raise RuntimeError(err)

    before = installed_state()["commit"]
    if progress:
        progress(0.1, "Pulling the latest changes…")
    rc, out, err = _git("pull", "--ff-only", timeout=300)
    if rc != 0:
        raise RuntimeError(f"git pull failed: {err or out or 'unknown error'}")

    deps_installed = False
    if REQUIREMENTS.exists():
        if progress:
            progress(0.5, "Installing Python dependencies…")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            raise RuntimeError(f"pip install failed: {(r.stderr or r.stdout)[-800:]}")
        deps_installed = True

    after = installed_state()["commit"]
    if progress:
        progress(1.0, "Update applied — restart to finish")
    return {"updated_from": before, "updated_to": after, "deps_installed": deps_installed,
            "changed": before != after, "restart_required": True}


def schedule_restart(delay_seconds: float = 0.75) -> None:
    """Re-exec the backend shortly, after the HTTP response has been sent.

    ``os.execv`` replaces this process with a fresh ``python main.py …`` using
    the same interpreter and arguments, so the packaged (vendored-Python) and
    dev (venv) installs restart the same way.
    """

    def _restart() -> None:
        os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Timer(delay_seconds, _restart).start()
