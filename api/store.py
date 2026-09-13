"""Minimal SQLite-backed users and sessions for Media Studio Enterprise.

A deliberately small stand-in that mirrors OpenSight's auth approach - bcrypt
password hashes and opaque session tokens in an HTTP-only cookie - at the level
a scaffold needs. Everything lives in a single SQLite file under ``data/``.
Replace with a fuller auth package (users, roles, lockout) as the app grows.
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


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the users and sessions tables if they do not exist yet."""
    conn = _connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'admin',
                created_at TEXT NOT NULL
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
    """Create the built-in admin (admin/admin) when the users table is empty."""
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO users (id, username, password_hash, display_name, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (shortuuid.uuid(), "admin", _hash_password("admin"), "Administrator", "admin", now),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_user(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    user = dict(row)
    user.pop("password_hash", None)
    return user


def authenticate(username: str, password: str) -> dict | None:
    """Return the user dict (without the hash) on valid credentials, else None."""
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    if row is None or not _verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row)


def get_user(user_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_user(row)


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
    """Return the user for a live session token, or None (expired tokens are purged)."""
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
    return get_user(user_id)


def delete_session(token: str) -> None:
    if not token:
        return
    conn = _connect()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()
