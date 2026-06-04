"""R2 — University / NPTEL / IGNOU extension.

We host AI Pathshala's content engine; universities + govt MOOC
platforms (NPTEL, IGNOU, DU correspondence) plug in to use it.
Each partner contracts a course list + integration kind
(LTI 1.3 for LMS embedding, REST API for course catalog sync,
SAML for SSO).

Different from R1 (corporate L&D — closed-tenant orgs) and O2
(content publishers — publishing TO us). R2 is **us publishing
INTO partner platforms**. Their LMS, their student account, our
content.

Three tables:
  university_partners        per-university contract + tenant cfg
  partner_courses            individual course offerings (n per partner)
  partner_enrollments        student enrollment tracked back to partner

Lifecycle:
  partner:   prospect → contracted → live → paused
  course:    draft → published → archived
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
CREATE TABLE IF NOT EXISTS university_partners (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    kind                TEXT NOT NULL,           -- 'nptel' | 'ignou' | 'du' | 'mooc' | 'university'
    integration_kind    TEXT NOT NULL,           -- 'lti13' | 'rest_api' | 'saml_sso' | 'embed'
    contact_email       TEXT,
    contract_value_inr  INTEGER,
    contracted_students INTEGER,                  -- est. seat count under contract
    revenue_share_pct   REAL NOT NULL DEFAULT 0.30,
    status              TEXT NOT NULL DEFAULT 'prospect',
    -- 'prospect' | 'contracted' | 'live' | 'paused'
    lti_client_id       TEXT,                    -- if integration_kind = 'lti13'
    lti_deployment_id   TEXT,
    api_token_hash      TEXT,                    -- bcrypt for REST integrations
    notes               TEXT,
    created_at          REAL NOT NULL,
    contracted_at       REAL,
    UNIQUE (name)
);
CREATE INDEX IF NOT EXISTS idx_upart_status
    ON university_partners(status, contracted_students DESC);

CREATE TABLE IF NOT EXISTS partner_courses (
    id                  TEXT PRIMARY KEY,
    partner_id          TEXT NOT NULL,
    course_code         TEXT NOT NULL,           -- their code, e.g. 'NPTEL_CS101'
    title               TEXT NOT NULL,
    description         TEXT,
    duration_weeks      INTEGER,
    credits             INTEGER,
    lesson_manifest_url TEXT,                    -- our lesson IDs/storage refs
    status              TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'published' | 'archived'
    enrollment_count    INTEGER NOT NULL DEFAULT 0,
    completion_count    INTEGER NOT NULL DEFAULT 0,
    created_at          REAL NOT NULL,
    published_at        REAL,
    UNIQUE (partner_id, course_code)
);
CREATE INDEX IF NOT EXISTS idx_pcourse_partner
    ON partner_courses(partner_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS partner_enrollments (
    id                  TEXT PRIMARY KEY,
    course_id           TEXT NOT NULL,
    partner_id          TEXT NOT NULL,
    partner_student_id  TEXT NOT NULL,           -- THEIR student id, not ours
    our_user_id         TEXT,                    -- linked padhai user if SSO'd
    status              TEXT NOT NULL DEFAULT 'enrolled',
    -- 'enrolled' | 'in_progress' | 'completed' | 'withdrawn'
    completion_pct      REAL NOT NULL DEFAULT 0,
    final_score         REAL,
    enrolled_at         REAL NOT NULL,
    started_at          REAL,
    completed_at        REAL,
    UNIQUE (course_id, partner_student_id)
);
CREATE INDEX IF NOT EXISTS idx_penroll_course
    ON partner_enrollments(course_id, status);
CREATE INDEX IF NOT EXISTS idx_penroll_partner
    ON partner_enrollments(partner_id, enrolled_at DESC);
"""


VALID_PARTNER_KINDS = frozenset({
    "nptel", "ignou", "du", "mooc", "university",
})

VALID_INTEGRATION_KINDS = frozenset({
    "lti13", "rest_api", "saml_sso", "embed",
})

VALID_PARTNER_STATUSES = frozenset({
    "prospect", "contracted", "live", "paused",
})

VALID_COURSE_STATUSES = frozenset({"draft", "published", "archived"})

VALID_ENROLLMENT_STATUSES = frozenset({
    "enrolled", "in_progress", "completed", "withdrawn",
})


# Sane bounds — partner contracts at 30% revenue share by default
# (NPTEL's standard), bounded to keep audits sane.
MIN_REV_SHARE = 0.10
MAX_REV_SHARE = 0.70


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
class Partner:
    id: str
    name: str
    kind: str
    integration_kind: str
    contact_email: str | None
    contract_value_inr: int | None
    contracted_students: int | None
    revenue_share_pct: float
    status: str
    lti_client_id: str | None
    lti_deployment_id: str | None
    notes: str | None
    created_at: float
    contracted_at: float | None


@dataclass(frozen=True)
class Course:
    id: str
    partner_id: str
    course_code: str
    title: str
    description: str | None
    duration_weeks: int | None
    credits: int | None
    lesson_manifest_url: str | None
    status: str
    enrollment_count: int
    completion_count: int
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Enrollment:
    id: str
    course_id: str
    partner_id: str
    partner_student_id: str
    our_user_id: str | None
    status: str
    completion_pct: float
    final_score: float | None
    enrolled_at: float
    started_at: float | None
    completed_at: float | None


_P_SEL = (
    "SELECT id, name, kind, integration_kind, contact_email, "
    "       contract_value_inr, contracted_students, "
    "       revenue_share_pct, status, lti_client_id, "
    "       lti_deployment_id, notes, created_at, contracted_at "
    "FROM university_partners"
)

_C_SEL = (
    "SELECT id, partner_id, course_code, title, description, "
    "       duration_weeks, credits, lesson_manifest_url, status, "
    "       enrollment_count, completion_count, created_at, "
    "       published_at "
    "FROM partner_courses"
)

_E_SEL = (
    "SELECT id, course_id, partner_id, partner_student_id, "
    "       our_user_id, status, completion_pct, final_score, "
    "       enrolled_at, started_at, completed_at "
    "FROM partner_enrollments"
)


def _row_to_partner(r) -> Partner:
    return Partner(
        id=r[0], name=r[1], kind=r[2],
        integration_kind=r[3], contact_email=r[4],
        contract_value_inr=r[5], contracted_students=r[6],
        revenue_share_pct=r[7], status=r[8],
        lti_client_id=r[9], lti_deployment_id=r[10],
        notes=r[11], created_at=r[12],
        contracted_at=r[13],
    )


def _row_to_course(r) -> Course:
    return Course(
        id=r[0], partner_id=r[1], course_code=r[2],
        title=r[3], description=r[4],
        duration_weeks=r[5], credits=r[6],
        lesson_manifest_url=r[7], status=r[8],
        enrollment_count=r[9], completion_count=r[10],
        created_at=r[11], published_at=r[12],
    )


def _row_to_enrollment(r) -> Enrollment:
    return Enrollment(
        id=r[0], course_id=r[1], partner_id=r[2],
        partner_student_id=r[3], our_user_id=r[4],
        status=r[5], completion_pct=r[6], final_score=r[7],
        enrolled_at=r[8], started_at=r[9], completed_at=r[10],
    )


# ---------- partner CRUD ----------

def register_partner(
    *,
    name: str,
    kind: str,
    integration_kind: str,
    contact_email: str | None = None,
    contract_value_inr: int | None = None,
    contracted_students: int | None = None,
    revenue_share_pct: float = 0.30,
    notes: str | None = None,
) -> Partner:
    name = (name or "").strip()
    if len(name) < 2 or len(name) > 200:
        raise ValueError("name must be [2, 200] chars")
    if kind not in VALID_PARTNER_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_PARTNER_KINDS)}")
    if integration_kind not in VALID_INTEGRATION_KINDS:
        raise ValueError(
            f"integration_kind must be in {sorted(VALID_INTEGRATION_KINDS)}",
        )
    if revenue_share_pct < MIN_REV_SHARE or revenue_share_pct > MAX_REV_SHARE:
        raise ValueError(
            f"revenue_share_pct must be in [{MIN_REV_SHARE}, {MAX_REV_SHARE}]",
        )
    if contracted_students is not None and contracted_students < 0:
        raise ValueError("contracted_students must be ≥0")
    pid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO university_partners "
                "(id, name, kind, integration_kind, contact_email, "
                " contract_value_inr, contracted_students, "
                " revenue_share_pct, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, name, kind, integration_kind, contact_email,
                 contract_value_inr, contracted_students,
                 revenue_share_pct, notes, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"partner {name!r} already registered") from e
    return get_partner(pid)  # type: ignore[return-value]


def get_partner(partner_id: str) -> Partner | None:
    with _conn() as conn:
        r = conn.execute(
            _P_SEL + " WHERE id = ?", (partner_id,),
        ).fetchone()
    return _row_to_partner(r) if r else None


def list_partners(
    *,
    status: str | None = None,
    kind: str | None = None,
) -> list[Partner]:
    sql = _P_SEL
    where: list[str] = []
    params: list = []
    if status:
        if status not in VALID_PARTNER_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_PARTNER_STATUSES)}",
            )
        where.append("status = ?"); params.append(status)
    if kind:
        if kind not in VALID_PARTNER_KINDS:
            raise ValueError(f"kind must be in {sorted(VALID_PARTNER_KINDS)}")
        where.append("kind = ?"); params.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY contracted_students DESC, name"
    with _conn() as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(sql, params).fetchall()
    return [_row_to_partner(r) for r in rows]


def set_partner_status(*, partner_id: str, status: str) -> bool:
    if status not in VALID_PARTNER_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_PARTNER_STATUSES)}",
        )
    now = time.time()
    with _conn() as conn:
        # First contracted_at write
        if status == "contracted":
            conn.execute(
                "UPDATE university_partners SET "
                " status = ?, contracted_at = COALESCE(contracted_at, ?) "
                "WHERE id = ?",
                (status, now, partner_id),
            )
        else:
            conn.execute(
                "UPDATE university_partners SET status = ? WHERE id = ?",
                (status, partner_id),
            )
        cur = conn.execute(
            "SELECT 1 FROM university_partners WHERE id = ?",
            (partner_id,),
        ).fetchone()
        return cur is not None


def set_lti_config(
    *,
    partner_id: str,
    lti_client_id: str,
    lti_deployment_id: str,
) -> bool:
    """Drop in LTI 1.3 cfg after the partner gives us their tool
    consumer details. Required before LTI-launched courses go live."""
    if len(lti_client_id) > 200 or len(lti_deployment_id) > 200:
        raise ValueError("LTI ids must be ≤200 chars")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE university_partners SET "
            " lti_client_id = ?, lti_deployment_id = ? "
            "WHERE id = ? AND integration_kind = 'lti13'",
            (lti_client_id, lti_deployment_id, partner_id),
        )
        return cur.rowcount > 0


# ---------- courses ----------

def create_course(
    *,
    partner_id: str,
    course_code: str,
    title: str,
    description: str | None = None,
    duration_weeks: int | None = None,
    credits: int | None = None,
    lesson_manifest_url: str | None = None,
) -> Course:
    partner = get_partner(partner_id)
    if not partner:
        raise ValueError("partner not found")
    if partner.status not in ("contracted", "live"):
        raise ValueError(
            f"partner status is {partner.status!r}, "
            "must be contracted or live",
        )
    course_code = (course_code or "").strip().upper()
    if not course_code or len(course_code) > 60:
        raise ValueError("course_code required (max 60 chars)")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if duration_weeks is not None and (duration_weeks < 1 or duration_weeks > 104):
        raise ValueError("duration_weeks must be in [1, 104]")
    if credits is not None and (credits < 0 or credits > 20):
        raise ValueError("credits must be in [0, 20]")
    cid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO partner_courses "
                "(id, partner_id, course_code, title, description, "
                " duration_weeks, credits, lesson_manifest_url, "
                " status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
                (cid, partner_id, course_code, title, description,
                 duration_weeks, credits, lesson_manifest_url,
                 time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"course_code {course_code!r} already exists for partner",
        ) from e
    return get_course(cid)  # type: ignore[return-value]


def get_course(course_id: str) -> Course | None:
    with _conn() as conn:
        r = conn.execute(
            _C_SEL + " WHERE id = ?", (course_id,),
        ).fetchone()
    return _row_to_course(r) if r else None


def list_courses(
    *,
    partner_id: str | None = None,
    status: str | None = None,
) -> list[Course]:
    sql = _C_SEL
    where: list[str] = []
    params: list = []
    if partner_id:
        where.append("partner_id = ?"); params.append(partner_id)
    if status:
        if status not in VALID_COURSE_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_COURSE_STATUSES)}",
            )
        where.append("status = ?"); params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY enrollment_count DESC, created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_course(r) for r in rows]


def publish_course(course_id: str) -> bool:
    """Course goes live for enrollment. Partner must be 'live' or
    'contracted'."""
    c = get_course(course_id)
    if not c:
        raise ValueError("course not found")
    if c.status != "draft":
        return False
    partner = get_partner(c.partner_id)
    if not partner:
        raise ValueError("partner missing")
    if partner.status not in ("contracted", "live"):
        raise ValueError(
            f"partner status is {partner.status!r}; can't publish",
        )
    with _conn() as conn:
        conn.execute(
            "UPDATE partner_courses SET "
            " status = 'published', published_at = ? "
            "WHERE id = ?",
            (time.time(), course_id),
        )
    return True


def archive_course(course_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE partner_courses SET status = 'archived' "
            "WHERE id = ? AND status != 'archived'",
            (course_id,),
        )
        return cur.rowcount > 0


# ---------- enrollments ----------

def enroll_student(
    *,
    course_id: str,
    partner_student_id: str,
    our_user_id: str | None = None,
) -> Enrollment:
    """Partner's LMS calls this via REST when a student registers
    on their side. our_user_id links to a padhai account if the
    student SSO'd through."""
    course = get_course(course_id)
    if not course:
        raise ValueError("course not found")
    if course.status != "published":
        raise ValueError(f"course is {course.status!r}, not enrollable")
    partner_student_id = (partner_student_id or "").strip()
    if not partner_student_id or len(partner_student_id) > 100:
        raise ValueError("partner_student_id required (max 100 chars)")
    eid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO partner_enrollments "
                "(id, course_id, partner_id, partner_student_id, "
                " our_user_id, status, enrolled_at) "
                "VALUES (?, ?, ?, ?, ?, 'enrolled', ?)",
                (eid, course_id, course.partner_id,
                 partner_student_id, our_user_id, time.time()),
            )
            conn.execute(
                "UPDATE partner_courses SET "
                " enrollment_count = enrollment_count + 1 "
                "WHERE id = ?", (course_id,),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            "student already enrolled in this course",
        ) from e
    return get_enrollment(eid)  # type: ignore[return-value]


def get_enrollment(enrollment_id: str) -> Enrollment | None:
    with _conn() as conn:
        r = conn.execute(
            _E_SEL + " WHERE id = ?", (enrollment_id,),
        ).fetchone()
    return _row_to_enrollment(r) if r else None


def update_progress(
    *,
    enrollment_id: str,
    completion_pct: float,
    final_score: float | None = None,
) -> Enrollment:
    if completion_pct < 0 or completion_pct > 100:
        raise ValueError("completion_pct must be in [0, 100]")
    e = get_enrollment(enrollment_id)
    if not e:
        raise ValueError("enrollment not found")
    if e.status in ("completed", "withdrawn"):
        raise ValueError(f"enrollment is {e.status!r}, can't update")
    now = time.time()
    started_at = e.started_at or (now if completion_pct > 0 else None)
    if completion_pct >= 100:
        status = "completed"
        completed_at = now
    elif completion_pct > 0:
        status = "in_progress"
        completed_at = None
    else:
        status = "enrolled"
        completed_at = None
    with _conn() as conn:
        conn.execute(
            "UPDATE partner_enrollments SET "
            " completion_pct = ?, status = ?, "
            " started_at = ?, completed_at = ?, final_score = ? "
            "WHERE id = ?",
            (completion_pct, status, started_at, completed_at,
             final_score, enrollment_id),
        )
        if status == "completed":
            conn.execute(
                "UPDATE partner_courses SET "
                " completion_count = completion_count + 1 "
                "WHERE id = ?", (e.course_id,),
            )
    return get_enrollment(enrollment_id)  # type: ignore[return-value]


def withdraw(enrollment_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE partner_enrollments SET "
            " status = 'withdrawn' "
            "WHERE id = ? AND status NOT IN ('completed', 'withdrawn')",
            (enrollment_id,),
        )
        return cur.rowcount > 0


def list_enrollments_for_course(
    course_id: str,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[Enrollment]:
    limit = max(1, min(limit, 1000))
    sql = _E_SEL + " WHERE course_id = ?"
    params: list = [course_id]
    if status:
        if status not in VALID_ENROLLMENT_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_ENROLLMENT_STATUSES)}",
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY enrolled_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_enrollment(r) for r in rows]


def partner_stats(partner_id: str) -> dict:
    """Aggregate enrollment + completion + revenue-eligible counts
    for the partner — drives their dashboard + our payouts."""
    with _conn() as conn:
        r1 = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(enrollment_count), 0), "
            "       COALESCE(SUM(completion_count), 0) "
            "FROM partner_courses WHERE partner_id = ?",
            (partner_id,),
        ).fetchone()
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM partner_enrollments "
            "WHERE partner_id = ? GROUP BY status",
            (partner_id,),
        ).fetchall()
    return {
        "course_count": int(r1[0] or 0),
        "total_enrollments": int(r1[1] or 0),
        "total_completions": int(r1[2] or 0),
        "enrollment_by_status": {row[0]: row[1] for row in by_status},
    }
