"""Self-update from the Git repo: check upstream for new commits and pull in place.

The install is expected to be a git checkout of the app repo (the installer lays
it down that way). ``check_for_update`` fetches and compares HEAD with its
upstream; ``apply_update`` fast-forwards, reinstalls Python deps into the
running interpreter (the vendored Python in a packaged install, the venv in
dev), and reports that a restart is required. ``schedule_restart`` relaunches
the backend in place so the new code is served — no reinstall needed.

Both paths degrade with a clear message when the install is not a checkout or
``git`` is missing, rather than failing obscurely.
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"


def _git(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run a git command in the repo root; never raises on a non-zero exit.

    Git — and Git Credential Manager — must never open a prompt: this process
    has no console and, in the desktop app, no visible window to answer it in,
    so a fetch against a private remote with no stored credential would hang
    until the timeout. With prompts off it fails fast with a clear message.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, env=env,
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

    # Runs on every visit to Settings, so an unreachable remote must not hold
    # the page for two minutes.
    rc, _, err = _git("fetch", "--quiet", timeout=45)
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

    # Drop any built UI file the pull did not bring. The installer copies files
    # in and never removes ones it no longer ships, so hashed chunks from older
    # versions pile up in frontend/dist/assets — and a browser or WebView that
    # still has an old index.html cached will happily load that whole stale UI
    # from them. Only untracked files under the built UI are removed; the pull
    # owns everything tracked.
    rc, out, err = _git("clean", "-fdq", "--", "frontend/dist")
    if rc != 0:
        log.warning("Could not clean stale built-UI files: %s", err or out)

    after = installed_state()["commit"]
    if progress:
        progress(1.0, "Update applied — restart to finish")
    return {"updated_from": before, "updated_to": after, "deps_installed": deps_installed,
            "changed": before != after, "restart_required": True}


def successor_command() -> list[str]:
    """The command that relaunches this backend: the same interpreter with the
    same arguments, so the packaged (vendored Python + boot.py) and dev (venv +
    main.py) installs restart the same way."""
    return [sys.executable, *sys.argv]


def _inheritable(stream):
    """``stream`` if a child process can inherit it, else None.

    Keeping stdout/stderr lets whoever reads this backend's output — the desktop
    shell drains them into its startup log — keep reading the successor's,
    instead of losing every line after the first restart. With None, CPython
    hands the child this process's own standard handle instead, so the shell's
    pipe is still passed on even when ``sys.stdout`` has been wrapped.
    """
    try:
        stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    return stream


def respawn() -> None:
    """Replace this process with a fresh copy of itself. Does not return.

    POSIX: a true ``execv`` — same PID, so a supervisor sees one process.

    Windows: ``os.execv`` is NOT an exec there. The C runtime spawns a new
    process and joins argv with spaces WITHOUT quoting, so any path containing
    a space — a profile like ``C:/Users/Jane Doe``, or the packaged install
    under ``.../Media Studio Enterprise/`` — starts a successor that dies at once
    with "can't open file", while the caller exits 0 as if all were well. So
    spawn the successor through ``subprocess`` (which quotes argv correctly),
    hand it our stdio, and only then exit.
    """
    cmd = successor_command()
    if os.name != "nt":
        os.execv(cmd[0], cmd)
        return  # pragma: no cover - execv does not return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=_inheritable(sys.stdout),
            stderr=_inheritable(sys.stderr),
        )
    except OSError:
        # Keep serving on the old code rather than die with nothing to show for
        # it; the message lands in the shell's log / startup report.
        log.exception("restart failed: could not start %s", cmd)
        return
    os._exit(0)


def schedule_restart(delay_seconds: float = 0.75) -> None:
    """Restart the backend shortly, after the HTTP response has been sent."""
    threading.Timer(delay_seconds, respawn).start()
