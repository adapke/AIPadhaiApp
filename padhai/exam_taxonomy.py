"""v3.1 — Exam taxonomy + Exam Packs.

Per gap-review §5 — student doesn't choose AI Pathshala because we
have 400 routes. They choose because we help them pass a specific
exam. The unit of value is an **Exam Pack**: the bundle of
syllabus + pattern + PYQs + topic weightage + community + mentor
list that a student lives inside while preparing.

Six tables:
  exam_segments        kinder / school / college / competitive / govt / job / professional / research
  exam_bodies          CBSE, NTA, UPSC, SSC, IBPS, RBI, RRB, ...
  exams                CBSE Class 10, UPSC CSE, SSC CGL, JEE Main, NEET UG, ...
  exam_topics          per-exam topic tree: chapter → topic → subtopic, with weightage
  exam_packs           the bundle students enroll into
  exam_pack_enrollments  student → pack subscription, drives daily-plan, mock list, community

Taxonomy is seeded on migrate. The 5 deep packs from review
§Phase 2 (CBSE 10, CBSE 12, UPSC CSE, SSC CGL, IBPS PO) are also
seeded as empty `exam_packs` rows; content lives in J6
question_bank + O1/O2/O3 marketplaces. The packs *bundle*; they
don't store content.
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
CREATE TABLE IF NOT EXISTS exam_segments (
    code            TEXT PRIMARY KEY,            -- 'kinder' / 'school' / 'college' / 'competitive' / 'govt' / 'job' / 'professional' / 'research'
    title           TEXT NOT NULL,
    description     TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS exam_bodies (
    code            TEXT PRIMARY KEY,             -- 'cbse' / 'icse' / 'upsc' / 'ssc' / 'ibps' / 'nta' / 'rbi' / 'rrb' / 'aibe' / 'icai'
    title           TEXT NOT NULL,
    country         TEXT NOT NULL DEFAULT 'IN',
    description     TEXT,
    website         TEXT
);

CREATE TABLE IF NOT EXISTS exams (
    code            TEXT PRIMARY KEY,             -- 'cbse_class_10' / 'upsc_cse' / 'ssc_cgl' / 'jee_main' / ...
    body_code       TEXT NOT NULL,
    segment_code    TEXT NOT NULL,
    title           TEXT NOT NULL,
    short_title     TEXT,                          -- 'UPSC CSE'
    level           TEXT,                          -- 'class_10' / 'graduate' / 'group_b' / ...
    description     TEXT,
    languages       TEXT,                          -- JSON array
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exams_lookup
    ON exams(segment_code, body_code, is_active);

CREATE TABLE IF NOT EXISTS exam_topics (
    id              TEXT PRIMARY KEY,
    exam_code       TEXT NOT NULL,
    parent_id       TEXT,                          -- NULL = chapter; else topic/subtopic
    code            TEXT NOT NULL,                 -- 'polity_fundamental_rights'
    title           TEXT NOT NULL,
    depth           INTEGER NOT NULL DEFAULT 0,    -- 0 = chapter, 1 = topic, 2 = subtopic
    weightage_pct   REAL,                          -- 0..100 — % of exam marks
    learning_objectives TEXT,                       -- JSON array
    sort_order      INTEGER NOT NULL DEFAULT 100,
    UNIQUE (exam_code, code)
);
CREATE INDEX IF NOT EXISTS idx_etopics_exam
    ON exam_topics(exam_code, parent_id, sort_order);

CREATE TABLE IF NOT EXISTS exam_packs (
    code            TEXT PRIMARY KEY,             -- 'cbse_class_10_2026' / 'upsc_cse_2026'
    exam_code       TEXT NOT NULL,
    title           TEXT NOT NULL,
    year            INTEGER,                       -- target attempt year
    description     TEXT,
    syllabus_url    TEXT,
    pattern_summary TEXT,                          -- '100 MCQs, 90 min, +4/-1 negative marking'
    cutoff_summary  TEXT,                          -- last 3 yrs cutoffs, plain text
    estimated_hours INTEGER,                       -- total prep effort
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'archived'
    enrollment_count INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_epacks_exam
    ON exam_packs(exam_code, status);

CREATE TABLE IF NOT EXISTS exam_pack_enrollments (
    id              TEXT PRIMARY KEY,
    pack_code       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    target_date     REAL,                          -- when the student plans to sit the exam
    daily_minutes   INTEGER NOT NULL DEFAULT 60,
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'paused' | 'completed' | 'abandoned'
    enrolled_at     REAL NOT NULL,
    completed_at    REAL,
    UNIQUE (pack_code, user_id)
);
CREATE INDEX IF NOT EXISTS idx_epenroll_user
    ON exam_pack_enrollments(user_id, status);
"""


VALID_SEGMENT_CODES = frozenset({
    "kinder", "school", "college", "competitive",
    "govt", "job", "professional", "research",
})

VALID_PACK_STATUSES = frozenset({"active", "archived"})

VALID_ENROLLMENT_STATUSES = frozenset({
    "active", "paused", "completed", "abandoned",
})


# Seed data — comes from the review §5 + §10. India-first taxonomy.
# This is the catalog; content sits in question_bank + lessons.

_SEGMENT_SEED = [
    ("kinder", "Kindergarten (LKG-UKG)", 10),
    ("school", "School (Class 1-12)", 20),
    ("college", "College / University", 30),
    ("competitive", "Competitive Exams (JEE/NEET/CUET/CAT/CLAT)", 40),
    ("govt", "Government Exams (UPSC/SSC/Banking/Railways/Defence)", 50),
    ("job", "Private Job / Career Skills", 60),
    ("professional", "Professional (CA/CS/CMA/Law/Medical/PhD)", 70),
    ("research", "Research / PhD", 80),
]

_BODY_SEED = [
    ("cbse", "Central Board of Secondary Education", "IN"),
    ("icse", "Indian Certificate of Secondary Education", "IN"),
    ("nios", "National Institute of Open Schooling", "IN"),
    ("upsc", "Union Public Service Commission", "IN"),
    ("ssc", "Staff Selection Commission", "IN"),
    ("ibps", "Institute of Banking Personnel Selection", "IN"),
    ("rbi", "Reserve Bank of India", "IN"),
    ("rrb", "Railway Recruitment Board", "IN"),
    ("nta", "National Testing Agency", "IN"),
    ("upsc_nda", "UPSC (NDA / CDS)", "IN"),
    ("ctet", "Central Teacher Eligibility Test", "IN"),
    ("icai", "Institute of Chartered Accountants of India", "IN"),
    ("aibe", "All India Bar Examination", "IN"),
]

# Top exams per body — the 5 deep packs from review §Phase 2 are the
# first 5 of this list. We seed beyond those because the taxonomy
# is content-free; adding rows costs nothing and lets future
# Exam Packs reference them.
_EXAM_SEED = [
    # body, code, segment, title, short_title, level, languages
    ("cbse", "cbse_class_10", "school", "CBSE Class 10 Board",
     "CBSE Class 10", "class_10", ["en", "hi"]),
    ("cbse", "cbse_class_12", "school", "CBSE Class 12 Board",
     "CBSE Class 12", "class_12", ["en", "hi"]),
    ("upsc", "upsc_cse", "govt", "UPSC Civil Services Examination",
     "UPSC CSE", "graduate",
     ["en", "hi", "mr", "bn", "ta", "te", "gu", "kn", "ml"]),
    ("ssc", "ssc_cgl", "govt", "SSC Combined Graduate Level",
     "SSC CGL", "graduate", ["en", "hi"]),
    ("ibps", "ibps_po", "govt", "IBPS Probationary Officer",
     "IBPS PO", "graduate", ["en", "hi"]),
    # Additional taxonomy entries — packs will be authored later
    ("ibps", "ibps_clerk", "govt", "IBPS Clerk",
     "IBPS Clerk", "graduate", ["en", "hi"]),
    ("rbi", "rbi_grade_b", "govt", "RBI Grade B Officer",
     "RBI Grade B", "graduate", ["en"]),
    ("rrb", "rrb_ntpc", "govt", "RRB Non-Technical Popular Categories",
     "RRB NTPC", "12_pass", ["en", "hi"]),
    ("ssc", "ssc_chsl", "govt", "SSC Combined Higher Secondary Level",
     "SSC CHSL", "12_pass", ["en", "hi"]),
    ("ssc", "ssc_mts", "govt", "SSC Multi Tasking Staff",
     "SSC MTS", "10_pass", ["en", "hi"]),
    ("nta", "jee_main", "competitive",
     "JEE Main (Engineering Entrance)",
     "JEE Main", "class_12", ["en", "hi"]),
    ("nta", "neet_ug", "competitive",
     "NEET UG (Medical Entrance)",
     "NEET UG", "class_12",
     ["en", "hi", "ta", "te", "mr", "bn", "kn", "ml"]),
    ("nta", "cuet_ug", "competitive",
     "CUET UG (Common University Entrance)",
     "CUET UG", "class_12", ["en", "hi"]),
    ("upsc_nda", "upsc_nda", "govt", "UPSC NDA / NA",
     "NDA", "class_12", ["en", "hi"]),
    ("upsc_nda", "upsc_cds", "govt", "UPSC Combined Defence Services",
     "CDS", "graduate", ["en"]),
    ("ctet", "ctet", "govt", "Central Teacher Eligibility Test",
     "CTET", "graduate", ["en", "hi"]),
    ("icai", "ca_foundation", "professional",
     "CA Foundation (ICAI)", "CA Foundation",
     "12_pass", ["en", "hi"]),
    ("aibe", "aibe", "professional",
     "All India Bar Examination", "AIBE",
     "law_graduate", ["en", "hi"]),
]

# Topic trees — review §5 requires per-exam taxonomy. We seed the
# CHAPTER level for the 5 deep packs (depth=0); topics + subtopics
# get ingested via the J6 worker pipeline + expert review. Wider
# coverage is content work, not engineering.
_TOPIC_SEED = {
    # CBSE Class 10 — Mathematics chapters (NCERT 2025-26)
    "cbse_class_10": [
        ("real_numbers", "Real Numbers", 6.0),
        ("polynomials", "Polynomials", 7.0),
        ("linear_equations", "Pair of Linear Equations in Two Variables", 6.0),
        ("quadratic_equations", "Quadratic Equations", 6.0),
        ("arithmetic_progressions", "Arithmetic Progressions", 5.0),
        ("triangles", "Triangles", 9.0),
        ("coord_geometry", "Coordinate Geometry", 6.0),
        ("trigonometry", "Introduction to Trigonometry", 8.0),
        ("circles", "Circles", 6.0),
        ("areas_volumes", "Surface Areas and Volumes", 7.0),
        ("statistics", "Statistics", 6.0),
        ("probability", "Probability", 5.0),
    ],
    # CBSE Class 12 — Physics chapters
    "cbse_class_12": [
        ("electrostatics", "Electric Charges and Fields", 8.0),
        ("current_electricity", "Current Electricity", 7.0),
        ("magnetic_effects", "Magnetic Effects of Current", 6.0),
        ("emi", "Electromagnetic Induction", 5.0),
        ("ac_circuits", "Alternating Current", 5.0),
        ("optics", "Ray and Wave Optics", 14.0),
        ("modern_physics", "Modern Physics", 11.0),
        ("electronics", "Semiconductor Electronics", 7.0),
    ],
    # UPSC CSE — GS Paper I structure
    "upsc_cse": [
        ("history_ancient", "Indian History — Ancient", 4.0),
        ("history_medieval", "Indian History — Medieval", 3.0),
        ("history_modern", "Indian History — Modern", 12.0),
        ("art_culture", "Indian Art and Culture", 5.0),
        ("polity", "Indian Polity and Governance", 16.0),
        ("economy", "Indian Economy", 14.0),
        ("geography_india", "Indian Geography", 8.0),
        ("geography_world", "World Geography", 5.0),
        ("environment", "Environment and Ecology", 12.0),
        ("science_tech", "Science and Technology", 10.0),
        ("current_affairs", "Current Affairs", 11.0),
    ],
    # SSC CGL — Tier 1 sections
    "ssc_cgl": [
        ("reasoning", "General Intelligence and Reasoning", 25.0),
        ("gk", "General Knowledge / Awareness", 25.0),
        ("quant", "Quantitative Aptitude", 25.0),
        ("english", "English Comprehension", 25.0),
    ],
    # IBPS PO — Preliminary + Mains topics
    "ibps_po": [
        ("english_lang", "English Language", 20.0),
        ("quant_aptitude", "Quantitative Aptitude", 25.0),
        ("reasoning_ability", "Reasoning Ability", 25.0),
        ("computer", "Computer Knowledge", 8.0),
        ("banking_awareness", "General/Banking Awareness", 22.0),
    ],
}

# The 5 deep Exam Packs from review §Phase 2.
_PACK_SEED = [
    ("cbse_class_10_2026", "cbse_class_10",
     "CBSE Class 10 — 2026 Boards", 2026,
     ("Complete 2026 board prep across all 6 subjects: "
      "NCERT chapter mastery, sample papers, PYQs, "
      "chapter tests, board-pattern mocks, parent dashboard."),
     "5 subjects × 80 marks + Internal Assessment 20 marks",
     "Pass: 33% per subject; Distinction: 90%+ aggregate",
     600),
    ("cbse_class_12_2026", "cbse_class_12",
     "CBSE Class 12 — 2026 Boards + JEE/NEET Bridge", 2026,
     ("Dual-mode prep: Class 12 boards + JEE Main/NEET UG "
      "topics interleaved. Formula sheets, PYQs from "
      "2015-2025, weak-topic tracker, timed practice."),
     "Boards: 5 subjects × 70 marks + Practical 30 marks",
     "Pass: 33% per subject",
     800),
    ("upsc_cse_2026", "upsc_cse",
     "UPSC CSE 2026 — Prelims + Mains", 2026,
     ("Full UPSC CSE prep: NCERT base, standard books "
      "tracker, GS 1-4 + CSAT, daily current affairs, "
      "answer-writing practice, optional subject support, "
      "interview prep."),
     ("Prelims: 2 papers × 200 marks (MCQ); "
      "Mains: 9 papers (descriptive); "
      "Interview: 275 marks"),
     "Prelims GS-I 2024 cutoff: 87.54 (Gen)",
     2400),
    ("ssc_cgl_2026", "ssc_cgl",
     "SSC CGL 2026 — Tier 1 + Tier 2", 2026,
     ("Tier 1 (CBT 100Q × 2 marks, 60 min) + Tier 2 "
      "(Quant + English + AAO/JSO papers). Daily current "
      "affairs, speed math drills, PYQs from 2015-2024."),
     ("Tier 1: 100 MCQ in 60 min, +2/-0.5 negative; "
      "Tier 2: Multi-paper based on post"),
     "Tier 1 2023 cutoff: ~125 (Gen, JSO)",
     400),
    ("ibps_po_2026", "ibps_po",
     "IBPS PO 2026 — Prelims + Mains + Interview", 2026,
     ("Prelims (English / Quant / Reasoning, 60 min) + "
      "Mains (5 sections, descriptive English) + "
      "Interview. Daily banking awareness, current "
      "affairs, sectional + full mocks."),
     ("Prelims: 100 MCQ in 60 min, sectional time; "
      "Mains: 200 MCQ + 25 marks descriptive in 200 min"),
     "Prelims 2023 cutoff: ~58-62 (Gen)",
     350),
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
    """Idempotent seed of the v3.1 taxonomy. Each section uses
    INSERT OR IGNORE so re-running on existing DB is a no-op."""
    with _conn() as conn:
        now = time.time()
        for code, title, sort in _SEGMENT_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO exam_segments "
                "(code, title, sort_order) VALUES (?, ?, ?)",
                (code, title, sort),
            )
        for code, title, country in _BODY_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO exam_bodies "
                "(code, title, country) VALUES (?, ?, ?)",
                (code, title, country),
            )
        for body, code, segment, title, short, level, langs in _EXAM_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO exams "
                "(code, body_code, segment_code, title, short_title, "
                " level, languages, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (code, body, segment, title, short, level,
                 json.dumps(langs), now),
            )
        for exam_code, chapters in _TOPIC_SEED.items():
            for sort, (tcode, ttitle, weight) in enumerate(chapters, start=10):
                conn.execute(
                    "INSERT OR IGNORE INTO exam_topics "
                    "(id, exam_code, parent_id, code, title, "
                    " depth, weightage_pct, sort_order) "
                    "VALUES (?, ?, NULL, ?, ?, 0, ?, ?)",
                    (uuid.uuid4().hex, exam_code, tcode, ttitle,
                     weight, sort * 10),
                )
        for code, exam, title, year, desc, pat, cut, hours in _PACK_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO exam_packs "
                "(code, exam_code, title, year, description, "
                " pattern_summary, cutoff_summary, "
                " estimated_hours, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (code, exam, title, year, desc, pat, cut, hours, now),
            )


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Segment:
    code: str
    title: str
    description: str | None
    sort_order: int


@dataclass(frozen=True)
class Body:
    code: str
    title: str
    country: str
    description: str | None
    website: str | None


@dataclass(frozen=True)
class Exam:
    code: str
    body_code: str
    segment_code: str
    title: str
    short_title: str | None
    level: str | None
    languages: list[str]
    is_active: bool
    created_at: float


@dataclass(frozen=True)
class Topic:
    id: str
    exam_code: str
    parent_id: str | None
    code: str
    title: str
    depth: int
    weightage_pct: float | None
    learning_objectives: list[str]
    sort_order: int


@dataclass(frozen=True)
class ExamPack:
    code: str
    exam_code: str
    title: str
    year: int | None
    description: str | None
    syllabus_url: str | None
    pattern_summary: str | None
    cutoff_summary: str | None
    estimated_hours: int | None
    status: str
    enrollment_count: int
    created_at: float


@dataclass(frozen=True)
class PackEnrollment:
    id: str
    pack_code: str
    user_id: str
    target_date: float | None
    daily_minutes: int
    status: str
    enrolled_at: float
    completed_at: float | None


# ---------- catalog reads ----------

def list_segments() -> list[Segment]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT code, title, description, sort_order "
            "FROM exam_segments ORDER BY sort_order",
        ).fetchall()
    return [Segment(*r) for r in rows]


def list_bodies(*, country: str | None = None) -> list[Body]:
    sql = (
        "SELECT code, title, country, description, website "
        "FROM exam_bodies"
    )
    params: list = []
    if country:
        sql += " WHERE country = ?"; params.append(country)
    sql += " ORDER BY title"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Body(*r) for r in rows]


def list_exams(
    *,
    segment_code: str | None = None,
    body_code: str | None = None,
    active_only: bool = True,
) -> list[Exam]:
    sql = (
        "SELECT code, body_code, segment_code, title, short_title, "
        "       level, languages, is_active, created_at "
        "FROM exams"
    )
    where: list[str] = []
    params: list = []
    if active_only:
        where.append("is_active = 1")
    if segment_code:
        if segment_code not in VALID_SEGMENT_CODES:
            raise ValueError(
                f"segment_code must be in {sorted(VALID_SEGMENT_CODES)}",
            )
        where.append("segment_code = ?"); params.append(segment_code)
    if body_code:
        where.append("body_code = ?"); params.append(body_code)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY segment_code, title"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Exam(
            code=r[0], body_code=r[1], segment_code=r[2],
            title=r[3], short_title=r[4], level=r[5],
            languages=json.loads(r[6]) if r[6] else [],
            is_active=bool(r[7]), created_at=r[8],
        )
        for r in rows
    ]


def get_exam(code: str) -> Exam | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT code, body_code, segment_code, title, short_title, "
            "       level, languages, is_active, created_at "
            "FROM exams WHERE code = ?", (code,),
        ).fetchone()
    if not r:
        return None
    return Exam(
        code=r[0], body_code=r[1], segment_code=r[2],
        title=r[3], short_title=r[4], level=r[5],
        languages=json.loads(r[6]) if r[6] else [],
        is_active=bool(r[7]), created_at=r[8],
    )


def list_topics(
    exam_code: str,
    *,
    parent_id: str | None = None,
    depth: int | None = None,
) -> list[Topic]:
    """List topics under an exam. With parent_id=None + depth=0 you
    get the chapter list; pass parent_id=<chapter_id> to drill in."""
    sql = (
        "SELECT id, exam_code, parent_id, code, title, depth, "
        "       weightage_pct, learning_objectives, sort_order "
        "FROM exam_topics WHERE exam_code = ?"
    )
    params: list = [exam_code]
    if parent_id is None:
        sql += " AND parent_id IS NULL"
    else:
        sql += " AND parent_id = ?"; params.append(parent_id)
    if depth is not None:
        sql += " AND depth = ?"; params.append(depth)
    sql += " ORDER BY sort_order, title"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Topic(
            id=r[0], exam_code=r[1], parent_id=r[2], code=r[3],
            title=r[4], depth=r[5], weightage_pct=r[6],
            learning_objectives=json.loads(r[7]) if r[7] else [],
            sort_order=r[8],
        )
        for r in rows
    ]


def add_topic(
    *,
    exam_code: str,
    code: str,
    title: str,
    parent_id: str | None = None,
    depth: int = 0,
    weightage_pct: float | None = None,
    learning_objectives: list[str] | None = None,
    sort_order: int = 100,
) -> Topic:
    if not get_exam(exam_code):
        raise ValueError(f"exam {exam_code!r} not found")
    if depth < 0 or depth > 2:
        raise ValueError("depth must be in [0, 2]")
    if weightage_pct is not None and (weightage_pct < 0 or weightage_pct > 100):
        raise ValueError("weightage_pct must be in [0, 100]")
    title = (title or "").strip()
    if len(title) < 2 or len(title) > 200:
        raise ValueError("title must be [2, 200] chars")
    tid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO exam_topics "
                "(id, exam_code, parent_id, code, title, depth, "
                " weightage_pct, learning_objectives, sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, exam_code, parent_id, code, title, depth,
                 weightage_pct,
                 json.dumps(learning_objectives) if learning_objectives else None,
                 sort_order),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"topic code {code!r} already exists for {exam_code!r}",
        ) from e
    return Topic(
        id=tid, exam_code=exam_code, parent_id=parent_id,
        code=code, title=title, depth=depth,
        weightage_pct=weightage_pct,
        learning_objectives=learning_objectives or [],
        sort_order=sort_order,
    )


# ---------- exam packs ----------

def list_packs(
    *,
    exam_code: str | None = None,
    active_only: bool = True,
) -> list[ExamPack]:
    sql = (
        "SELECT code, exam_code, title, year, description, "
        "       syllabus_url, pattern_summary, cutoff_summary, "
        "       estimated_hours, status, enrollment_count, created_at "
        "FROM exam_packs"
    )
    where: list[str] = []
    params: list = []
    if active_only:
        where.append("status = 'active'")
    if exam_code:
        where.append("exam_code = ?"); params.append(exam_code)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY enrollment_count DESC, year DESC, title"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_pack(r) for r in rows]


def get_pack(code: str) -> ExamPack | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT code, exam_code, title, year, description, "
            "       syllabus_url, pattern_summary, cutoff_summary, "
            "       estimated_hours, status, enrollment_count, created_at "
            "FROM exam_packs WHERE code = ?", (code,),
        ).fetchone()
    return _row_to_pack(r) if r else None


def _row_to_pack(r) -> ExamPack:
    return ExamPack(
        code=r[0], exam_code=r[1], title=r[2], year=r[3],
        description=r[4], syllabus_url=r[5],
        pattern_summary=r[6], cutoff_summary=r[7],
        estimated_hours=r[8], status=r[9],
        enrollment_count=r[10], created_at=r[11],
    )


def create_pack(
    *,
    code: str,
    exam_code: str,
    title: str,
    year: int | None = None,
    description: str | None = None,
    syllabus_url: str | None = None,
    pattern_summary: str | None = None,
    cutoff_summary: str | None = None,
    estimated_hours: int | None = None,
) -> ExamPack:
    if not get_exam(exam_code):
        raise ValueError(f"exam {exam_code!r} not found")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if year is not None and (year < 2020 or year > 2050):
        raise ValueError("year must be in [2020, 2050]")
    if estimated_hours is not None and (
        estimated_hours < 1 or estimated_hours > 10000
    ):
        raise ValueError("estimated_hours must be in [1, 10000]")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO exam_packs "
                "(code, exam_code, title, year, description, "
                " syllabus_url, pattern_summary, cutoff_summary, "
                " estimated_hours, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (code, exam_code, title, year, description,
                 syllabus_url, pattern_summary, cutoff_summary,
                 estimated_hours, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(f"pack code {code!r} already exists") from e
    return get_pack(code)  # type: ignore[return-value]


def archive_pack(code: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE exam_packs SET status = 'archived' "
            "WHERE code = ? AND status != 'archived'",
            (code,),
        )
        return cur.rowcount > 0


# ---------- enrollments ----------

def enroll(
    *,
    pack_code: str,
    user_id: str,
    target_date: float | None = None,
    daily_minutes: int = 60,
) -> PackEnrollment:
    pack = get_pack(pack_code)
    if not pack:
        raise ValueError(f"pack {pack_code!r} not found")
    if pack.status != "active":
        raise ValueError(f"pack is {pack.status!r}, not enrollable")
    if daily_minutes < 10 or daily_minutes > 720:
        raise ValueError("daily_minutes must be in [10, 720]")
    eid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO exam_pack_enrollments "
                "(id, pack_code, user_id, target_date, "
                " daily_minutes, status, enrolled_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?)",
                (eid, pack_code, user_id, target_date,
                 daily_minutes, time.time()),
            )
            conn.execute(
                "UPDATE exam_packs SET "
                " enrollment_count = enrollment_count + 1 "
                "WHERE code = ?", (pack_code,),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError("already enrolled in this pack") from e
    return get_enrollment(eid)  # type: ignore[return-value]


def get_enrollment(enrollment_id: str) -> PackEnrollment | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, pack_code, user_id, target_date, "
            "       daily_minutes, status, enrolled_at, completed_at "
            "FROM exam_pack_enrollments WHERE id = ?",
            (enrollment_id,),
        ).fetchone()
    if not r:
        return None
    return PackEnrollment(
        id=r[0], pack_code=r[1], user_id=r[2],
        target_date=r[3], daily_minutes=r[4],
        status=r[5], enrolled_at=r[6], completed_at=r[7],
    )


def list_user_enrollments(user_id: str) -> list[PackEnrollment]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, pack_code, user_id, target_date, "
            "       daily_minutes, status, enrolled_at, completed_at "
            "FROM exam_pack_enrollments "
            "WHERE user_id = ? ORDER BY enrolled_at DESC",
            (user_id,),
        ).fetchall()
    return [
        PackEnrollment(
            id=r[0], pack_code=r[1], user_id=r[2],
            target_date=r[3], daily_minutes=r[4], status=r[5],
            enrolled_at=r[6], completed_at=r[7],
        )
        for r in rows
    ]


def set_enrollment_status(
    *,
    enrollment_id: str,
    status: str,
) -> bool:
    if status not in VALID_ENROLLMENT_STATUSES:
        raise ValueError(
            f"status must be in {sorted(VALID_ENROLLMENT_STATUSES)}",
        )
    completed_at = time.time() if status == "completed" else None
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE exam_pack_enrollments SET "
            " status = ?, "
            " completed_at = COALESCE(?, completed_at) "
            "WHERE id = ?",
            (status, completed_at, enrollment_id),
        )
        return cur.rowcount > 0


def pack_stats(pack_code: str) -> dict:
    with _conn() as conn:
        r = conn.execute(
            "SELECT status, COUNT(*) "
            "FROM exam_pack_enrollments WHERE pack_code = ? "
            "GROUP BY status",
            (pack_code,),
        ).fetchall()
    by_status = {row[0]: row[1] for row in r}
    total = sum(by_status.values())
    return {
        "total_enrollments": total,
        "by_status": by_status,
        "active_rate": (
            round(by_status.get("active", 0) / total, 4)
            if total else 0.0
        ),
    }


# Map an exam body code → the board_hint key pedagogy.BOARD_GUIDANCE expects.
# Anything not listed here falls back to the body code uppercased.
_BODY_TO_BOARD_HINT: dict[str, str] = {
    "cbse": "CBSE",
    "icse": "ICSE",
    "igcse": "IGCSE",
    "maharashtra": "Maharashtra",
    "karnataka": "Karnataka",
    "tamil_nadu": "TamilNadu",
    "ap_telangana": "AP_Telangana",
    "up_board": "UP",
    "nta": "JEE",   # NTA conducts both JEE Main and NEET — JEE is the safer default
    "upsc": "UPSC",
    "ssc": "SSC",
}

# Map specific exam codes that should override the body-level mapping. NEET
# is conducted by NTA but should use the NEET guidance, not JEE.
_EXAM_TO_BOARD_HINT: dict[str, str] = {
    "neet_ug": "NEET",
    "neet_pg": "NEET",
    "jee_main": "JEE",
    "jee_advanced": "JEE",
    "upsc_cse": "UPSC",
    "ssc_cgl": "SSC",
    "ssc_chsl": "SSC",
}


def board_hint_for_exam(exam_code: str) -> str | None:
    """Translate an exam code to the BOARD_GUIDANCE key used by
    pedagogy. Returns None when no mapping exists."""
    if exam_code in _EXAM_TO_BOARD_HINT:
        return _EXAM_TO_BOARD_HINT[exam_code]
    exam = get_exam(exam_code)
    if not exam:
        return None
    return _BODY_TO_BOARD_HINT.get(exam.body_code)


def taxonomy_scope_for_user(user_id: str) -> dict | None:
    """Derive a lesson-generation scope from the user's most recent
    active exam-pack enrollment. Used by pedagogy.generate_lesson to
    auto-fill board_hint and inject an in-scope topic list into the
    prompt.

    Returns None when the user has no active enrollment (lesson
    generation then falls back to the caller-supplied board_hint, or
    no guidance at all).
    """
    enrollments = [
        e for e in list_user_enrollments(user_id) if e.status == "active"
    ]
    if not enrollments:
        return None
    pack = get_pack(enrollments[0].pack_code)
    if not pack:
        return None
    exam = get_exam(pack.exam_code)
    if not exam:
        return None
    chapters = list_topics(exam.code, parent_id=None, depth=0)
    chapter_titles = [c.title for c in chapters][:40]
    board_hint = board_hint_for_exam(exam.code)
    scope_summary = (
        f"Student is preparing for {exam.short_title or exam.title} "
        f"(pack: {pack.title}). Stay strictly within this syllabus. "
        f"In-scope chapters: {'; '.join(chapter_titles) or 'see exam taxonomy'}."
    )
    return {
        "exam_code": exam.code,
        "exam_title": exam.short_title or exam.title,
        "pack_code": pack.code,
        "board_hint": board_hint,
        "chapter_titles": chapter_titles,
        "scope_summary": scope_summary,
    }
