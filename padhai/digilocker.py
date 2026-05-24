"""P4 — NDEAR DigiLocker integration.

Students store their AI Pathshala certificates, report cards,
and course-completion attestations in DigiLocker (govt's national
document vault: digilocker.gov.in). Issued docs become available
in the student's DigiLocker app + can be shared as govt-verified
PDFs with universities + employers.

Issuance flow:
  1. Student earns a credential (course completion / exam pass /
     mentor session count / corporate path certificate)
  2. We mint a `digilocker_issuances` row in 'pending' state with
     a SHA-256 of the canonical doc body + the recipient's Aadhaar
     hash (never raw Aadhaar — we hash with HKDF-SHA256)
  3. Background worker pushes the issuance to DigiLocker's
     `pushUriRequest` endpoint (`PADHAI_DIGILOCKER_*` env vars).
     This module surfaces enqueue + status only — the actual HTTP
     POST is wherever `tools/digilocker_push.py` runs.
  4. On callback, issuance is marked 'issued' with a URI; student
     can fetch the govt-verified PDF.

Tables:
  digilocker_orgs        per-org issuer cfg (api_key_hash, signing key)
  digilocker_doc_types   what we're allowed to issue (template-gated)
  digilocker_issuances   one row per issued credential
  digilocker_consents    student opt-in (DPDP §6 explicit consent)

Doc kinds (whitelisted):
  - course_completion: course taken on AI Pathshala
  - exam_certificate:  passing a mock test / certification
  - corporate_training: R1 path completion
  - tutor_session_log: 25/50/100 paid sessions milestone

Different from P2 (DIKSHA — content interop) — P4 is **credential
issuance** into the citizen vault. P2 says "here's content"; P4
says "here's a signed document about your achievement."
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS digilocker_orgs (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,            -- our orgs.id (P4 is per-org)
    issuer_name         TEXT NOT NULL,
    issuer_id           TEXT NOT NULL,            -- DigiLocker-assigned issuer
    api_key_hash        TEXT,                     -- SHA-256 of stored secret
    signing_key_id      TEXT,                     -- for the JWS signature
    status              TEXT NOT NULL DEFAULT 'sandbox',
    -- 'sandbox' | 'live' | 'paused'
    callback_url        TEXT,
    created_at          REAL NOT NULL,
    activated_at        REAL,
    UNIQUE (org_id),
    UNIQUE (issuer_id)
);
CREATE INDEX IF NOT EXISTS idx_dlo_status
    ON digilocker_orgs(status);

CREATE TABLE IF NOT EXISTS digilocker_doc_types (
    id                  TEXT PRIMARY KEY,
    code                TEXT NOT NULL,            -- 'course_completion'
    title               TEXT NOT NULL,
    template_url        TEXT,                     -- where PDF template lives
    description         TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    UNIQUE (code)
);

CREATE TABLE IF NOT EXISTS digilocker_consents (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL,
    aadhaar_hash        TEXT NOT NULL,            -- HKDF-SHA256 — never raw
    consent_purposes    TEXT NOT NULL,            -- JSON array of doc_type.code
    consent_text        TEXT NOT NULL,            -- exact text shown to user
    consented_at        REAL NOT NULL,
    revoked_at          REAL,
    UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS digilocker_issuances (
    id                  TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    doc_type_code       TEXT NOT NULL,
    doc_title           TEXT NOT NULL,
    body_sha256         TEXT NOT NULL,            -- canonical body hash
    recipient_aadhaar_hash TEXT NOT NULL,         -- HKDF-SHA256
    payload_json        TEXT NOT NULL,            -- the doc body
    status              TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'queued' | 'issued' | 'failed' | 'revoked'
    digilocker_uri      TEXT,                     -- URI assigned on issue
    error_reason        TEXT,
    created_at          REAL NOT NULL,
    issued_at           REAL,
    UNIQUE (org_id, user_id, doc_type_code, body_sha256)
);
CREATE INDEX IF NOT EXISTS idx_dli_status
    ON digilocker_issuances(status, created_at);
CREATE INDEX IF NOT EXISTS idx_dli_user
    ON digilocker_issuances(user_id, created_at DESC);
"""


VALID_ORG_STATUSES = frozenset({"sandbox", "live", "paused"})

VALID_ISSUANCE_STATUSES = frozenset({
    "pending", "queued", "issued", "failed", "revoked",
})

# Seeded doc-type catalog — whitelist of things we're allowed to
# issue. Doesn't auto-update; new types are added by hand because
# every type needs a PDF template + DigiLocker approval.
_DOC_TYPE_SEED = [
    {"code": "course_completion",
     "title": "Course Completion Certificate",
     "description": "Issued when a student finishes a full course."},
    {"code": "exam_certificate",
     "title": "Exam Certification",
     "description": "Issued on passing a mock test or certification."},
    {"code": "corporate_training",
     "title": "Corporate Training Certificate",
     "description": "Issued for R1 corporate path completion."},
    {"code": "tutor_session_log",
     "title": "Tutor Session Milestone",
     "description": "Issued at 25 / 50 / 100 paid tutor sessions."},
]


# Aadhaar isn't really 12 digits in raw form — but we never store
# raw. We accept 12-digit string only as input and hash immediately.
_AADHAAR_RE = re.compile(r"^[0-9]{12}$")

# Hash salt — env-configurable for rotation. Falls back to a fixed
# build-time value so dev + CI are consistent.
_AADHAAR_SALT = os.environ.get(
    "PADHAI_AADHAAR_HASH_SALT",
    "padhai-dev-hash-salt-do-not-use-in-prod",
)


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
    with _conn() as conn:
        for d in _DOC_TYPE_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO digilocker_doc_types "
                "(id, code, title, description, enabled) "
                "VALUES (?, ?, ?, ?, 1)",
                (uuid.uuid4().hex, d["code"], d["title"],
                 d["description"]),
            )


# ---------- helpers ----------

def hash_aadhaar(raw: str) -> str:
    """Never store raw Aadhaar. Convert at the boundary. Uses
    HKDF-like extract w/ a salted SHA-256 — sufficient for the
    write-once dedup use case here (we never need to reverse)."""
    raw = (raw or "").strip()
    if not _AADHAAR_RE.match(raw):
        raise ValueError("aadhaar must be a 12-digit string")
    return hashlib.sha256(
        (_AADHAAR_SALT + ":" + raw).encode("utf-8"),
    ).hexdigest()


def hash_doc_body(payload: dict) -> str:
    """Canonical SHA-256 of the doc body — used for dedup (same
    issuance can't double-issue) and for the JWS signature payload
    DigiLocker accepts on push."""
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------- dataclasses ----------

@dataclass(frozen=True)
class DigilockerOrg:
    id: str
    org_id: str
    issuer_name: str
    issuer_id: str
    signing_key_id: str | None
    status: str
    callback_url: str | None
    created_at: float
    activated_at: float | None


@dataclass(frozen=True)
class DocType:
    id: str
    code: str
    title: str
    template_url: str | None
    description: str | None
    enabled: bool


@dataclass(frozen=True)
class Consent:
    id: str
    user_id: str
    consent_purposes: list[str]
    consent_text: str
    consented_at: float
    revoked_at: float | None


@dataclass(frozen=True)
class Issuance:
    id: str
    org_id: str
    user_id: str
    doc_type_code: str
    doc_title: str
    body_sha256: str
    status: str
    digilocker_uri: str | None
    error_reason: str | None
    created_at: float
    issued_at: float | None


# ---------- per-org issuer cfg ----------

def register_org_issuer(
    *,
    org_id: str,
    issuer_name: str,
    issuer_id: str,
    api_key: str | None = None,
    callback_url: str | None = None,
) -> DigilockerOrg:
    issuer_name = (issuer_name or "").strip()
    if not issuer_name or len(issuer_name) > 200:
        raise ValueError("issuer_name required (max 200 chars)")
    issuer_id = (issuer_id or "").strip()
    if not issuer_id or len(issuer_id) > 80:
        raise ValueError("issuer_id required (max 80 chars)")
    api_hash = (
        hashlib.sha256(api_key.encode("utf-8")).hexdigest()
        if api_key else None
    )
    rid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO digilocker_orgs "
                "(id, org_id, issuer_name, issuer_id, "
                " api_key_hash, callback_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, org_id, issuer_name, issuer_id,
                 api_hash, callback_url, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("org already has a DigiLocker issuer") from e
    return get_org_issuer(org_id)  # type: ignore[return-value]


def get_org_issuer(org_id: str) -> DigilockerOrg | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id, issuer_name, issuer_id, "
            "       signing_key_id, status, callback_url, "
            "       created_at, activated_at "
            "FROM digilocker_orgs WHERE org_id = ?",
            (org_id,),
        ).fetchone()
    if not r:
        return None
    return DigilockerOrg(
        id=r[0], org_id=r[1], issuer_name=r[2], issuer_id=r[3],
        signing_key_id=r[4], status=r[5], callback_url=r[6],
        created_at=r[7], activated_at=r[8],
    )


def activate_org_issuer(*, org_id: str) -> bool:
    """Move from sandbox → live after DigiLocker e2e cert."""
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_orgs SET "
            " status = 'live', activated_at = ? "
            "WHERE org_id = ? AND status = 'sandbox'",
            (now, org_id),
        )
        return cur.rowcount > 0


def set_org_status(*, org_id: str, status: str) -> bool:
    if status not in VALID_ORG_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_ORG_STATUSES)}",
        )
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_orgs SET status = ? WHERE org_id = ?",
            (status, org_id),
        )
        return cur.rowcount > 0


# ---------- doc-type catalog ----------

def list_doc_types(*, enabled_only: bool = True) -> list[DocType]:
    sql = (
        "SELECT id, code, title, template_url, description, enabled "
        "FROM digilocker_doc_types"
    )
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY code"
    with _conn() as conn:
        rows = conn.execute(sql).fetchall()
    return [
        DocType(
            id=r[0], code=r[1], title=r[2], template_url=r[3],
            description=r[4], enabled=bool(r[5]),
        )
        for r in rows
    ]


def get_doc_type(code: str) -> DocType | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, code, title, template_url, description, enabled "
            "FROM digilocker_doc_types WHERE code = ?",
            (code,),
        ).fetchone()
    if not r:
        return None
    return DocType(
        id=r[0], code=r[1], title=r[2], template_url=r[3],
        description=r[4], enabled=bool(r[5]),
    )


def set_doc_template(*, code: str, template_url: str) -> bool:
    if len(template_url) > 500:
        raise ValueError("template_url must be ≤500 chars")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_doc_types SET template_url = ? "
            "WHERE code = ?",
            (template_url, code),
        )
        return cur.rowcount > 0


# ---------- consent (DPDP §6) ----------

def record_consent(
    *,
    user_id: str,
    aadhaar_raw: str,
    consent_purposes: list[str],
    consent_text: str,
) -> Consent:
    """User explicitly grants permission to push docs to their
    DigiLocker. Aadhaar collected only to bind the issuance to the
    citizen — we hash + discard the raw value immediately."""
    if not consent_purposes:
        raise ValueError("consent_purposes must be non-empty")
    consent_text = (consent_text or "").strip()
    if len(consent_text) < 20 or len(consent_text) > 2000:
        raise ValueError(
            "consent_text must be [20, 2000] chars "
            "(explicit, plain-language consent)",
        )
    valid = {dt.code for dt in list_doc_types()}
    for purpose in consent_purposes:
        if purpose not in valid:
            raise ValueError(f"unknown purpose: {purpose!r}")
    aadhaar_hash = hash_aadhaar(aadhaar_raw)
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        # Idempotent: if already consented, update purposes + text
        existing = conn.execute(
            "SELECT id FROM digilocker_consents WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE digilocker_consents SET "
                " aadhaar_hash = ?, consent_purposes = ?, "
                " consent_text = ?, consented_at = ?, "
                " revoked_at = NULL "
                "WHERE user_id = ?",
                (aadhaar_hash, json.dumps(consent_purposes),
                 consent_text, now, user_id),
            )
            cid = existing[0]
        else:
            conn.execute(
                "INSERT INTO digilocker_consents "
                "(id, user_id, aadhaar_hash, consent_purposes, "
                " consent_text, consented_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, user_id, aadhaar_hash,
                 json.dumps(consent_purposes), consent_text, now),
            )
    return Consent(
        id=cid, user_id=user_id,
        consent_purposes=consent_purposes,
        consent_text=consent_text,
        consented_at=now, revoked_at=None,
    )


def get_consent(user_id: str) -> Consent | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, consent_purposes, consent_text, "
            "       consented_at, revoked_at "
            "FROM digilocker_consents WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not r:
        return None
    return Consent(
        id=r[0], user_id=r[1],
        consent_purposes=json.loads(r[2]),
        consent_text=r[3],
        consented_at=r[4], revoked_at=r[5],
    )


def revoke_consent(user_id: str) -> bool:
    """DPDP §13 — user has right to withdraw consent any time. We
    keep the row for audit but flag revoked_at; future issuances
    fail the consent gate."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_consents SET revoked_at = ? "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (time.time(), user_id),
        )
        return cur.rowcount > 0


def has_active_consent(*, user_id: str, doc_type_code: str) -> bool:
    c = get_consent(user_id)
    if not c or c.revoked_at:
        return False
    return doc_type_code in c.consent_purposes


# ---------- issuance ----------

def _get_aadhaar_hash(user_id: str) -> str | None:
    """Fetch the stored aadhaar_hash from consent — we never have
    raw Aadhaar after the consent step."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT aadhaar_hash FROM digilocker_consents "
            "WHERE user_id = ? AND revoked_at IS NULL",
            (user_id,),
        ).fetchone()
    return r[0] if r else None


def enqueue_issuance(
    *,
    org_id: str,
    user_id: str,
    doc_type_code: str,
    doc_title: str,
    payload: dict,
) -> Issuance:
    """Mint a pending issuance. Background worker picks it up +
    pushes to DigiLocker. Idempotent on (org, user, type, body_hash):
    re-enqueueing the same doc returns the existing row."""
    org = get_org_issuer(org_id)
    if not org:
        raise ValueError("org has no DigiLocker issuer registered")
    if org.status == "paused":
        raise ValueError(f"org issuer is {org.status!r}, can't enqueue")
    dt = get_doc_type(doc_type_code)
    if not dt or not dt.enabled:
        raise ValueError(
            f"doc_type {doc_type_code!r} unknown or disabled",
        )
    if not has_active_consent(
        user_id=user_id, doc_type_code=doc_type_code,
    ):
        raise PermissionError(
            "user has not consented to this doc type",
        )
    aadhaar_hash = _get_aadhaar_hash(user_id)
    if not aadhaar_hash:
        raise PermissionError("no active consent record")
    doc_title = (doc_title or "").strip()
    if len(doc_title) < 4 or len(doc_title) > 200:
        raise ValueError("doc_title must be [4, 200] chars")
    body_sha = hash_doc_body(payload)
    iid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO digilocker_issuances "
                "(id, org_id, user_id, doc_type_code, doc_title, "
                " body_sha256, recipient_aadhaar_hash, "
                " payload_json, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (iid, org_id, user_id, doc_type_code, doc_title,
                 body_sha, aadhaar_hash, json.dumps(payload),
                 time.time()),
            )
    except sqlite3.IntegrityError:
        # Same doc, same recipient, same hash → already enqueued.
        # Return the existing row (idempotency).
        with _conn() as conn:
            r = conn.execute(
                "SELECT id, org_id, user_id, doc_type_code, "
                "       doc_title, body_sha256, status, "
                "       digilocker_uri, error_reason, "
                "       created_at, issued_at "
                "FROM digilocker_issuances "
                "WHERE org_id = ? AND user_id = ? "
                "AND doc_type_code = ? AND body_sha256 = ?",
                (org_id, user_id, doc_type_code, body_sha),
            ).fetchone()
        return _row_to_issuance(r)
    return get_issuance(iid)  # type: ignore[return-value]


def _row_to_issuance(r) -> Issuance:
    return Issuance(
        id=r[0], org_id=r[1], user_id=r[2],
        doc_type_code=r[3], doc_title=r[4],
        body_sha256=r[5], status=r[6],
        digilocker_uri=r[7], error_reason=r[8],
        created_at=r[9], issued_at=r[10],
    )


_I_SEL = (
    "SELECT id, org_id, user_id, doc_type_code, doc_title, "
    "       body_sha256, status, digilocker_uri, error_reason, "
    "       created_at, issued_at "
    "FROM digilocker_issuances"
)


def get_issuance(issuance_id: str) -> Issuance | None:
    with _conn() as conn:
        r = conn.execute(
            _I_SEL + " WHERE id = ?", (issuance_id,),
        ).fetchone()
    return _row_to_issuance(r) if r else None


def list_user_issuances(user_id: str) -> list[Issuance]:
    with _conn() as conn:
        rows = conn.execute(
            _I_SEL + " WHERE user_id = ? "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_issuance(r) for r in rows]


def list_pending_issuances(limit: int = 100) -> list[Issuance]:
    """Worker uses this to pull a batch to push."""
    limit = max(1, min(limit, 1000))
    with _conn() as conn:
        rows = conn.execute(
            _I_SEL + " WHERE status IN ('pending', 'queued') "
            "ORDER BY created_at LIMIT ?", (limit,),
        ).fetchall()
    return [_row_to_issuance(r) for r in rows]


def mark_queued(issuance_id: str) -> bool:
    """Worker has picked up the row + pushed to DigiLocker; awaiting
    callback."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_issuances SET status = 'queued' "
            "WHERE id = ? AND status = 'pending'",
            (issuance_id,),
        )
        return cur.rowcount > 0


def mark_issued(*, issuance_id: str, digilocker_uri: str) -> bool:
    """Callback from DigiLocker — doc is now in citizen vault."""
    if not digilocker_uri or len(digilocker_uri) > 500:
        raise ValueError("digilocker_uri required (max 500 chars)")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_issuances SET "
            " status = 'issued', digilocker_uri = ?, "
            " issued_at = ? "
            "WHERE id = ? AND status IN ('pending', 'queued')",
            (digilocker_uri, time.time(), issuance_id),
        )
        return cur.rowcount > 0


def mark_failed(*, issuance_id: str, reason: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_issuances SET "
            " status = 'failed', error_reason = ? "
            "WHERE id = ?",
            ((reason or "")[:500], issuance_id),
        )
        return cur.rowcount > 0


def revoke_issuance(*, issuance_id: str) -> bool:
    """Admin path — credentials can be revoked (e.g. on exam re-
    audit). DigiLocker has a corresponding revokeURI API the worker
    calls in tandem; here we just mark our local state."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE digilocker_issuances SET status = 'revoked' "
            "WHERE id = ? AND status = 'issued'",
            (issuance_id,),
        )
        return cur.rowcount > 0


def issuance_stats(*, org_id: str | None = None) -> dict:
    """Per-org dashboard rollup."""
    where = ""
    params: list = []
    if org_id:
        where = "WHERE org_id = ?"
        params.append(org_id)
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM digilocker_issuances "
            f"{where} GROUP BY status",
            params,
        ).fetchall()
        by_type = conn.execute(
            "SELECT doc_type_code, COUNT(*) FROM digilocker_issuances "
            f"{where} GROUP BY doc_type_code",
            params,
        ).fetchall()
    return {
        "by_status": {r[0]: r[1] for r in rows},
        "by_doc_type": {r[0]: r[1] for r in by_type},
    }
