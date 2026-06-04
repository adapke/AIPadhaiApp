"""Standalone authentication for the Admin Console.

Self-contained — does NOT import from `padhai.*`. Has its own:
- `admin_users` SQLite/Postgres table (separate from the main app's users)
- `ADMIN_JWT_SECRET` env var (separate from the main app's JWT secret)
- bcrypt password hashing, JWT issue/verify, login + signup endpoints
- `AdminUser` dataclass

Why this separation: when the admin console moves to its own repository
(planned next iteration), no code needs to change — just move the
`admin/` directory. The student app and admin app never share a Python
import path, only the underlying database file (which is the
operational contract between them, not an internal API).

The first signup is allowed without an invite — that user becomes the
bootstrap admin. Subsequent signups require an existing admin to mint
an invite via POST /admin/invites (out of scope for v0.8.1; for now
the bootstrap pattern is "first signup wins").
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt
import jwt as pyjwt
from fastapi import HTTPException, Request, status

JWT_ALG = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12 hours — admin sessions are short


# Where the admin users table lives. Single SQLite file under the same
# `~/.padhai` directory as the main app's job DB — they're co-located
# only because operators usually back up that whole directory; there is
# no schema dependency between the two databases.
def _db_path() -> Path:
    custom = os.environ.get("ADMIN_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "admin.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id              TEXT PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    display_name    TEXT,
    created_at      REAL NOT NULL,
    last_login_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users(email);
"""


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class AdminUser:
    id: str
    email: str
    display_name: str | None
    created_at: float
    last_login_at: float | None


# ---------- password + token helpers ----------

def _jwt_secret() -> str:
    secret = os.environ.get("ADMIN_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "ADMIN_JWT_SECRET not set. Generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` "
            "and set it on the admin process. This is intentionally "
            "separate from PADHAI_JWT_SECRET so the two services can "
            "be deployed with independent key rotations."
        )
    return secret


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def issue_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
        "iss": "padhai-admin",
    }
    return pyjwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def decode_token(token: str) -> str | None:
    try:
        payload = pyjwt.decode(
            token, _jwt_secret(), algorithms=[JWT_ALG],
            options={"require": ["sub", "exp"]},
            issuer="padhai-admin",
        )
        return str(payload["sub"])
    except pyjwt.PyJWTError:
        return None


# ---------- user repository ----------

def count_users() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]


def create_user(email: str, password: str, display_name: str | None = None) -> AdminUser:
    """Insert a new admin user. Raises on duplicate email."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "invalid email")
    if len(password) < 8:
        raise HTTPException(400, "password must be at least 8 characters")
    uid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO admin_users (id, email, password_hash, "
                "display_name, created_at) VALUES (?, ?, ?, ?, ?)",
                (uid, email, hash_password(password), display_name, now),
            )
    except sqlite3.IntegrityError:
        raise HTTPException(409, "email already registered")
    return AdminUser(
        id=uid, email=email, display_name=display_name,
        created_at=now, last_login_at=None,
    )


def find_by_email(email: str) -> tuple[AdminUser, str] | None:
    """Returns (user, password_hash) so the login flow can verify
    without leaking the hash through AdminUser itself."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, display_name, created_at, "
            "last_login_at FROM admin_users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    if not row:
        return None
    return (
        AdminUser(id=row[0], email=row[1], display_name=row[3],
                  created_at=row[4], last_login_at=row[5]),
        row[2],
    )


def find_by_id(user_id: str) -> AdminUser | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, email, display_name, created_at, last_login_at "
            "FROM admin_users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return AdminUser(id=row[0], email=row[1], display_name=row[2],
                     created_at=row[3], last_login_at=row[4])


def mark_login(user_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE admin_users SET last_login_at = ? WHERE id = ?",
            (time.time(), user_id),
        )


# ---------- FastAPI dependencies ----------

# Cookie name. Intentionally different from the main app's `padhai_token`
# so a logged-in student session does not auto-grant admin access.
COOKIE_NAME = "admin_token"


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()
    return request.cookies.get(COOKIE_NAME)


def require_admin(request: Request) -> AdminUser:
    """FastAPI dependency. 401 if no token, 401 if invalid, 401 if the
    user record was deleted between issue and use."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing authentication token",
        )
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        )
    user = find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found",
        )
    return user


def optional_admin(request: Request) -> AdminUser | None:
    """Like require_admin but returns None instead of raising. Used by
    the home page which decides login vs dashboard render."""
    try:
        return require_admin(request)
    except HTTPException:
        return None
