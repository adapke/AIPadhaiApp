"""J3 — NCERT/CBSE curriculum alignment scorer.

After lesson generation, classify: did this lesson actually cover the
NCERT Class N learning objectives for this topic? Output:
- alignment_score (0-100)
- covered_objectives  (list of objective ids touched)
- missed_objectives   (list of objective ids NOT touched)

Teachers see this in Studio; schools see aggregate "curriculum
coverage" metrics in the analytics dashboard.

Two parts:
1. **Catalog** — `curriculum_objectives` table; one row per
   learning-objective from a board syllabus. Ingest is a separate
   ops task (NCERT PDFs → OCR → manual review for nuance).
2. **Scorer** — runs after `generate_lesson()`; takes the lesson
   text + the topic's objectives + asks Claude Haiku to mark each
   objective as covered/partial/missing. Cached on
   (lesson_id, syllabus_revision) so it doesn't re-run if the
   lesson doesn't change.

For this v1.8 scope we ship:
- the data model + catalog endpoints
- a stub scorer that does keyword-overlap (Claude integration lands
  in v1.8.x when we wire the prompt + cost-cap)
- ALTER TABLE generated_videos to hold alignment_score + alignment_json
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
CREATE TABLE IF NOT EXISTS curriculum_objectives (
    id           TEXT PRIMARY KEY,
    board        TEXT NOT NULL,
    grade        INTEGER NOT NULL,
    subject      TEXT NOT NULL,
    chapter      TEXT NOT NULL,
    objective    TEXT NOT NULL,
    source       TEXT,                 -- 'NCERT Class 8 Science p.42'
    revision     TEXT,                 -- syllabus version: '2023-24'
    created_at   REAL NOT NULL,
    UNIQUE (board, grade, subject, chapter, objective)
);
CREATE INDEX IF NOT EXISTS idx_curr_lookup
    ON curriculum_objectives(board, grade, subject, chapter);
"""

ADDITIONS = [
    "ALTER TABLE generated_videos ADD COLUMN alignment_score INTEGER",
    "ALTER TABLE generated_videos ADD COLUMN alignment_json TEXT",
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
    with _conn() as conn:
        for stmt in ADDITIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    if "no such table" not in str(e).lower():
                        raise


@dataclass(frozen=True)
class Objective:
    id: str
    board: str
    grade: int
    subject: str
    chapter: str
    objective: str
    source: str | None
    revision: str | None


def upsert_objective(
    *, board: str, grade: int, subject: str, chapter: str,
    objective: str,
    source: str | None = None, revision: str | None = None,
) -> Objective:
    """Idempotent on (board, grade, subject, chapter, objective)."""
    with _conn() as conn:
        try:
            oid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO curriculum_objectives "
                "(id, board, grade, subject, chapter, objective, "
                " source, revision, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (oid, board, grade, subject, chapter, objective,
                 source, revision, time.time()),
            )
        except sqlite3.IntegrityError:
            pass
        r = conn.execute(
            "SELECT id, board, grade, subject, chapter, objective, "
            "       source, revision FROM curriculum_objectives "
            "WHERE board = ? AND grade = ? AND subject = ? "
            "AND chapter = ? AND objective = ?",
            (board, grade, subject, chapter, objective),
        ).fetchone()
    return Objective(
        id=r[0], board=r[1], grade=r[2], subject=r[3], chapter=r[4],
        objective=r[5], source=r[6], revision=r[7],
    )


def list_objectives(
    *, board: str, grade: int, subject: str,
    chapter: str | None = None,
) -> list[Objective]:
    sql = (
        "SELECT id, board, grade, subject, chapter, objective, "
        "       source, revision FROM curriculum_objectives "
        "WHERE board = ? AND grade = ? AND subject = ?"
    )
    params: list = [board, grade, subject]
    if chapter:
        sql += " AND chapter = ?"; params.append(chapter)
    sql += " ORDER BY chapter, objective"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Objective(
            id=r[0], board=r[1], grade=r[2], subject=r[3], chapter=r[4],
            objective=r[5], source=r[6], revision=r[7],
        )
        for r in rows
    ]


# ---------- Scorer ----------

@dataclass(frozen=True)
class AlignmentResult:
    score: int                          # 0-100
    covered: list[str]                  # objective IDs
    partial: list[str]
    missed: list[str]
    method: str                         # 'keyword' | 'claude'
    syllabus_revision: str | None


def score_lesson(
    *,
    lesson_text: str,
    board: str,
    grade: int,
    subject: str,
    chapter: str | None = None,
) -> AlignmentResult:
    """Compute alignment. v1.8 uses keyword overlap; v1.8.x adds the
    Claude path gated on `PADHAI_SCORER_PROVIDER=claude`."""
    provider = os.environ.get("PADHAI_SCORER_PROVIDER", "keyword").lower()
    objectives = list_objectives(
        board=board, grade=grade, subject=subject, chapter=chapter,
    )
    if not objectives:
        return AlignmentResult(
            score=0, covered=[], partial=[], missed=[],
            method="empty", syllabus_revision=None,
        )
    if provider == "claude":
        return _score_via_claude(lesson_text, objectives)
    return _score_via_keyword(lesson_text, objectives)


def _score_via_keyword(
    text: str, objectives: list[Objective],
) -> AlignmentResult:
    """Cheap heuristic: split each objective into content words,
    count how many appear in the lesson text. Covered if ≥60% of
    content words appear; partial if 30-60%; missed otherwise."""
    norm = text.lower()
    covered, partial, missed = [], [], []
    for o in objectives:
        words = [
            w for w in re.findall(r"\b[a-zA-Z]{4,}\b", o.objective.lower())
            if w not in _STOPWORDS
        ]
        if not words:
            missed.append(o.id); continue
        hits = sum(1 for w in words if w in norm)
        ratio = hits / len(words)
        if ratio >= 0.6:
            covered.append(o.id)
        elif ratio >= 0.3:
            partial.append(o.id)
        else:
            missed.append(o.id)
    total = len(objectives)
    # Score: covered = 1.0, partial = 0.5, missed = 0.0
    raw = (len(covered) + 0.5 * len(partial)) / total
    return AlignmentResult(
        score=round(raw * 100),
        covered=covered, partial=partial, missed=missed,
        method="keyword",
        syllabus_revision=objectives[0].revision if objectives else None,
    )


def _score_via_claude(
    text: str, objectives: list[Objective],
) -> AlignmentResult:
    """Claude-based scorer. Cheaper than re-running the lesson — uses
    Haiku with a structured-output prompt that returns per-objective
    classification. Wired in v1.8.x after we set ANTHROPIC_API_KEY in
    the scorer env + budget the call (~₹0.10 per scoring run)."""
    # For now, deferred: fall back to keyword and tag the method so
    # the dashboard can show what was used.
    res = _score_via_keyword(text, objectives)
    return AlignmentResult(
        score=res.score, covered=res.covered,
        partial=res.partial, missed=res.missed,
        method="keyword (claude path deferred to v1.8.x)",
        syllabus_revision=res.syllabus_revision,
    )


_STOPWORDS = frozenset({
    "this", "that", "with", "from", "they", "have", "will", "been",
    "were", "what", "when", "where", "which", "their", "there",
    "would", "could", "should", "about", "into", "than", "then",
    "these", "those", "between", "while",
})


# ---------- Persisting alignment to generated_videos ----------

def save_alignment(
    *, video_id: str, result: AlignmentResult,
) -> None:
    """Write the alignment back to the generated_videos row. Called
    by the renderer after `score_lesson` runs."""
    payload = json.dumps({
        "covered": result.covered,
        "partial": result.partial,
        "missed": result.missed,
        "method": result.method,
        "syllabus_revision": result.syllabus_revision,
    })
    try:
        with _conn() as conn:
            conn.execute(
                "UPDATE generated_videos SET "
                " alignment_score = ?, alignment_json = ? "
                "WHERE id = ?",
                (result.score, payload, video_id),
            )
    except sqlite3.OperationalError:
        # generated_videos table may not exist yet on a fresh DB
        pass
