"""SQLite-backed users, sessions and audit log for Media Studio Enterprise.

Mirrors OpenSight's auth approach - bcrypt password hashes and opaque session
tokens in an HTTP-only cookie - with the account model the Enterprise edition
needs: two roles, deactivation instead of deletion, a must-change-password flag
for new and reset accounts, and a last-login stamp. Everything lives in a single
SQLite file under ``data/``.

This module is the only place that speaks SQL. The audit *vocabulary* and the
never-raises ``audit()`` façade live in ``api/audit.py``; the plain row
functions here (``record_audit`` / ``list_audit`` / ``purge_audit``) raise like
any other database call, and it is the façade that decides a failed audit write
is not the caller's problem.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import shortuuid

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "media_studio.db"
SESSION_LIFETIME_DAYS = 7

# Editors do all studio work; admins additionally manage accounts and updates.
# The users table's SQL default for ``role`` is 'editor' too, so a row inserted
# without a role can never become an admin by accident. (Databases created
# before 2026-09-14 keep the old 'admin' column default - SQLite cannot change
# a default in place - which is harmless because every insert names its role.)
ROLES = ("admin", "editor")
DEFAULT_ROLE = "editor"

# Columns added to ``users`` after the first release, as (name, declaration).
# init_db() adds any that an existing database lacks.
_USER_COLUMN_MIGRATIONS = (
    ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    ("must_change_password", "INTEGER NOT NULL DEFAULT 0"),
    ("last_login", "TEXT"),
)

# The most rows one ``GET /api/admin/audit`` may return, and what it returns
# when the caller names no limit.
AUDIT_MAX_LIMIT = 1000
AUDIT_DEFAULT_LIMIT = 100


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create the users, sessions and audit_log tables, and bring an older users
    table up to date.

    Databases created before the account columns existed (the dev one, any
    packaged install) are migrated in place: each column missing from
    ``PRAGMA table_info(users)`` is added with its default, so existing rows
    stay active with no forced password change. ``audit_log`` arrives the same
    way - ``CREATE TABLE IF NOT EXISTS`` on every start - so an install from
    before the audit log simply grows the table on its next boot.
    """
    conn = _connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'editor',
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                last_login TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )"""
        )
        # ``user_id`` is nullable: an action with no authenticated actor (a failed
        # login) is a real audit event and must still be recorded. ``username``
        # is denormalised on purpose - the row keeps reading sensibly after the
        # account is gone, and a failed login can name the username that was
        # tried even though it has no user row to point at.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT REFERENCES users(id),
                username TEXT,
                action TEXT NOT NULL,
                entity TEXT,
                entity_id TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        # created_at for the default newest-first read; action and user_id
        # because ``GET /api/admin/audit`` filters on them in SQL rather than
        # in the browser.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log (action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log (user_id)")
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for name, declaration in _USER_COLUMN_MIGRATIONS:
            if name not in existing:
                conn.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")
        conn.commit()
    finally:
        conn.close()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def seed_admin() -> None:
    """Create the built-in admin (admin/admin) when the users table is empty.

    The seeded admin must set a new password at first login, so a fresh install
    never keeps admin/admin. Existing installs are untouched: the migration
    default for ``must_change_password`` is 0.
    """
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role, created_at, "
            "is_active, must_change_password) VALUES (?, ?, ?, ?, ?, ?, 1, 1)",
            (shortuuid.uuid(), "admin", _hash_password("admin"), "Administrator", "admin", _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    """Everything on the row except the hash - role, is_active, must_change_password, last_login included."""
    if row is None:
        return None
    user = dict(row)
    user.pop("password_hash", None)
    return user


def display_name_of(user: dict) -> str:
    """The human name for a user row: their display name, else their username.

    One definition, because two features denormalise it: the audit log copies
    it onto every entry and a project copies it onto its record, so both still
    read sensibly once the account is gone.
    """
    return (user.get("display_name") or "").strip() or (user.get("username") or "")


def authenticate(username: str, password: str) -> dict | None:
    """Return the user dict (without the hash) on valid credentials, else None.

    A deactivated account is refused even with the right password.
    """
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    if row is None or not row["is_active"] or not _verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row)


def get_user(user_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row)


def list_users(active_only: bool = False) -> list[dict]:
    """All accounts (or only the active ones) ordered by display name, without hashes."""
    query = "SELECT * FROM users"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY display_name COLLATE NOCASE, username"
    conn = _connect()
    try:
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()
    return [_row_to_user(row) for row in rows]


def create_user(username: str, password: str, display_name: str, role: str = DEFAULT_ROLE,
                must_change_password: bool = True) -> dict:
    """Create an account and return it; by default it must change its password at first login."""
    if role not in ROLES:
        raise ValueError("Unknown role")
    user_id = shortuuid.uuid()
    conn = _connect()
    try:
        try:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role, created_at, "
                "is_active, must_change_password) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (user_id, username, _hash_password(password), display_name, role, _now(),
                 1 if must_change_password else 0),
            )
        except sqlite3.IntegrityError:
            raise ValueError("Username already exists") from None
        conn.commit()
    finally:
        conn.close()
    return get_user(user_id)


def update_user(user_id: str, **fields) -> dict | None:
    """Update display_name / role / is_active (other keys are ignored); None for an unknown user.

    Deactivating also ends the user's sessions, so it takes effect on their next
    request rather than when the session would have expired.
    """
    changes = {k: v for k, v in fields.items() if k in ("display_name", "role", "is_active")}
    if "role" in changes and changes["role"] not in ROLES:
        raise ValueError("Unknown role")
    if "is_active" in changes:
        changes["is_active"] = 1 if changes["is_active"] else 0
    if not changes:
        return get_user(user_id)
    assignments = ", ".join(f"{k} = ?" for k in changes)
    conn = _connect()
    try:
        cur = conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*changes.values(), user_id))
        if cur.rowcount == 0:
            return None
        conn.commit()
    finally:
        conn.close()
    if changes.get("is_active") == 0:
        delete_sessions_for_user(user_id)
    return get_user(user_id)


def change_password(user_id: str, new_password: str, *, must_change: bool = False) -> None:
    """Store a new password hash; ``must_change`` makes the user pick their own at next login."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE users SET password_hash = ?, must_change_password = ? WHERE id = ?",
            (_hash_password(new_password), 1 if must_change else 0, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def verify_password(user_id: str, password: str) -> bool:
    """Check a user's current password (before allowing a change)."""
    conn = _connect()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return row is not None and _verify_password(password, row["password_hash"])


def count_active_admins() -> int:
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]
    finally:
        conn.close()


def touch_last_login(user_id: str) -> str:
    """Stamp the user's last login with now (UTC, ISO 8601) and return the stamp."""
    now = _now()
    conn = _connect()
    try:
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
        conn.commit()
    finally:
        conn.close()
    return now


def create_session(user_id: str) -> str:
    """Issue a new opaque session token for a user."""
    token = shortuuid.uuid()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_LIFETIME_DAYS)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now.isoformat(), expires.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def validate_session(token: str) -> dict | None:
    """Return the user for a live session token, or None (expired tokens are purged).

    A deactivated user's token is not honoured either, so deactivation ends an
    existing session on its next request.
    """
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return None
        user_id = row["user_id"]
    finally:
        conn.close()
    user = get_user(user_id)
    if user is None or not user["is_active"]:
        return None
    return user


def delete_session(token: str) -> None:
    if not token:
        return
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def delete_sessions_for_user(user_id: str) -> None:
    """End every session of a user (deactivation, password reset)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ── audit log ────────────────────────────────────────────────────────────────
# Plain SQL over ``audit_log``. These raise on failure like every other call in
# this module; ``api.audit.audit`` is the one that decides a failed audit write
# must not fail the user's request.

def record_audit(action: str, user_id: str | None = None, username: str | None = None,
                 entity: str | None = None, entity_id: str | None = None,
                 detail: str | None = None) -> None:
    """Insert one audit row, stamped now (UTC, ISO 8601).

    A ``user_id`` that no longer has a users row is stored as NULL rather than
    refused, so a write can never fail on a deleted account - the denormalised
    ``username`` is what keeps the row readable. (SQLite does not enforce the
    REFERENCES clause unless ``PRAGMA foreign_keys`` is on, which this app never
    turns on, so this check is the enforcement rather than a way around it.)
    """
    conn = _connect()
    try:
        if user_id is not None and conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
            user_id = None
        conn.execute(
            "INSERT INTO audit_log (user_id, username, action, entity, entity_id, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username, action, entity, entity_id, detail, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(limit: int = AUDIT_DEFAULT_LIMIT, action: str | None = None,
               user_id: str | None = None) -> list[dict]:
    """Audit rows newest first, filtered in SQL (never in the browser).

    ``limit`` is clamped to 1..``AUDIT_MAX_LIMIT`` so no caller can ask for the
    whole table. ``username`` is on the row, so there is no join.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = AUDIT_DEFAULT_LIMIT
    limit = max(1, min(limit, AUDIT_MAX_LIMIT))

    clauses: list[str] = []
    params: list = []
    if action:
        clauses.append("action = ?")
        params.append(action)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    sql = "SELECT * FROM audit_log"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    # id breaks a tie: two rows written in the same microsecond still come back
    # in the order they were written.
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)

    conn = _connect()
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def purge_audit(days: int) -> int:
    """Delete audit rows older than ``days`` days; returns how many went."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM audit_log WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()
