"""User authentication, session tokens, and tier enforcement.

Two integration shapes ship today:

  1. LocalAuth — bcrypt-hashed passwords stored in the `users` table,
     short-lived JWTs (HS256) issued + verified locally. Right default
     for a self-hosted prototype.

  2. ClerkAuth — external IDP; we just verify the JWTs Clerk issues
     against their JWKS endpoint. Right default for production once
     you don't want to own password resets, MFA, social login.

The web tier consumes them through `current_user`, a FastAPI dependency
that returns an `AuthUser` (None when anonymous in the dev path; raises
401 otherwise once `PADHAI_REQUIRE_AUTH=1`).

Tier enforcement
----------------
`AuthUser.subscription_tier` ('M1' / 'M2' / 'M3' / 'M4a' / 'M4b' / 'M4c'
/ 'M4d' / 'M4e') maps deterministically to the talking-head provider
the renderer should use for that user. `resolve_provider_for_tier()`
is the single source of truth; POST /lessons calls it after auth so
clients can't request a tier above what they're paying for. See LEARN.md
§7.1 for the subscription matrix this encodes."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Protocol

# bcrypt / pyjwt are imported lazily so the module still loads when
# auth is disabled in dev and the deps haven't been installed.


# ---- JWT settings ---------------------------------------------------------

JWT_ALG = "HS256"
JWT_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


_DEV_SECRET_MARKERS = ("dev-", "change-me", "CHANGE_ME", "secret-change", "placeholder")


def _jwt_secret() -> str:
    secret = os.environ.get("PADHAI_JWT_SECRET")
    if not secret:
        raise RuntimeError(
            "PADHAI_JWT_SECRET not set. Generate one with "
            "`python -c 'import secrets; print(secrets.token_urlsafe(48))'` "
            "and set it on every web/worker process."
        )
    if any(m in secret for m in _DEV_SECRET_MARKERS):
        is_prod = os.environ.get("APP_ENV", "").lower() in ("production", "prod")
        if is_prod:
            raise RuntimeError(
                "PADHAI_JWT_SECRET looks like a dev placeholder. "
                "Set a cryptographically random secret before deploying to production."
            )
        import warnings
        warnings.warn(
            "PADHAI_JWT_SECRET looks like a dev placeholder — "
            "never use this in production.",
            stacklevel=2,
        )
    return secret


# ---- Domain types ---------------------------------------------------------


@dataclass
class AuthUser:
    id: str
    email: str
    subscription_tier: str    # "M1" .. "M4e"
    subscription_level: str   # "L1" .. "L5"
    account_locked: bool = False


# ---- Password hashing -----------------------------------------------------


def hash_password(plain: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    import bcrypt
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ---- JWT issue / verify ---------------------------------------------------


def issue_token(user_id: str) -> str:
    import jwt as pyjwt
    payload = {
        "sub": user_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_TTL_SECONDS,
    }
    return pyjwt.encode(payload, _jwt_secret(), algorithm=JWT_ALG)


def decode_token(token: str) -> str | None:
    """Return the user_id if the token is valid + unexpired, else None."""
    import jwt as pyjwt
    try:
        payload = pyjwt.decode(token, _jwt_secret(), algorithms=[JWT_ALG])
    except pyjwt.PyJWTError:
        return None
    return payload.get("sub")


# ---- User repository ------------------------------------------------------


class UserRepository(Protocol):
    def create(
        self, email: str, password_hash: str,
        tier: str = "M1", level: str = "L3",
    ) -> AuthUser: ...

    def find_by_email(self, email: str) -> tuple[AuthUser, str] | None:
        """Returns (user, password_hash) or None. The hash is returned
        separately so callers can verify_password against it."""
        ...

    def find_by_id(self, user_id: str) -> AuthUser | None: ...


class PostgresUserRepository:
    """Backs LocalAuth onto the `users` table from padhai/db.py."""

    def __init__(self, pool):
        self.pool = pool

    def create(
        self, email: str, password_hash: str,
        tier: str = "M1", level: str = "L3",
    ) -> AuthUser:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (email, password_hash, subscription_tier, "
                "subscription_level) VALUES (%s, %s, %s, %s) RETURNING id",
                (email.lower(), password_hash, tier, level),
            )
            row = cur.fetchone()
        return AuthUser(
            id=str(row[0]), email=email.lower(),
            subscription_tier=tier, subscription_level=level,
        )

    def find_by_email(self, email: str) -> tuple[AuthUser, str] | None:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, password_hash, subscription_tier, "
                "subscription_level, account_locked FROM users WHERE email = %s",
                (email.lower(),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return (
            AuthUser(
                id=str(row[0]), email=row[1],
                subscription_tier=row[3], subscription_level=row[4],
                account_locked=bool(row[5]),
            ),
            row[2],
        )

    def find_by_id(self, user_id: str) -> AuthUser | None:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, email, subscription_tier, subscription_level, "
                "account_locked FROM users WHERE id = %s", (user_id,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return AuthUser(
            id=str(row[0]), email=row[1],
            subscription_tier=row[2], subscription_level=row[3],
            account_locked=bool(row[4]),
        )


# ---- SQLite user repository (dev / single-server mode) --------------------


class SQLiteUserRepository:
    """Local-auth backed by a SQLite `users` table.

    Activates automatically when DATABASE_URL is not set so that
    signup / login work out of the box in a fresh dev checkout or
    a single-server production deploy without Postgres."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS users (
        id              TEXT PRIMARY KEY,
        email           TEXT NOT NULL UNIQUE,
        password_hash   TEXT NOT NULL,
        subscription_tier  TEXT NOT NULL DEFAULT 'M1',
        subscription_level TEXT NOT NULL DEFAULT 'L3',
        account_locked  INTEGER NOT NULL DEFAULT 0,
        dob             TEXT,
        parent_email    TEXT,
        parent_consent_at REAL,
        parent_consent_ip TEXT,
        created_at      REAL NOT NULL DEFAULT (unixepoch())
    );
    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    """

    def __init__(self, db_path: str | None = None):
        """When `db_path` is None we use the project-wide shared
        SQLite file (env `PADHAI_DB_PATH` → `~/.padhai/jobs.db`) so
        the `users` table lives in the same DB as every other module's
        tables. Passing an explicit path is supported for tests that
        want isolation."""
        import sqlite3
        import uuid as _uuid
        if db_path is None:
            from . import db as _db
            db_path = str(_db.sqlite_path())
        self._db_path = db_path
        self._uuid = _uuid
        conn = sqlite3.connect(db_path)
        conn.executescript(self._DDL)
        # Idempotent column adds — for repos that existed before the
        # DPDP columns were part of the DDL. SQLite raises on "duplicate
        # column" so each one is wrapped in try/except.
        for stmt in (
            "ALTER TABLE users ADD COLUMN dob TEXT",
            "ALTER TABLE users ADD COLUMN parent_email TEXT",
            "ALTER TABLE users ADD COLUMN parent_consent_at REAL",
            "ALTER TABLE users ADD COLUMN parent_consent_ip TEXT",
        ):
            try:  # noqa: SIM105
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass   # column already exists
        conn.commit()
        conn.close()

    def _conn(self):
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create(
        self, email: str, password_hash: str,
        tier: str = "M1", level: str = "L3",
    ) -> AuthUser:
        uid = str(self._uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, subscription_tier,"
                " subscription_level) VALUES (?,?,?,?,?)",
                (uid, email.lower(), password_hash, tier, level),
            )
        return AuthUser(id=uid, email=email.lower(), subscription_tier=tier,
                        subscription_level=level)

    def find_by_email(self, email: str) -> tuple[AuthUser, str] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, email, password_hash, subscription_tier,"
                " subscription_level, account_locked FROM users WHERE email=?",
                (email.lower(),),
            ).fetchone()
        if not row:
            return None
        return (
            AuthUser(id=row["id"], email=row["email"],
                     subscription_tier=row["subscription_tier"],
                     subscription_level=row["subscription_level"],
                     account_locked=bool(row["account_locked"])),
            row["password_hash"],
        )

    def find_by_id(self, user_id: str) -> AuthUser | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, email, subscription_tier, subscription_level,"
                " account_locked FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return AuthUser(id=row["id"], email=row["email"],
                        subscription_tier=row["subscription_tier"],
                        subscription_level=row["subscription_level"],
                        account_locked=bool(row["account_locked"]))

    def unlock_for_consent(
        self, user_id: str, *, parent_ip: str, consented_at: float,
    ) -> bool:
        """DPDP §9 — flip account_locked off and record consent
        metadata. Called from web._verify_parent_consent after
        dpdp.verify_consent_token returns the consent record."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET parent_consent_at = ?, "
                " parent_consent_ip = ?, account_locked = 0 "
                "WHERE id = ?",
                (consented_at, parent_ip, user_id),
            )
            return cur.rowcount > 0


# ---- Tier → provider mapping ---------------------------------------------

# The single source of truth for the LEARN.md §7.1 subscription matrix.
# Server-side enforcement: client can't request HeyGen if they paid for
# M2 — they get cartoon + Bhashini regardless of what their form submits.
TIER_TO_PROVIDER: dict[str, str] = {
    "M1":  "cartoon",
    "M2":  "cartoon",       # cartoon avatar, premium voice via TTSProvider
    "M3":  "wav2lip",
    "M4a": "d-id",
    "M4b": "heygen",
    "M4c": "tavus",
    "M4d": "synthesia",
    "M4e": "deepbrain",
}


def resolve_provider_for_tier(user: AuthUser | None) -> str:
    """Map a user's subscription tier to the talking-head provider they
    are entitled to. Anonymous users get M1 (cartoon)."""
    if user is None:
        return "cartoon"
    return TIER_TO_PROVIDER.get(user.subscription_tier, "cartoon")


# ---- FastAPI dependency ---------------------------------------------------


def _require_auth() -> bool:
    return os.environ.get("PADHAI_REQUIRE_AUTH", "1") in ("1", "true", "yes")


def make_current_user_dependency(repo_or_getter):
    """Return a FastAPI dependency that resolves the bearer token to an
    AuthUser. Returns None for anonymous when PADHAI_REQUIRE_AUTH=0;
    raises 401 otherwise. Repo None means auth is disabled — always
    returns None.

    repo_or_getter may be a UserRepository, None, or a zero-arg callable
    that returns one of the above. The callable form lets callers defer
    the repo lookup to request time so the dependency works even when the
    repo is initialised after module import (e.g. in the lifespan)."""
    from fastapi import Cookie, Header, HTTPException

    require_auth = _require_auth()

    async def current_user(
        authorization: str | None = Header(default=None),
        pathshala_token: str | None = Cookie(default=None),
    ) -> AuthUser | None:
        repo = repo_or_getter() if callable(repo_or_getter) else repo_or_getter
        if repo is None:
            if require_auth:
                raise HTTPException(503, "auth required but not configured")
            return None

        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[len("bearer "):].strip()
        elif pathshala_token:
            token = pathshala_token.strip()

        if token:
            user_id = decode_token(token)
            if user_id is None:
                raise HTTPException(401, "invalid or expired token")
            user = repo.find_by_id(user_id)
            if user is None:
                raise HTTPException(401, "user not found")
            if user.account_locked:
                raise HTTPException(403, "account suspended — contact support")
            return user

        if require_auth:
            raise HTTPException(401, "missing bearer token")
        return None

    return current_user
