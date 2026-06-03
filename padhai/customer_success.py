"""Q4 — Customer success automation.

Per-org health score (0-100) combining:
  - engagement (DAU/MAU ratio + active classes)
  - support load (open tickets — placeholder until ticketing module)
  - payment health (overdue invoices via E5)
  - growth velocity (new members + new content this period)

Below thresholds we auto-alert the CSM; below critical we escalate
to leadership. Renewal pipeline tracks orgs in the 90-day window
before contract end with a churn risk score.

Three tables:
  org_health_scores       periodic snapshots
  cs_events               log of alerts / nudges / onboarding steps
  renewal_pipeline        per-org renewal predictions

Designed to read from existing v0/v1/v2 tables — no schema changes
to orgs, events, jobs, etc.
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
CREATE TABLE IF NOT EXISTS org_health_scores (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    score           REAL NOT NULL,           -- 0..100
    components_json TEXT NOT NULL,           -- {engagement, payment, support, growth}
    computed_at     REAL NOT NULL,
    period_days     INTEGER NOT NULL DEFAULT 30
);
CREATE INDEX IF NOT EXISTS idx_hs_org_time
    ON org_health_scores(org_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_hs_score
    ON org_health_scores(score, computed_at DESC);

CREATE TABLE IF NOT EXISTS cs_events (
    id              TEXT PRIMARY KEY,
    org_id          TEXT,
    user_id         TEXT,
    kind            TEXT NOT NULL,
    -- 'onboarding_step' | 'alert' | 'nudge' | 'escalation' | 'note'
    severity        TEXT NOT NULL DEFAULT 'info',
    -- 'info' | 'warning' | 'critical'
    title           TEXT NOT NULL,
    body            TEXT,
    payload_json    TEXT,
    resolved_at     REAL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cse_org_time
    ON cs_events(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cse_unresolved
    ON cs_events(kind, severity, resolved_at);

CREATE TABLE IF NOT EXISTS renewal_pipeline (
    org_id              TEXT PRIMARY KEY,
    plan_tier           TEXT NOT NULL,
    current_period_end  REAL NOT NULL,
    predicted_renewal   REAL NOT NULL DEFAULT 0.5,  -- 0..1
    churn_risk          TEXT NOT NULL DEFAULT 'low', -- 'low' | 'medium' | 'high'
    last_action_at      REAL,
    last_action_kind    TEXT,
    notes               TEXT,
    updated_at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rp_period
    ON renewal_pipeline(current_period_end);
CREATE INDEX IF NOT EXISTS idx_rp_risk
    ON renewal_pipeline(churn_risk, current_period_end);
"""


VALID_EVENT_KINDS = frozenset({
    "onboarding_step", "alert", "nudge", "escalation", "note",
})

VALID_SEVERITIES = frozenset({"info", "warning", "critical"})

VALID_CHURN_RISKS = frozenset({"low", "medium", "high"})


# Health-score thresholds — tunable per growth-stage of the company.
HEALTH_THRESHOLDS = {
    "healthy": 75,
    "watch": 50,
    "at_risk": 25,
}


# Default onboarding step sequence — 7 nudges over 14 days.
ONBOARDING_STEPS = [
    {"step": "welcome", "day_offset": 0,
     "title": "Welcome to AI Pathshala",
     "body": "Set up your school profile + invite 1 admin teammate."},
    {"step": "first_class", "day_offset": 1,
     "title": "Create your first class",
     "body": "Add 5 students to see attendance + assignments roll."},
    {"step": "first_lesson", "day_offset": 3,
     "title": "Generate your first lesson",
     "body": "Upload a textbook page → get a 5-min video in any language."},
    {"step": "branding", "day_offset": 5,
     "title": "Add your school branding",
     "body": "Upload your logo + pick colors. Takes 2 minutes."},
    {"step": "parent_link", "day_offset": 7,
     "title": "Invite parents",
     "body": "Share a code; parents see their child's progress in real time."},
    {"step": "live_class", "day_offset": 10,
     "title": "Try a live class",
     "body": "Schedule a 15-min session. We host the video + chat."},
    {"step": "feedback", "day_offset": 14,
     "title": "Two-week check-in",
     "body": "How's it going? Reply to this nudge with a thumb."},
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
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class HealthScore:
    id: str
    org_id: str
    score: float
    components: dict          # {engagement, payment, support, growth}
    computed_at: float
    period_days: int
    band: str                 # 'healthy' | 'watch' | 'at_risk' | 'critical'


@dataclass(frozen=True)
class CSEvent:
    id: str
    org_id: str | None
    user_id: str | None
    kind: str
    severity: str
    title: str
    body: str | None
    payload: dict | None
    resolved_at: float | None
    created_at: float


@dataclass(frozen=True)
class RenewalEntry:
    org_id: str
    plan_tier: str
    current_period_end: float
    predicted_renewal: float
    churn_risk: str
    last_action_at: float | None
    last_action_kind: str | None
    notes: str | None
    updated_at: float


# ---------- health scoring ----------

def _band(score: float) -> str:
    if score >= HEALTH_THRESHOLDS["healthy"]:
        return "healthy"
    if score >= HEALTH_THRESHOLDS["watch"]:
        return "watch"
    if score >= HEALTH_THRESHOLDS["at_risk"]:
        return "at_risk"
    return "critical"


def compute_health(
    *,
    org_id: str,
    period_days: int = 30,
) -> HealthScore:
    """Read existing tables to compute the four health components
    + persist a snapshot. Each component scaled to 0..25; total 0..100.

    Engagement (0..25): active members / total members in the period
      pulled from events table when present, else fallback to assignment
      completions.

    Payment (0..25): 25 if no overdue invoices, scales down with each.

    Support (0..25): placeholder (defaults to 20) until ticketing
    module lands.

    Growth (0..25): new members + new lessons in this period.
    """
    if period_days < 1 or period_days > 365:
        raise ValueError("period_days must be in [1, 365]")
    since = time.time() - period_days * 86400
    engagement = _component_engagement(org_id, since)
    payment = _component_payment(org_id)
    support = _component_support(org_id, since)
    growth = _component_growth(org_id, since)
    score = engagement + payment + support + growth
    components = {
        "engagement": round(engagement, 2),
        "payment": round(payment, 2),
        "support": round(support, 2),
        "growth": round(growth, 2),
    }
    hid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_health_scores "
            "(id, org_id, score, components_json, computed_at, "
            " period_days) VALUES (?, ?, ?, ?, ?, ?)",
            (hid, org_id, round(score, 2),
             json.dumps(components), now, period_days),
        )
    return HealthScore(
        id=hid, org_id=org_id, score=round(score, 2),
        components=components,
        computed_at=now, period_days=period_days,
        band=_band(score),
    )


def _component_engagement(org_id: str, since: float) -> float:
    """Max 25. Uses Q3 events when available; falls back to org_member
    last_active proxy."""
    try:
        with _conn() as conn:
            r = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM events "
                "WHERE org_id = ? AND created_at >= ?",
                (org_id, since),
            ).fetchone()
            active = int(r[0] or 0) if r else 0
            r2 = conn.execute(
                "SELECT COUNT(*) FROM org_members WHERE org_id = ?",
                (org_id,),
            ).fetchone()
            total = int(r2[0] or 0) if r2 else 0
    except sqlite3.OperationalError:
        return 12.0  # neutral fallback when tables haven't loaded
    if total == 0:
        return 0.0
    ratio = min(1.0, active / total)
    return round(ratio * 25, 2)


def _component_payment(org_id: str) -> float:
    """Max 25. Counts overdue invoices from E5 fees module. Returns
    25 - 5×overdue, floor 0."""
    try:
        with _conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM org_fee_invoices "
                "WHERE org_id = ? AND status = 'overdue'",
                (org_id,),
            ).fetchone()
            overdue = int(r[0] or 0) if r else 0
    except sqlite3.OperationalError:
        return 20.0
    return max(0.0, 25.0 - 5.0 * overdue)


def _component_support(org_id: str, since: float) -> float:
    """Max 25. Counts critical CS events in the window — each one
    reduces score. No critical events = full 25."""
    try:
        with _conn() as conn:
            r = conn.execute(
                "SELECT COUNT(*) FROM cs_events "
                "WHERE org_id = ? AND severity = 'critical' "
                "AND created_at >= ? AND resolved_at IS NULL",
                (org_id, since),
            ).fetchone()
            crits = int(r[0] or 0) if r else 0
    except sqlite3.OperationalError:
        return 20.0
    return max(0.0, 25.0 - 4.0 * crits)


def _component_growth(org_id: str, since: float) -> float:
    """Max 25. New members + lessons rendered in window. Logarithmic
    scaling so a few moves the needle but it asymptotes."""
    import math
    try:
        with _conn() as conn:
            r1 = conn.execute(
                "SELECT COUNT(*) FROM org_members "
                "WHERE org_id = ? AND joined_at >= ?",
                (org_id, since),
            ).fetchone()
            new_members = int(r1[0] or 0) if r1 else 0
            r2 = conn.execute(
                "SELECT COUNT(*) FROM events "
                "WHERE org_id = ? AND kind = 'lesson.start' "
                "AND created_at >= ?",
                (org_id, since),
            ).fetchone()
            lessons = int(r2[0] or 0) if r2 else 0
    except sqlite3.OperationalError:
        return 10.0
    raw = math.log1p(new_members * 2 + lessons)   # log scaling
    return round(min(25.0, raw * 5.0), 2)


def latest_health(org_id: str) -> HealthScore | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id, score, components_json, "
            "       computed_at, period_days "
            "FROM org_health_scores WHERE org_id = ? "
            "ORDER BY computed_at DESC LIMIT 1",
            (org_id,),
        ).fetchone()
    if not r:
        return None
    return HealthScore(
        id=r[0], org_id=r[1], score=r[2],
        components=json.loads(r[3]),
        computed_at=r[4], period_days=r[5],
        band=_band(r[2]),
    )


def at_risk_orgs(*, threshold: float = 50.0, limit: int = 50) -> list[HealthScore]:
    """Returns the latest snapshot per org, filtered to scores below
    threshold. Drives the CSM's daily attention queue."""
    limit = max(1, min(limit, 500))
    # Subquery: latest snapshot per org
    with _conn() as conn:
        rows = conn.execute(
            "SELECT h.id, h.org_id, h.score, h.components_json, "
            "       h.computed_at, h.period_days "
            "FROM org_health_scores h "
            "JOIN ("
            "  SELECT org_id, MAX(computed_at) AS latest "
            "  FROM org_health_scores GROUP BY org_id"
            ") latest ON latest.org_id = h.org_id "
            " AND latest.latest = h.computed_at "
            "WHERE h.score < ? "
            "ORDER BY h.score LIMIT ?",
            (threshold, limit),
        ).fetchall()
    return [
        HealthScore(
            id=r[0], org_id=r[1], score=r[2],
            components=json.loads(r[3]),
            computed_at=r[4], period_days=r[5],
            band=_band(r[2]),
        )
        for r in rows
    ]


# ---------- CS events (alerts / nudges / notes) ----------

def log_event(
    *,
    kind: str,
    title: str,
    org_id: str | None = None,
    user_id: str | None = None,
    severity: str = "info",
    body: str | None = None,
    payload: dict | None = None,
) -> CSEvent:
    if kind not in VALID_EVENT_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_EVENT_KINDS)}")
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be in {sorted(VALID_SEVERITIES)}")
    if not title.strip():
        raise ValueError("title required")
    eid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO cs_events "
            "(id, org_id, user_id, kind, severity, title, body, "
            " payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, org_id, user_id, kind, severity, title.strip(),
             body, json.dumps(payload) if payload else None, now),
        )
    return _get_event(eid)


def _get_event(event_id: str) -> CSEvent:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id, user_id, kind, severity, title, body, "
            "       payload_json, resolved_at, created_at "
            "FROM cs_events WHERE id = ?", (event_id,),
        ).fetchone()
    return CSEvent(
        id=r[0], org_id=r[1], user_id=r[2], kind=r[3],
        severity=r[4], title=r[5], body=r[6],
        payload=json.loads(r[7]) if r[7] else None,
        resolved_at=r[8], created_at=r[9],
    )


def resolve_event(event_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE cs_events SET resolved_at = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (time.time(), event_id),
        )
        return cur.rowcount > 0


def list_events_for_org(
    *,
    org_id: str,
    unresolved_only: bool = False,
    limit: int = 50,
) -> list[CSEvent]:
    limit = max(1, min(limit, 500))
    sql = (
        "SELECT id, org_id, user_id, kind, severity, title, body, "
        "       payload_json, resolved_at, created_at "
        "FROM cs_events WHERE org_id = ?"
    )
    params: list = [org_id]
    if unresolved_only:
        sql += " AND resolved_at IS NULL"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        CSEvent(
            id=r[0], org_id=r[1], user_id=r[2], kind=r[3],
            severity=r[4], title=r[5], body=r[6],
            payload=json.loads(r[7]) if r[7] else None,
            resolved_at=r[8], created_at=r[9],
        )
        for r in rows
    ]


def unresolved_alerts(*, limit: int = 50) -> list[CSEvent]:
    """Global CSM queue — unresolved alerts + escalations across all
    orgs, severity desc then age asc."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, org_id, user_id, kind, severity, title, body, "
            "       payload_json, resolved_at, created_at "
            "FROM cs_events WHERE resolved_at IS NULL "
            "AND kind IN ('alert', 'escalation') "
            "ORDER BY CASE severity WHEN 'critical' THEN 0 "
            "  WHEN 'warning' THEN 1 ELSE 2 END, "
            "  created_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        CSEvent(
            id=r[0], org_id=r[1], user_id=r[2], kind=r[3],
            severity=r[4], title=r[5], body=r[6],
            payload=json.loads(r[7]) if r[7] else None,
            resolved_at=r[8], created_at=r[9],
        )
        for r in rows
    ]


# ---------- onboarding sequence ----------

def get_onboarding_steps() -> list[dict]:
    """Public catalog. Caller (cron job) iterates + emits a nudge
    event when day_offset matches the org's age."""
    return list(ONBOARDING_STEPS)


def emit_onboarding_step(
    *, org_id: str, step_key: str,
) -> CSEvent:
    """Idempotency: caller checks `list_events_for_org()` for an
    existing 'onboarding_step' event with this step_key before
    calling. We don't enforce uniqueness at the DB to allow rare
    re-prompts on parent feedback."""
    step = next(
        (s for s in ONBOARDING_STEPS if s["step"] == step_key), None,
    )
    if not step:
        raise ValueError(f"unknown onboarding step {step_key!r}")
    return log_event(
        kind="onboarding_step", severity="info",
        title=step["title"], body=step["body"],
        org_id=org_id,
        payload={"step_key": step_key,
                 "day_offset": step["day_offset"]},
    )


# ---------- renewal pipeline ----------

def upsert_renewal(
    *,
    org_id: str,
    plan_tier: str,
    current_period_end: float,
    predicted_renewal: float | None = None,
    churn_risk: str | None = None,
    notes: str | None = None,
) -> RenewalEntry:
    """Upsert the pipeline entry. `predicted_renewal` defaults to a
    derived value from the latest health score; `churn_risk` is
    derived from the band."""
    if predicted_renewal is None or churn_risk is None:
        hs = latest_health(org_id)
        if hs:
            predicted_renewal = predicted_renewal or round(
                hs.score / 100.0, 2,
            )
            churn_risk = churn_risk or (
                "low" if hs.score >= 75
                else "medium" if hs.score >= 50
                else "high"
            )
        else:
            predicted_renewal = predicted_renewal or 0.5
            churn_risk = churn_risk or "medium"
    if churn_risk not in VALID_CHURN_RISKS:
        raise ValueError(f"churn_risk must be in {sorted(VALID_CHURN_RISKS)}")
    predicted_renewal = max(0.0, min(1.0, predicted_renewal))
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO renewal_pipeline "
            "(org_id, plan_tier, current_period_end, "
            " predicted_renewal, churn_risk, notes, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(org_id) DO UPDATE SET "
            " plan_tier = excluded.plan_tier, "
            " current_period_end = excluded.current_period_end, "
            " predicted_renewal = excluded.predicted_renewal, "
            " churn_risk = excluded.churn_risk, "
            " notes = COALESCE(excluded.notes, renewal_pipeline.notes), "
            " updated_at = excluded.updated_at",
            (org_id, plan_tier, current_period_end,
             predicted_renewal, churn_risk, notes, now),
        )
    return _get_renewal(org_id)  # type: ignore[return-value]


def _get_renewal(org_id: str) -> RenewalEntry | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT org_id, plan_tier, current_period_end, "
            "       predicted_renewal, churn_risk, last_action_at, "
            "       last_action_kind, notes, updated_at "
            "FROM renewal_pipeline WHERE org_id = ?",
            (org_id,),
        ).fetchone()
    if not r:
        return None
    return RenewalEntry(
        org_id=r[0], plan_tier=r[1], current_period_end=r[2],
        predicted_renewal=r[3], churn_risk=r[4],
        last_action_at=r[5], last_action_kind=r[6],
        notes=r[7], updated_at=r[8],
    )


def get_renewal(org_id: str) -> RenewalEntry | None:
    return _get_renewal(org_id)


def record_renewal_action(
    *,
    org_id: str,
    action_kind: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE renewal_pipeline SET "
            " last_action_at = ?, last_action_kind = ? "
            "WHERE org_id = ?",
            (time.time(), action_kind, org_id),
        )
        return cur.rowcount > 0


def upcoming_renewals(*, days_ahead: int = 90, limit: int = 100) -> list[RenewalEntry]:
    """Orgs with current_period_end within the next N days, sorted by
    churn_risk desc then deadline asc."""
    limit = max(1, min(limit, 1000))
    end = time.time() + days_ahead * 86400
    with _conn() as conn:
        rows = conn.execute(
            "SELECT org_id, plan_tier, current_period_end, "
            "       predicted_renewal, churn_risk, last_action_at, "
            "       last_action_kind, notes, updated_at "
            "FROM renewal_pipeline "
            "WHERE current_period_end <= ? "
            "ORDER BY CASE churn_risk WHEN 'high' THEN 0 "
            "  WHEN 'medium' THEN 1 ELSE 2 END, "
            "  current_period_end LIMIT ?",
            (end, limit),
        ).fetchall()
    return [
        RenewalEntry(
            org_id=r[0], plan_tier=r[1], current_period_end=r[2],
            predicted_renewal=r[3], churn_risk=r[4],
            last_action_at=r[5], last_action_kind=r[6],
            notes=r[7], updated_at=r[8],
        )
        for r in rows
    ]
