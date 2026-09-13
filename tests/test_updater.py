"""Tests for the Git self-updater (git and pip mocked — nothing touches the network)."""

import subprocess
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
