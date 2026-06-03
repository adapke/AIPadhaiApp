"""Q5 — Sales pipeline integration.

Lead capture + stage tracking + activity log + outbound sync to
HubSpot / Salesforce when configured. Webhook-driven so the rest
of the codebase stays decoupled — anywhere a lead-shaped event
happens, call `sales_pipeline.create_lead(...)` and the CRM
integration handles itself.

Two tables:
  leads               id, source, org_name, contact, stage, score
  lead_activities     id, lead_id, kind, title, body, created_by

Sync: outbound HTTP POST to `SALES_WEBHOOK_URL` (HubSpot's
inbound-webhook endpoint, or any CRM that accepts JSON). Body
includes the canonical lead state on every transition.

Scoring (max 100):
  + size: 0..30  based on org_name + headcount + state govt match
  + intent: 0..30  recent activity + page views + demo requested
  + recency: 0..20  age-decay on last activity
  + completeness: 0..20  fields filled
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
CREATE TABLE IF NOT EXISTS leads (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    -- 'web' | 'referral' | 'outreach' | 'gem' | 'partnership' | 'inbound_call'
    org_name        TEXT NOT NULL,
    org_kind        TEXT,                     -- 'school' | 'coaching' | 'corporate' | 'govt' | 'university'
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    state_code      TEXT,                     -- IN state for govt deals
    estimated_seats INTEGER,
    expected_value_inr INTEGER,
    stage           TEXT NOT NULL DEFAULT 'new',
    -- 'new' | 'qualified' | 'demo' | 'proposal' | 'negotiation' | 'won' | 'lost'
    score           INTEGER NOT NULL DEFAULT 0,
    owner_user_id   TEXT,
    crm_external_id TEXT,                      -- HubSpot/Salesforce id once synced
    notes           TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_stage_score
    ON leads(stage, score DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_owner
    ON leads(owner_user_id, stage);
CREATE INDEX IF NOT EXISTS idx_leads_source
    ON leads(source, created_at DESC);

CREATE TABLE IF NOT EXISTS lead_activities (
    id              TEXT PRIMARY KEY,
    lead_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    -- 'call' | 'email' | 'demo' | 'note' | 'task' | 'stage_change' | 'meeting'
    title           TEXT NOT NULL,
    body            TEXT,
    payload_json    TEXT,
    created_by      TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_la_lead_time
    ON lead_activities(lead_id, created_at DESC);
"""


VALID_SOURCES = frozenset({
    "web", "referral", "outreach", "gem", "partnership",
    "inbound_call", "event",
})

VALID_ORG_KINDS = frozenset({
    "school", "coaching", "corporate", "govt", "university", "ngo",
})

VALID_STAGES = frozenset({
    "new", "qualified", "demo", "proposal", "negotiation",
    "won", "lost",
})

VALID_ACTIVITY_KINDS = frozenset({
    "call", "email", "demo", "note", "task",
    "stage_change", "meeting", "form_submit",
})


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
class Lead:
    id: str
    source: str
    org_name: str
    org_kind: str | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    state_code: str | None
    estimated_seats: int | None
    expected_value_inr: int | None
    stage: str
    score: int
    owner_user_id: str | None
    crm_external_id: str | None
    notes: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Activity:
    id: str
    lead_id: str
    kind: str
    title: str
    body: str | None
    payload: dict | None
    created_by: str | None
    created_at: float


_LEAD_SEL = (
    "SELECT id, source, org_name, org_kind, contact_name, "
    "       contact_email, contact_phone, state_code, "
    "       estimated_seats, expected_value_inr, stage, score, "
    "       owner_user_id, crm_external_id, notes, "
    "       created_at, updated_at "
    "FROM leads"
)


def _row_to_lead(r) -> Lead:
    return Lead(
        id=r[0], source=r[1], org_name=r[2], org_kind=r[3],
        contact_name=r[4], contact_email=r[5], contact_phone=r[6],
        state_code=r[7], estimated_seats=r[8],
        expected_value_inr=r[9], stage=r[10], score=r[11],
        owner_user_id=r[12], crm_external_id=r[13], notes=r[14],
        created_at=r[15], updated_at=r[16],
    )


# ---------- lead CRUD ----------

def create_lead(
    *,
    source: str,
    org_name: str,
    org_kind: str | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    state_code: str | None = None,
    estimated_seats: int | None = None,
    expected_value_inr: int | None = None,
    notes: str | None = None,
    owner_user_id: str | None = None,
) -> Lead:
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be in {sorted(VALID_SOURCES)}")
    org_name = (org_name or "").strip()
    if len(org_name) < 2 or len(org_name) > 200:
        raise ValueError("org_name must be [2, 200] chars")
    if org_kind and org_kind not in VALID_ORG_KINDS:
        raise ValueError(f"org_kind must be in {sorted(VALID_ORG_KINDS)}")
    lid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO leads "
            "(id, source, org_name, org_kind, contact_name, "
            " contact_email, contact_phone, state_code, "
            " estimated_seats, expected_value_inr, stage, score, "
            " owner_user_id, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', 0, ?, "
            "        ?, ?, ?)",
            (lid, source, org_name, org_kind, contact_name,
             contact_email, contact_phone, state_code,
             estimated_seats, expected_value_inr,
             owner_user_id, notes, now, now),
        )
    lead = get_lead(lid)
    if lead:
        new_score = compute_score(lid)
        _set_score(lid, new_score)
        _fire_webhook("created", lead._replace(score=new_score)
                      if hasattr(lead, "_replace") else lead)
    return get_lead(lid)  # type: ignore[return-value]


def get_lead(lead_id: str) -> Lead | None:
    with _conn() as conn:
        r = conn.execute(
            _LEAD_SEL + " WHERE id = ?", (lead_id,),
        ).fetchone()
    return _row_to_lead(r) if r else None


def list_leads(
    *,
    stage: str | None = None,
    owner_user_id: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Lead]:
    limit = max(1, min(limit, 500))
    sql = _LEAD_SEL + " WHERE 1=1"
    params: list = []
    if stage:
        if stage not in VALID_STAGES:
            raise ValueError(f"stage must be in {sorted(VALID_STAGES)}")
        sql += " AND stage = ?"; params.append(stage)
    if owner_user_id:
        sql += " AND owner_user_id = ?"; params.append(owner_user_id)
    if source:
        sql += " AND source = ?"; params.append(source)
    sql += " ORDER BY score DESC, updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, max(0, offset)])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_lead(r) for r in rows]


def update_stage(
    *,
    lead_id: str,
    new_stage: str,
    user_id: str | None = None,
    note: str | None = None,
) -> Lead:
    if new_stage not in VALID_STAGES:
        raise ValueError(f"stage must be in {sorted(VALID_STAGES)}")
    lead = get_lead(lead_id)
    if not lead:
        raise ValueError("lead not found")
    if lead.stage == new_stage:
        return lead
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE leads SET stage = ?, updated_at = ? "
            "WHERE id = ?",
            (new_stage, now, lead_id),
        )
        # Log stage-change activity inline (cheap; gives sequential
        # audit trail without a separate listener)
        conn.execute(
            "INSERT INTO lead_activities "
            "(id, lead_id, kind, title, body, payload_json, "
            " created_by, created_at) "
            "VALUES (?, ?, 'stage_change', ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, lead_id,
             f"Stage: {lead.stage} → {new_stage}",
             note,
             json.dumps({"from": lead.stage, "to": new_stage}),
             user_id, now),
        )
    new_score = compute_score(lead_id)
    _set_score(lead_id, new_score)
    updated = get_lead(lead_id)
    if updated:
        _fire_webhook("stage_changed", updated)
    return updated  # type: ignore[return-value]


def assign(*, lead_id: str, owner_user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE leads SET owner_user_id = ?, updated_at = ? "
            "WHERE id = ?",
            (owner_user_id, time.time(), lead_id),
        )
        return cur.rowcount > 0


def set_crm_id(*, lead_id: str, crm_external_id: str) -> bool:
    """Called by the webhook return path after the CRM creates its
    own record + sends us back its id."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE leads SET crm_external_id = ?, updated_at = ? "
            "WHERE id = ?",
            (crm_external_id, time.time(), lead_id),
        )
        return cur.rowcount > 0


# ---------- activities ----------

def log_activity(
    *,
    lead_id: str,
    kind: str,
    title: str,
    body: str | None = None,
    payload: dict | None = None,
    created_by: str | None = None,
) -> Activity:
    if kind not in VALID_ACTIVITY_KINDS:
        raise ValueError(
            f"kind must be in {sorted(VALID_ACTIVITY_KINDS)}",
        )
    if not get_lead(lead_id):
        raise ValueError("lead not found")
    title = (title or "").strip()
    if len(title) < 2 or len(title) > 200:
        raise ValueError("title must be [2, 200] chars")
    aid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO lead_activities "
            "(id, lead_id, kind, title, body, payload_json, "
            " created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, lead_id, kind, title, body,
             json.dumps(payload) if payload else None,
             created_by, now),
        )
        # Bump lead.updated_at so it floats in the pipeline view
        conn.execute(
            "UPDATE leads SET updated_at = ? WHERE id = ?",
            (now, lead_id),
        )
    # Activity boosts intent/recency components → recompute
    new_score = compute_score(lead_id)
    _set_score(lead_id, new_score)
    return _get_activity(aid)


def _get_activity(activity_id: str) -> Activity:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, lead_id, kind, title, body, payload_json, "
            "       created_by, created_at "
            "FROM lead_activities WHERE id = ?", (activity_id,),
        ).fetchone()
    return Activity(
        id=r[0], lead_id=r[1], kind=r[2], title=r[3], body=r[4],
        payload=json.loads(r[5]) if r[5] else None,
        created_by=r[6], created_at=r[7],
    )


def list_activities(lead_id: str, *, limit: int = 100) -> list[Activity]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, lead_id, kind, title, body, payload_json, "
            "       created_by, created_at "
            "FROM lead_activities WHERE lead_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (lead_id, limit),
        ).fetchall()
    return [
        Activity(
            id=r[0], lead_id=r[1], kind=r[2], title=r[3], body=r[4],
            payload=json.loads(r[5]) if r[5] else None,
            created_by=r[6], created_at=r[7],
        )
        for r in rows
    ]


# ---------- scoring ----------

def compute_score(lead_id: str) -> int:
    """Returns 0..100. Public — admins call to dry-run before
    a change. `update_stage` + `log_activity` already auto-recompute."""
    lead = get_lead(lead_id)
    if not lead:
        return 0
    # Size component (0..30)
    size = 0
    if lead.estimated_seats:
        if lead.estimated_seats >= 5000:
            size = 30
        elif lead.estimated_seats >= 1000:
            size = 20
        elif lead.estimated_seats >= 100:
            size = 10
        else:
            size = 5
    if lead.expected_value_inr:
        if lead.expected_value_inr >= 10_000_000:
            size = max(size, 30)
        elif lead.expected_value_inr >= 1_000_000:
            size = max(size, 20)
    if lead.org_kind == "govt":
        size = min(30, size + 10)   # govt deals are big by default
    # Intent component (0..30) — activities in last 14 days
    since = time.time() - 14 * 86400
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM lead_activities "
            "WHERE lead_id = ? AND created_at >= ?",
            (lead_id, since),
        ).fetchone()
        recent = int(r[0] or 0)
        r2 = conn.execute(
            "SELECT COUNT(*) FROM lead_activities "
            "WHERE lead_id = ? AND kind = 'demo'",
            (lead_id,),
        ).fetchone()
        demos = int(r2[0] or 0)
    intent = min(30, recent * 3 + demos * 10)
    # Recency component (0..20) — age decay on updated_at
    age_days = (time.time() - lead.updated_at) / 86400.0
    recency = max(0, min(20, int(20 - age_days)))
    # Completeness component (0..20)
    filled = sum(
        1 for v in (
            lead.contact_name, lead.contact_email, lead.contact_phone,
            lead.estimated_seats, lead.expected_value_inr, lead.notes,
            lead.org_kind, lead.state_code,
        ) if v
    )
    completeness = min(20, filled * 3)
    # Stage adjustment
    stage_bonus = {
        "qualified": 5, "demo": 10, "proposal": 15,
        "negotiation": 20, "won": 30, "lost": -50,
    }.get(lead.stage, 0)
    total = size + intent + recency + completeness + stage_bonus
    return max(0, min(100, total))


def _set_score(lead_id: str, score: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE leads SET score = ? WHERE id = ?",
            (int(score), lead_id),
        )


def recompute_all_scores() -> int:
    """Cron-friendly: re-score every active lead (anything not
    won/lost). Returns count updated."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM leads WHERE stage NOT IN ('won', 'lost')",
        ).fetchall()
    n = 0
    for (lead_id,) in rows:
        _set_score(lead_id, compute_score(lead_id))
        n += 1
    return n


# ---------- pipeline summary ----------

def pipeline_summary() -> dict:
    """Stage-by-stage rollup: count + expected_value + avg_score.
    Drives the sales-team dashboard's funnel chart."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT stage, COUNT(*), "
            "       COALESCE(SUM(expected_value_inr), 0), "
            "       COALESCE(AVG(score), 0) "
            "FROM leads GROUP BY stage",
        ).fetchall()
    out = {s: {"count": 0, "value_inr": 0, "avg_score": 0.0}
           for s in VALID_STAGES}
    for r in rows:
        out[r[0]] = {
            "count": int(r[1]),
            "value_inr": int(r[2]),
            "avg_score": round(r[3], 1),
        }
    return out


# ---------- outbound CRM webhook ----------

def _fire_webhook(event_kind: str, lead: Lead) -> None:
    """Best-effort POST to SALES_WEBHOOK_URL. Swallows all errors —
    CRM sync failures must never break the in-app lead lifecycle.

    Format is generic JSON; HubSpot's inbound-webhook + Salesforce
    Flow both accept this shape with minimal mapping."""
    url = os.environ.get("SALES_WEBHOOK_URL")
    if not url:
        return
    try:
        import requests   # noqa: WPS433
    except ImportError:
        return
    payload = {
        "event": event_kind,
        "lead": {
            "id": lead.id, "source": lead.source,
            "org_name": lead.org_name, "org_kind": lead.org_kind,
            "contact_name": lead.contact_name,
            "contact_email": lead.contact_email,
            "contact_phone": lead.contact_phone,
            "state_code": lead.state_code,
            "estimated_seats": lead.estimated_seats,
            "expected_value_inr": lead.expected_value_inr,
            "stage": lead.stage, "score": lead.score,
            "owner_user_id": lead.owner_user_id,
            "crm_external_id": lead.crm_external_id,
            "created_at": lead.created_at,
            "updated_at": lead.updated_at,
        },
        "ts": time.time(),
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:  # noqa: BLE001
        print(f"[sales] webhook fire failed (non-fatal): {e}")
