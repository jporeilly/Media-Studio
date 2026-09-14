"""SQLite-backed users and sessions for Media Studio Enterprise.

Mirrors OpenSight's auth approach - bcrypt password hashes and opaque session
tokens in an HTTP-only cookie - with the account model the Enterprise edition
needs: two roles, deactivation instead of deletion, a must-change-password flag
for new and reset accounts, and a last-login stamp. Everything lives in a single
SQLite file under ``data/``.
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


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """Create the users and sessions tables, and bring an older users table up to date.

    Databases created before the account columns existed (the dev one, any
    packaged install) are migrated in place: each column missing from
    ``PRAGMA table_info(users)`` is added with its default, so existing rows
    stay active with no forced password change.
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
