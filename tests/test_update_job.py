"""The update as a job the Settings page can follow (E7 fix round, the review's m1 and n9).

``GET /api/system/update/job`` answers the update in flight, ``{"active_job":
{id, kind, status, user_id}}`` or ``null`` - how a reload of the Settings page
finds an update still running, and how the page knows the server answers again
after it gave up on one. ``POST /api/system/update`` refuses a second update
with a 409 while one is queued or running: two ``git pull``/``pip install`` runs
over one install at once are never wanted, and the page gives **Update now**
back after it lets go of a job it could not reach.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, updater
from utils.config import config

PASSWORD = "Editor-pass-12345"


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    from api.app import app

    with TestClient(app) as admin:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert admin.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        auth_store.create_user("editor", PASSWORD, "Ed Editor", role="editor", must_change_password=False)
        editor = TestClient(app)
        assert editor.post("/api/auth/login", json={"username": "editor", "password": PASSWORD}).status_code == 200
        yield {"admin": admin, "editor": editor}


class _HeldUpdate:
    """``updater.apply_update`` replaced by one that runs until released."""

    def __init__(self, monkeypatch):
        self.started, self.release, self.calls = threading.Event(), threading.Event(), 0

        def apply_update(progress=None):
            self.calls += 1
            self.started.set()
            self.release.wait(10)
            return {"updated_from": "a", "updated_to": "b", "changed": True}

        monkeypatch.setattr(updater, "apply_update", apply_update)


def _wait_done(job_id):
    deadline = time.time() + 5
    while jobs.get(job_id)["status"] not in ("done", "error"):
        assert time.time() < deadline, "the update job did not finish"
        time.sleep(0.01)


def test_a_second_update_is_refused_while_one_runs(clients, monkeypatch):
    held = _HeldUpdate(monkeypatch)
    first = clients["admin"].post("/api/system/update")
    assert first.status_code == 200, first.text
    job_id = first.json()["job_id"]
    assert held.started.wait(5)
    try:
        second = clients["admin"].post("/api/system/update")
        assert second.status_code == 409, second.text
        assert second.json()["detail"] == "An update is already running. Wait for it to finish."
        assert held.calls == 1, "no second git pull / pip install was started"
    finally:
        held.release.set()
    _wait_done(job_id)
    third = clients["admin"].post("/api/system/update")
    assert third.status_code == 200, "idle again: an update can start"
    held.release.set()
    _wait_done(third.json()["job_id"])


def test_the_update_in_flight_is_reported_to_any_signed_in_user(clients, monkeypatch):
    assert clients["editor"].get("/api/system/update/job").json() == {"active_job": None}
    held = _HeldUpdate(monkeypatch)
    job_id = clients["admin"].post("/api/system/update").json()["job_id"]
    assert held.started.wait(5)
    try:
        for who in ("admin", "editor"):
            r = clients[who].get("/api/system/update/job")
            assert r.status_code == 200, r.text
            assert r.json() == {"active_job": {"id": job_id, "kind": "update", "status": "running", "user_id": None}}
            assert clients[who].get(f"/api/jobs/{job_id}").status_code == 200, "and the job itself is readable"
    finally:
        held.release.set()
    _wait_done(job_id)
    assert clients["editor"].get("/api/system/update/job").json() == {"active_job": None}, "a finished update is not active"


def test_only_an_admin_may_cancel_the_update(clients, monkeypatch):
    """The update records no starter, so every signed-in user may read and
    follow it - and, before this, "cancel" it: a request that stops nothing
    (an update never looks at the flag) and still wrote a ``job.cancel`` audit
    row, now that the update's id is discoverable. It is an admin's alone."""
    held = _HeldUpdate(monkeypatch)
    job_id = clients["admin"].post("/api/system/update").json()["job_id"]
    assert held.started.wait(5)
    try:
        refused = clients["editor"].post(f"/api/jobs/{job_id}/cancel")
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == "Only the user who started this job, or an admin, can cancel it."
        assert jobs.get(job_id)["cancel_requested"] is False, "nothing was asked of the job"
        audit = clients["admin"].get("/api/admin/audit", params={"action": "job.cancel"})
        assert audit.status_code == 200 and audit.json()["entries"] == [], "no audit row for a refused cancel"
        assert clients["admin"].post(f"/api/jobs/{job_id}/cancel").status_code == 200, "an admin may"
        assert jobs.get(job_id)["cancel_requested"] is True
        assert len(clients["admin"].get("/api/admin/audit", params={"action": "job.cancel"}).json()["entries"]) == 1
    finally:
        held.release.set()
    _wait_done(job_id)


def test_the_update_lock_admits_one_of_many_concurrent_starts(monkeypatch):
    """``start_single`` checks and submits under the start lock. Sixteen
    threads released together, with the window between the check and the
    submit widened, still start exactly one update; the rest are refused."""
    real_active = jobs.active_of_kind

    def slow_active(kind):
        found = real_active(kind)
        time.sleep(0.005)  # the window a second request would slip through without the lock
        return found

    monkeypatch.setattr(jobs, "active_of_kind", slow_active)
    release = threading.Event()
    barrier = threading.Barrier(16)
    started, refused = [], []

    def work(progress):
        release.wait(10)

    def attempt():
        barrier.wait(5)
        try:
            started.append(jobs.start_single("update-race", work))
        except jobs.KindBusy:
            refused.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    try:
        assert len(started) == 1, f"{len(started)} updates started at once"
        assert len(refused) == 15
    finally:
        release.set()
    for job_id in started:
        _wait_done(job_id)


def test_a_project_job_is_never_reported_as_the_update(clients):
    """``active_of_kind`` is for jobs that belong to no project."""
    started, release = threading.Event(), threading.Event()

    def work(progress):
        started.set()
        release.wait(10)

    job_id = jobs.submit("update", work, project_id="0123456789ab")
    assert started.wait(5)
    try:
        assert clients["admin"].get("/api/system/update/job").json() == {"active_job": None}
    finally:
        release.set()
    _wait_done(job_id)


def test_the_route_needs_a_session(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    from api.app import app

    with TestClient(app) as anonymous:
        assert anonymous.get("/api/system/update/job").status_code == 401
