"""H2 — SCIM 2.0 auto-provisioning.

The IdP (Okta / AzureAD) pushes user create/update/deactivate events
to us via SCIM 2.0. When HR removes a teacher from the IdP, our app
deactivates them within minutes — no more leftover access after
offboarding.

SCIM 2.0 is JSON-over-HTTPS with a strict schema (RFC 7643/7644).
This module implements the User endpoints; Groups (RFC 7643 §4.2)
land in v1.3.x.

Schema is additive — extends the existing users + org_members tables
with SCIM externalId tracking + deactivation flag:

  ALTER TABLE users ADD COLUMN scim_external_id TEXT
  ALTER TABLE users ADD COLUMN deactivated_at REAL

Per-org SCIM bearer tokens live in their own table so an org admin
can rotate them without touching anyone else's:

  org_scim_tokens     opaque bearer tokens, hashed; one or more per org
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_scim_tokens (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,         -- 'Okta Production', 'AzureAD Test'
    token_hash      TEXT NOT NULL,         -- sha256 of the raw bearer
    token_prefix    TEXT NOT NULL,         -- first 8 chars for UI display
    created_at      REAL NOT NULL,
    last_used       REAL,
    revoked_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_scim_tok_org ON org_scim_tokens(org_id);
"""

# Additive columns we add to existing tables — wrapped in try/except
# at migrate time since they might already exist.
ADDITIONS = [
    "ALTER TABLE users ADD COLUMN scim_external_id TEXT",
    "ALTER TABLE users ADD COLUMN deactivated_at REAL",
]


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    """Idempotent: SCIM table via CREATE IF NOT EXISTS; users columns
    via best-effort ALTER (silent skip when already present)."""
    with _conn() as conn:
        for stmt in ADDITIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():  # noqa: SIM102
                    # users table may not exist yet (Postgres-only auth
                    # path); ignore quietly.
                    if "no such table" not in str(e).lower():
                        raise


# ---------- token management ----------

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SCIMToken:
    id: str
    org_id: str
    name: str
    token_prefix: str
    created_at: float
    last_used: float | None
    revoked_at: float | None


def issue_token(*, org_id: str, name: str) -> tuple[SCIMToken, str]:
    """Returns (token_record, raw_bearer). The raw bearer is shown to
    the admin ONCE — we only store the hash. Standard secret-handling
    pattern (GitHub PATs, Stripe keys)."""
    raw = "padhai_scim_" + secrets.token_urlsafe(32)
    tid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_scim_tokens "
            "(id, org_id, name, token_hash, token_prefix, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (tid, org_id, name, _hash_token(raw), raw[:8], now),
        )
    return (
        SCIMToken(
            id=tid, org_id=org_id, name=name, token_prefix=raw[:8],
            created_at=now, last_used=None, revoked_at=None,
        ),
        raw,
    )


def revoke_token(*, token_id: str, org_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE org_scim_tokens SET revoked_at = ? "
            "WHERE id = ? AND org_id = ? AND revoked_at IS NULL",
            (time.time(), token_id, org_id),
        )
        return cur.rowcount > 0


def list_tokens(org_id: str) -> list[SCIMToken]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, org_id, name, token_prefix, created_at, "
            "       last_used, revoked_at "
            "FROM org_scim_tokens WHERE org_id = ? "
            "ORDER BY created_at DESC",
            (org_id,),
        ).fetchall()
    return [
        SCIMToken(
            id=r[0], org_id=r[1], name=r[2], token_prefix=r[3],
            created_at=r[4], last_used=r[5], revoked_at=r[6],
        )
        for r in rows
    ]


def authenticate(raw_bearer: str) -> str | None:
    """Validate an incoming bearer token. Returns the org_id if valid
    (and not revoked), else None. Bumps last_used on success.

    Constant-time wouldn't help here since the hash is itself
    deterministic and we look up by hash — the secrecy lives in the
    raw bearer length (32 bytes)."""
    if not raw_bearer:
        return None
    h = _hash_token(raw_bearer)
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id FROM org_scim_tokens "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (h,),
        ).fetchone()
        if not r:
            return None
        conn.execute(
            "UPDATE org_scim_tokens SET last_used = ? WHERE id = ?",
            (time.time(), r[0]),
        )
    return r[1]


# ---------- SCIM 2.0 resource shape ----------

def user_to_scim(user, *, member=None) -> dict:
    """Render a User row + optional org_member row as a SCIM 2.0 User
    resource. Schema follows RFC 7643 §4.1."""
    # `user` shape: id, email, name, scim_external_id, deactivated_at,
    # created_at. Member adds role, class_id.
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": user.id,
        "externalId": getattr(user, "scim_external_id", None),
        "userName": user.email,
        "name": {
            "formatted": getattr(user, "name", None) or user.email,
        },
        "emails": [{"value": user.email, "primary": True}],
        "active": getattr(user, "deactivated_at", None) is None,
        "roles": ([{"value": member.role}] if member and getattr(member, "role", None) else []),
        "meta": {
            "resourceType": "User",
            "created": _iso(getattr(user, "created_at", 0)),
            "lastModified": _iso(
                getattr(user, "updated_at", None) or getattr(user, "created_at", 0)
            ),
        },
    }


def _iso(ts: float | None) -> str | None:
    if not ts:
        return None
    import datetime as _dt
    return _dt.datetime.utcfromtimestamp(ts).isoformat(timespec="seconds") + "Z"


def parse_scim_user(payload: dict) -> dict:
    """Validate + normalize an incoming SCIM User. Returns a flat dict
    suitable for our internal user creation/update path."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    schemas = payload.get("schemas") or []
    if "urn:ietf:params:scim:schemas:core:2.0:User" not in schemas:
        raise ValueError(
            "payload must include 'urn:ietf:params:scim:schemas:core:2.0:User' "
            "in schemas"
        )
    user_name = (payload.get("userName") or "").strip()
    emails = payload.get("emails") or []
    primary = next(
        (e.get("value") for e in emails if e.get("primary")), None,
    ) or (emails[0].get("value") if emails else None)
    email = primary or user_name
    if not email or "@" not in email:
        raise ValueError("a primary email is required")
    name = payload.get("name") or {}
    return {
        "external_id": payload.get("externalId"),
        "email": email,
        "user_name": user_name or email,
        "display_name": name.get("formatted")
            or " ".join(filter(None, [name.get("givenName"), name.get("familyName")]))
            or None,
        "active": payload.get("active", True),
        "roles": [r.get("value") for r in (payload.get("roles") or []) if r.get("value")],
    }


# ---------- SCIM ListResponse + ErrorResponse helpers ----------

def list_response(*, resources: list, total: int, start: int = 1,
                  per_page: int = 100) -> dict:
    """SCIM 2.0 ListResponse shape (RFC 7644 §3.4.2)."""
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": start,
        "itemsPerPage": per_page,
        "Resources": resources,
    }


def error_response(*, status: int, detail: str, scim_type: str | None = None) -> dict:
    """SCIM 2.0 Error shape (RFC 7644 §3.12)."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status),
        "detail": detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return body
