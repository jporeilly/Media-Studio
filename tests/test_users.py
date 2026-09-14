"""Tests for accounts: the users-table migration, the seeded admin, and the
/api/users + /api/auth/change-password endpoints (roles, guards, deactivation,
password resets).

The auth database is a throwaway SQLite file per test and the password policy
is the default one (``api/passwords.py``; the policy itself is tested in
``test_passwords.py`` / ``test_settings_policy.py``), so the passwords used here
are policy-shaped. Each signed-in actor gets its own TestClient because a
client carries one cookie jar and the cookie wins over a bearer header.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from api import store
from utils.config import config

PASSWORD = "Secret-Pass-123"
NEW_PASSWORD = "Another-Pass-456"
TEMP_PASSWORD = "Temp-Pass-789"


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Point the auth store at a fresh SQLite file under tmp_path, and the
    password policy at an in-memory config (the developer's data/config.json
    may carry a stricter policy than the passwords used here)."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    return store.DB_PATH


@pytest.fixture
def app():
    """The FastAPI app with its lifespan run (init_db + seed_admin) against the
    isolated file, with the seeded admin's first-login password change already
    made (keeping "admin" as the password): until that change the API refuses
    everything but the auth routes (api/deps.py), which has its own tests below."""
    from api.app import app as fastapi_app

    with TestClient(fastapi_app):
        admin = store.authenticate("admin", "admin")
        store.change_password(admin["id"], "admin", must_change=False)
        yield fastapi_app


def _client(app, username=None, password=None) -> TestClient:
    """A fresh client with its own cookie jar, signed in when credentials are given."""
    c = TestClient(app)
    if username is not None:
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
    return c


def _create(admin: TestClient, username: str, display_name: str, role: str | None = None) -> dict:
    body = {"username": username, "password": PASSWORD, "display_name": display_name}
    if role:
        body["role"] = role
    r = admin.post("/api/users", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── schema + seed ─────────────────────────────────────────────────────────────

def test_init_db_migrates_an_old_users_table(isolated_db):
    # A database from before the account columns existed: the original CREATE TABLE and one row.
    isolated_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(isolated_db))
    conn.execute(
        """CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            display_name TEXT, role TEXT NOT NULL DEFAULT 'admin', created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, display_name, role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("old-id", "olduser", store._hash_password("old-pass"), "Old User", "admin", "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    store.init_db()
    store.init_db()  # idempotent: a second run must not try to add the columns again

    conn = sqlite3.connect(str(isolated_db))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    conn.close()
    assert {"is_active", "must_change_password", "last_login"} <= columns

    # The existing row keeps working with the defaults: active, no forced change, never logged in;
    # and the table is not empty, so the admin is not re-seeded over it.
    store.seed_admin()
    user = store.authenticate("olduser", "old-pass")
    assert user["id"] == "old-id"
    assert user["is_active"] == 1
    assert user["must_change_password"] == 0
    assert user["last_login"] is None
    assert [u["username"] for u in store.list_users()] == ["olduser"]


def test_seeded_admin_must_change_password():
    # Straight from the seed - the `app` fixture deliberately clears this flag.
    store.init_db()
    store.seed_admin()
    admin = store.authenticate("admin", "admin")
    assert admin["must_change_password"] == 1
    assert admin["is_active"] == 1
    assert admin["role"] == "admin"
    assert "password_hash" not in admin


def test_login_stamps_last_login(app):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    stamp = r.json()["user"]["last_login"]
    assert stamp
    assert store.get_user(r.json()["user"]["id"])["last_login"] == stamp


# ── create + first login + change password ────────────────────────────────────

def test_admin_creates_user_who_logs_in_and_changes_password(app):
    admin = _client(app, "admin", "admin")
    r = admin.post("/api/users", json={"username": " sam ", "password": PASSWORD, "display_name": " Sam Editor ", "role": "editor"})
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["username"] == "sam" and created["display_name"] == "Sam Editor"  # trimmed
    assert created["role"] == "editor"
    assert created["must_change_password"] == 1 and created["is_active"] == 1
    assert "password_hash" not in created

    # Editor is the default role.
    assert _create(admin, "eve", "Eve")["role"] == "editor"

    sam = _client(app, "sam", PASSWORD)
    me = sam.get("/api/auth/me").json()["user"]
    assert me["id"] == created["id"]
    assert me["must_change_password"] == 1
    assert me["last_login"] is not None

    r = sam.post("/api/auth/change-password", json={"current_password": "wrong", "new_password": NEW_PASSWORD})
    assert r.status_code == 400 and r.json()["detail"] == "Current password is incorrect"
    assert store.authenticate("sam", PASSWORD)  # nothing changed

    r = sam.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 200
    # The session that made the change is still valid, and the flag is cleared.
    assert sam.get("/api/auth/me").json()["user"]["must_change_password"] == 0
    # Only the new password works now.
    assert store.authenticate("sam", PASSWORD) is None
    assert store.authenticate("sam", NEW_PASSWORD)["id"] == created["id"]


def test_list_users_includes_inactive_and_hides_hashes(app):
    admin = _client(app, "admin", "admin")
    bob = _create(admin, "bob", "Bob")
    assert admin.patch(f"/api/users/{bob['id']}", json={"is_active": False}).status_code == 200

    users = admin.get("/api/users").json()["users"]
    assert [u["username"] for u in users] == ["admin", "bob"]  # ordered by display name
    assert {u["username"]: u["is_active"] for u in users} == {"admin": 1, "bob": 0}
    assert all("password_hash" not in u for u in users)
    assert store.list_users(active_only=True) and [u["username"] for u in store.list_users(active_only=True)] == ["admin"]


# ── authorisation ─────────────────────────────────────────────────────────────

def test_non_admins_cannot_manage_accounts(app):
    admin = _client(app, "admin", "admin")
    ed = _create(admin, "ed", "Ed")
    editor = _client(app, "ed", PASSWORD)

    assert editor.get("/api/users").status_code == 403
    assert editor.post("/api/users", json={"username": "x", "password": PASSWORD, "display_name": "X"}).status_code == 403
    assert editor.patch(f"/api/users/{ed['id']}", json={"display_name": "Nope"}).status_code == 403
    assert editor.post(f"/api/users/{ed['id']}/reset-password", json={"new_password": PASSWORD}).status_code == 403
    # Editors still manage their own password.
    assert editor.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
    # Signed-out callers get 401.
    assert TestClient(app).get("/api/users").status_code == 401


# ── validation ────────────────────────────────────────────────────────────────

def test_create_rejects_duplicates_unknown_roles_and_blank_fields(app):
    admin = _client(app, "admin", "admin")
    base = {"username": "dup", "password": PASSWORD, "display_name": "Dup"}
    assert admin.post("/api/users", json=base).status_code == 201

    r = admin.post("/api/users", json=base)
    assert r.status_code == 400 and r.json()["detail"] == "Username already exists"
    r = admin.post("/api/users", json={**base, "username": "other", "role": "superuser"})
    assert r.status_code == 400 and r.json()["detail"] == "Unknown role"
    assert admin.post("/api/users", json={**base, "username": "   "}).status_code == 400
    assert admin.post("/api/users", json={**base, "username": "blank", "display_name": " "}).status_code == 400
    assert [u["username"] for u in store.list_users()] == ["admin", "dup"]


def test_patch_validation_and_unknown_user(app):
    admin = _client(app, "admin", "admin")
    bob = _create(admin, "bob", "Bob")
    assert admin.patch("/api/users/nope", json={"display_name": "X"}).status_code == 404
    assert admin.patch(f"/api/users/{bob['id']}", json={"role": "boss"}).status_code == 400
    assert admin.patch(f"/api/users/{bob['id']}", json={"display_name": "  "}).status_code == 400
    r = admin.patch(f"/api/users/{bob['id']}", json={"display_name": " Robert ", "role": "admin"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Robert" and r.json()["role"] == "admin"
    assert "password_hash" not in r.json()


def test_password_policy_message_becomes_a_400(app, monkeypatch):
    """Wiring only (the policy itself is the owner's): whatever validate_password
    returns is the 400 detail on create, reset and change, and nothing is stored."""
    from api.routers import auth as auth_router
    from api.routers import users as users_router

    seen = []

    def policy(password, username=None):
        seen.append(username)
        return "Password rejected by policy" if password == "weak" else None

    monkeypatch.setattr(users_router, "validate_password", policy)
    monkeypatch.setattr(auth_router, "validate_password", policy)
    admin = _client(app, "admin", "admin")

    r = admin.post("/api/users", json={"username": "w", "password": "weak", "display_name": "W"})
    assert r.status_code == 400 and r.json()["detail"] == "Password rejected by policy"
    bob = _create(admin, "bob", "Bob")
    r = admin.post(f"/api/users/{bob['id']}/reset-password", json={"new_password": "weak"})
    assert r.status_code == 400 and r.json()["detail"] == "Password rejected by policy"
    r = admin.post("/api/auth/change-password", json={"current_password": "admin", "new_password": "weak"})
    assert r.status_code == 400 and r.json()["detail"] == "Password rejected by policy"

    assert seen == ["w", "bob", "bob", "admin"]  # the policy sees the account's username
    assert store.authenticate("admin", "admin") and store.authenticate("bob", PASSWORD)
    assert store.get_user(bob["id"])["must_change_password"] == 1  # create's flag, not a reset's


# ── admin guards ──────────────────────────────────────────────────────────────

def test_admin_cannot_deactivate_or_demote_themselves(app):
    admin = _client(app, "admin", "admin")
    admin_id = admin.get("/api/auth/me").json()["user"]["id"]
    # A second active admin, so the last-admin rule is not what refuses these.
    other = _create(admin, "root2", "Root Two", role="admin")
    assert store.count_active_admins() == 2

    r = admin.patch(f"/api/users/{admin_id}", json={"is_active": False})
    assert r.status_code == 400 and r.json()["detail"] == "You cannot deactivate your own account"
    r = admin.patch(f"/api/users/{admin_id}", json={"role": "editor"})
    assert r.status_code == 400 and r.json()["detail"] == "You cannot remove your own admin role"
    # Renaming yourself is fine, and the session survives.
    r = admin.patch(f"/api/users/{admin_id}", json={"display_name": "Chief"})
    assert r.status_code == 200 and r.json()["display_name"] == "Chief"
    assert admin.get("/api/auth/me").json()["user"]["display_name"] == "Chief"
    # Another admin may be demoted while this one remains.
    assert admin.patch(f"/api/users/{other['id']}", json={"role": "editor"}).status_code == 200
    assert store.count_active_admins() == 1


def test_last_active_admin_cannot_be_demoted_or_deactivated(app):
    admin = _client(app, "admin", "admin")
    admin_id = admin.get("/api/auth/me").json()["user"]["id"]
    assert store.count_active_admins() == 1

    for change in ({"role": "editor"}, {"is_active": False}):
        r = admin.patch(f"/api/users/{admin_id}", json=change)
        assert r.status_code == 400, change
        assert "last active admin" in r.json()["detail"]
    # Still an active admin with a live session; a same-role edit is not a demotion.
    assert admin.patch(f"/api/users/{admin_id}", json={"role": "admin"}).status_code == 200
    assert admin.get("/api/users").status_code == 200
    assert store.count_active_admins() == 1


# ── deactivation + reset ──────────────────────────────────────────────────────

def test_deactivation_ends_sessions_and_refuses_login(app):
    admin = _client(app, "admin", "admin")
    bob = _create(admin, "bob", "Bob")
    bob_client = _client(app, "bob", PASSWORD)
    assert bob_client.get("/api/auth/me").status_code == 200

    r = admin.patch(f"/api/users/{bob['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] == 0
    assert bob_client.get("/api/auth/me").status_code == 401  # the existing session is dead
    r = TestClient(app).post("/api/auth/login", json={"username": "bob", "password": PASSWORD})
    assert r.status_code == 401  # and no new one is issued
    assert store.authenticate("bob", PASSWORD) is None

    # Reactivation lets them sign in again.
    assert admin.patch(f"/api/users/{bob['id']}", json={"is_active": True}).status_code == 200
    assert _client(app, "bob", PASSWORD).get("/api/auth/me").status_code == 200


def test_store_refuses_inactive_users_even_with_a_live_session_row(app):
    bob = store.create_user("bob", PASSWORD, "Bob")
    token = store.create_session(bob["id"])
    assert store.validate_session(token)["id"] == bob["id"]

    # Flip the flag directly (not through update_user, which also deletes the sessions)
    # to prove validate_session and authenticate check is_active themselves.
    conn = sqlite3.connect(str(store.DB_PATH))
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (bob["id"],))
    conn.commit()
    conn.close()
    assert store.validate_session(token) is None
    assert store.authenticate("bob", PASSWORD) is None


def test_reset_password_forces_a_change_and_ends_sessions(app):
    admin = _client(app, "admin", "admin")
    bob = _create(admin, "bob", "Bob")
    bob_client = _client(app, "bob", PASSWORD)
    assert bob_client.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}).status_code == 200
    assert bob_client.get("/api/auth/me").json()["user"]["must_change_password"] == 0

    r = admin.post(f"/api/users/{bob['id']}/reset-password", json={"new_password": TEMP_PASSWORD})
    assert r.status_code == 200
    assert bob_client.get("/api/auth/me").status_code == 401  # session ended
    assert store.authenticate("bob", NEW_PASSWORD) is None  # the old password is gone
    fresh = store.authenticate("bob", TEMP_PASSWORD)
    assert fresh["must_change_password"] == 1
    # They sign in with the temporary password and are asked to change it.
    assert _client(app, "bob", TEMP_PASSWORD).get("/api/auth/me").json()["user"]["must_change_password"] == 1

    assert admin.post("/api/users/nope/reset-password", json={"new_password": TEMP_PASSWORD}).status_code == 404


# ── review follow-ups: the other-admin case, self reset, and the API-level gate ──

def test_admin_can_deactivate_the_only_other_admin_and_ends_their_session(app):
    admin = _client(app, "admin", "admin")
    other = _create(admin, "second", "Second Admin", role="admin")
    other_client = _client(app, "second", PASSWORD)
    assert other_client.get("/api/auth/me").status_code == 200
    r = admin.patch(f"/api/users/{other['id']}", json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] == 0
    assert other_client.get("/api/auth/me").status_code == 401
    assert admin.get("/api/users").status_code == 200  # the caller is still an active admin


def test_admin_resetting_their_own_password_ends_their_own_session(app):
    admin = _client(app, "admin", "admin")
    me = admin.get("/api/auth/me").json()["user"]
    r = admin.post(f"/api/users/{me['id']}/reset-password", json={"new_password": TEMP_PASSWORD})
    assert r.status_code == 200
    assert admin.get("/api/auth/me").status_code == 401
    again = _client(app, "admin", TEMP_PASSWORD)
    assert again.get("/api/auth/me").json()["user"]["must_change_password"] == 1


def test_api_refuses_real_work_until_the_password_is_changed(app):
    """The first-login gate is enforced by the API, not only by the UI."""
    admin = _client(app, "admin", "admin")
    _create(admin, "newbie", "New Person")
    newbie = _client(app, "newbie", PASSWORD)
    r = newbie.get("/api/projects")
    assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    assert newbie.get("/api/auth/me").status_code == 200
    assert newbie.get("/api/settings/password-policy").status_code == 200
    r = newbie.post("/api/auth/change-password",
                    json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert r.status_code == 200, r.text
    assert newbie.get("/api/projects").status_code == 200


def test_seeded_admin_is_gated_until_its_first_password_change():
    """A fresh install's admin/admin can sign in, and do nothing else until it picks a password."""
    from api.app import app as fastapi_app

    with TestClient(fastapi_app):
        fresh = _client(fastapi_app, "admin", "admin")
        assert fresh.get("/api/users").status_code == 403
        assert fresh.get("/api/system/update").status_code == 403
        r = fresh.post("/api/auth/change-password",
                       json={"current_password": "admin", "new_password": NEW_PASSWORD})
        assert r.status_code == 200, r.text
        assert fresh.get("/api/users").status_code == 200
