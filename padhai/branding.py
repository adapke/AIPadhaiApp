"""White-label branding for institutional plans.

Schools/coaching on Enterprise tier upload their logo + pick brand
colors. The student-facing app then shows THEIR branding instead of
AI Pathshala's defaults:

  - Header logo + name
  - Primary brand color (CSS variable injection)
  - Optional custom subdomain (stpauls.aipathshala.in → routes to
    that org's branded view)

Schema is additive — five new columns on the existing `orgs` table:
  brand_name        display name (defaults to org.name)
  brand_logo_url    R2 URL or null
  brand_color       hex like '#5E60CE' (defaults to platform color)
  brand_accent      secondary hex (defaults to platform purple)
  brand_subdomain   url-safe slug; UNIQUE; null = no custom subdomain

Subdomain resolution is best-effort: incoming request's Host header
gets parsed; the leftmost label (when 3+ labels: 'stpauls.aipathshala.in')
is matched against orgs.brand_subdomain. Match → org's branding gets
injected into the SPA's CSS vars. Miss → platform defaults.

We do NOT do per-tenant runtime auth scoping in this module — that's
already handled by the org-membership tables. Branding is purely
visual.

To split admin/student tenants later (multi-app per subdomain), this
resolver is the seam.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


# ---------- additive schema migration ----------

ADDITIONS = [
    "ALTER TABLE orgs ADD COLUMN brand_name TEXT",
    "ALTER TABLE orgs ADD COLUMN brand_logo_url TEXT",
    "ALTER TABLE orgs ADD COLUMN brand_color TEXT",
    "ALTER TABLE orgs ADD COLUMN brand_accent TEXT",
    "ALTER TABLE orgs ADD COLUMN brand_subdomain TEXT",
]

# UNIQUE constraint via separate index (SQLite ALTER TABLE doesn't
# accept UNIQUE in a single statement reliably).
EXTRA_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_orgs_subdomain "
    "ON orgs(brand_subdomain) WHERE brand_subdomain IS NOT NULL"
)


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def migrate() -> None:
    """Idempotent. Tolerates duplicate-column errors (column already
    added on a prior run). The UNIQUE index is also `IF NOT EXISTS`."""
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        has_orgs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='orgs'"
        ).fetchone()
        if not has_orgs:
            return  # orgs module hasn't bootstrapped yet — try again later
        for stmt in ADDITIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        try:
            conn.execute(EXTRA_INDEX)
        except sqlite3.OperationalError:
            pass


# ---------- dataclass + sanitisers ----------

# Platform defaults — surfaced when an org has no branding override.
DEFAULT_BRANDING = {
    "brand_name": "AI Pathshala",
    "brand_color": "#5E60CE",
    "brand_accent": "#7C3AED",
    "brand_logo_url": None,
    "brand_subdomain": None,
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


def is_valid_color(c: str) -> bool:
    return bool(c and _HEX_RE.match(c))


def is_valid_subdomain(s: str) -> bool:
    """Subdomain must be 1-32 chars, [a-z0-9-], start+end with alnum.
    Conservative — avoids issues with DNS labels + reserved names."""
    if not s or len(s) > 32:
        return False
    if s in _RESERVED_SUBDOMAINS:
        return False
    return bool(_SUBDOMAIN_RE.match(s))


_RESERVED_SUBDOMAINS = frozenset({
    "www", "api", "admin", "app", "auth", "billing", "blog", "cdn",
    "console", "dashboard", "dev", "docs", "help", "mail", "marketing",
    "ops", "preview", "qa", "staging", "status", "support", "test",
})


@dataclass(frozen=True)
class Branding:
    org_id: str | None
    brand_name: str
    brand_color: str
    brand_accent: str
    brand_logo_url: str | None
    brand_subdomain: str | None


def platform_default() -> Branding:
    return Branding(
        org_id=None,
        brand_name=DEFAULT_BRANDING["brand_name"],
        brand_color=DEFAULT_BRANDING["brand_color"],
        brand_accent=DEFAULT_BRANDING["brand_accent"],
        brand_logo_url=None,
        brand_subdomain=None,
    )


# ---------- read / write ----------

def get_branding(org_id: str) -> Branding | None:
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT id, name, brand_name, brand_color, brand_accent, "
                "       brand_logo_url, brand_subdomain "
                "FROM orgs WHERE id = ?", (org_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        # orgs table not created yet (lazy bootstrap in orgs.py).
        return None
    if not r:
        return None
    return Branding(
        org_id=r[0],
        brand_name=r[2] or r[1] or DEFAULT_BRANDING["brand_name"],
        brand_color=r[3] or DEFAULT_BRANDING["brand_color"],
        brand_accent=r[4] or DEFAULT_BRANDING["brand_accent"],
        brand_logo_url=r[5],
        brand_subdomain=r[6],
    )


def update_branding(
    *,
    org_id: str,
    brand_name: str | None = None,
    brand_color: str | None = None,
    brand_accent: str | None = None,
    brand_logo_url: str | None = None,
    brand_subdomain: str | None = None,
) -> Branding:
    """Patch — only fields explicitly passed are updated. Validates
    color hex + subdomain format + subdomain uniqueness (via the
    UNIQUE index — IntegrityError surfaces as ValueError to the caller)."""
    if brand_color is not None and not is_valid_color(brand_color):
        raise ValueError("brand_color must be #RRGGBB hex")
    if brand_accent is not None and not is_valid_color(brand_accent):
        raise ValueError("brand_accent must be #RRGGBB hex")
    if brand_subdomain and not is_valid_subdomain(brand_subdomain):
        raise ValueError(
            "brand_subdomain must be 1-32 chars [a-z0-9-] and not "
            "a reserved name (api/admin/www/etc.)",
        )

    sets: list[str] = []
    params: list = []
    for name, val in [
        ("brand_name", brand_name),
        ("brand_color", brand_color),
        ("brand_accent", brand_accent),
        ("brand_logo_url", brand_logo_url),
        ("brand_subdomain", brand_subdomain),
    ]:
        if val is not None:
            sets.append(f"{name} = ?")
            params.append(val)
    if not sets:
        existing = get_branding(org_id)
        if not existing:
            raise ValueError("org not found")
        return existing
    params.append(org_id)
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            conn.execute(
                f"UPDATE orgs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
    except sqlite3.IntegrityError as e:
        if "subdomain" in str(e).lower():
            raise ValueError(
                f"subdomain {brand_subdomain!r} is already taken by "
                "another organisation",
            )
        raise
    return get_branding(org_id)


def resolve_by_subdomain(host: str) -> Branding | None:
    """Parse the Host header → leftmost subdomain label → branding row.

    Examples:
      stpauls.aipathshala.in → look up brand_subdomain='stpauls'
      aipathshala.in          → no subdomain → None
      localhost:8000          → no subdomain → None

    The router middleware uses this to inject the right CSS variables
    into the HTML response before serving."""
    if not host:
        return None
    # Strip port
    host = host.split(":")[0].strip().lower()
    parts = host.split(".")
    # Require at least 3 labels to consider it a subdomain (a.b.c).
    # 'aipathshala.in' is 2 labels — not a subdomain.
    if len(parts) < 3:
        return None
    sub = parts[0]
    if sub in _RESERVED_SUBDOMAINS:
        return None
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT id FROM orgs WHERE brand_subdomain = ?",
                (sub,),
            ).fetchone()
    except sqlite3.OperationalError:
        # orgs table not bootstrapped yet — no org can claim a subdomain.
        return None
    return get_branding(r[0]) if r else None
