"""K2 — SAARC expansion (Bangladesh, Nepal, Sri Lanka).

Adds country awareness to orgs so we can:
- Route to the right payment provider (bKash for BD, eSewa for NP,
  PayHere for LK; Razorpay for IN)
- Apply the right curriculum catalog (NEB for Nepal, BISE boards
  for Pakistan, etc.)
- Use the right currency for fees + invoicing

Single source of truth — `orgs.country` (additive column). The
payment + curriculum modules read this when routing.

This module is the lookup table + helpers; the actual payment
provider integrations (bKash/eSewa/PayHere) land in v1.9.x per the
schedule in ROADMAP_V2.md K2.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ADDITIONS = [
    "ALTER TABLE orgs ADD COLUMN country TEXT DEFAULT 'IN'",
    "ALTER TABLE orgs ADD COLUMN currency TEXT DEFAULT 'INR'",
]


@dataclass(frozen=True)
class CountryProfile:
    code: str            # ISO 3166 alpha-2
    name: str
    currency: str        # ISO 4217
    payment_provider: str
    primary_language: str
    secondary_languages: tuple[str, ...]
    timezone: str
    education_boards: tuple[str, ...]


# SAARC + neighbouring markets we serve in v1.9.
PROFILES = {
    "IN": CountryProfile(
        code="IN", name="India",
        currency="INR",
        payment_provider="razorpay",
        primary_language="hi",
        secondary_languages=("en", "ta", "te", "mr", "kn", "ml",
                             "bn", "gu", "pa", "or"),
        timezone="Asia/Kolkata",
        education_boards=("cbse", "icse", "ib", "state_mh", "state_ka",
                          "state_tn", "state_up", "state_wb"),
    ),
    "BD": CountryProfile(
        code="BD", name="Bangladesh",
        currency="BDT",
        payment_provider="bkash",     # SSLCOMMERZ as fallback
        primary_language="bn",
        secondary_languages=("en",),
        timezone="Asia/Dhaka",
        education_boards=("bd_dhaka", "bd_chittagong", "bd_rajshahi",
                          "bd_madrasah"),
    ),
    "NP": CountryProfile(
        code="NP", name="Nepal",
        currency="NPR",
        payment_provider="esewa",     # Khalti as fallback
        primary_language="ne",
        secondary_languages=("hi", "en"),
        timezone="Asia/Kathmandu",
        education_boards=("neb", "see"),
    ),
    "LK": CountryProfile(
        code="LK", name="Sri Lanka",
        currency="LKR",
        payment_provider="payhere",
        primary_language="si",
        secondary_languages=("ta", "en"),
        timezone="Asia/Colombo",
        education_boards=("lk_natl", "lk_gce_ol", "lk_gce_al"),
    ),
    "PK": CountryProfile(
        code="PK", name="Pakistan",
        currency="PKR",
        payment_provider="jazzcash",  # EasyPaisa as fallback; defer K2.x
        primary_language="ur",
        secondary_languages=("en", "pa"),
        timezone="Asia/Karachi",
        education_boards=("fbise", "bise_lahore", "bise_karachi",
                          "agha_khan"),
    ),
}


VALID_COUNTRIES = frozenset(PROFILES.keys())


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
    except sqlite3.OperationalError:
        return


def profile_for(code: str) -> CountryProfile:
    """Resolve country code to profile. Defaults to India when
    unknown (matches the existing single-country deployment)."""
    code = (code or "IN").upper()
    return PROFILES.get(code, PROFILES["IN"])


def get_country(org_id: str) -> str:
    try:
        with sqlite3.connect(_db_path(), timeout=10.0) as conn:
            r = conn.execute(
                "SELECT country FROM orgs WHERE id = ?", (org_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return "IN"
    return (r[0] if r and r[0] else "IN").upper()


def set_country(*, org_id: str, code: str) -> str:
    code = (code or "").upper()
    if code not in VALID_COUNTRIES:
        raise ValueError(
            f"country must be in {sorted(VALID_COUNTRIES)}"
        )
    prof = profile_for(code)
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        conn.execute(
            "UPDATE orgs SET country = ?, currency = ? WHERE id = ?",
            (code, prof.currency, org_id),
        )
    return code
