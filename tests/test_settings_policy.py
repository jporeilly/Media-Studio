"""``GET``/``PUT /api/settings/password-policy`` and its enforcement on every
path that sets a password: account creation, admin reset, self change."""

import pytest
from fastapi.testclient import TestClient

from api import store
from utils.config import config

STRICT = {
    "min_length": 16, "require_upper": True, "require_digit": True, "require_symbol": False,
    "forbid_username": True, "forbid_common": True,
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """A throwaway auth database and an in-memory config (never data/config.json)."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


@pytest.fixture
def app():
    from api.app import app as fastapi_app

    with TestClient(fastapi_app):
        # The seeded admin must change its password before it may call anything
        # but the auth routes (api/deps.py). These tests are about the policy,
        # so start from an admin that has already done so - keeping "admin" as
        # the password for the helpers' sake.
        admin = store.authenticate("admin", "admin")
        store.change_password(admin["id"], "admin", must_change=False)
        yield fastapi_app


def _login(app, username, password) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _create(admin, username, password) -> dict:
    r = admin.post("/api/users", json={"username": username, "password": password, "display_name": username})
    assert r.status_code == 201, r.text
    return r.json()


def test_every_signed_in_user_can_read_the_policy(app):
    admin = _login(app, "admin", "admin")
    r = admin.get("/api/settings/password-policy")
    assert r.status_code == 200
    assert r.json()["policy"]["min_length"] == 12
    assert r.json()["description"].startswith("At least 12 characters")
    _create(admin, "ed", "Editor-Pass-2026")
    editor = _login(app, "ed", "Editor-Pass-2026")
    assert editor.get("/api/settings/password-policy").status_code == 200
    assert TestClient(app).get("/api/settings/password-policy").status_code == 401


def test_only_admins_change_the_policy(app):
    admin = _login(app, "admin", "admin")
    _create(admin, "ed", "Editor-Pass-2026")
    editor = _login(app, "ed", "Editor-Pass-2026")
    assert editor.put("/api/settings/password-policy", json=STRICT).status_code == 403
    assert TestClient(app).put("/api/settings/password-policy", json=STRICT).status_code == 401
    assert config._config == {}


@pytest.mark.parametrize("bad_length", [2, 65, "twelve"])
def test_put_validates_the_bounds(app, bad_length):
    admin = _login(app, "admin", "admin")
    r = admin.put("/api/settings/password-policy", json={**STRICT, "min_length": bad_length})
    assert r.status_code == 422


def test_saved_policy_is_enforced_on_every_password_path(app):
    admin = _login(app, "admin", "admin")
    r = admin.put("/api/settings/password-policy", json=STRICT)
    assert r.status_code == 200
    assert r.json()["policy"] == STRICT
    assert r.json()["description"].startswith("At least 16 characters; an upper-case letter; a digit")
    assert config._config["password_policy"] == STRICT

    # Creating an account: the old 12-character shape is now too short.
    r = admin.post("/api/users", json={"username": "ed", "password": "Editor-Pass-1", "display_name": "Ed"})
    assert r.status_code == 400 and "at least 16" in r.json()["detail"]
    created = _create(admin, "ed", "Editor-Pass-2026-Long")

    # An admin reset is held to the same policy.
    r = admin.post(f"/api/users/{created['id']}/reset-password", json={"new_password": "short-temp-1"})
    assert r.status_code == 400 and "at least 16" in r.json()["detail"]

    # So is a self-service change.
    editor = _login(app, "ed", "Editor-Pass-2026-Long")
    r = editor.post("/api/auth/change-password",
                    json={"current_password": "Editor-Pass-2026-Long", "new_password": "all-lower-case-2026"})
    assert r.status_code == 400 and "upper-case" in r.json()["detail"]
    r = editor.post("/api/auth/change-password",
                    json={"current_password": "Editor-Pass-2026-Long", "new_password": "Proper-Pass-2026-XL"})
    assert r.status_code == 200, r.text
