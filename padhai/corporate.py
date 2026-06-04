"""R1 — Corporate training mode.

Reuses the lesson generator + assessment engine for L&D in Indian
IT (Infosys, TCS, Wipro, etc.) — ~3M employees/year, ₹1k/employee
TAM = ₹3000 Cr.

Engineering scope:
  - Different "org kind" tag (corporate vs school)
  - Per-employee enrollment + completion tracking
  - LMS-style integrations: SCORM 1.2 (legacy) + xAPI (modern)

Four tables:
  corporate_orgs        the company itself (separate from school orgs
                        in the v0.9 orgs table — different shape)
  training_paths        a learning path = ordered list of modules
  enrollments           per-employee in a path with progress
  xapi_statements       xAPI event log for LMS hand-off

xAPI verb catalog used here:
  experienced / attempted / completed / passed / failed
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
CREATE TABLE IF NOT EXISTS corporate_orgs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    industry        TEXT,
    headcount       INTEGER,
    contact_name    TEXT,
    contact_email   TEXT,
    integration_kind TEXT NOT NULL DEFAULT 'api',
    -- 'api' | 'scorm' | 'xapi' | 'lti' | 'sso_saml'
    api_key_hash    TEXT,
    seat_limit      INTEGER NOT NULL DEFAULT 100,
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'paused' | 'cancelled'
    created_at      REAL NOT NULL,
    UNIQUE (name)
);
CREATE INDEX IF NOT EXISTS idx_corp_status
    ON corporate_orgs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS training_paths (
    id              TEXT PRIMARY KEY,
    corp_org_id     TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    category        TEXT,                     -- 'compliance' | 'sales' | 'tech' | 'leadership' | 'onboarding'
    modules_json    TEXT NOT NULL,             -- [{title, kind, ref_id, est_min}]
    duration_min    INTEGER,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'published' | 'archived'
    created_by      TEXT,
    created_at      REAL NOT NULL,
    published_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_tp_corp
    ON training_paths(corp_org_id, status);
CREATE INDEX IF NOT EXISTS idx_tp_category
    ON training_paths(status, category);

CREATE TABLE IF NOT EXISTS enrollments (
    id              TEXT PRIMARY KEY,
    path_id         TEXT NOT NULL,
    employee_user_id TEXT NOT NULL,
    enrolled_at     REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    completion_pct  REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'enrolled',
    -- 'enrolled' | 'in_progress' | 'completed' | 'failed' | 'withdrawn'
    final_score     REAL,
    UNIQUE (path_id, employee_user_id)
);
CREATE INDEX IF NOT EXISTS idx_enr_path
    ON enrollments(path_id, status);
CREATE INDEX IF NOT EXISTS idx_enr_emp
    ON enrollments(employee_user_id, enrolled_at DESC);

CREATE TABLE IF NOT EXISTS xapi_statements (
    id              TEXT PRIMARY KEY,
    enrollment_id   TEXT NOT NULL,
    actor_user_id   TEXT NOT NULL,
    verb            TEXT NOT NULL,
    -- 'experienced' | 'attempted' | 'completed' | 'passed' | 'failed' | 'progressed'
    object_id       TEXT NOT NULL,             -- module id or activity id
    object_kind     TEXT,                       -- 'module' | 'quiz' | 'video' | 'path'
    result_json     TEXT,                       -- {score, success, completion, duration}
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xs_enr_time
    ON xapi_statements(enrollment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xs_actor
    ON xapi_statements(actor_user_id, created_at DESC);
"""


VALID_INTEGRATIONS = frozenset({
    "api", "scorm", "xapi", "lti", "sso_saml",
})

VALID_STATUSES = frozenset({"active", "paused", "cancelled"})

VALID_PATH_STATUSES = frozenset({"draft", "published", "archived"})

VALID_ENR_STATUSES = frozenset({
    "enrolled", "in_progress", "completed", "failed", "withdrawn",
})

VALID_VERBS = frozenset({
    "experienced", "attempted", "completed",
    "passed", "failed", "progressed",
})

VALID_CATEGORIES = frozenset({
    "compliance", "sales", "tech", "leadership", "onboarding",
    "diversity", "security", "soft_skills",
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
class CorporateOrg:
    id: str
    name: str
    industry: str | None
    headcount: int | None
    contact_name: str | None
    contact_email: str | None
    integration_kind: str
    seat_limit: int
    status: str
    created_at: float


@dataclass(frozen=True)
class TrainingPath:
    id: str
    corp_org_id: str
    title: str
    description: str | None
    category: str | None
    modules: list[dict]
    duration_min: int | None
    status: str
    created_by: str | None
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Enrollment:
    id: str
    path_id: str
    employee_user_id: str
    enrolled_at: float
    started_at: float | None
    completed_at: float | None
    completion_pct: float
    status: str
    final_score: float | None


# ---------- corporate org CRUD ----------

def register_corp_org(
    *,
    name: str,
    industry: str | None = None,
    headcount: int | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    integration_kind: str = "api",
    seat_limit: int = 100,
) -> CorporateOrg:
    name = (name or "").strip()
    if len(name) < 2 or len(name) > 200:
        raise ValueError("name must be [2, 200] chars")
    if integration_kind not in VALID_INTEGRATIONS:
        raise ValueError(
            f"integration_kind must be in {sorted(VALID_INTEGRATIONS)}",
        )
    if seat_limit < 1 or seat_limit > 1_000_000:
        raise ValueError("seat_limit must be in [1, 1000000]")
    cid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO corporate_orgs "
                "(id, name, industry, headcount, contact_name, "
                " contact_email, integration_kind, seat_limit, "
                " status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (cid, name, industry, headcount, contact_name,
                 contact_email, integration_kind, seat_limit,
                 time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"corporate org {name!r} already exists") from e
    return get_corp_org(cid)  # type: ignore[return-value]


def get_corp_org(corp_id: str) -> CorporateOrg | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, name, industry, headcount, contact_name, "
            "       contact_email, integration_kind, seat_limit, "
            "       status, created_at "
            "FROM corporate_orgs WHERE id = ?", (corp_id,),
        ).fetchone()
    if not r:
        return None
    return CorporateOrg(
        id=r[0], name=r[1], industry=r[2], headcount=r[3],
        contact_name=r[4], contact_email=r[5],
        integration_kind=r[6], seat_limit=r[7],
        status=r[8], created_at=r[9],
    )


def list_corp_orgs(
    *, status: str | None = None,
) -> list[CorporateOrg]:
    sql = (
        "SELECT id, name, industry, headcount, contact_name, "
        "       contact_email, integration_kind, seat_limit, "
        "       status, created_at "
        "FROM corporate_orgs"
    )
    params: list = []
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
        sql += " WHERE status = ?"; params.append(status)
    sql += " ORDER BY headcount DESC, name"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        CorporateOrg(
            id=r[0], name=r[1], industry=r[2], headcount=r[3],
            contact_name=r[4], contact_email=r[5],
            integration_kind=r[6], seat_limit=r[7],
            status=r[8], created_at=r[9],
        )
        for r in rows
    ]


# ---------- training paths ----------

def create_path(
    *,
    corp_org_id: str,
    title: str,
    modules: list[dict],
    description: str | None = None,
    category: str | None = None,
    duration_min: int | None = None,
    created_by: str | None = None,
) -> TrainingPath:
    if not get_corp_org(corp_org_id):
        raise ValueError("corporate org not found")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if not modules or len(modules) > 100:
        raise ValueError("modules must be [1, 100] entries")
    if category and category not in VALID_CATEGORIES:
        raise ValueError(f"category must be in {sorted(VALID_CATEGORIES)}")
    # Normalize each module
    normalized = []
    for m in modules:
        if not isinstance(m, dict):
            continue
        normalized.append({
            "id": m.get("id") or uuid.uuid4().hex[:12],
            "title": (m.get("title") or "Module")[:200],
            "kind": m.get("kind", "lesson"),
            "ref_id": m.get("ref_id"),
            "est_min": int(m.get("est_min", 10)),
        })
    if not normalized:
        raise ValueError("no valid modules provided")
    if duration_min is None:
        duration_min = sum(m["est_min"] for m in normalized)
    pid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO training_paths "
            "(id, corp_org_id, title, description, category, "
            " modules_json, duration_min, status, created_by, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)",
            (pid, corp_org_id, title, description, category,
             json.dumps(normalized), duration_min,
             created_by, time.time()),
        )
    return get_path(pid)  # type: ignore[return-value]


def get_path(path_id: str) -> TrainingPath | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, corp_org_id, title, description, category, "
            "       modules_json, duration_min, status, "
            "       created_by, created_at, published_at "
            "FROM training_paths WHERE id = ?", (path_id,),
        ).fetchone()
    if not r:
        return None
    return TrainingPath(
        id=r[0], corp_org_id=r[1], title=r[2], description=r[3],
        category=r[4],
        modules=json.loads(r[5]) if r[5] else [],
        duration_min=r[6], status=r[7],
        created_by=r[8], created_at=r[9], published_at=r[10],
    )


def list_paths(
    *,
    corp_org_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
) -> list[TrainingPath]:
    sql = (
        "SELECT id, corp_org_id, title, description, category, "
        "       modules_json, duration_min, status, "
        "       created_by, created_at, published_at "
        "FROM training_paths WHERE 1=1"
    )
    params: list = []
    if corp_org_id:
        sql += " AND corp_org_id = ?"; params.append(corp_org_id)
    if status:
        if status not in VALID_PATH_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_PATH_STATUSES)}"
            )
        sql += " AND status = ?"; params.append(status)
    if category:
        sql += " AND category = ?"; params.append(category)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        TrainingPath(
            id=r[0], corp_org_id=r[1], title=r[2], description=r[3],
            category=r[4],
            modules=json.loads(r[5]) if r[5] else [],
            duration_min=r[6], status=r[7],
            created_by=r[8], created_at=r[9], published_at=r[10],
        )
        for r in rows
    ]


def publish_path(*, path_id: str) -> bool:
    p = get_path(path_id)
    if not p:
        return False
    if p.status != "draft":
        return False
    if not p.modules:
        raise ValueError("path has no modules")
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE training_paths SET "
            " status = 'published', published_at = ? "
            "WHERE id = ? AND status = 'draft'",
            (now, path_id),
        )
    return True


# ---------- enrollments ----------

def enroll(
    *,
    path_id: str,
    employee_user_id: str,
) -> Enrollment:
    p = get_path(path_id)
    if not p:
        raise ValueError("path not found")
    if p.status != "published":
        raise ValueError(f"path status is {p.status!r}, not enrollable")
    # Seat-limit check
    corp = get_corp_org(p.corp_org_id)
    if not corp:
        raise ValueError("corporate org missing")
    if corp.status != "active":
        raise ValueError(f"corp status is {corp.status!r}")
    eid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            # Enforce seat limit across all paths for this corp
            r = conn.execute(
                "SELECT COUNT(DISTINCT e.employee_user_id) "
                "FROM enrollments e "
                "JOIN training_paths p ON p.id = e.path_id "
                "WHERE p.corp_org_id = ? "
                "AND e.status != 'withdrawn'",
                (p.corp_org_id,),
            ).fetchone()
            current_seats = int(r[0] or 0)
            # Allow re-enrollment of existing employees but cap new ones
            r2 = conn.execute(
                "SELECT 1 FROM enrollments e "
                "JOIN training_paths p ON p.id = e.path_id "
                "WHERE p.corp_org_id = ? AND e.employee_user_id = ?",
                (p.corp_org_id, employee_user_id),
            ).fetchone()
            is_new_seat = r2 is None
            if is_new_seat and current_seats >= corp.seat_limit:
                raise ValueError(
                    f"seat limit reached "
                    f"({current_seats}/{corp.seat_limit}) — "
                    "contact sales to expand",
                )
            conn.execute(
                "INSERT INTO enrollments "
                "(id, path_id, employee_user_id, enrolled_at, status) "
                "VALUES (?, ?, ?, ?, 'enrolled')",
                (eid, path_id, employee_user_id, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"already enrolled in path {path_id!r}",
        ) from e
    return get_enrollment(eid)  # type: ignore[return-value]


def get_enrollment(enrollment_id: str) -> Enrollment | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, path_id, employee_user_id, enrolled_at, "
            "       started_at, completed_at, completion_pct, "
            "       status, final_score "
            "FROM enrollments WHERE id = ?", (enrollment_id,),
        ).fetchone()
    if not r:
        return None
    return Enrollment(
        id=r[0], path_id=r[1], employee_user_id=r[2],
        enrolled_at=r[3], started_at=r[4], completed_at=r[5],
        completion_pct=r[6], status=r[7], final_score=r[8],
    )


def list_enrollments_for_path(path_id: str) -> list[Enrollment]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, path_id, employee_user_id, enrolled_at, "
            "       started_at, completed_at, completion_pct, "
            "       status, final_score "
            "FROM enrollments WHERE path_id = ? "
            "ORDER BY enrolled_at DESC", (path_id,),
        ).fetchall()
    return [
        Enrollment(
            id=r[0], path_id=r[1], employee_user_id=r[2],
            enrolled_at=r[3], started_at=r[4], completed_at=r[5],
            completion_pct=r[6], status=r[7], final_score=r[8],
        )
        for r in rows
    ]


def update_progress(
    *,
    enrollment_id: str,
    completion_pct: float,
    final_score: float | None = None,
) -> Enrollment:
    e = get_enrollment(enrollment_id)
    if not e:
        raise ValueError("enrollment not found")
    completion_pct = max(0.0, min(100.0, completion_pct))
    now = time.time()
    new_status = e.status
    started_at = e.started_at
    completed_at = e.completed_at
    if e.status == "enrolled" and completion_pct > 0:
        new_status = "in_progress"
        started_at = started_at or now
    if completion_pct >= 100 and e.status != "completed":
        new_status = "completed"
        completed_at = completed_at or now
    with _conn() as conn:
        conn.execute(
            "UPDATE enrollments SET "
            " completion_pct = ?, status = ?, "
            " started_at = COALESCE(?, started_at), "
            " completed_at = COALESCE(?, completed_at), "
            " final_score = COALESCE(?, final_score) "
            "WHERE id = ?",
            (completion_pct, new_status, started_at, completed_at,
             final_score, enrollment_id),
        )
    return get_enrollment(enrollment_id)  # type: ignore[return-value]


def withdraw(*, enrollment_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE enrollments SET status = 'withdrawn' "
            "WHERE id = ? AND status IN ('enrolled', 'in_progress')",
            (enrollment_id,),
        )
        return cur.rowcount > 0


# ---------- xAPI statements ----------

def emit_xapi(
    *,
    enrollment_id: str,
    actor_user_id: str,
    verb: str,
    object_id: str,
    object_kind: str | None = None,
    result: dict | None = None,
) -> str:
    """Record an xAPI statement. Caller is responsible for calling
    update_progress() separately when verb is 'completed' / 'passed'
    / 'failed' — keeping them separate makes replay debugging easier."""
    if verb not in VALID_VERBS:
        raise ValueError(f"verb must be in {sorted(VALID_VERBS)}")
    if not get_enrollment(enrollment_id):
        raise ValueError("enrollment not found")
    sid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO xapi_statements "
            "(id, enrollment_id, actor_user_id, verb, object_id, "
            " object_kind, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, enrollment_id, actor_user_id, verb, object_id,
             object_kind, json.dumps(result) if result else None,
             time.time()),
        )
    return sid


def xapi_statements_for_enrollment(
    enrollment_id: str, *, limit: int = 100,
) -> list[dict]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, enrollment_id, actor_user_id, verb, "
            "       object_id, object_kind, result_json, created_at "
            "FROM xapi_statements WHERE enrollment_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (enrollment_id, limit),
        ).fetchall()
    return [
        {"id": r[0], "enrollment_id": r[1],
         "actor_user_id": r[2], "verb": r[3],
         "object_id": r[4], "object_kind": r[5],
         "result": json.loads(r[6]) if r[6] else None,
         "created_at": r[7]}
        for r in rows
    ]


def org_completion_stats(corp_org_id: str) -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM training_paths "
            "WHERE corp_org_id = ?", (corp_org_id,),
        ).fetchone()
        path_count = int(r[0] or 0)
        rows = conn.execute(
            "SELECT e.status, COUNT(*), AVG(e.completion_pct), "
            "       AVG(e.final_score) "
            "FROM enrollments e "
            "JOIN training_paths p ON p.id = e.path_id "
            "WHERE p.corp_org_id = ? "
            "GROUP BY e.status",
            (corp_org_id,),
        ).fetchall()
    by_status = {}
    for r in rows:
        by_status[r[0]] = {
            "count": int(r[1] or 0),
            "avg_completion_pct": round(r[2] or 0, 2),
            "avg_final_score": (
                round(r[3], 2) if r[3] is not None else None
            ),
        }
    return {
        "corp_org_id": corp_org_id,
        "paths": path_count,
        "by_status": by_status,
    }
