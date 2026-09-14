"""Tests for the Git self-updater (git and pip mocked — nothing touches the network)."""

import json
import subprocess
import sys
import textwrap
import types

import pytest

from services import updater


class _Result:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _fake_git(monkeypatch, responses: dict, *, pip_rc=0):
    """responses maps a git subcommand (first arg) -> (rc, stdout, stderr)."""
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "git":
            rc, out, err = responses.get(cmd[1], (0, "", ""))
            return _Result(rc, out, err)
        if "-m" in cmd and "pip" in cmd:
            return _Result(pip_rc, "", "pip boom" if pip_rc else "")
        return _Result(0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(updater, "git_available", lambda: True)
    monkeypatch.setattr(updater, "is_git_checkout", lambda: True)
    return calls


def test_reports_not_a_checkout(monkeypatch):
    monkeypatch.setattr(updater, "git_available", lambda: True)
    monkeypatch.setattr(updater, "is_git_checkout", lambda: False)
    st = updater.check_for_update()
    assert st["update_available"] is False
    assert "not a Git checkout" in st["error"]


def test_reports_git_missing(monkeypatch):
    monkeypatch.setattr(updater, "git_available", lambda: False)
    st = updater.check_for_update()
    assert "git is not installed" in st["error"]
    with pytest.raises(RuntimeError):
        updater.apply_update()


def test_check_up_to_date(monkeypatch):
    _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "fetch": (0, "", ""), "rev-list": (0, "0", "")})
    st = updater.check_for_update()
    assert st["error"] is None
    assert st["update_available"] is False and st["behind"] == 0
    assert st["commit"] == "abc1234"


def test_check_update_available(monkeypatch):
    _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "fetch": (0, "", ""), "rev-list": (0, "3", "")})
    st = updater.check_for_update()
    assert st["update_available"] is True and st["behind"] == 3


def test_check_fetch_failure_is_reported_not_raised(monkeypatch):
    _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "fetch": (1, "", "could not resolve host")})
    st = updater.check_for_update()
    assert st["update_available"] is False
    assert "Could not reach the Git remote" in st["error"]


def test_apply_update_pulls_and_installs(monkeypatch, tmp_path):
    calls = _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "pull": (0, "Updating abc1234..def5678", "")})
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi\n")
    monkeypatch.setattr(updater, "REQUIREMENTS", req)
    progress = []
    result = updater.apply_update(progress=lambda f, m="": progress.append(f))
    assert result["restart_required"] is True and result["deps_installed"] is True
    assert any(c[:2] == ["git", "pull"] and "--ff-only" in c for c in calls)
    assert any("pip" in c for c in calls)
    assert progress and progress[-1] == 1.0


def test_apply_update_raises_on_pull_failure(monkeypatch):
    _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "pull": (1, "", "not possible to fast-forward")})
    with pytest.raises(RuntimeError, match="git pull failed"):
        updater.apply_update()


def test_apply_update_raises_on_pip_failure(monkeypatch, tmp_path):
    _fake_git(monkeypatch, {"rev-parse": (0, "abc1234", ""), "pull": (0, "", "")}, pip_rc=1)
    req = tmp_path / "requirements.txt"
    req.write_text("fastapi\n")
    monkeypatch.setattr(updater, "REQUIREMENTS", req)
    with pytest.raises(RuntimeError, match="pip install failed"):
        updater.apply_update()


def test_schedule_restart_uses_a_timer(monkeypatch):
    # Must not exec inline; it defers so the HTTP response can be sent first.
    started = types.SimpleNamespace(timer=None)

    class _Timer:
        def __init__(self, delay, fn):
            started.timer = (delay, fn)

        def start(self):
            pass

    monkeypatch.setattr(updater.threading, "Timer", _Timer)
    updater.schedule_restart(0.5)
    assert started.timer is not None and started.timer[0] == 0.5


def test_successor_command_is_this_interpreter_and_argv():
    assert updater.successor_command() == [sys.executable, *sys.argv]


def _restart_script(root: str, flag: str, marker: str) -> str:
    """A script that restarts itself once, then records the argv it came back with.

    The generation guard is a FILE, not an environment variable: it cannot be
    inherited by accident from the test runner (which would skip the restart
    and pass vacuously) and it caps the chain at exactly one respawn.
    """
    return textwrap.dedent(f"""
        import json, os, sys, time
        sys.path.insert(0, {root!r})
        from services import updater
        if os.path.exists({flag!r}):
            with open({marker!r}, "w") as fh:
                json.dump(sys.argv, fh)
            sys.exit(0)
        open({flag!r}, "w").close()
        updater.schedule_restart(0)
        time.sleep(10)  # the restart ends this process within a second
        sys.exit(3)  # only reached if the restart never happened
        """)


def _vendored_pythons() -> list[str]:
    """The packaged app's interpreter (3.12 embeddable), when the desktop runtime
    has been fetched on this machine — it is the one that actually restarts in
    the installed app."""
    exe = updater.ROOT / "desktop" / "src-tauri" / "vendor" / "python" / "python.exe"
    return [str(exe)] if exe.is_file() else []


@pytest.mark.parametrize("interpreter", [sys.executable, *_vendored_pythons()])
def test_restart_hands_the_successor_its_argv_intact(tmp_path, interpreter):
    """Regression: ``os.execv`` on Windows joins argv without quoting, so a path
    with a space (the packaged install lives under ``Media Studio Enterprise``)
    started a successor that died on "can't open file" while the old process
    exited 0. Perform a REAL restart from a directory with spaces and check
    exactly what the successor received."""
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    marker = spaced / "argv.json"
    script = spaced / "restart me.py"
    script.write_text(
        _restart_script(str(updater.ROOT), str(spaced / "restarted.flag"), str(marker)),
        encoding="utf-8",
    )
    # run() waits for the pipes to close, i.e. for the successor too (it inherits them).
    proc = subprocess.run(
        [interpreter, str(script), "an arg with spaces"],
        capture_output=True, text=True, timeout=60,
    )
    assert marker.exists(), f"the successor never ran: {proc.stderr[-600:]}"
    assert json.loads(marker.read_text()) == [str(script), "an arg with spaces"]
