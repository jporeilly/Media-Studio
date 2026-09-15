"""Deleting a project either removes it or says why it could not.

The store's own behaviour is tested in ``test_projects.py``; this is the route,
which has to turn a refusal into something the Projects page can show instead of
a project that quietly lost its files. Who is allowed to delete at all is
``test_project_ownership.py``.
"""

import pytest
from fastapi.testclient import TestClient

from services import projects as store
from utils.config import config


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A signed-in admin with the store, the auth database and the config
    isolated under tmp_path."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)

    from api import store as auth_store

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")

    from api.app import app

    with TestClient(app) as c:
        # The seeded admin must change its password before the API serves it
        # anything but the auth routes; this suite is not about that gate.
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def test_a_delete_that_cannot_finish_answers_409_and_keeps_the_project(client, monkeypatch):
    """A file still open in a player or an encoder cannot be unlinked on Windows.

    The user is told to close whatever is holding it and try again. Before this,
    the delete swallowed the error and reported success, so the project vanished
    from the list while its largest file stayed on disk - a real case left a
    76 MB orphan nothing could ever see or remove.
    """
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]

    def _refuse(_pid):
        raise store.ProjectDeleteError(
            "Could not delete this project: clip.mp4 (in use). "
            "Something is still using it - close the video if it is open, then try again."
        )

    monkeypatch.setattr(store, "delete_project", _refuse)

    r = client.delete(f"/api/projects/{pid}")

    assert r.status_code == 409, r.text
    assert "clip.mp4" in r.json()["detail"]
    assert "try again" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}").status_code == 200, "still there to retry"
    assert [p["id"] for p in client.get("/api/projects").json()["projects"]] == [pid]


def test_a_delete_that_works_answers_204_and_the_project_is_gone(client):
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/projects").json()["projects"] == []


def test_deleting_something_that_is_not_there_is_a_404(client):
    assert client.delete("/api/projects/abcdef123456").status_code == 404
