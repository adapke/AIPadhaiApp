"""Q1 — Feature flags + A/B testing framework.

Server-side flags. Every new feature ships behind a flag, gradually
rolled out, A/B tested for engagement / retention / cost impact.

Two query paths:
  is_enabled(key, user_id=...)        → bool, deterministic
  variant_for(key, user_id=...)       → 'control' | 'A' | 'B' | ...

Targeting (in order, first match wins):
  1. user_ids list — explicit allow
  2. org_ids list  — explicit allow
  3. role list     — role-based (admin / teacher / student)
  4. rollout_pct   — deterministic hash bucket (stable for a user)
  5. enabled_default

Self-built — we don't need LaunchDarkly's $/seat pricing at our
scale. Swap-ready for an external provider via the same interface.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_flags (
    flag_key         TEXT PRIMARY KEY,
    description      TEXT,
    enabled_default  INTEGER NOT NULL DEFAULT 0,
    rollout_pct      INTEGER NOT NULL DEFAULT 0,
    target_user_ids  TEXT,                       -- JSON array
    target_org_ids   TEXT,                       -- JSON array
    target_roles     TEXT,                       -- JSON array: 'admin','teacher','student'
    variants_json    TEXT,                       -- JSON: {variant_name: weight}
    updated_at       REAL NOT NULL,
    created_at       REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_flag_events (
    id          TEXT PRIMARY KEY,
    flag_key    TEXT NOT NULL,
    user_id     TEXT,
    variant     TEXT,
    enabled     INTEGER NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ffe_flag_time
    ON feature_flag_events(flag_key, created_at DESC);
"""


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


@dataclass(frozen=True)
class Flag:
    flag_key: str
    description: str | None
    enabled_default: bool
    rollout_pct: int
    target_user_ids: list[str] = field(default_factory=list)
    target_org_ids: list[str] = field(default_factory=list)
    target_roles: list[str] = field(default_factory=list)
    variants: dict[str, int] = field(default_factory=dict)
    updated_at: float = 0.0
    created_at: float = 0.0


def _row_to_flag(r) -> Flag:
    return Flag(
        flag_key=r[0],
        description=r[1],
        enabled_default=bool(r[2]),
        rollout_pct=int(r[3] or 0),
        target_user_ids=json.loads(r[4]) if r[4] else [],
        target_org_ids=json.loads(r[5]) if r[5] else [],
        target_roles=json.loads(r[6]) if r[6] else [],
        variants=json.loads(r[7]) if r[7] else {},
        updated_at=r[8],
        created_at=r[9],
    )


_SEL = (
    "flag_key, description, enabled_default, rollout_pct, "
    "target_user_ids, target_org_ids, target_roles, variants_json, "
    "updated_at, created_at"
)


def upsert(
    *,
    flag_key: str,
    description: str | None = None,
    enabled_default: bool = False,
    rollout_pct: int = 0,
    target_user_ids: list[str] | None = None,
    target_org_ids: list[str] | None = None,
    target_roles: list[str] | None = None,
    variants: dict[str, int] | None = None,
) -> Flag:
    """Insert or update one flag. Idempotent on flag_key."""
    if not flag_key or not flag_key.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise ValueError("flag_key must be [a-zA-Z0-9._-]+")
    rollout_pct = max(0, min(100, int(rollout_pct)))
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT created_at FROM feature_flags WHERE flag_key = ?",
            (flag_key,),
        ).fetchone()
        created_at = existing[0] if existing else now
        conn.execute(
            "INSERT INTO feature_flags "
            "(flag_key, description, enabled_default, rollout_pct, "
            " target_user_ids, target_org_ids, target_roles, "
            " variants_json, updated_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(flag_key) DO UPDATE SET "
            " description = excluded.description, "
            " enabled_default = excluded.enabled_default, "
            " rollout_pct = excluded.rollout_pct, "
            " target_user_ids = excluded.target_user_ids, "
            " target_org_ids = excluded.target_org_ids, "
            " target_roles = excluded.target_roles, "
            " variants_json = excluded.variants_json, "
            " updated_at = excluded.updated_at",
            (
                flag_key, description, 1 if enabled_default else 0,
                rollout_pct,
                json.dumps(target_user_ids) if target_user_ids else None,
                json.dumps(target_org_ids) if target_org_ids else None,
                json.dumps(target_roles) if target_roles else None,
                json.dumps(variants) if variants else None,
                now, created_at,
            ),
        )
    return get(flag_key)  # type: ignore[return-value]


def get(flag_key: str) -> Flag | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM feature_flags WHERE flag_key = ?",
            (flag_key,),
        ).fetchone()
    return _row_to_flag(r) if r else None


def list_all() -> list[Flag]:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM feature_flags ORDER BY flag_key",
        ).fetchall()
    return [_row_to_flag(r) for r in rows]


def delete(flag_key: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM feature_flags WHERE flag_key = ?", (flag_key,),
        )
        return cur.rowcount > 0


# ---------- evaluation ----------

def _bucket(flag_key: str, user_id: str) -> int:
    """Deterministic [0,99] bucket from (flag_key, user_id). Stable
    across requests so the same user is always on the same side of
    the rollout boundary — critical for A/B test integrity."""
    h = hashlib.sha256(f"{flag_key}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % 100


def is_enabled(
    flag_key: str,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    role: str | None = None,
) -> bool:
    """Returns True if the flag is on for this caller.

    Order: explicit user_id match → org_id match → role match →
    rollout_pct bucket → enabled_default."""
    f = get(flag_key)
    if f is None:
        return False
    if user_id and user_id in f.target_user_ids:
        return True
    if org_id and org_id in f.target_org_ids:
        return True
    if role and role in f.target_roles:
        return True
    if f.rollout_pct > 0 and user_id:
        return _bucket(flag_key, user_id) < f.rollout_pct
    return f.enabled_default


def variant_for(
    flag_key: str,
    *,
    user_id: str | None = None,
) -> str | None:
    """A/B/n variant selection. Returns one of the keys in
    `variants` map, weighted by the int weight. Stable per user.
    Returns None when the flag isn't variant-style."""
    f = get(flag_key)
    if f is None or not f.variants or not user_id:
        return None
    total = sum(int(w) for w in f.variants.values())
    if total <= 0:
        return None
    bucket = _bucket(flag_key + ":variant", user_id) % total
    cum = 0
    for name, weight in f.variants.items():
        cum += int(weight)
        if bucket < cum:
            return name
    # Fallback (rounding) — return last
    return list(f.variants.keys())[-1]


def resolve_all(
    *,
    user_id: str | None = None,
    org_id: str | None = None,
    role: str | None = None,
) -> dict[str, dict]:
    """Snapshot of every flag's resolved state for the caller. Used by
    the SPA's /api/me/flags endpoint to seed the client-side flag cache."""
    out: dict[str, dict] = {}
    for f in list_all():
        out[f.flag_key] = {
            "enabled": is_enabled(
                f.flag_key, user_id=user_id, org_id=org_id, role=role,
            ),
            "variant": variant_for(f.flag_key, user_id=user_id),
        }
    return out


# ---------- exposure event logging (A/B test analysis input) ----------

def log_exposure(
    *,
    flag_key: str,
    user_id: str | None,
    enabled: bool,
    variant: str | None = None,
) -> None:
    """Best-effort log of a flag evaluation. Used to compute
    treatment vs. control cohorts during A/B analysis. Sampling cap
    (10% of events) keeps the table from exploding at scale."""
    import random
    import uuid as _uuid
    if random.random() > 0.10:
        return
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO feature_flag_events "
                "(id, flag_key, user_id, variant, enabled, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (_uuid.uuid4().hex, flag_key, user_id, variant,
                 1 if enabled else 0, time.time()),
            )
    except Exception:  # noqa: BLE001
        pass


def exposure_stats(flag_key: str, *, hours: float = 168.0) -> dict:
    """Aggregate exposures over the last N hours. Per-variant counts
    drive the A/B test cohort split confirmation."""
    since = time.time() - hours * 3600
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM feature_flag_events "
            "WHERE flag_key = ? AND created_at >= ?",
            (flag_key, since),
        ).fetchone()[0]
        enabled = conn.execute(
            "SELECT COUNT(*) FROM feature_flag_events "
            "WHERE flag_key = ? AND created_at >= ? AND enabled = 1",
            (flag_key, since),
        ).fetchone()[0]
        variants = conn.execute(
            "SELECT variant, COUNT(*) FROM feature_flag_events "
            "WHERE flag_key = ? AND created_at >= ? "
            "AND variant IS NOT NULL "
            "GROUP BY variant",
            (flag_key, since),
        ).fetchall()
    return {
        "flag_key": flag_key,
        "hours": hours,
        "total": int(total),
        "enabled": int(enabled),
        "enabled_pct": (enabled / total) if total else 0,
        "variants": {r[0]: r[1] for r in variants},
        "sample_rate": 0.10,
    }
