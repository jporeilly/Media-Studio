"""The project's running job, whoever started it (E7 #1), and the rules that
go with following one.

``GET /api/projects/{pid}/job`` answers ``{"active_job": {id, kind, status,
user_id}}`` for the job holding the project, or ``{"active_job": null}``: the
page asks it to follow a job it did not start (after a reload, in another tab,
or when someone else started it). The page then polls ``GET /api/jobs/{id}``,
which the project's OWNER may read whoever started the job; cancelling stays
with the starter or an admin. A delete is held by a job like every other
writer.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs
from services import projects as store
from utils.config import config

PASSWORD = "Owner-pass-12345"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


def _signed_in(app, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def cast():
    """The admin, a project's owner (an editor) and another editor, each with
    their own cookie jar, plus the accounts."""
    from api.app import app

    with TestClient(app):
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        owner = auth_store.create_user("owner", PASSWORD, "Olive Owner", role="editor", must_change_password=False)
        other = auth_store.create_user("other", PASSWORD, "Otto Other", role="editor", must_change_password=False)
        yield {
            "admin": _signed_in(app, "admin", "admin"),
            "owner": _signed_in(app, "owner", PASSWORD),
            "other": _signed_in(app, "other", PASSWORD),
            "admin_account": seeded,
            "owner_account": owner,
            "other_account": other,
        }


class _Held:
    """A job attached to ``pid``, started as ``user_id``, running until the
    test releases it."""

    def __init__(self, pid: str, kind: str, user_id: str | None):
        self.started, self.release = threading.Event(), threading.Event()

        def work(progress):
            self.started.set()
            self.release.wait(10)
            return {"ok": True}

        self.id = jobs.start(kind, work, project_id=pid, user_id=user_id)
        assert self.started.wait(5)

    def finish(self) -> None:
        self.release.set()
        deadline = time.time() + 5
        while time.time() < deadline:
            if jobs.get(self.id)["status"] in ("done", "error"):
                return
            time.sleep(0.01)
        raise AssertionError("the held job did not finish")


def _owned_video(cast) -> str:
    owner = cast["owner_account"]
    return store.import_upload("clip.mp4", b"video-bytes", owner_id=owner["id"], owner_name=owner["display_name"])["id"]


def test_a_project_with_no_job_answers_null(cast):
    pid = _owned_video(cast)
    r = cast["owner"].get(f"/api/projects/{pid}/job")
    assert r.status_code == 200, r.text
    assert r.json() == {"active_job": None}


def test_the_running_job_is_reported_with_exactly_the_four_fields_and_is_gone_once_done(cast):
    pid = _owned_video(cast)
    held = _Held(pid, "transcribe", cast["owner_account"]["id"])
    try:
        r = cast["owner"].get(f"/api/projects/{pid}/job")
        assert r.status_code == 200, r.text
        assert r.json() == {"active_job": {
            "id": held.id, "kind": "transcribe", "status": "running", "user_id": cast["owner_account"]["id"],
        }}, "which job, which card, still in flight, and who started it - nothing else"
    finally:
        held.finish()
    assert cast["owner"].get(f"/api/projects/{pid}/job").json() == {"active_job": None}, "a finished job is not active"


def test_a_job_someone_else_started_is_reported_and_readable_by_the_owner_but_not_cancellable(cast):
    """An administrator's job on an editor's project: the owner's page follows
    it - it may read the job's progress - but only the starter or an admin
    may stop it (the cancel route's own rule, unchanged)."""
    pid = _owned_video(cast)
    admin_id = cast["admin_account"]["id"]
    held = _Held(pid, "revoice", admin_id)
    try:
        active = cast["owner"].get(f"/api/projects/{pid}/job").json()["active_job"]
        assert active == {"id": held.id, "kind": "revoice", "status": "running", "user_id": admin_id}

        read = cast["owner"].get(f"/api/jobs/{held.id}")
        assert read.status_code == 200, "the owner watches the job on their own project"
        assert read.json()["id"] == held.id and read.json()["status"] == "running"

        refused = cast["owner"].post(f"/api/jobs/{held.id}/cancel")
        assert refused.status_code == 403
        assert "Only the user who started this job" in refused.json()["detail"]
        assert jobs.get(held.id)["cancel_requested"] is False, "nothing was asked of the job"

        assert cast["admin"].post(f"/api/jobs/{held.id}/cancel").status_code == 200, "the starter (an admin) may"
        assert jobs.get(held.id)["cancel_requested"] is True
    finally:
        held.finish()


def test_another_editor_sees_neither_the_active_job_nor_the_job(cast):
    """The access rule: the route is a project route (403 for a project that
    is not theirs), and the widened read of ``GET /api/jobs/{id}`` reaches no
    further than the project's own access rule."""
    pid = _owned_video(cast)
    held = _Held(pid, "transcribe", cast["owner_account"]["id"])
    try:
        r = cast["other"].get(f"/api/projects/{pid}/job")
        assert r.status_code == 403 and "admin or the project's owner" in r.json()["detail"]
        assert cast["other"].get(f"/api/jobs/{held.id}").status_code == 403
        assert cast["other"].post(f"/api/jobs/{held.id}/cancel").status_code == 403
        # The admin reads and sees it, as for every project.
        assert cast["admin"].get(f"/api/projects/{pid}/job").json()["active_job"]["id"] == held.id
        assert cast["admin"].get(f"/api/jobs/{held.id}").status_code == 200
    finally:
        held.finish()


def test_a_job_on_an_admin_owned_project_stays_the_admins(cast):
    """A project with no owner is admin-owned: an editor may not read a job
    on it any more than before."""
    pid = store.import_upload("legacy.mp4", b"video-bytes")["id"]
    held = _Held(pid, "transcribe", cast["admin_account"]["id"])
    try:
        assert cast["owner"].get(f"/api/jobs/{held.id}").status_code == 403
        assert cast["owner"].get(f"/api/projects/{pid}/job").status_code == 403
    finally:
        held.finish()


def test_the_read_refusal_names_everyone_who_may_read(cast):
    """The refusal says who may see a job now that the project's owner is one
    of them; the cancel refusal still names only the starter and an admin."""
    pid = _owned_video(cast)
    held = _Held(pid, "transcribe", cast["owner_account"]["id"])
    try:
        r = cast["other"].get(f"/api/jobs/{held.id}")
        assert r.status_code == 403
        assert r.json()["detail"] == "Only the user who started this job, the owner of its project, or an admin can see it."
        r = cast["other"].post(f"/api/jobs/{held.id}/cancel")
        assert r.json()["detail"] == "Only the user who started this job, or an admin, can cancel it."
    finally:
        held.finish()


def test_a_job_with_no_starter_is_anyones_to_read(cast):
    """An update records no starter and belongs to no project: every signed-in
    user may read it (the rule for a job with no ``user_id``, kept by the new
    read rule), and so may follow it on the Settings page."""
    started, release = threading.Event(), threading.Event()

    def work(progress):
        started.set()
        release.wait(10)

    job_id = jobs.submit("update", work)
    assert started.wait(5)
    try:
        for who in ("owner", "other", "admin"):
            r = cast[who].get(f"/api/jobs/{job_id}")
            assert r.status_code == 200, (who, r.text)
            assert r.json()["user_id"] is None and r.json()["project_id"] is None
    finally:
        release.set()
    deadline = time.time() + 5
    while jobs.get(job_id)["status"] not in ("done", "error") and time.time() < deadline:
        time.sleep(0.01)


def test_a_lost_job_is_a_404_with_the_message_the_page_reads(cast):
    """Jobs live in memory: after a restart every id a page was polling is
    unknown. The page reads exactly this 404 as "the job was lost"."""
    r = cast["owner"].get("/api/jobs/0123456789ab")
    assert r.status_code == 404 and r.json()["detail"] == "Job not found."


def test_a_delete_is_refused_while_a_job_holds_the_project(cast):
    """Every other writer takes ``require_idle``; the delete does too - a job
    has its own copy of the project and would go on writing into a
    directory that is being removed."""
    pid = _owned_video(cast)
    held = _Held(pid, "transcribe", cast["owner_account"]["id"])
    try:
        r = cast["owner"].delete(f"/api/projects/{pid}")
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == (
            "A job is running for this project (transcribe). Wait for it to finish, then try again."
        )
        assert store.get_project(pid) is not None, "nothing was removed"
        assert (store.PROJECTS_DIR / pid / "clip.mp4").is_file()
        assert cast["admin"].delete(f"/api/projects/{pid}").status_code == 409, "an admin is held too"
    finally:
        held.finish()
    assert cast["owner"].delete(f"/api/projects/{pid}").status_code == 204, "idle again: the delete goes through"
    assert store.get_project(pid) is None
