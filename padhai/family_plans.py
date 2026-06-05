"""N2 — Family plans + sibling discount.

One parent account ↔ multiple children. Bulk pricing — 2 kids = 30%
off the second; 3+ = 40% off the additional. Shared payment +
reporting.

Three tables:
  family_groups          one row per family
  family_members         (group_id, user_id, role: parent | child)
  family_subscriptions   tier + cycle + discount applied, status

Discount tiers (configurable; defaults below):
  1 child   → 0% off
  2 children → 30% off the 2nd's price
  3+ children → 40% off each additional past the 1st

Integrates with E8 parent_links (shipped v0.14) where applicable —
this module adds the **subscription bundling** layer on top of the
existing parent-child relationship.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS family_groups (
    id              TEXT PRIMARY KEY,
    name            TEXT,
    primary_parent_user_id TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_family_parent
    ON family_groups(primary_parent_user_id);

CREATE TABLE IF NOT EXISTS family_members (
    group_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    role            TEXT NOT NULL,           -- 'parent' | 'child'
    relation        TEXT,                     -- 'father' | 'mother' | 'guardian' | 'son' | 'daughter'
    joined_at       REAL NOT NULL,
    PRIMARY KEY (group_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_fm_user
    ON family_members(user_id);

CREATE TABLE IF NOT EXISTS family_subscriptions (
    id              TEXT PRIMARY KEY,
    group_id        TEXT NOT NULL,
    tier            TEXT NOT NULL,           -- 'M2' | 'M3' | 'M4'
    billing_cycle   TEXT NOT NULL,           -- 'monthly' | 'annual'
    base_price_paise INTEGER NOT NULL,        -- per-child price before discount
    child_count     INTEGER NOT NULL,
    discount_pct    REAL NOT NULL,
    total_paise     INTEGER NOT NULL,
    started_at      REAL NOT NULL,
    next_renewal_at REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',  -- active | cancelled | expired
    cancelled_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_fs_group
    ON family_subscriptions(group_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_fs_status
    ON family_subscriptions(status, next_renewal_at);
"""


VALID_ROLES = frozenset({"parent", "child"})
VALID_TIERS = frozenset({"M2", "M3", "M4"})
VALID_CYCLES = frozenset({"monthly", "annual"})

# Base prices in paise (₹/100) per tier + cycle. Source of truth for
# the quote engine; admin updates these via env or a future
# /api/admin/pricing endpoint.
DEFAULT_PRICES_PAISE = {
    ("M2", "monthly"): 4900,         # ₹49/mo
    ("M2", "annual"): 49000,         # ₹490/yr (2 months free)
    ("M3", "monthly"): 19900,        # ₹199/mo
    ("M3", "annual"): 199000,        # ₹1990/yr
    ("M4", "monthly"): 99900,        # ₹999/mo (enterprise / families)
    ("M4", "annual"): 999000,
}


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
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class FamilyGroup:
    id: str
    name: str | None
    primary_parent_user_id: str
    created_at: float


@dataclass(frozen=True)
class FamilyMember:
    group_id: str
    user_id: str
    role: str
    relation: str | None
    joined_at: float


@dataclass(frozen=True)
class FamilySubscription:
    id: str
    group_id: str
    tier: str
    billing_cycle: str
    base_price_paise: int
    child_count: int
    discount_pct: float
    total_paise: int
    started_at: float
    next_renewal_at: float
    status: str


# ---------- groups + members ----------

def create_family(
    *,
    primary_parent_user_id: str,
    name: str | None = None,
) -> FamilyGroup:
    gid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO family_groups "
            "(id, name, primary_parent_user_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (gid, (name or None), primary_parent_user_id, now),
        )
        # Auto-add the primary parent as a member
        conn.execute(
            "INSERT INTO family_members "
            "(group_id, user_id, role, relation, joined_at) "
            "VALUES (?, ?, 'parent', NULL, ?)",
            (gid, primary_parent_user_id, now),
        )
    return get_family(gid)  # type: ignore[return-value]


def get_family(group_id: str) -> FamilyGroup | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, name, primary_parent_user_id, created_at "
            "FROM family_groups WHERE id = ?", (group_id,),
        ).fetchone()
    if not r:
        return None
    return FamilyGroup(
        id=r[0], name=r[1],
        primary_parent_user_id=r[2], created_at=r[3],
    )


def add_member(
    *,
    group_id: str,
    user_id: str,
    role: str,
    relation: str | None = None,
) -> FamilyMember:
    if role not in VALID_ROLES:
        raise ValueError(f"role must be in {sorted(VALID_ROLES)}")
    if not get_family(group_id):
        raise ValueError(f"family {group_id!r} not found")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO family_members "
                "(group_id, user_id, role, relation, joined_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (group_id, user_id, role, relation, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"user {user_id!r} already in family {group_id!r}",
        ) from e
    return _get_member(group_id, user_id)  # type: ignore[return-value]


def _get_member(group_id: str, user_id: str) -> FamilyMember | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT group_id, user_id, role, relation, joined_at "
            "FROM family_members "
            "WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
    if not r:
        return None
    return FamilyMember(
        group_id=r[0], user_id=r[1], role=r[2],
        relation=r[3], joined_at=r[4],
    )


def remove_member(*, group_id: str, user_id: str) -> bool:
    f = get_family(group_id)
    if f and f.primary_parent_user_id == user_id:
        raise ValueError("cannot remove primary parent; delete the family first")
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM family_members "
            "WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        )
        return cur.rowcount > 0


def list_members(group_id: str) -> list[FamilyMember]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT group_id, user_id, role, relation, joined_at "
            "FROM family_members WHERE group_id = ? "
            "ORDER BY role, joined_at",
            (group_id,),
        ).fetchall()
    return [
        FamilyMember(
            group_id=r[0], user_id=r[1], role=r[2],
            relation=r[3], joined_at=r[4],
        )
        for r in rows
    ]


def families_for_user(user_id: str) -> list[FamilyGroup]:
    """Every family the user is in (typically 1; supports
    blended-family edge case)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT fg.id, fg.name, fg.primary_parent_user_id, fg.created_at "
            "FROM family_groups fg "
            "JOIN family_members fm ON fm.group_id = fg.id "
            "WHERE fm.user_id = ? ORDER BY fg.created_at",
            (user_id,),
        ).fetchall()
    return [
        FamilyGroup(
            id=r[0], name=r[1],
            primary_parent_user_id=r[2], created_at=r[3],
        )
        for r in rows
    ]


def count_children(group_id: str) -> int:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM family_members "
            "WHERE group_id = ? AND role = 'child'",
            (group_id,),
        ).fetchone()
    return int(r[0] or 0)


# ---------- pricing quote ----------

@dataclass(frozen=True)
class Quote:
    tier: str
    billing_cycle: str
    base_price_paise: int          # per-child
    child_count: int
    discount_pct: float
    total_paise: int
    savings_paise: int
    per_child_breakdown: list[dict]


def quote(
    *,
    tier: str,
    billing_cycle: str,
    child_count: int,
) -> Quote:
    """Compute price for a family with N children. First child at
    full rate; subsequent children get a per-extra-child discount
    that scales with family size."""
    if tier not in VALID_TIERS:
        raise ValueError(f"tier must be in {sorted(VALID_TIERS)}")
    if billing_cycle not in VALID_CYCLES:
        raise ValueError(f"billing_cycle must be in {sorted(VALID_CYCLES)}")
    if child_count < 1 or child_count > 10:
        raise ValueError("child_count must be in [1, 10]")
    base = DEFAULT_PRICES_PAISE.get((tier, billing_cycle), 0)
    if base == 0:
        raise ValueError(
            f"no price configured for ({tier!r}, {billing_cycle!r})",
        )
    # Per-extra-child discount: 0% / 30% / 40%+
    breakdown = []
    total = 0
    for i in range(child_count):
        if i == 0:
            child_discount = 0.0
        elif i == 1:
            child_discount = 0.30
        else:
            child_discount = 0.40
        child_price = round(base * (1 - child_discount))
        breakdown.append({
            "child_index": i + 1,
            "discount_pct": child_discount,
            "price_paise": child_price,
        })
        total += child_price
    full = base * child_count
    discount_pct = (full - total) / full if full else 0.0
    return Quote(
        tier=tier, billing_cycle=billing_cycle,
        base_price_paise=base, child_count=child_count,
        discount_pct=round(discount_pct, 4),
        total_paise=total,
        savings_paise=full - total,
        per_child_breakdown=breakdown,
    )


# ---------- subscriptions ----------

def start_subscription(
    *,
    group_id: str,
    tier: str,
    billing_cycle: str,
) -> FamilySubscription:
    children = count_children(group_id)
    if children == 0:
        raise ValueError("family has no children — add at least one child")
    q = quote(
        tier=tier, billing_cycle=billing_cycle,
        child_count=children,
    )
    sid = uuid.uuid4().hex
    now = time.time()
    renewal_seconds = 30 * 86400 if billing_cycle == "monthly" else 365 * 86400
    next_renewal = now + renewal_seconds
    with _conn() as conn:
        # Cancel any active sub for this group first
        conn.execute(
            "UPDATE family_subscriptions SET status = 'expired' "
            "WHERE group_id = ? AND status = 'active'",
            (group_id,),
        )
        conn.execute(
            "INSERT INTO family_subscriptions "
            "(id, group_id, tier, billing_cycle, base_price_paise, "
            " child_count, discount_pct, total_paise, started_at, "
            " next_renewal_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
            (sid, group_id, tier, billing_cycle, q.base_price_paise,
             q.child_count, q.discount_pct, q.total_paise,
             now, next_renewal),
        )
    return get_subscription(sid)  # type: ignore[return-value]


def get_subscription(sub_id: str) -> FamilySubscription | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, group_id, tier, billing_cycle, base_price_paise, "
            "       child_count, discount_pct, total_paise, "
            "       started_at, next_renewal_at, status "
            "FROM family_subscriptions WHERE id = ?", (sub_id,),
        ).fetchone()
    if not r:
        return None
    return FamilySubscription(
        id=r[0], group_id=r[1], tier=r[2], billing_cycle=r[3],
        base_price_paise=r[4], child_count=r[5],
        discount_pct=r[6], total_paise=r[7],
        started_at=r[8], next_renewal_at=r[9], status=r[10],
    )


def active_for_group(group_id: str) -> FamilySubscription | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, group_id, tier, billing_cycle, base_price_paise, "
            "       child_count, discount_pct, total_paise, "
            "       started_at, next_renewal_at, status "
            "FROM family_subscriptions "
            "WHERE group_id = ? AND status = 'active' "
            "ORDER BY started_at DESC LIMIT 1",
            (group_id,),
        ).fetchone()
    if not r:
        return None
    return FamilySubscription(
        id=r[0], group_id=r[1], tier=r[2], billing_cycle=r[3],
        base_price_paise=r[4], child_count=r[5],
        discount_pct=r[6], total_paise=r[7],
        started_at=r[8], next_renewal_at=r[9], status=r[10],
    )


def cancel_subscription(*, sub_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE family_subscriptions SET "
            " status = 'cancelled', cancelled_at = ? "
            "WHERE id = ? AND status = 'active'",
            (time.time(), sub_id),
        )
        return cur.rowcount > 0
