"""Organizations (schools / coaching institutes) — the institutional
portal data layer.

Tables:
  - orgs              one row per school or coaching institute
  - org_members       which users belong to which org + their role
  - org_classes       class groups inside an org (Class 8A, Class 8B, …)
  - org_assignments   "watch this video by Friday" tasks pushed to a class

Auth model:
  An org has one or more `admin` members (org owner + delegates).
  Teachers can create assignments; students consume them. We re-use the
  main app's `users` table; org membership is the join.

What's intentionally OUT of scope (per PRD §19 — full School ERP is
Phase 4+ work):
  - attendance, exams, fees, timetable, parent communication, report
    cards, fee reminders. Those need a real student-information-system
    schema; we don't pretend to be one in v0.9.

SQLite-first; the same SQL works on Postgres. Switch via DATABASE_URL.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id              TEXT PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,        -- url-safe, e.g. 'st-pauls-bangalore'
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,               -- 'school' | 'coaching' | 'ngo' | 'gov'
    board           TEXT,                        -- 'CBSE' | 'ICSE' | 'state' | etc.
    city            TEXT,
    contact_email   TEXT,
    plan_tier       TEXT NOT NULL DEFAULT 'pilot', -- pilot | school | coaching | enterprise
    owner_user_id   TEXT NOT NULL,
    logo_url        TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS org_members (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    user_id         TEXT,                        -- null for invited-but-not-yet-signed-up
    invited_email   TEXT,                        -- carries email until user_id is bound
    role            TEXT NOT NULL,               -- 'admin' | 'teacher' | 'student'
    class_id        TEXT,                        -- optional — only meaningful for students
    display_name    TEXT,
    joined_at       REAL NOT NULL,
    UNIQUE (org_id, invited_email)
);
CREATE INDEX IF NOT EXISTS idx_org_members_org ON org_members(org_id);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members(user_id);

CREATE TABLE IF NOT EXISTS org_classes (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,               -- 'Class 8A', 'JEE Foundation', etc.
    grade_level     TEXT,                        -- '8', 'NEET', etc. — free-form
    section         TEXT,                        -- 'A', 'B', null
    created_at      REAL NOT NULL,
    UNIQUE (org_id, name)
);
CREATE INDEX IF NOT EXISTS idx_org_classes_org ON org_classes(org_id);

CREATE TABLE IF NOT EXISTS org_assignments (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    class_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    topic           TEXT NOT NULL,               -- the topic the video should explain
    language        TEXT NOT NULL DEFAULT 'en',
    level           TEXT NOT NULL DEFAULT 'middle',
    due_date        TEXT,                        -- ISO date, optional
    notes           TEXT,
    created_by      TEXT NOT NULL,               -- user_id
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assignments_org ON org_assignments(org_id);
CREATE INDEX IF NOT EXISTS idx_assignments_class ON org_assignments(class_id);

CREATE TABLE IF NOT EXISTS org_assignment_completions (
    id              TEXT PRIMARY KEY,
    assignment_id   TEXT NOT NULL,
    user_id         TEXT NOT NULL,        -- the student
    watched_at      REAL,                  -- first time the player got the video to the end
    watch_pct       INTEGER,               -- 0-100, sampled from <video> timeupdate
    quiz_score      INTEGER,               -- 0-100, NULL if no quiz attempted
    quiz_attempts   INTEGER NOT NULL DEFAULT 0,
    last_attempt_at REAL,
    updated_at      REAL NOT NULL,
    UNIQUE (assignment_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_compl_assignment
    ON org_assignment_completions(assignment_id);
CREATE INDEX IF NOT EXISTS idx_compl_user
    ON org_assignment_completions(user_id);

-- E3 Attendance (v0.13)
CREATE TABLE IF NOT EXISTS org_attendance (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    class_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,             -- the student
    date        TEXT NOT NULL,             -- YYYY-MM-DD
    status      TEXT NOT NULL,             -- present | absent | late | excused
    marked_by   TEXT NOT NULL,             -- teacher user_id
    notes       TEXT,
    marked_at   REAL NOT NULL,
    UNIQUE (class_id, user_id, date)
);
CREATE INDEX IF NOT EXISTS idx_attendance_class_date
    ON org_attendance(class_id, date);
CREATE INDEX IF NOT EXISTS idx_attendance_user_date
    ON org_attendance(user_id, date);

-- E6 Timetable (v0.13)
CREATE TABLE IF NOT EXISTS org_timetable_slots (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    class_id        TEXT NOT NULL,
    day_of_week     INTEGER NOT NULL,      -- 1=Monday … 7=Sunday
    start_time      TEXT NOT NULL,         -- 'HH:MM'
    end_time        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    teacher_user_id TEXT,
    room            TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_timetable_class_day
    ON org_timetable_slots(class_id, day_of_week);
CREATE INDEX IF NOT EXISTS idx_timetable_teacher
    ON org_timetable_slots(teacher_user_id);

-- E4 Exams (v0.15)
CREATE TABLE IF NOT EXISTS org_exams (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    class_id        TEXT NOT NULL,
    title           TEXT NOT NULL,
    subject         TEXT,
    topic           TEXT NOT NULL,       -- the topic Claude generated questions about
    scheduled_at    REAL,                 -- epoch — null means "open now"
    duration_min    INTEGER NOT NULL,
    max_marks       INTEGER NOT NULL,
    status          TEXT NOT NULL,        -- draft | scheduled | in_progress | done | grading
    questions_json  TEXT NOT NULL,        -- list of {q, options, answer?, marks, kind}
    created_by      TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exams_org   ON org_exams(org_id);
CREATE INDEX IF NOT EXISTS idx_exams_class ON org_exams(class_id);

CREATE TABLE IF NOT EXISTS org_exam_attempts (
    id              TEXT PRIMARY KEY,
    exam_id         TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    started_at      REAL NOT NULL,
    submitted_at    REAL,
    answers_json    TEXT,                 -- {question_index: chosen_letter_or_text}
    auto_score      INTEGER,              -- MCQ machine grading
    manual_score    INTEGER,              -- free-form teacher grading
    total_score     INTEGER,
    feedback        TEXT,
    -- S4 anti-cheat: client-side beacons. Counts persist; lets the
    -- teacher see "this student tab-switched 12 times" without us
    -- blocking the submission entirely (their reading is the call).
    tab_blur_count  INTEGER NOT NULL DEFAULT 0,
    fullscreen_exit_count INTEGER NOT NULL DEFAULT 0,
    flags_json      TEXT,                 -- detailed event log
    UNIQUE (exam_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_attempts_exam ON org_exam_attempts(exam_id);
CREATE INDEX IF NOT EXISTS idx_attempts_user ON org_exam_attempts(user_id);

-- E5 Fees + invoicing (v0.16)
CREATE TABLE IF NOT EXISTS org_fee_structures (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    name            TEXT NOT NULL,        -- 'Class 8 Annual', 'Bus fee', etc.
    amount_paise    INTEGER NOT NULL,     -- store in paise to avoid float math
    currency        TEXT NOT NULL DEFAULT 'INR',
    applies_to      TEXT NOT NULL,        -- 'class:<id>' | 'all'
    due_date        TEXT,                  -- YYYY-MM-DD, optional
    notes           TEXT,
    created_by      TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fee_struct_org ON org_fee_structures(org_id);

CREATE TABLE IF NOT EXISTS org_fee_invoices (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    user_id         TEXT NOT NULL,        -- the student owing the fee
    structure_id    TEXT NOT NULL,
    amount_paise    INTEGER NOT NULL,     -- snapshotted at generation; doesn't
                                          -- follow structure if you edit it later
    currency        TEXT NOT NULL DEFAULT 'INR',
    status          TEXT NOT NULL DEFAULT 'pending',
                                          -- pending | paid | overdue | cancelled |
                                          -- refunded
    due_date        TEXT,
    paid_at         REAL,
    razorpay_order_id   TEXT,
    razorpay_payment_id TEXT,
    receipt_url     TEXT,
    notes           TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (structure_id, user_id)        -- one invoice per (fee, student)
);
CREATE INDEX IF NOT EXISTS idx_fee_inv_org    ON org_fee_invoices(org_id);
CREATE INDEX IF NOT EXISTS idx_fee_inv_user   ON org_fee_invoices(user_id);
CREATE INDEX IF NOT EXISTS idx_fee_inv_status ON org_fee_invoices(status);
"""


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn(read_only: bool = False) -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only:
        # Eager-create the schema before opening read-only so the first
        # read on a fresh DB doesn't crash with "no such table". Without
        # this, has_active_exam(user_id) at app start would 500 the
        # /api/exam-mode/active endpoint until something else triggered
        # a write.
        if not path.exists():
            sqlite3.connect(str(path), timeout=10.0).executescript(SCHEMA)
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10.0)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    """Idempotent table-create. Called from the FastAPI startup hook
    alongside every other module's migrate() so org_* tables exist on
    fresh-DB boots before the first request lands."""
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Org:
    id: str
    slug: str
    name: str
    kind: str
    board: str | None
    city: str | None
    contact_email: str | None
    plan_tier: str
    owner_user_id: str
    logo_url: str | None
    created_at: float


@dataclass(frozen=True)
class OrgMember:
    id: str
    org_id: str
    user_id: str | None
    invited_email: str | None
    role: str
    class_id: str | None
    display_name: str | None
    joined_at: float


@dataclass(frozen=True)
class OrgClass:
    id: str
    org_id: str
    name: str
    grade_level: str | None
    section: str | None
    created_at: float


@dataclass(frozen=True)
class Assignment:
    id: str
    org_id: str
    class_id: str
    title: str
    topic: str
    language: str
    level: str
    due_date: str | None
    notes: str | None
    created_by: str
    created_at: float


# ---------- slug + validation helpers ----------

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def make_slug(name: str) -> str:
    """Generate a url-safe slug from a free-text org name.
    Collisions resolved by appending -2, -3, … in `create_org`."""
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return base[:48] or "org"


VALID_KINDS = {"school", "coaching", "ngo", "gov"}
VALID_ROLES = {"admin", "teacher", "student"}
VALID_TIERS = {"pilot", "school", "coaching", "enterprise"}


# ---------- write operations ----------

def create_org(
    *, name: str, kind: str, owner_user_id: str,
    board: str | None = None, city: str | None = None,
    contact_email: str | None = None, plan_tier: str = "pilot",
) -> Org:
    if kind not in VALID_KINDS:
        raise ValueError(f"kind must be in {sorted(VALID_KINDS)}")
    if plan_tier not in VALID_TIERS:
        raise ValueError(f"plan_tier must be in {sorted(VALID_TIERS)}")
    if not name.strip():
        raise ValueError("name is required")
    oid = uuid.uuid4().hex
    now = time.time()
    base_slug = make_slug(name)
    # Resolve slug collisions deterministically — first writer wins the
    # bare slug; later writers get -2, -3, …
    with _conn() as conn:
        slug = base_slug
        suffix = 2
        while conn.execute("SELECT 1 FROM orgs WHERE slug=?", (slug,)).fetchone():
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        conn.execute(
            "INSERT INTO orgs (id, slug, name, kind, board, city, "
            "contact_email, plan_tier, owner_user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, slug, name.strip(), kind, board, city,
             contact_email, plan_tier, owner_user_id, now),
        )
        # Owner is automatically an admin member.
        conn.execute(
            "INSERT INTO org_members (id, org_id, user_id, role, joined_at) "
            "VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, oid, owner_user_id, "admin", now),
        )
    return Org(
        id=oid, slug=slug, name=name.strip(), kind=kind, board=board,
        city=city, contact_email=contact_email, plan_tier=plan_tier,
        owner_user_id=owner_user_id, logo_url=None, created_at=now,
    )


def add_class(*, org_id: str, name: str,
              grade_level: str | None = None,
              section: str | None = None) -> OrgClass:
    cid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO org_classes (id, org_id, name, grade_level, "
                "section, created_at) VALUES (?,?,?,?,?,?)",
                (cid, org_id, name.strip(), grade_level, section, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"class {name!r} already exists in this org") from e
    return OrgClass(id=cid, org_id=org_id, name=name.strip(),
                    grade_level=grade_level, section=section, created_at=now)


def add_member(
    *, org_id: str, role: str,
    invited_email: str | None = None,
    user_id: str | None = None,
    class_id: str | None = None,
    display_name: str | None = None,
) -> OrgMember:
    """Either invited_email OR user_id (or both — when the invited user
    has already signed up) must be present."""
    if role not in VALID_ROLES:
        raise ValueError(f"role must be in {sorted(VALID_ROLES)}")
    if not (invited_email or user_id):
        raise ValueError("invited_email or user_id required")
    mid = uuid.uuid4().hex
    now = time.time()
    invited_email = invited_email.lower().strip() if invited_email else None
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO org_members (id, org_id, user_id, "
                "invited_email, role, class_id, display_name, joined_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (mid, org_id, user_id, invited_email, role, class_id,
                 display_name, now),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"{invited_email} is already in this org") from e
    return OrgMember(
        id=mid, org_id=org_id, user_id=user_id, invited_email=invited_email,
        role=role, class_id=class_id, display_name=display_name, joined_at=now,
    )


def deactivate_member(*, org_id: str, member_id: str) -> bool:
    """Soft-delete an org_member by setting their role to 'deactivated'.
    Used by SCIM PATCH (H2) when an IdP deprovisions a user, and by
    the admin UI's 'remove member' button.

    Returns True if a row was updated, False if not found / already
    deactivated. Audit-friendly: history remains queryable; only the
    privilege gate changes.
    """
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE org_members SET role = 'deactivated' "
            "WHERE id = ? AND org_id = ? AND role != 'deactivated'",
            (member_id, org_id),
        )
        return cur.rowcount > 0


def import_roster_csv(
    *, org_id: str, csv_bytes: bytes,
    default_role: str = "student",
) -> dict:
    """Bulk-import students/teachers from a CSV.

    Required columns: email
    Optional columns: name, role (admin|teacher|student), class

    Returns {"added": N, "skipped": [(row, reason), …], "duplicates": N}.
    Silently skips header rows; logs each rejected row with a reason so
    the UI can show a precise error list."""
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return {"added": 0, "duplicates": 0,
                "skipped": [(0, "empty csv")]}

    # Normalise headers (case-insensitive)
    field_map = {f.lower().strip(): f for f in reader.fieldnames}
    email_col = field_map.get("email") or field_map.get("e-mail")
    if not email_col:
        return {"added": 0, "duplicates": 0,
                "skipped": [(0, "missing 'email' column")]}
    name_col = field_map.get("name") or field_map.get("display name")
    role_col = field_map.get("role")
    class_col = field_map.get("class") or field_map.get("section")

    # Build a name → class_id map up front so each row's "class" string
    # resolves into an actual class_id (creating the class if missing).
    classes_by_name: dict[str, str] = {}
    for c in list_classes(org_id):
        classes_by_name[c.name.lower()] = c.id

    added = 0
    duplicates = 0
    skipped: list[tuple[int, str]] = []
    for idx, row in enumerate(reader, start=2):  # row 1 is the header
        email = (row.get(email_col) or "").strip().lower()
        if not email or "@" not in email:
            skipped.append((idx, f"invalid email: {email!r}"))
            continue
        role = ((row.get(role_col) if role_col else "")
                or default_role).strip().lower()
        if role not in VALID_ROLES:
            skipped.append((idx, f"unknown role: {role!r}"))
            continue
        class_id: str | None = None
        if class_col:
            class_name = (row.get(class_col) or "").strip()
            if class_name:
                lower = class_name.lower()
                if lower not in classes_by_name:
                    new_class = add_class(org_id=org_id, name=class_name)
                    classes_by_name[lower] = new_class.id
                class_id = classes_by_name[lower]
        display_name = ((row.get(name_col) or "").strip()
                        if name_col else None) or None
        try:
            add_member(
                org_id=org_id, role=role,
                invited_email=email, class_id=class_id,
                display_name=display_name,
            )
            added += 1
        except ValueError:
            duplicates += 1
    return {"added": added, "duplicates": duplicates, "skipped": skipped}


def create_assignment(
    *, org_id: str, class_id: str, title: str, topic: str,
    language: str = "en", level: str = "middle",
    due_date: str | None = None, notes: str | None = None,
    created_by: str,
) -> Assignment:
    aid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_assignments (id, org_id, class_id, title, "
            "topic, language, level, due_date, notes, created_by, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (aid, org_id, class_id, title.strip(), topic.strip(),
             language, level, due_date, notes, created_by, now),
        )
    return Assignment(
        id=aid, org_id=org_id, class_id=class_id, title=title.strip(),
        topic=topic.strip(), language=language, level=level,
        due_date=due_date, notes=notes, created_by=created_by, created_at=now,
    )


# ---------- read operations ----------

def _row_to_org(r) -> Org:
    return Org(
        id=r[0], slug=r[1], name=r[2], kind=r[3], board=r[4], city=r[5],
        contact_email=r[6], plan_tier=r[7], owner_user_id=r[8],
        logo_url=r[9], created_at=r[10],
    )


_ORG_COLS = ("id, slug, name, kind, board, city, contact_email, "
             "plan_tier, owner_user_id, logo_url, created_at")


def get_org(org_id: str) -> Org | None:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            f"SELECT {_ORG_COLS} FROM orgs WHERE id = ?", (org_id,),
        ).fetchone()
    return _row_to_org(r) if r else None


def get_org_by_slug(slug: str) -> Org | None:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            f"SELECT {_ORG_COLS} FROM orgs WHERE slug = ?", (slug,),
        ).fetchone()
    return _row_to_org(r) if r else None


def find_orgs_for_user(user_id: str) -> list[Org]:
    """All orgs the user is a member of (any role)."""
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            f"SELECT {', '.join('o.' + c for c in _ORG_COLS.split(', '))} "
            "FROM orgs o JOIN org_members m ON m.org_id = o.id "
            "WHERE m.user_id = ? "
            "ORDER BY o.created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_org(r) for r in rows]


def list_members(org_id: str,
                 role: str | None = None,
                 limit: int = 500) -> list[OrgMember]:
    where = ["org_id = ?"]
    params: list = [org_id]
    if role:
        where.append("role = ?")
        params.append(role)
    params.append(limit)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, org_id, user_id, invited_email, role, class_id, "
            "display_name, joined_at FROM org_members "
            "WHERE " + " AND ".join(where) +
            " ORDER BY joined_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [
        OrgMember(
            id=r[0], org_id=r[1], user_id=r[2], invited_email=r[3],
            role=r[4], class_id=r[5], display_name=r[6], joined_at=r[7],
        )
        for r in rows
    ]


def list_classes(org_id: str, *, limit: int = 200) -> list[OrgClass]:
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, org_id, name, grade_level, section, created_at "
            "FROM org_classes WHERE org_id = ? "
            "ORDER BY name ASC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    return [
        OrgClass(id=r[0], org_id=r[1], name=r[2], grade_level=r[3],
                 section=r[4], created_at=r[5])
        for r in rows
    ]


def list_assignments(org_id: str, class_id: str | None = None, *, limit: int = 200) -> list[Assignment]:
    where = ["org_id = ?"]
    params: list = [org_id]
    if class_id:
        where.append("class_id = ?")
        params.append(class_id)
    params.append(limit)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, org_id, class_id, title, topic, language, level, "
            "due_date, notes, created_by, created_at FROM org_assignments "
            "WHERE " + " AND ".join(where) +
            " ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [
        Assignment(
            id=r[0], org_id=r[1], class_id=r[2], title=r[3], topic=r[4],
            language=r[5], level=r[6], due_date=r[7], notes=r[8],
            created_by=r[9], created_at=r[10],
        )
        for r in rows
    ]


def user_role_in_org(*, org_id: str, user_id: str) -> str | None:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT role FROM org_members WHERE org_id = ? AND user_id = ?",
            (org_id, user_id),
        ).fetchone()
    return r[0] if r else None


def org_stats(org_id: str) -> dict:
    """Aggregate dashboard numbers: member counts by role + class counts
    + assignment counts + (best-effort) videos generated by members
    over the last 7 days."""
    with _conn(read_only=True) as conn:
        member_rows = conn.execute(
            "SELECT role, COUNT(*) FROM org_members WHERE org_id = ? "
            "GROUP BY role", (org_id,),
        ).fetchall()
        class_count = conn.execute(
            "SELECT COUNT(*) FROM org_classes WHERE org_id = ?", (org_id,),
        ).fetchone()[0]
        assignment_count = conn.execute(
            "SELECT COUNT(*) FROM org_assignments WHERE org_id = ?",
            (org_id,),
        ).fetchone()[0]

        # Cross-table join into the jobs table: how many videos did
        # members of THIS org generate in the last 7 days. Approximate —
        # only counts members with a bound user_id (signed-up users).
        # The jobs table is owned by another module; tolerate its
        # absence (e.g. fresh DB before any video has been generated).
        try:
            videos_7d = conn.execute(
                "SELECT COUNT(*) FROM jobs j "
                "WHERE j.status='succeeded' "
                "AND j.created_at > strftime('%s','now') - 604800 "
                "AND EXISTS ("
                "  SELECT 1 FROM org_members m "
                "  WHERE m.org_id = ? AND m.user_id IS NOT NULL "
                "  AND json_extract(j.payload, '$.user_id') = m.user_id"
                ")",
                (org_id,),
            ).fetchone()[0]
        except sqlite3.OperationalError:
            videos_7d = 0

    members_by_role = {r[0]: r[1] for r in member_rows}
    return {
        "students": members_by_role.get("student", 0),
        "teachers": members_by_role.get("teacher", 0),
        "admins": members_by_role.get("admin", 0),
        "total_members": sum(members_by_role.values()),
        "classes": class_count,
        "assignments": assignment_count,
        "videos_last_7d": videos_7d,
    }


# ---------- guards ----------

# ---------- per-student analytics (E1) ----------

@dataclass(frozen=True)
class Completion:
    id: str
    assignment_id: str
    user_id: str
    watched_at: float | None
    watch_pct: int | None
    quiz_score: int | None
    quiz_attempts: int
    last_attempt_at: float | None
    updated_at: float


def record_completion(
    *, assignment_id: str, user_id: str,
    watch_pct: int | None = None,
    quiz_score: int | None = None,
    quiz_attempt: bool = False,
) -> Completion:
    """Upsert a student's progress on an assignment.

    Called from the video player (every 30s via timeupdate) and from
    the quiz finisher. Each call may set watch_pct OR quiz_score —
    whichever fields are provided are persisted; others are preserved.
    Idempotent on (assignment_id, user_id) so dropped network calls
    don't double-count.
    """
    import uuid
    now = time.time()
    watched_at_clause = ""
    watched_at_param = []
    if watch_pct is not None and watch_pct >= 95:
        watched_at_clause = ", watched_at = COALESCE(watched_at, ?)"
        watched_at_param = [now]

    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, watched_at, watch_pct, quiz_score, quiz_attempts "
            "FROM org_assignment_completions "
            "WHERE assignment_id = ? AND user_id = ?",
            (assignment_id, user_id),
        ).fetchone()

        if existing:
            cid, old_watched, old_pct, old_score, old_attempts = existing
            # Watch percent only ratchets up — re-opening the video
            # later shouldn't reset the high-watermark.
            new_pct = max(old_pct or 0, watch_pct) if watch_pct is not None else old_pct
            # Quiz score takes the best of any attempt.
            new_score = old_score
            new_attempts = old_attempts
            if quiz_score is not None:
                new_score = max(old_score or 0, quiz_score)
            if quiz_attempt:
                new_attempts = (old_attempts or 0) + 1

            params: list = [new_pct, new_score, new_attempts]
            sets = ["watch_pct = ?", "quiz_score = ?",
                    "quiz_attempts = ?", "updated_at = ?"]
            params.append(now)
            if quiz_attempt:
                sets.append("last_attempt_at = ?")
                params.append(now)
            if watched_at_clause:
                # Stitch the COALESCE clause in too
                sets.append("watched_at = COALESCE(watched_at, ?)")
                params.append(now)
            params.extend([assignment_id, user_id])

            conn.execute(
                "UPDATE org_assignment_completions SET "
                + ", ".join(sets)
                + " WHERE assignment_id = ? AND user_id = ?",
                params,
            )
            return _completion_by_id(cid)

        cid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO org_assignment_completions "
            "(id, assignment_id, user_id, watched_at, watch_pct, quiz_score, "
            " quiz_attempts, last_attempt_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, assignment_id, user_id,
             now if (watch_pct or 0) >= 95 else None,
             watch_pct, quiz_score,
             1 if quiz_attempt else 0,
             now if quiz_attempt else None,
             now),
        )
    return _completion_by_id(cid)


def _completion_by_id(cid: str) -> Completion:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT id, assignment_id, user_id, watched_at, watch_pct, "
            "       quiz_score, quiz_attempts, last_attempt_at, updated_at "
            "FROM org_assignment_completions WHERE id = ?",
            (cid,),
        ).fetchone()
    return Completion(
        id=r[0], assignment_id=r[1], user_id=r[2], watched_at=r[3],
        watch_pct=r[4], quiz_score=r[5], quiz_attempts=r[6],
        last_attempt_at=r[7], updated_at=r[8],
    )


def assignment_class_stats(assignment_id: str, class_id: str) -> dict:
    """Per-assignment rollup for the teacher view.

    Returns the class roster joined with each student's completion
    state — including students who have NOT started — so the teacher
    sees the full class picture, not just the ones who clicked
    'watch'."""
    with _conn(read_only=True) as conn:
        members = conn.execute(
            "SELECT user_id, display_name, invited_email "
            "FROM org_members "
            "WHERE class_id = ? AND role = 'student' AND user_id IS NOT NULL",
            (class_id,),
        ).fetchall()

        completions = {
            r[0]: r for r in conn.execute(
                "SELECT user_id, watch_pct, quiz_score, quiz_attempts, "
                "       watched_at, last_attempt_at "
                "FROM org_assignment_completions WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchall()
        }

    rows = []
    for user_id, name, email in members:
        c = completions.get(user_id)
        rows.append({
            "user_id": user_id,
            "display_name": name,
            "email": email,
            "watch_pct": c[1] if c else None,
            "quiz_score": c[2] if c else None,
            "quiz_attempts": c[3] if c else 0,
            "watched_at": c[4] if c else None,
            "last_attempt_at": c[5] if c else None,
            "status": (
                "completed" if c and c[1] and c[1] >= 95 else
                "in_progress" if c and c[1] else
                "not_started"
            ),
        })

    n = len(rows) or 1
    completed = sum(1 for r in rows if r["status"] == "completed")
    in_progress = sum(1 for r in rows if r["status"] == "in_progress")
    not_started = sum(1 for r in rows if r["status"] == "not_started")
    quiz_scores = [r["quiz_score"] for r in rows if r["quiz_score"] is not None]
    avg_quiz = round(sum(quiz_scores) / len(quiz_scores), 1) if quiz_scores else None

    return {
        "total": len(rows),
        "completed": completed,
        "in_progress": in_progress,
        "not_started": not_started,
        "completion_pct": round(completed * 100 / n, 1),
        "avg_quiz_score": avg_quiz,
        "students": rows,
    }


def student_assignment_history(*, org_id: str, user_id: str) -> list[dict]:
    """All assignments for a given student in an org. Used by the
    'click a student' drawer to show their full record."""
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT a.id, a.title, a.topic, a.due_date, "
            "       c.watch_pct, c.quiz_score, c.last_attempt_at "
            "FROM org_assignments a "
            "LEFT JOIN org_assignment_completions c "
            "  ON c.assignment_id = a.id AND c.user_id = ? "
            "WHERE a.org_id = ? "
            "ORDER BY a.created_at DESC",
            (user_id, org_id),
        ).fetchall()
    return [
        {
            "assignment_id": r[0], "title": r[1], "topic": r[2],
            "due_date": r[3], "watch_pct": r[4],
            "quiz_score": r[5], "last_attempt_at": r[6],
            "status": (
                "completed" if (r[4] or 0) >= 95 else
                "in_progress" if r[4] else
                "not_started"
            ),
        }
        for r in rows
    ]


# ---------- E3: Attendance ----------

VALID_ATTENDANCE_STATUS = {"present", "absent", "late", "excused"}


@dataclass(frozen=True)
class AttendanceRecord:
    id: str
    org_id: str
    class_id: str
    user_id: str
    date: str
    status: str
    marked_by: str
    notes: str | None
    marked_at: float


def mark_attendance(
    *, org_id: str, class_id: str,
    marked_by: str,
    records: list[dict],
) -> dict:
    """Bulk-mark attendance for a class on a date.

    `records` is a list of {user_id, date, status, notes?} dicts. The
    UNIQUE (class_id, user_id, date) index makes this idempotent on
    repeat marks — UPDATE-or-INSERT pattern.

    Returns {marked: N, errors: [(row, reason)]}.
    """
    import uuid
    now = time.time()
    marked = 0
    errors: list[tuple[int, str]] = []

    with _conn() as conn:
        for idx, r in enumerate(records):
            user_id = r.get("user_id")
            date = r.get("date")
            status = (r.get("status") or "").lower().strip()
            notes = r.get("notes")
            if not (user_id and date and status):
                errors.append((idx, "user_id, date, status required"))
                continue
            if status not in VALID_ATTENDANCE_STATUS:
                errors.append((idx, f"status must be in {sorted(VALID_ATTENDANCE_STATUS)}"))
                continue
            # Upsert via DELETE-then-INSERT to keep the schema portable
            # (SQLite supports ON CONFLICT but Postgres syntax differs);
            # a single transaction makes it atomic.
            existing = conn.execute(
                "SELECT id FROM org_attendance "
                "WHERE class_id = ? AND user_id = ? AND date = ?",
                (class_id, user_id, date),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE org_attendance SET status = ?, marked_by = ?, "
                    "notes = ?, marked_at = ? WHERE id = ?",
                    (status, marked_by, notes, now, existing[0]),
                )
            else:
                conn.execute(
                    "INSERT INTO org_attendance "
                    "(id, org_id, class_id, user_id, date, status, "
                    " marked_by, notes, marked_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, org_id, class_id, user_id,
                     date, status, marked_by, notes, now),
                )
            marked += 1
    return {"marked": marked, "errors": errors}


def class_attendance_for_date(class_id: str, date: str) -> list[dict]:
    """Roster + mark for one (class, date). Includes students who
    haven't been marked yet — `status` is None for them, so the
    teacher UI can show the full roster with empty cells."""
    with _conn(read_only=True) as conn:
        # Roster
        students = conn.execute(
            "SELECT user_id, display_name, invited_email "
            "FROM org_members "
            "WHERE class_id = ? AND role = 'student' AND user_id IS NOT NULL",
            (class_id,),
        ).fetchall()
        # Already-marked entries for this date
        marks = {
            r[0]: (r[1], r[2], r[3]) for r in conn.execute(
                "SELECT user_id, status, notes, marked_at "
                "FROM org_attendance WHERE class_id = ? AND date = ?",
                (class_id, date),
            ).fetchall()
        }
    rows = []
    for user_id, name, email in students:
        m = marks.get(user_id)
        rows.append({
            "user_id": user_id,
            "display_name": name,
            "email": email,
            "status": m[0] if m else None,
            "notes": m[1] if m else None,
            "marked_at": m[2] if m else None,
        })
    return rows


def student_attendance_history(
    *, org_id: str, user_id: str,
    from_date: str | None = None, to_date: str | None = None,
) -> list[dict]:
    """Per-student attendance history within a date range."""
    where = ["org_id = ?", "user_id = ?"]
    params: list = [org_id, user_id]
    if from_date:
        where.append("date >= ?"); params.append(from_date)
    if to_date:
        where.append("date <= ?"); params.append(to_date)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT date, status, notes, marked_at FROM org_attendance "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY date DESC LIMIT 365",
            params,
        ).fetchall()
    return [
        {"date": r[0], "status": r[1], "notes": r[2], "marked_at": r[3]}
        for r in rows
    ]


def class_attendance_summary(
    *, class_id: str,
    from_date: str | None = None, to_date: str | None = None,
) -> dict:
    """Per-student rollup over a date range: present/absent/late counts
    + attendance percentage. Used for monthly reports."""
    where = ["class_id = ?"]
    params: list = [class_id]
    if from_date:
        where.append("date >= ?"); params.append(from_date)
    if to_date:
        where.append("date <= ?"); params.append(to_date)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT user_id, status, COUNT(*) FROM org_attendance "
            f"WHERE {' AND '.join(where)} "
            "GROUP BY user_id, status",
            params,
        ).fetchall()
        students = conn.execute(
            "SELECT user_id, display_name FROM org_members "
            "WHERE class_id = ? AND role = 'student' AND user_id IS NOT NULL",
            (class_id,),
        ).fetchall()

    by_user: dict[str, dict] = {}
    for user_id, status, count in rows:
        bucket = by_user.setdefault(user_id, {
            "present": 0, "absent": 0, "late": 0, "excused": 0,
        })
        bucket[status] = count

    student_rows = []
    for user_id, display_name in students:
        b = by_user.get(user_id, {"present": 0, "absent": 0, "late": 0, "excused": 0})
        total_marks = b["present"] + b["absent"] + b["late"] + b["excused"]
        attended = b["present"] + b["late"]  # late counts as attended
        pct = round(attended * 100 / total_marks, 1) if total_marks else None
        student_rows.append({
            "user_id": user_id, "display_name": display_name,
            "present": b["present"], "absent": b["absent"],
            "late": b["late"], "excused": b["excused"],
            "attendance_pct": pct,
        })
    return {
        "from_date": from_date, "to_date": to_date,
        "students": student_rows,
    }


# ---------- E6: Timetable ----------

DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


@dataclass(frozen=True)
class TimetableSlot:
    id: str
    org_id: str
    class_id: str
    day_of_week: int
    start_time: str
    end_time: str
    subject: str
    teacher_user_id: str | None
    room: str | None
    created_at: float


def add_timetable_slot(
    *, org_id: str, class_id: str,
    day_of_week: int,
    start_time: str, end_time: str,
    subject: str,
    teacher_user_id: str | None = None,
    room: str | None = None,
) -> TimetableSlot:
    """Insert a single timetable slot. Validates day + time format."""
    import re
    import uuid
    if day_of_week not in DAY_NAMES:
        raise ValueError("day_of_week must be 1-7")
    time_re = re.compile(r"^[0-2]\d:[0-5]\d$")
    if not time_re.match(start_time) or not time_re.match(end_time):
        raise ValueError("start_time and end_time must be HH:MM")
    if start_time >= end_time:
        raise ValueError("start_time must be before end_time")
    if not subject.strip():
        raise ValueError("subject is required")
    sid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_timetable_slots "
            "(id, org_id, class_id, day_of_week, start_time, end_time, "
            " subject, teacher_user_id, room, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, org_id, class_id, day_of_week, start_time, end_time,
             subject.strip(), teacher_user_id, room, now),
        )
    return TimetableSlot(
        id=sid, org_id=org_id, class_id=class_id,
        day_of_week=day_of_week, start_time=start_time, end_time=end_time,
        subject=subject.strip(), teacher_user_id=teacher_user_id,
        room=room, created_at=now,
    )


def replace_class_timetable(
    *, org_id: str, class_id: str,
    slots: list[dict],
) -> dict:
    """Atomically delete + reinsert all slots for a class. Used by the
    bulk-CSV upload path. Returns {added: N, errors: [(row, reason)]}.
    """
    import uuid
    now = time.time()
    added = 0
    errors: list[tuple[int, str]] = []
    with _conn() as conn:
        # Delete-replace pattern in a single transaction
        conn.execute(
            "DELETE FROM org_timetable_slots WHERE class_id = ?",
            (class_id,),
        )
        for idx, s in enumerate(slots):
            try:
                # Validate inside the loop so partial-success has a clean
                # error list — we DON'T roll back successful inserts on
                # one bad row.
                day = int(s.get("day_of_week"))
                start = s.get("start_time", "")
                end = s.get("end_time", "")
                subject = s.get("subject", "").strip()
                if day not in DAY_NAMES:
                    raise ValueError("day_of_week 1-7")
                if not start or not end or not subject:
                    raise ValueError("day_of_week, start_time, end_time, subject required")
                if start >= end:
                    raise ValueError("start_time must be before end_time")
                conn.execute(
                    "INSERT INTO org_timetable_slots "
                    "(id, org_id, class_id, day_of_week, start_time, end_time, "
                    " subject, teacher_user_id, room, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, org_id, class_id, day, start, end,
                     subject, s.get("teacher_user_id"), s.get("room"), now),
                )
                added += 1
            except (ValueError, KeyError, TypeError) as e:
                errors.append((idx, str(e)))
    return {"added": added, "errors": errors}


def class_timetable(class_id: str) -> list[dict]:
    """Weekly grid for a class, sorted by day + start_time."""
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, day_of_week, start_time, end_time, subject, "
            "       teacher_user_id, room "
            "FROM org_timetable_slots WHERE class_id = ? "
            "ORDER BY day_of_week, start_time",
            (class_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "day_of_week": r[1],
            "day_name": DAY_NAMES.get(r[1], "?"),
            "start_time": r[2], "end_time": r[3],
            "subject": r[4],
            "teacher_user_id": r[5], "room": r[6],
        }
        for r in rows
    ]


def today_for_user(*, org_id: str, user_id: str) -> list[dict]:
    """What's on for me today. Returns slots from the user's classes
    (student membership) OR slots where they're the teacher. Sorted
    by start_time so the UI shows "next class" first."""
    import datetime as _dt
    iso_dow = _dt.date.today().isoweekday()  # 1=Mon … 7=Sun
    with _conn(read_only=True) as conn:
        # User's classes
        class_ids = [
            r[0] for r in conn.execute(
                "SELECT class_id FROM org_members "
                "WHERE org_id = ? AND user_id = ? AND class_id IS NOT NULL",
                (org_id, user_id),
            ).fetchall()
        ]
        rows = []
        if class_ids:
            qmarks = ",".join("?" for _ in class_ids)
            student_rows = conn.execute(
                f"SELECT class_id, start_time, end_time, subject, room "
                f"FROM org_timetable_slots "
                f"WHERE day_of_week = ? AND class_id IN ({qmarks}) "
                f"ORDER BY start_time",
                [iso_dow] + class_ids,
            ).fetchall()
            for r in student_rows:
                rows.append({
                    "class_id": r[0], "start_time": r[1],
                    "end_time": r[2], "subject": r[3], "room": r[4],
                    "role": "attending",
                })
        # Slots where the user is the teacher
        teacher_rows = conn.execute(
            "SELECT class_id, start_time, end_time, subject, room "
            "FROM org_timetable_slots "
            "WHERE day_of_week = ? AND teacher_user_id = ? "
            "ORDER BY start_time",
            (iso_dow, user_id),
        ).fetchall()
        for r in teacher_rows:
            rows.append({
                "class_id": r[0], "start_time": r[1],
                "end_time": r[2], "subject": r[3], "room": r[4],
                "role": "teaching",
            })
    rows.sort(key=lambda r: r["start_time"])
    return rows


# ---------- E4: Exams + auto-grading ----------

EXAM_STATUSES = {"draft", "scheduled", "in_progress", "done", "grading"}


@dataclass(frozen=True)
class Exam:
    id: str
    org_id: str
    class_id: str
    title: str
    subject: str | None
    topic: str
    scheduled_at: float | None
    duration_min: int
    max_marks: int
    status: str
    questions: list[dict]
    created_by: str
    created_at: float


@dataclass(frozen=True)
class ExamAttempt:
    id: str
    exam_id: str
    user_id: str
    started_at: float
    submitted_at: float | None
    answers: dict
    auto_score: int | None
    manual_score: int | None
    total_score: int | None
    feedback: str | None
    tab_blur_count: int
    fullscreen_exit_count: int
    flags: list[dict]


def create_exam(
    *,
    org_id: str,
    class_id: str,
    title: str,
    topic: str,
    questions: list[dict],
    duration_min: int,
    created_by: str,
    subject: str | None = None,
    scheduled_at: float | None = None,
    status: str = "scheduled",
) -> Exam:
    """Persist an exam. `questions` is the teacher-reviewed list — each
    item: {q, options{A,B,C,D}, answer, marks, kind: 'mcq'|'free'}.
    Validates marks sum > 0 and at least one question."""
    import uuid
    if status not in EXAM_STATUSES:
        raise ValueError(f"status must be in {sorted(EXAM_STATUSES)}")
    if not questions:
        raise ValueError("at least one question required")
    max_marks = sum(int(q.get("marks", 1)) for q in questions)
    if max_marks <= 0:
        raise ValueError("total marks must be > 0")
    eid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_exams "
            "(id, org_id, class_id, title, subject, topic, scheduled_at, "
            " duration_min, max_marks, status, questions_json, "
            " created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, org_id, class_id, title.strip(), subject, topic,
             scheduled_at, duration_min, max_marks, status,
             json.dumps(questions), created_by, now),
        )
    return Exam(
        id=eid, org_id=org_id, class_id=class_id, title=title.strip(),
        subject=subject, topic=topic, scheduled_at=scheduled_at,
        duration_min=duration_min, max_marks=max_marks, status=status,
        questions=questions, created_by=created_by, created_at=now,
    )


def _row_to_exam(r) -> Exam:
    return Exam(
        id=r[0], org_id=r[1], class_id=r[2], title=r[3],
        subject=r[4], topic=r[5], scheduled_at=r[6],
        duration_min=r[7], max_marks=r[8], status=r[9],
        questions=json.loads(r[10]) if r[10] else [],
        created_by=r[11], created_at=r[12],
    )


_EXAM_COLS = ("id, org_id, class_id, title, subject, topic, "
              "scheduled_at, duration_min, max_marks, status, "
              "questions_json, created_by, created_at")


def get_exam(eid: str) -> Exam | None:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            f"SELECT {_EXAM_COLS} FROM org_exams WHERE id = ?", (eid,),
        ).fetchone()
    return _row_to_exam(r) if r else None


def list_exams(org_id: str, class_id: str | None = None) -> list[Exam]:
    where = ["org_id = ?"]
    params: list = [org_id]
    if class_id:
        where.append("class_id = ?")
        params.append(class_id)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            f"SELECT {_EXAM_COLS} FROM org_exams "
            "WHERE " + " AND ".join(where) +
            " ORDER BY created_at DESC LIMIT 200",
            params,
        ).fetchall()
    return [_row_to_exam(r) for r in rows]


def begin_attempt(*, exam_id: str, user_id: str) -> ExamAttempt:
    """Idempotent attempt start. Re-calling returns the existing
    attempt (so a student who refreshes the page doesn't lose their
    place / restart the timer)."""
    import uuid
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM org_exam_attempts WHERE exam_id = ? AND user_id = ?",
            (exam_id, user_id),
        ).fetchone()
        if existing:
            return _attempt_by_id(existing[0])
        aid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO org_exam_attempts "
            "(id, exam_id, user_id, started_at) VALUES (?,?,?,?)",
            (aid, exam_id, user_id, now),
        )
    return _attempt_by_id(aid)


def submit_attempt(
    *,
    exam_id: str,
    user_id: str,
    answers: dict,
    tab_blur_count: int = 0,
    fullscreen_exit_count: int = 0,
    flags: list[dict] | None = None,
) -> ExamAttempt:
    """Auto-grade MCQ, persist everything. Free-form questions get a
    null manual_score until a teacher grades. total_score = auto_score
    until manual grading happens, then auto + manual."""
    exam = get_exam(exam_id)
    if not exam:
        raise ValueError("exam not found")

    auto_score = 0
    for i, q in enumerate(exam.questions):
        if q.get("kind", "mcq") == "mcq":
            given = (answers.get(str(i)) or "").strip().upper()
            if given and given == (q.get("answer") or "").strip().upper():
                auto_score += int(q.get("marks", 1))

    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE org_exam_attempts SET "
            " submitted_at = ?, answers_json = ?, "
            " auto_score = ?, total_score = ?, "
            " tab_blur_count = ?, fullscreen_exit_count = ?, "
            " flags_json = ? "
            "WHERE exam_id = ? AND user_id = ?",
            (now, json.dumps(answers), auto_score, auto_score,
             tab_blur_count, fullscreen_exit_count,
             json.dumps(flags or []), exam_id, user_id),
        )
        row = conn.execute(
            "SELECT id FROM org_exam_attempts WHERE exam_id = ? AND user_id = ?",
            (exam_id, user_id),
        ).fetchone()
    return _attempt_by_id(row[0])


def grade_attempt(
    *,
    attempt_id: str,
    manual_score: int,
    feedback: str | None = None,
) -> ExamAttempt:
    """Teacher posts manual marks for the free-form questions.
    Recomputes total_score = auto_score + manual_score."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT auto_score FROM org_exam_attempts WHERE id = ?",
            (attempt_id,),
        ).fetchone()
        if not row:
            raise ValueError("attempt not found")
        auto = row[0] or 0
        conn.execute(
            "UPDATE org_exam_attempts SET "
            " manual_score = ?, feedback = ?, total_score = ? "
            "WHERE id = ?",
            (manual_score, feedback, auto + manual_score, attempt_id),
        )
    return _attempt_by_id(attempt_id)


def _attempt_by_id(aid: str) -> ExamAttempt:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT id, exam_id, user_id, started_at, submitted_at, "
            "       answers_json, auto_score, manual_score, total_score, "
            "       feedback, tab_blur_count, fullscreen_exit_count, "
            "       flags_json FROM org_exam_attempts WHERE id = ?",
            (aid,),
        ).fetchone()
    return ExamAttempt(
        id=r[0], exam_id=r[1], user_id=r[2],
        started_at=r[3], submitted_at=r[4],
        answers=json.loads(r[5]) if r[5] else {},
        auto_score=r[6], manual_score=r[7], total_score=r[8],
        feedback=r[9],
        tab_blur_count=r[10] or 0,
        fullscreen_exit_count=r[11] or 0,
        flags=json.loads(r[12]) if r[12] else [],
    )


def list_attempts(exam_id: str, *, limit: int = 200) -> list[ExamAttempt]:
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id FROM org_exam_attempts WHERE exam_id = ? "
            "ORDER BY submitted_at DESC NULLS LAST LIMIT ?",
            (exam_id, limit),
        ).fetchall()
    return [_attempt_by_id(r[0]) for r in rows]


def has_active_exam(user_id: str) -> str | None:
    """S4: return the exam_id if the user has any active (started,
    not-submitted) attempt right now. Used to gate doubt-chat + voice
    tutor during exam mode."""
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT exam_id FROM org_exam_attempts "
            "WHERE user_id = ? AND submitted_at IS NULL "
            "LIMIT 1",
            (user_id,),
        ).fetchone()
    return r[0] if r else None


# ---------- E5: Fees + invoicing ----------

VALID_INVOICE_STATUSES = {"pending", "paid", "overdue", "cancelled", "refunded"}


@dataclass(frozen=True)
class FeeStructure:
    id: str
    org_id: str
    name: str
    amount_paise: int
    currency: str
    applies_to: str           # 'class:<id>' or 'all'
    due_date: str | None
    notes: str | None
    created_by: str
    created_at: float


@dataclass(frozen=True)
class FeeInvoice:
    id: str
    org_id: str
    user_id: str
    structure_id: str
    amount_paise: int
    currency: str
    status: str
    due_date: str | None
    paid_at: float | None
    razorpay_order_id: str | None
    razorpay_payment_id: str | None
    receipt_url: str | None
    notes: str | None
    created_at: float


def create_fee_structure(
    *,
    org_id: str,
    name: str,
    amount_paise: int,
    applies_to: str,
    created_by: str,
    due_date: str | None = None,
    notes: str | None = None,
    currency: str = "INR",
) -> FeeStructure:
    """Define a new fee. `applies_to` is 'all' or 'class:<class_id>'."""
    import uuid
    if amount_paise <= 0:
        raise ValueError("amount_paise must be > 0")
    if not name.strip():
        raise ValueError("name is required")
    if applies_to != "all" and not applies_to.startswith("class:"):
        raise ValueError("applies_to must be 'all' or 'class:<id>'")
    fid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO org_fee_structures "
            "(id, org_id, name, amount_paise, currency, applies_to, "
            " due_date, notes, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fid, org_id, name.strip(), amount_paise, currency, applies_to,
             due_date, notes, created_by, now),
        )
    return FeeStructure(
        id=fid, org_id=org_id, name=name.strip(),
        amount_paise=amount_paise, currency=currency,
        applies_to=applies_to, due_date=due_date, notes=notes,
        created_by=created_by, created_at=now,
    )


def list_fee_structures(org_id: str, *, limit: int = 200) -> list[FeeStructure]:
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, org_id, name, amount_paise, currency, applies_to, "
            "       due_date, notes, created_by, created_at "
            "FROM org_fee_structures WHERE org_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
    return [FeeStructure(*r) for r in rows]


def generate_invoices_for_structure(
    *,
    structure_id: str,
) -> dict:
    """Bulk-create invoices for every student the structure applies to.
    UNIQUE(structure_id, user_id) means re-running is safe — already-
    invoiced students get skipped.

    Returns {created: N, skipped_already_invoiced: M, students_targeted: K}.
    """
    import uuid
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, org_id, amount_paise, currency, applies_to, due_date "
            "FROM org_fee_structures WHERE id = ?",
            (structure_id,),
        ).fetchone()
        if not r:
            raise ValueError("fee structure not found")
        struct_id, org_id, amount_paise, currency, applies_to, due_date = r

        # Resolve target student user_ids
        if applies_to == "all":
            student_ids = [row[0] for row in conn.execute(
                "SELECT user_id FROM org_members "
                "WHERE org_id = ? AND role = 'student' AND user_id IS NOT NULL",
                (org_id,),
            ).fetchall()]
        elif applies_to.startswith("class:"):
            class_id = applies_to.split(":", 1)[1]
            student_ids = [row[0] for row in conn.execute(
                "SELECT user_id FROM org_members "
                "WHERE org_id = ? AND class_id = ? "
                "AND role = 'student' AND user_id IS NOT NULL",
                (org_id, class_id),
            ).fetchall()]
        else:
            student_ids = []

        created = 0
        skipped = 0
        now = time.time()
        for uid in student_ids:
            try:
                conn.execute(
                    "INSERT INTO org_fee_invoices "
                    "(id, org_id, user_id, structure_id, amount_paise, "
                    " currency, status, due_date, created_at) "
                    "VALUES (?,?,?,?,?,?,'pending',?,?)",
                    (uuid.uuid4().hex, org_id, uid, struct_id, amount_paise,
                     currency, due_date, now),
                )
                created += 1
            except sqlite3.IntegrityError:
                skipped += 1
    return {
        "created": created,
        "skipped_already_invoiced": skipped,
        "students_targeted": len(student_ids),
    }


def list_invoices(
    org_id: str,
    *,
    status: str | None = None,
    user_id: str | None = None,
    limit: int = 200,
) -> list[FeeInvoice]:
    where = ["org_id = ?"]
    params: list = [org_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    params.append(limit)
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT id, org_id, user_id, structure_id, amount_paise, "
            "       currency, status, due_date, paid_at, "
            "       razorpay_order_id, razorpay_payment_id, receipt_url, "
            "       notes, created_at "
            f"FROM org_fee_invoices WHERE {' AND '.join(where)} "
            "ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [FeeInvoice(*r) for r in rows]


def get_invoice(invoice_id: str) -> FeeInvoice | None:
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT id, org_id, user_id, structure_id, amount_paise, "
            "       currency, status, due_date, paid_at, "
            "       razorpay_order_id, razorpay_payment_id, receipt_url, "
            "       notes, created_at "
            "FROM org_fee_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
    return FeeInvoice(*r) if r else None


def attach_razorpay_order(*, invoice_id: str, order_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE org_fee_invoices SET razorpay_order_id = ? WHERE id = ?",
            (order_id, invoice_id),
        )


def mark_invoice_paid(
    *,
    invoice_id: str,
    razorpay_payment_id: str | None = None,
    receipt_url: str | None = None,
) -> FeeInvoice:
    """Idempotent — re-calling on an already-paid invoice is a no-op."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT status FROM org_fee_invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        if not r:
            raise ValueError("invoice not found")
        if r[0] == "paid":
            return get_invoice(invoice_id)
        conn.execute(
            "UPDATE org_fee_invoices SET status = 'paid', paid_at = ?, "
            " razorpay_payment_id = COALESCE(?, razorpay_payment_id), "
            " receipt_url = COALESCE(?, receipt_url) "
            "WHERE id = ?",
            (time.time(), razorpay_payment_id, receipt_url, invoice_id),
        )
    return get_invoice(invoice_id)


def find_invoice_by_order(order_id: str) -> FeeInvoice | None:
    """Lookup for the Razorpay webhook handler."""
    with _conn(read_only=True) as conn:
        r = conn.execute(
            "SELECT id FROM org_fee_invoices WHERE razorpay_order_id = ?",
            (order_id,),
        ).fetchone()
    return get_invoice(r[0]) if r else None


def cancel_invoice(invoice_id: str) -> FeeInvoice:
    with _conn() as conn:
        conn.execute(
            "UPDATE org_fee_invoices SET status = 'cancelled' "
            "WHERE id = ? AND status NOT IN ('paid', 'refunded')",
            (invoice_id,),
        )
    return get_invoice(invoice_id)


def fee_summary(org_id: str) -> dict:
    """Aggregate counts + totals for the admin dashboard."""
    with _conn(read_only=True) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(amount_paise), 0) "
            "FROM org_fee_invoices WHERE org_id = ? GROUP BY status",
            (org_id,),
        ).fetchall()
    counts: dict[str, int] = {}
    totals_paise: dict[str, int] = {}
    for status, count, total in rows:
        counts[status] = count
        totals_paise[status] = total
    return {
        "counts": counts,
        "totals_paise": totals_paise,
        "collected_paise": totals_paise.get("paid", 0),
        "pending_paise": totals_paise.get("pending", 0),
        "overdue_paise": totals_paise.get("overdue", 0),
    }


def require_role(*, org_id: str, user_id: str, allowed: set[str]) -> None:
    """Raise PermissionError if the user's role in the org isn't in
    `allowed`. Used by API handlers before any mutation."""
    role = user_role_in_org(org_id=org_id, user_id=user_id)
    if role is None:
        raise PermissionError("not a member of this org")
    if role not in allowed:
        raise PermissionError(
            f"role {role!r} not authorised (need one of {sorted(allowed)})"
        )
