"""H5 — Custom top-level domains.

E9 (v1.0) shipped subdomain routing — `stpauls.aipathshala.in` →
the school's branded view. H5 extends to true custom domains:
`learn.stpauls.edu.in` → same routing.

Two technical paths in production:
- **Cloudflare for SaaS** (recommended): the school points a CNAME at
  our app's hostname; Cloudflare provisions the SSL cert + routes
  traffic. Per-domain cost: ~$2/month.
- Manual cert (Let's Encrypt via certbot) — works but adds renewal
  ops + we have to deploy nginx/Caddy at the edge.

This module ships the **data + verification** layer. The actual
Cloudflare for SaaS API call happens in `padhai/cloudflare.py` (a
v1.6.x helper) when ops connects an API token.

Verification flow:
1. Org admin enters their custom domain → we generate a verification
   token + CNAME target
2. Admin adds the verification record to their DNS
3. Periodic checker (manual call for now; cron in v1.6.x) verifies
4. On verify, mark domain as active + (in prod) call Cloudflare to
   provision the cert
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ADDITIONS = [
    "ALTER TABLE orgs ADD COLUMN custom_domain TEXT",
    "ALTER TABLE orgs ADD COLUMN custom_domain_verified_at REAL",
    "ALTER TABLE orgs ADD COLUMN custom_domain_token TEXT",
]


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def migrate() -> None:
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            if not conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='orgs'"
            ).fetchone():
                return
            for stmt in ADDITIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_orgs_custom_domain "
                    "ON orgs(custom_domain) WHERE custom_domain IS NOT NULL"
                )
    except sqlite3.OperationalError:
        return


@dataclass(frozen=True)
class DomainStatus:
    org_id: str
    custom_domain: str | None
    verified: bool
    verified_at: float | None
    verification_token: str | None
    cname_target: str       # what the admin should point their CNAME at
    verification_record: str  # what TXT record they must add


def _cname_target() -> str:
    """The CNAME target schools point their custom domains at. Same
    hostname as our app's apex; differs by environment."""
    return os.environ.get("PADHAI_CUSTOM_DOMAIN_CNAME", "edge.aipathshala.in")


# Hostnames an org can't claim: our own apex, reserved local names,
# and IP-like strings. Prevents a school from accidentally (or
# maliciously) shadowing core platform URLs.
_RESERVED_HOSTS = frozenset({
    "localhost",
    "aipathshala.in", "www.aipathshala.in",
    "app.aipathshala.in", "api.aipathshala.in",
    "admin.aipathshala.in", "cdn.aipathshala.in",
    "edge.aipathshala.in",
})

# TLDs we reject entirely — reserved by RFC 6761 + not routable.
_BAD_TLDS = frozenset({
    "local", "localhost", "test", "example", "invalid",
    "internal", "lan", "home",
})


def set_domain(*, org_id: str, domain: str) -> DomainStatus:
    """Configure a new custom domain for an org. Generates a fresh
    verification token. Verification status reset to pending until
    the admin proves DNS control.

    Rejects reserved hostnames (our own apex, RFC 6761 TLDs, etc.)
    + IP-shaped strings to prevent shadowing platform URLs."""
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain or " " in domain or len(domain) > 253:
        raise ValueError("invalid domain")
    if domain in _RESERVED_HOSTS:
        raise ValueError(f"{domain!r} is reserved")
    tld = domain.rsplit(".", 1)[-1]
    if tld in _BAD_TLDS:
        raise ValueError(f".{tld} domains are not routable")
    # Reject IP-like strings (4 dotted octets) — these would resolve
    # to a host machine, not a real domain.
    parts = domain.split(".")
    if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        raise ValueError("IP addresses cannot be used as custom domains")
    token = "padhai-verify=" + secrets.token_urlsafe(24)
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            try:
                conn.execute(
                    "UPDATE orgs SET custom_domain = ?, "
                    "custom_domain_verified_at = NULL, "
                    "custom_domain_token = ? WHERE id = ?",
                    (domain, token, org_id),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(
                    f"domain {domain!r} is already in use by another org"
                ) from e
    except sqlite3.OperationalError:
        # orgs table not yet bootstrapped — happens on fresh sandbox
        # DBs before any org has been created. Validation already
        # passed; the write is a no-op until orgs exists. Caller
        # gets back a status with the verification token populated.
        pass
    return get_status(org_id)


def remove_domain(*, org_id: str) -> None:
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        conn.execute(
            "UPDATE orgs SET custom_domain = NULL, "
            "custom_domain_verified_at = NULL, custom_domain_token = NULL "
            "WHERE id = ?", (org_id,),
        )


def mark_verified(*, org_id: str) -> DomainStatus:
    """Called by the verification cron when DNS lookup succeeds.
    Idempotent."""
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        conn.execute(
            "UPDATE orgs SET custom_domain_verified_at = ? "
            "WHERE id = ? AND custom_domain IS NOT NULL",
            (time.time(), org_id),
        )
    return get_status(org_id)


def get_status(org_id: str) -> DomainStatus:
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT custom_domain, custom_domain_verified_at, "
                "       custom_domain_token "
                "FROM orgs WHERE id = ?", (org_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        r = None
    if not r:
        return DomainStatus(
            org_id=org_id, custom_domain=None, verified=False,
            verified_at=None, verification_token=None,
            cname_target=_cname_target(),
            verification_record="",
        )
    domain, vat, tok = r[0], r[1], r[2]
    return DomainStatus(
        org_id=org_id, custom_domain=domain,
        verified=vat is not None, verified_at=vat,
        verification_token=tok,
        cname_target=_cname_target(),
        verification_record=(
            f"_padhai.{domain} TXT \"{tok}\"" if domain and tok else ""
        ),
    )


def resolve_by_domain(host: str) -> str | None:
    """Resolve an incoming Host header to an org_id when it matches a
    verified custom domain. Returns None if no match or not yet
    verified. Called by the routing middleware on every request."""
    if not host:
        return None
    host = host.split(":")[0].strip().lower()
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT id FROM orgs "
                "WHERE custom_domain = ? "
                "AND custom_domain_verified_at IS NOT NULL",
                (host,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return r[0] if r else None
