"""R3 — Vouchers + bundles.

Two related concepts:
  Voucher: a coupon code the buyer enters at checkout. Discount can
    be `percent` (e.g. 20% off), `fixed` (e.g. ₹500 off), or
    `bundle` (auto-priced — see Bundle below). Vouchers can scope
    to specific SKUs (course/pack/sub) or be global. Optional cap
    on max_uses + expires_at.
  Bundle: a pre-defined SKU combination that auto-discounts when
    all SKUs are in cart. e.g. "Class 10 Math + Science course" =
    25% off if both are in the same cart.

Distinguishes from R1 (geo + PPP pricing) and R2 (income-aware
scholarships) — R3 is **promotional**, not means-tested. Marketing
runs vouchers; finance owns bundles.

Tables:
  vouchers              one row per coupon code (code is PK)
  voucher_redemptions   one row per (code, user_id) — idempotent
  bundles               named bundle definitions w/ SKU list
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS vouchers (
    code            TEXT PRIMARY KEY,            -- 'WELCOME20' (uppercase)
    kind            TEXT NOT NULL,               -- 'percent' | 'fixed' | 'bundle'
    value           INTEGER NOT NULL,            -- percent: 0-100; fixed: paise
    applies_to      TEXT,                        -- JSON array of sku patterns, NULL = any
    max_uses        INTEGER,                     -- NULL = unlimited
    max_uses_per_user INTEGER NOT NULL DEFAULT 1,
    redeemed_count  INTEGER NOT NULL DEFAULT 0,
    starts_at       REAL,                        -- NULL = active now
    expires_at      REAL,                        -- NULL = no expiry
    min_order_paise INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'paused' | 'expired'
    description     TEXT,
    created_by_user_id TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vouchers_status
    ON vouchers(status, expires_at);

CREATE TABLE IF NOT EXISTS voucher_redemptions (
    id              TEXT PRIMARY KEY,
    voucher_code    TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    applied_to_sku  TEXT,
    order_paise     INTEGER NOT NULL,
    discount_paise  INTEGER NOT NULL,
    redeemed_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_code
    ON voucher_redemptions(voucher_code, redeemed_at DESC);
CREATE INDEX IF NOT EXISTS idx_vr_user
    ON voucher_redemptions(user_id, voucher_code);

CREATE TABLE IF NOT EXISTS bundles (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    sku_codes            TEXT NOT NULL,          -- JSON array of required SKUs
    bundle_discount_pct  INTEGER NOT NULL,       -- 0-100
    status               TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'paused'
    description          TEXT,
    created_at           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bundles_status
    ON bundles(status);
"""


VALID_VOUCHER_KINDS = frozenset({"percent", "fixed", "bundle"})
VALID_VOUCHER_STATUSES = frozenset({"active", "paused", "expired"})
VALID_BUNDLE_STATUSES = frozenset({"active", "paused"})


class VoucherError(Exception):
    """Raised when a voucher can't be applied. Message is user-facing."""


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
class Voucher:
    code: str
    kind: str
    value: int
    applies_to: list[str]                    # empty = any
    max_uses: int | None
    max_uses_per_user: int
    redeemed_count: int
    starts_at: float | None
    expires_at: float | None
    min_order_paise: int
    status: str
    description: str | None
    created_by_user_id: str | None
    created_at: float


@dataclass(frozen=True)
class Bundle:
    id: str
    title: str
    sku_codes: list[str]
    bundle_discount_pct: int
    status: str
    description: str | None
    created_at: float


@dataclass(frozen=True)
class DiscountResult:
    discount_paise: int
    final_paise: int
    voucher_code: str | None = None
    bundle_id: str | None = None
    reason: str = ""


_V_SEL = (
    "SELECT code, kind, value, applies_to, max_uses, "
    "       max_uses_per_user, redeemed_count, starts_at, "
    "       expires_at, min_order_paise, status, description, "
    "       created_by_user_id, created_at "
    "FROM vouchers"
)


def _row_to_voucher(r) -> Voucher:
    return Voucher(
        code=r[0], kind=r[1], value=r[2],
        applies_to=json.loads(r[3]) if r[3] else [],
        max_uses=r[4], max_uses_per_user=r[5],
        redeemed_count=r[6], starts_at=r[7], expires_at=r[8],
        min_order_paise=r[9], status=r[10], description=r[11],
        created_by_user_id=r[12], created_at=r[13],
    )


def _row_to_bundle(r) -> Bundle:
    return Bundle(
        id=r[0], title=r[1],
        sku_codes=json.loads(r[2]),
        bundle_discount_pct=r[3], status=r[4],
        description=r[5], created_at=r[6],
    )


# ---------- voucher admin ----------

def create_voucher(
    *,
    code: str,
    kind: str,
    value: int,
    applies_to: list[str] | None = None,
    max_uses: int | None = None,
    max_uses_per_user: int = 1,
    starts_at: float | None = None,
    expires_at: float | None = None,
    min_order_paise: int = 0,
    description: str | None = None,
    created_by_user_id: str | None = None,
) -> Voucher:
    code = (code or "").strip().upper()
    if len(code) < 3 or len(code) > 32 or not code.replace("_", "").isalnum():
        raise ValueError("code must be 3-32 alphanumeric/underscore chars")
    if kind not in VALID_VOUCHER_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_VOUCHER_KINDS)}")
    if kind == "percent" and (value < 1 or value > 100):
        raise ValueError("percent value must be in [1, 100]")
    if kind == "fixed" and value < 1:
        raise ValueError("fixed value (paise) must be ≥1")
    if kind == "bundle" and value < 1:
        # For bundle vouchers the `value` is bundle_discount_pct.
        raise ValueError("bundle value (pct) must be ≥1")
    if max_uses is not None and max_uses < 1:
        raise ValueError("max_uses must be ≥1")
    if max_uses_per_user < 1:
        raise ValueError("max_uses_per_user must be ≥1")
    if starts_at and expires_at and expires_at <= starts_at:
        raise ValueError("expires_at must be > starts_at")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO vouchers "
                "(code, kind, value, applies_to, max_uses, "
                " max_uses_per_user, starts_at, expires_at, "
                " min_order_paise, description, "
                " created_by_user_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, kind, value,
                 json.dumps(applies_to) if applies_to else None,
                 max_uses, max_uses_per_user, starts_at, expires_at,
                 min_order_paise, description,
                 created_by_user_id, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"voucher code {code!r} already exists") from e
    return get_voucher(code)  # type: ignore[return-value]


def get_voucher(code: str) -> Voucher | None:
    with _conn() as conn:
        r = conn.execute(
            _V_SEL + " WHERE code = ?", (code.upper(),),
        ).fetchone()
    return _row_to_voucher(r) if r else None


def list_vouchers(*, status: str | None = None) -> list[Voucher]:
    sql = _V_SEL
    params: list = []
    if status:
        if status not in VALID_VOUCHER_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_VOUCHER_STATUSES)}",
            )
        sql += " WHERE status = ?"; params.append(status)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_voucher(r) for r in rows]


def set_voucher_status(*, code: str, status: str) -> bool:
    if status not in VALID_VOUCHER_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_VOUCHER_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE vouchers SET status = ? WHERE code = ?",
            (status, code.upper()),
        )
        return cur.rowcount > 0


# ---------- redemption logic ----------

def _user_redemption_count(code: str, user_id: str) -> int:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM voucher_redemptions "
            "WHERE voucher_code = ? AND user_id = ?",
            (code, user_id),
        ).fetchone()
    return int(r[0] or 0)


def _sku_matches(sku: str | None, patterns: list[str]) -> bool:
    """Patterns can be exact SKU codes or prefix wildcards
    ('course_*'). Empty patterns list = matches anything."""
    if not patterns:
        return True
    if not sku:
        return False
    for p in patterns:
        if p == sku:
            return True
        if p.endswith("*") and sku.startswith(p[:-1]):
            return True
    return False


def validate_voucher(
    *,
    code: str,
    user_id: str,
    order_paise: int,
    sku: str | None = None,
) -> DiscountResult:
    """Compute the discount this voucher would apply, without
    redeeming it. Raises VoucherError with a user-facing message
    on any disqualifying condition."""
    code = (code or "").strip().upper()
    v = get_voucher(code)
    if not v:
        raise VoucherError("voucher not found")
    if v.status != "active":
        raise VoucherError(f"voucher is {v.status}")
    now = time.time()
    if v.starts_at and now < v.starts_at:
        raise VoucherError("voucher not yet active")
    if v.expires_at and now > v.expires_at:
        raise VoucherError("voucher expired")
    if v.max_uses is not None and v.redeemed_count >= v.max_uses:
        raise VoucherError("voucher fully redeemed")
    used = _user_redemption_count(code, user_id)
    if used >= v.max_uses_per_user:
        raise VoucherError("you have already used this voucher")
    if order_paise < v.min_order_paise:
        raise VoucherError(
            f"order must be ≥ ₹{v.min_order_paise // 100}",
        )
    if not _sku_matches(sku, v.applies_to):
        raise VoucherError("voucher not applicable to this item")
    # Discount math
    if v.kind == "percent":
        discount = int(round(order_paise * (v.value / 100)))
    elif v.kind == "fixed":
        discount = min(v.value, order_paise)
    else:  # 'bundle' kind treated as percent
        discount = int(round(order_paise * (v.value / 100)))
    discount = max(0, min(discount, order_paise))
    return DiscountResult(
        discount_paise=discount,
        final_paise=order_paise - discount,
        voucher_code=code,
        reason=v.description or "",
    )


def redeem_voucher(
    *,
    code: str,
    user_id: str,
    order_paise: int,
    sku: str | None = None,
) -> DiscountResult:
    """Validate + record the redemption atomically. Caller is
    responsible for charging the discounted final_paise."""
    result = validate_voucher(
        code=code, user_id=user_id,
        order_paise=order_paise, sku=sku,
    )
    rid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO voucher_redemptions "
            "(id, voucher_code, user_id, applied_to_sku, "
            " order_paise, discount_paise, redeemed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, result.voucher_code, user_id, sku,
             order_paise, result.discount_paise, time.time()),
        )
        conn.execute(
            "UPDATE vouchers SET redeemed_count = redeemed_count + 1 "
            "WHERE code = ?",
            (result.voucher_code,),
        )
    return result


def list_user_redemptions(user_id: str, *, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, voucher_code, applied_to_sku, "
            "       order_paise, discount_paise, redeemed_at "
            "FROM voucher_redemptions WHERE user_id = ? "
            "ORDER BY redeemed_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "voucher_code": r[1], "applied_to_sku": r[2],
         "order_paise": r[3], "discount_paise": r[4],
         "redeemed_at": r[5]}
        for r in rows
    ]


# ---------- bundles ----------

def create_bundle(
    *,
    title: str,
    sku_codes: list[str],
    bundle_discount_pct: int,
    description: str | None = None,
) -> Bundle:
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if not sku_codes or len(sku_codes) < 2:
        raise ValueError("bundle must include ≥2 SKUs")
    if len(sku_codes) > 20:
        raise ValueError("bundle can include ≤20 SKUs")
    if bundle_discount_pct < 1 or bundle_discount_pct > 80:
        raise ValueError("bundle_discount_pct must be in [1, 80]")
    bid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO bundles "
            "(id, title, sku_codes, bundle_discount_pct, "
            " description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (bid, title, json.dumps(sorted(sku_codes)),
             bundle_discount_pct, description, time.time()),
        )
    return get_bundle(bid)  # type: ignore[return-value]


def get_bundle(bundle_id: str) -> Bundle | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, title, sku_codes, bundle_discount_pct, "
            "       status, description, created_at "
            "FROM bundles WHERE id = ?", (bundle_id,),
        ).fetchone()
    return _row_to_bundle(r) if r else None


def list_bundles(*, active_only: bool = True) -> list[Bundle]:
    sql = (
        "SELECT id, title, sku_codes, bundle_discount_pct, "
        "       status, description, created_at "
        "FROM bundles"
    )
    if active_only:
        sql += " WHERE status = 'active'"
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_bundle(r) for r in rows]


def find_matching_bundle(skus: list[str]) -> Bundle | None:
    """Returns the highest-discount bundle whose required SKUs are
    all present in the cart. None if no bundle qualifies."""
    if not skus:
        return None
    have = set(skus)
    best: Bundle | None = None
    for b in list_bundles(active_only=True):
        required = set(b.sku_codes)
        if required.issubset(have):
            if not best or b.bundle_discount_pct > best.bundle_discount_pct:
                best = b
    return best


def apply_bundle(
    *,
    skus: list[str],
    total_paise: int,
) -> DiscountResult:
    """Auto-detect the best bundle for the SKUs in cart and apply
    its discount to the total. Returns a no-op DiscountResult if no
    bundle qualifies."""
    if total_paise < 1:
        raise ValueError("total_paise must be ≥1")
    b = find_matching_bundle(skus)
    if not b:
        return DiscountResult(
            discount_paise=0, final_paise=total_paise,
            reason="no matching bundle",
        )
    discount = int(round(total_paise * (b.bundle_discount_pct / 100)))
    discount = max(0, min(discount, total_paise))
    return DiscountResult(
        discount_paise=discount,
        final_paise=total_paise - discount,
        bundle_id=b.id,
        reason=f"bundle: {b.title}",
    )


def set_bundle_status(*, bundle_id: str, status: str) -> bool:
    if status not in VALID_BUNDLE_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_BUNDLE_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE bundles SET status = ? WHERE id = ?",
            (status, bundle_id),
        )
        return cur.rowcount > 0
