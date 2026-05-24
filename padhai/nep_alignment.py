"""P1 — NEP 2020 + NCF 2023 framework alignment.

Maps every lesson to:
  - NEP 2020 21st-century skills + foundational outcomes
  - NCF 2023 stage-specific competencies (foundational / preparatory /
    middle / secondary)

Report shows NEP/NCF coverage per student + per org for inspection
(govt audits, NEP-compliance certificates, parent transparency).

Three tables:
  nep_competencies          21st-century skills + foundational outcomes
  ncf_competencies          NCF curriculum outcomes by stage + area
  lesson_alignment          (lesson_id, framework, competency_key, score)

Catalog seeded on migrate(). Ingest of full NCF detail is an ops
task — the seed below covers the high-level NEP/NCF entries enough
to score against.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS nep_competencies (
    key             TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    description     TEXT NOT NULL,
    category        TEXT NOT NULL,     -- 'fln' | '21cs' | 'multidisciplinary' | 'digital'
    keywords        TEXT,               -- JSON array for scoring
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ncf_competencies (
    key             TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    description     TEXT NOT NULL,
    stage           TEXT NOT NULL,     -- 'foundational' | 'preparatory' | 'middle' | 'secondary'
    area            TEXT NOT NULL,     -- 'languages' | 'mathematics' | 'sciences' | ...
    keywords        TEXT,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lesson_alignment (
    id              TEXT PRIMARY KEY,
    lesson_id       TEXT NOT NULL,
    framework       TEXT NOT NULL,     -- 'nep' | 'ncf'
    competency_key  TEXT NOT NULL,
    score           REAL NOT NULL,      -- 0..1 coverage
    created_at      REAL NOT NULL,
    UNIQUE (lesson_id, framework, competency_key)
);
CREATE INDEX IF NOT EXISTS idx_la_lesson
    ON lesson_alignment(lesson_id);
CREATE INDEX IF NOT EXISTS idx_la_framework_key
    ON lesson_alignment(framework, competency_key);
"""


VALID_FRAMEWORKS = frozenset({"nep", "ncf"})

VALID_STAGES = frozenset({
    "foundational", "preparatory", "middle", "secondary",
})


# Starter catalog — seeded on first migrate(). Real catalog ingestion
# (~120 entries from the official PDFs) is an ops task.
_NEP_SEED = [
    {"key": "fln.literacy",
     "label": "Foundational Literacy",
     "category": "fln",
     "description": "Reading + writing with comprehension in grade-level text.",
     "keywords": ["read", "write", "literacy", "language", "vocabulary"]},
    {"key": "fln.numeracy",
     "label": "Foundational Numeracy",
     "category": "fln",
     "description": "Number sense, basic arithmetic, problem framing.",
     "keywords": ["number", "count", "arithmetic", "addition", "subtraction"]},
    {"key": "21cs.critical_thinking",
     "label": "Critical Thinking",
     "category": "21cs",
     "description": "Analyzing arguments, identifying assumptions, evaluating evidence.",
     "keywords": ["analyse", "analyze", "evaluate", "argument", "evidence", "compare"]},
    {"key": "21cs.problem_solving",
     "label": "Problem Solving",
     "category": "21cs",
     "description": "Breaking down problems + sequencing solutions + testing.",
     "keywords": ["solve", "problem", "approach", "strategy", "test"]},
    {"key": "21cs.creativity",
     "label": "Creativity + Innovation",
     "category": "21cs",
     "description": "Generating novel ideas + connections across domains.",
     "keywords": ["create", "design", "invent", "original", "imagine"]},
    {"key": "21cs.communication",
     "label": "Communication",
     "category": "21cs",
     "description": "Expressing ideas clearly + listening actively.",
     "keywords": ["explain", "present", "discuss", "describe", "communicate"]},
    {"key": "21cs.collaboration",
     "label": "Collaboration",
     "category": "21cs",
     "description": "Working effectively in teams toward shared goals.",
     "keywords": ["team", "together", "group", "share", "collaborate"]},
    {"key": "digital.literacy",
     "label": "Digital Literacy",
     "category": "digital",
     "description": "Using digital tools to search, create, evaluate, share.",
     "keywords": ["digital", "computer", "internet", "online", "software"]},
    {"key": "multi.interdisciplinary",
     "label": "Multidisciplinary Learning",
     "category": "multidisciplinary",
     "description": "Connecting concepts across subject boundaries.",
     "keywords": ["across", "connect", "real-world", "application", "history of"]},
    {"key": "21cs.ethical_reasoning",
     "label": "Ethical Reasoning + Values",
     "category": "21cs",
     "description": "Considering moral implications + civic responsibility.",
     "keywords": ["ethics", "moral", "responsibility", "fairness", "justice"]},
]

_NCF_SEED = [
    # Foundational stage (grades K-2, ages 3-8)
    {"key": "found.lang.oral",
     "label": "Foundational Oral Language",
     "stage": "foundational", "area": "languages",
     "description": "Listening + speaking in mother tongue with vocabulary growth.",
     "keywords": ["speak", "listen", "tell", "rhyme", "story"]},
    {"key": "found.num.counting",
     "label": "Counting + Number Sense (1-100)",
     "stage": "foundational", "area": "mathematics",
     "description": "Counting forward + backward, comparing, basic patterns.",
     "keywords": ["count", "number", "more", "less", "pattern"]},
    # Preparatory stage (grades 3-5, ages 8-11)
    {"key": "prep.lang.reading",
     "label": "Preparatory Reading Comprehension",
     "stage": "preparatory", "area": "languages",
     "description": "Read for meaning, summarize, infer.",
     "keywords": ["read", "summarise", "summarize", "comprehend", "infer"]},
    {"key": "prep.math.fractions",
     "label": "Preparatory Fractions + Decimals",
     "stage": "preparatory", "area": "mathematics",
     "description": "Conceptual understanding of fractions + decimal notation.",
     "keywords": ["fraction", "decimal", "half", "quarter", "ratio"]},
    {"key": "prep.sci.environment",
     "label": "Preparatory Environmental Studies",
     "stage": "preparatory", "area": "sciences",
     "description": "Living vs. non-living, plants, animals, weather, water.",
     "keywords": ["plant", "animal", "weather", "water", "environment"]},
    # Middle stage (grades 6-8, ages 11-14)
    {"key": "middle.sci.method",
     "label": "Scientific Method",
     "stage": "middle", "area": "sciences",
     "description": "Hypothesise + experiment + observe + conclude.",
     "keywords": ["hypothesis", "experiment", "observe", "conclude", "method"]},
    {"key": "middle.math.algebra",
     "label": "Middle-stage Algebra",
     "stage": "middle", "area": "mathematics",
     "description": "Variables, linear equations, simple expressions.",
     "keywords": ["variable", "equation", "algebra", "solve for", "expression"]},
    {"key": "middle.social.civics",
     "label": "Civics + Constitutional Awareness",
     "stage": "middle", "area": "social_sciences",
     "description": "Rights, duties, three organs of govt, democratic values.",
     "keywords": ["rights", "constitution", "democracy", "government", "citizen"]},
    # Secondary stage (grades 9-12, ages 14-18)
    {"key": "sec.math.calculus",
     "label": "Introductory Calculus",
     "stage": "secondary", "area": "mathematics",
     "description": "Limits, derivatives, integrals, application to physics.",
     "keywords": ["limit", "derivative", "integral", "calculus", "rate of change"]},
    {"key": "sec.sci.physics_mechanics",
     "label": "Mechanics + Newton's Laws",
     "stage": "secondary", "area": "sciences",
     "description": "Motion, force, momentum, energy conservation.",
     "keywords": ["newton", "force", "motion", "energy", "momentum"]},
    {"key": "sec.sci.biology_genetics",
     "label": "Genetics + Inheritance",
     "stage": "secondary", "area": "sciences",
     "description": "Mendelian genetics, DNA, evolution basics.",
     "keywords": ["gene", "dna", "inherit", "evolution", "mendel"]},
    {"key": "sec.lang.composition",
     "label": "Composition + Persuasive Writing",
     "stage": "secondary", "area": "languages",
     "description": "Structured argument essays, citation, audience awareness.",
     "keywords": ["essay", "thesis", "argument", "evidence", "introduction"]},
]


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
    """Schema + seed both NEP and NCF catalogs idempotently."""
    with _conn() as conn:
        for c in _NEP_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO nep_competencies "
                "(key, label, description, category, keywords, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (c["key"], c["label"], c["description"], c["category"],
                 json.dumps(c["keywords"]), time.time()),
            )
        for c in _NCF_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO ncf_competencies "
                "(key, label, description, stage, area, keywords, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c["key"], c["label"], c["description"], c["stage"],
                 c["area"], json.dumps(c["keywords"]), time.time()),
            )


# ---------- dataclasses ----------

@dataclass(frozen=True)
class NEPCompetency:
    key: str
    label: str
    description: str
    category: str
    keywords: list[str]


@dataclass(frozen=True)
class NCFCompetency:
    key: str
    label: str
    description: str
    stage: str
    area: str
    keywords: list[str]


@dataclass(frozen=True)
class AlignmentResult:
    framework: str
    matches: list[dict]   # [{competency_key, label, score}]
    total_competencies: int
    overall_score: float


# ---------- catalog reads ----------

def list_nep(*, category: str | None = None) -> list[NEPCompetency]:
    sql = ("SELECT key, label, description, category, keywords "
           "FROM nep_competencies")
    params: list = []
    if category:
        sql += " WHERE category = ?"; params.append(category)
    sql += " ORDER BY category, key"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        NEPCompetency(
            key=r[0], label=r[1], description=r[2],
            category=r[3],
            keywords=json.loads(r[4]) if r[4] else [],
        )
        for r in rows
    ]


def list_ncf(
    *, stage: str | None = None, area: str | None = None,
) -> list[NCFCompetency]:
    sql = ("SELECT key, label, description, stage, area, keywords "
           "FROM ncf_competencies WHERE 1=1")
    params: list = []
    if stage:
        if stage not in VALID_STAGES:
            raise ValueError(f"stage must be in {sorted(VALID_STAGES)}")
        sql += " AND stage = ?"; params.append(stage)
    if area:
        sql += " AND area = ?"; params.append(area)
    sql += " ORDER BY stage, area, key"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        NCFCompetency(
            key=r[0], label=r[1], description=r[2],
            stage=r[3], area=r[4],
            keywords=json.loads(r[5]) if r[5] else [],
        )
        for r in rows
    ]


# ---------- scoring ----------

def _normalize_text(t: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (t or "").lower())


def score_text(text: str, *, framework: str,
               stage: str | None = None) -> AlignmentResult:
    """Score a piece of text (lesson narration, video transcript,
    chapter summary) against the framework's competency catalog.

    Per-competency score = fraction of keywords found in normalized
    text. Overall score = weighted-avg of per-competency scores,
    capped at 1.0."""
    if framework not in VALID_FRAMEWORKS:
        raise ValueError(f"framework must be in {sorted(VALID_FRAMEWORKS)}")
    text_norm = _normalize_text(text)
    if framework == "nep":
        catalog = [
            (c.key, c.label, c.keywords) for c in list_nep()
        ]
    else:
        catalog = [
            (c.key, c.label, c.keywords) for c in list_ncf(stage=stage)
        ]
    matches = []
    total_score = 0.0
    for key, label, kws in catalog:
        if not kws:
            continue
        hits = sum(1 for kw in kws if kw.lower() in text_norm)
        score = hits / len(kws) if kws else 0.0
        if score > 0:
            matches.append({
                "competency_key": key, "label": label,
                "score": round(score, 3),
            })
            total_score += score
    matches.sort(key=lambda m: m["score"], reverse=True)
    overall = (total_score / len(catalog)) if catalog else 0.0
    return AlignmentResult(
        framework=framework,
        matches=matches,
        total_competencies=len(catalog),
        overall_score=round(min(overall, 1.0), 3),
    )


def persist_lesson_alignment(
    *,
    lesson_id: str,
    framework: str,
    matches: list[dict],
) -> None:
    """Idempotent — replaces any prior alignment rows for the
    (lesson_id, framework) pair."""
    if framework not in VALID_FRAMEWORKS:
        raise ValueError(f"framework must be in {sorted(VALID_FRAMEWORKS)}")
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "DELETE FROM lesson_alignment "
            "WHERE lesson_id = ? AND framework = ?",
            (lesson_id, framework),
        )
        for m in matches:
            conn.execute(
                "INSERT INTO lesson_alignment "
                "(id, lesson_id, framework, competency_key, "
                " score, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, lesson_id, framework,
                 m["competency_key"], float(m["score"]), now),
            )


def get_lesson_alignment(
    *, lesson_id: str, framework: str | None = None,
) -> list[dict]:
    sql = ("SELECT lesson_id, framework, competency_key, score, "
           "created_at FROM lesson_alignment WHERE lesson_id = ?")
    params: list = [lesson_id]
    if framework:
        sql += " AND framework = ?"; params.append(framework)
    sql += " ORDER BY score DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"lesson_id": r[0], "framework": r[1],
         "competency_key": r[2], "score": r[3],
         "created_at": r[4]}
        for r in rows
    ]


def coverage_summary(*, lesson_ids: list[str], framework: str) -> dict:
    """Aggregate coverage across a set of lessons (e.g. all lessons
    a student has watched). Returns {competency_key: avg_score}."""
    if not lesson_ids:
        return {"by_competency": {}, "total_lessons": 0}
    placeholders = ",".join("?" * len(lesson_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT competency_key, AVG(score), COUNT(*) "
            f"FROM lesson_alignment "
            f"WHERE framework = ? AND lesson_id IN ({placeholders}) "
            f"GROUP BY competency_key ORDER BY AVG(score) DESC",
            (framework, *lesson_ids),
        ).fetchall()
    return {
        "total_lessons": len(lesson_ids),
        "by_competency": {
            r[0]: {"avg_score": round(r[1], 3),
                   "lesson_count": r[2]}
            for r in rows
        },
    }
