"""H4 — Data residency flag.

Per-org choice of where data lives. Required by DPDP §16 (cross-border
data flows) + government RFPs. When `data_residency='india'`:
- R2/S3 writes route to AP-South-1
- Postgres queries use the Mumbai instance (Neon AP-South-1)
- Logs / analytics scrub PII from cross-border transit
- Backups stay India-only

This module is the policy layer — `should_route_to_india(org_id)` is
the single source of truth that storage / DB / observability layers
query. The actual routing (different R2 endpoints, different DB
connection pools) is wired in the cutover sprint when we have a
second region's infrastructure provisioned (per G4 in ROADMAP_V2.md).

Schema is one additive column on orgs:

  ALTER TABLE orgs ADD COLUMN data_residency TEXT DEFAULT 'global'

Values: 'global' | 'india' | 'eu' (future).

Once an org is set to 'india', it can't be changed back without a
data migration (the data has been written into India-only storage;
flipping the flag without moving the data would leave it inaccessible
from the new region). Hence the super-admin lock on the API.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


ADDITIONS = [
    "ALTER TABLE orgs ADD COLUMN data_residency TEXT DEFAULT 'global'",
]


VALID_REGIONS = frozenset({"global", "india", "eu"})


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def migrate() -> None:
    """Idempotent. orgs table might not exist yet (lazy bootstrap)."""
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            has_orgs = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orgs'"
            ).fetchone()
            if not has_orgs:
                return
            for stmt in ADDITIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
    except sqlite3.OperationalError:
        # DB itself not writable yet; caller (web.py lifespan) will retry.
        return


# ---------- read / write ----------

def get_residency(org_id: str) -> str:
    """Returns the org's data_residency value, defaulting to 'global'
    if the column is missing or the row not found."""
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT data_residency FROM orgs WHERE id = ?",
                (org_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return "global"
    if not r:
        return "global"
    return (r[0] or "global").lower()


def set_residency(*, org_id: str, region: str) -> str:
    """Update the residency flag. Caller (web.py) gates this on
    super-admin role + a one-way warning. Raises ValueError on invalid
    region or when trying to downgrade ('india' → 'global' without a
    data migration)."""
    region = (region or "").lower().strip()
    if region not in VALID_REGIONS:
        raise ValueError(
            f"region must be one of {sorted(VALID_REGIONS)}, got {region!r}"
        )
    current = get_residency(org_id)
    if current != "global" and region == "global":
        raise ValueError(
            f"cannot downgrade from {current!r} to 'global' without "
            "a data migration — open a support ticket"
        )
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        conn.execute(
            "UPDATE orgs SET data_residency = ? WHERE id = ?",
            (region, org_id),
        )
    return region


# ---------- routing helpers ----------

def should_route_to_india(org_id: str | None) -> bool:
    """Single source of truth for storage / DB / observability code.
    Returns True when this org's data must stay in India."""
    if org_id is None:
        return False
    return get_residency(org_id) == "india"


@dataclass(frozen=True)
class StorageEndpoint:
    """Resolved storage configuration for an org. Caller uses
    `endpoint` + `bucket` when constructing the boto3 client; falls
    back to the global env config when residency == 'global'."""
    region: str             # 'global' | 'india' | 'eu'
    endpoint_url: str | None  # e.g. https://*.r2.cloudflarestorage.com
    bucket: str | None


def storage_for_org(org_id: str | None) -> StorageEndpoint:
    """Resolve the right R2/S3 endpoint based on data_residency.
    Reads env vars (no hardcoded URLs):

      Global:    S3_ENDPOINT_URL + S3_BUCKET (today's defaults)
      India:     S3_ENDPOINT_URL_IN + S3_BUCKET_IN
      EU:        S3_ENDPOINT_URL_EU + S3_BUCKET_EU

    When the India-specific env vars aren't set, falls back to the
    global ones with a logged warning — better to keep writing than
    fail closed during a region rollout."""
    region = get_residency(org_id) if org_id else "global"
    if region == "india":
        ep = os.environ.get("S3_ENDPOINT_URL_IN") or os.environ.get("S3_ENDPOINT_URL")
        bkt = os.environ.get("S3_BUCKET_IN") or os.environ.get("S3_BUCKET")
        if not os.environ.get("S3_ENDPOINT_URL_IN"):
            print(
                f"[residency] WARN: org={org_id} marked 'india' but "
                "S3_ENDPOINT_URL_IN unset; falling back to global "
                "endpoint. Configure India bucket before launching "
                "data-resident customers."
            )
        return StorageEndpoint(region="india", endpoint_url=ep, bucket=bkt)
    if region == "eu":
        ep = os.environ.get("S3_ENDPOINT_URL_EU") or os.environ.get("S3_ENDPOINT_URL")
        bkt = os.environ.get("S3_BUCKET_EU") or os.environ.get("S3_BUCKET")
        return StorageEndpoint(region="eu", endpoint_url=ep, bucket=bkt)
    return StorageEndpoint(
        region="global",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
        bucket=os.environ.get("S3_BUCKET"),
    )
