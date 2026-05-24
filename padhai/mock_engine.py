"""v3.2 — Universal mock test engine.

Per gap-review §15. Existing pieces (mock_test_events.py for
analytics, practice_test.py for AI-generated short tests) don't
cover the **exam-pattern-faithful** mock test that the review
identifies as table-stakes: section timing + negative marking +
question palette + mark-for-review + percentile + topic-weakness
rollup.

This module is the engine. The content (papers themselves) sits
in J6 question_bank + O3 question_pack_market + exam_taxonomy
PYQ refs; the engine **composes** a paper from question refs and
runs the attempt.

Four tables:
  mock_papers           paper template — sections, timing, marking
  mock_paper_questions  the question list per paper (order + section)
  mock_attempts         one row per (paper, user) attempt
  mock_responses        per-question response within an attempt

Attempt lifecycle: started → in_progress → submitted → analysed
(auto-grade on submit, percentile fills in async).

Three modes: full / sectional / pyq. The engine doesn't care
which — modes are author metadata on the paper.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_papers (
    id              TEXT PRIMARY KEY,
    pack_code       TEXT,                          -- exam_packs.code; NULL = standalone
    exam_code       TEXT,                          -- exams.code
    title           TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'full',
    -- 'full' | 'sectional' | 'pyq'
    sections_json   TEXT NOT NULL,                  -- [{code, title, time_min, marks_per_q, negative}, ...]
    total_questions INTEGER NOT NULL DEFAULT 0,
    total_marks     INTEGER NOT NULL DEFAULT 0,
    total_time_min  INTEGER NOT NULL,
    negative_marking REAL NOT NULL DEFAULT 0,       -- e.g. 0.25 = -1/4 per wrong
    status          TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'published' | 'archived'
    created_at      REAL NOT NULL,
    published_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_mp_pack
    ON mock_papers(pack_code, status);
CREATE INDEX IF NOT EXISTS idx_mp_exam
    ON mock_papers(exam_code, status);

CREATE TABLE IF NOT EXISTS mock_paper_questions (
    paper_id        TEXT NOT NULL,
    position        INTEGER NOT NULL,
    section_code    TEXT NOT NULL,
    question_id     TEXT NOT NULL,                  -- FK question_bank.id (or qb-pack id)
    correct_answer  TEXT,                            -- override / cache for grading
    topic_code      TEXT,                            -- for topic-wise analysis
    PRIMARY KEY (paper_id, position)
);
CREATE INDEX IF NOT EXISTS idx_mpq_section
    ON mock_paper_questions(paper_id, section_code);

CREATE TABLE IF NOT EXISTS mock_attempts (
    id              TEXT PRIMARY KEY,
    paper_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'started',
    -- 'started' | 'in_progress' | 'submitted'
    started_at      REAL NOT NULL,
    submitted_at    REAL,
    duration_sec    INTEGER,
    raw_score       REAL,
    max_score       INTEGER,
    correct_count   INTEGER NOT NULL DEFAULT 0,
    wrong_count     INTEGER NOT NULL DEFAULT 0,
    unattempted_count INTEGER NOT NULL DEFAULT 0,
    percentile      REAL,                            -- 0..100; NULL until cohort job
    section_scores_json TEXT,                        -- {"reasoning": 18, "quant": 22}
    topic_breakdown_json TEXT                        -- {"polity": {"correct":4,"wrong":1}, ...}
);
CREATE INDEX IF NOT EXISTS idx_ma_user
    ON mock_attempts(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ma_paper
    ON mock_attempts(paper_id, submitted_at DESC);

CREATE TABLE IF NOT EXISTS mock_responses (
    attempt_id      TEXT NOT NULL,
    position        INTEGER NOT NULL,
    chosen_answer   TEXT,                            -- NULL = unattempted
    is_correct      INTEGER,                         -- 0/1; NULL = unattempted
    marks_awarded   REAL NOT NULL DEFAULT 0,
    time_seconds    INTEGER,
    marked_review   INTEGER NOT NULL DEFAULT 0,
    answered_at     REAL,
    PRIMARY KEY (attempt_id, position)
);
CREATE INDEX IF NOT EXISTS idx_mr_attempt
    ON mock_responses(attempt_id, position);
"""


VALID_MODES = frozenset({"full", "sectional", "pyq"})
VALID_PAPER_STATUSES = frozenset({"draft", "published", "archived"})
VALID_ATTEMPT_STATUSES = frozenset({"started", "in_progress", "submitted"})


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
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Paper:
    id: str
    pack_code: str | None
    exam_code: str | None
    title: str
    mode: str
    sections: list[dict]
    total_questions: int
    total_marks: int
    total_time_min: int
    negative_marking: float
    status: str
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Attempt:
    id: str
    paper_id: str
    user_id: str
    status: str
    started_at: float
    submitted_at: float | None
    duration_sec: int | None
    raw_score: float | None
    max_score: int | None
    correct_count: int
    wrong_count: int
    unattempted_count: int
    percentile: float | None
    section_scores: dict
    topic_breakdown: dict


# ---------- paper authoring ----------

def create_paper(
    *,
    title: str,
    sections: list[dict],
    total_time_min: int,
    pack_code: str | None = None,
    exam_code: str | None = None,
    mode: str = "full",
    negative_marking: float = 0.0,
) -> Paper:
    """Create a mock paper template. Sections is a list of dicts:
      {code, title, time_min, marks_per_q, negative? (overrides paper-level)}
    """
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be in {sorted(VALID_MODES)}")
    if total_time_min < 1 or total_time_min > 600:
        raise ValueError("total_time_min must be in [1, 600]")
    if negative_marking < 0 or negative_marking > 1:
        raise ValueError("negative_marking must be in [0, 1]")
    if not sections:
        raise ValueError("at least one section required")
    if len(sections) > 20:
        raise ValueError("max 20 sections per paper")
    seen_codes: set[str] = set()
    for i, s in enumerate(sections):
        if not isinstance(s, dict):
            raise ValueError(f"sections[{i}] must be dict")
        code = s.get("code")
        if not code or not isinstance(code, str) or len(code) > 40:
            raise ValueError(
                f"sections[{i}].code required (max 40 chars)",
            )
        if code in seen_codes:
            raise ValueError(f"duplicate section code {code!r}")
        seen_codes.add(code)
        mpq = s.get("marks_per_q")
        if mpq is None or mpq <= 0 or mpq > 100:
            raise ValueError(
                f"sections[{i}].marks_per_q must be in (0, 100]",
            )
    pid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO mock_papers "
            "(id, pack_code, exam_code, title, mode, "
            " sections_json, total_time_min, negative_marking, "
            " status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            (pid, pack_code, exam_code, title, mode,
             json.dumps(sections), total_time_min,
             negative_marking, time.time()),
        )
    return get_paper(pid)  # type: ignore[return-value]


def add_question(
    *,
    paper_id: str,
    position: int,
    section_code: str,
    question_id: str,
    correct_answer: str | None = None,
    topic_code: str | None = None,
) -> int:
    """Add a question to a paper at `position` (1-indexed). Updates
    total_questions + total_marks aggregates."""
    p = get_paper(paper_id)
    if not p:
        raise ValueError("paper not found")
    if p.status != "draft":
        raise ValueError("paper must be draft to add questions")
    if position < 1 or position > 1000:
        raise ValueError("position must be in [1, 1000]")
    sect = next(
        (s for s in p.sections if s.get("code") == section_code),
        None,
    )
    if not sect:
        raise ValueError(
            f"section {section_code!r} not in paper",
        )
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO mock_paper_questions "
                "(paper_id, position, section_code, question_id, "
                " correct_answer, topic_code) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (paper_id, position, section_code, question_id,
                 correct_answer, topic_code),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"position {position} already taken on paper",
        ) from e
    # Re-aggregate counts atomically
    _recount_paper(paper_id)
    return _question_count(paper_id)


def _recount_paper(paper_id: str) -> None:
    """Refresh total_questions + total_marks from the questions
    table. Called after add/remove."""
    p = get_paper(paper_id)
    if not p:
        return
    marks_by_section = {s["code"]: float(s["marks_per_q"]) for s in p.sections}
    with _conn() as conn:
        rows = conn.execute(
            "SELECT section_code, COUNT(*) "
            "FROM mock_paper_questions WHERE paper_id = ? "
            "GROUP BY section_code",
            (paper_id,),
        ).fetchall()
        total_q = sum(r[1] for r in rows)
        total_marks = sum(
            r[1] * marks_by_section.get(r[0], 0)
            for r in rows
        )
        conn.execute(
            "UPDATE mock_papers SET "
            " total_questions = ?, total_marks = ? "
            "WHERE id = ?",
            (total_q, int(round(total_marks)), paper_id),
        )


def _question_count(paper_id: str) -> int:
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM mock_paper_questions "
            "WHERE paper_id = ?", (paper_id,),
        ).fetchone()
    return int(r[0] or 0)


def publish_paper(paper_id: str) -> bool:
    """Lock paper for attempts. Requires ≥5 questions; under that
    it's not really a 'mock'."""
    p = get_paper(paper_id)
    if not p:
        raise ValueError("paper not found")
    if p.status != "draft":
        return False
    if p.total_questions < 5:
        raise ValueError("paper must have ≥5 questions to publish")
    with _conn() as conn:
        conn.execute(
            "UPDATE mock_papers SET "
            " status = 'published', published_at = ? "
            "WHERE id = ?",
            (time.time(), paper_id),
        )
    return True


def archive_paper(paper_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE mock_papers SET status = 'archived' "
            "WHERE id = ? AND status != 'archived'",
            (paper_id,),
        )
        return cur.rowcount > 0


def get_paper(paper_id: str) -> Paper | None:
    with _conn() as conn:
        r = conn.execute(
            _PAPER_SEL + " WHERE id = ?", (paper_id,),
        ).fetchone()
    return _row_to_paper(r) if r else None


_PAPER_SEL = (
    "SELECT id, pack_code, exam_code, title, mode, sections_json, "
    "       total_questions, total_marks, total_time_min, "
    "       negative_marking, status, created_at, published_at "
    "FROM mock_papers"
)


def _row_to_paper(r) -> Paper:
    return Paper(
        id=r[0], pack_code=r[1], exam_code=r[2], title=r[3],
        mode=r[4], sections=json.loads(r[5]),
        total_questions=r[6], total_marks=r[7],
        total_time_min=r[8], negative_marking=r[9],
        status=r[10], created_at=r[11], published_at=r[12],
    )


def list_papers(
    *,
    pack_code: str | None = None,
    exam_code: str | None = None,
    mode: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[Paper]:
    limit = max(1, min(limit, 500))
    sql = _PAPER_SEL
    where: list[str] = []
    params: list = []
    if pack_code:
        where.append("pack_code = ?"); params.append(pack_code)
    if exam_code:
        where.append("exam_code = ?"); params.append(exam_code)
    if mode:
        if mode not in VALID_MODES:
            raise ValueError(f"mode must be in {sorted(VALID_MODES)}")
        where.append("mode = ?"); params.append(mode)
    if status:
        if status not in VALID_PAPER_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_PAPER_STATUSES)}",
            )
        where.append("status = ?"); params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_paper(r) for r in rows]


# ---------- attempts ----------

def start_attempt(
    *,
    paper_id: str,
    user_id: str,
) -> Attempt:
    p = get_paper(paper_id)
    if not p:
        raise ValueError("paper not found")
    if p.status != "published":
        raise ValueError(
            f"paper is {p.status!r}, not attemptable",
        )
    if p.total_questions < 1:
        raise ValueError("paper has no questions")
    aid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO mock_attempts "
            "(id, paper_id, user_id, status, started_at, "
            " max_score) VALUES (?, ?, ?, 'started', ?, ?)",
            (aid, paper_id, user_id, time.time(), p.total_marks),
        )
    return get_attempt(aid)  # type: ignore[return-value]


def get_attempt(attempt_id: str) -> Attempt | None:
    with _conn() as conn:
        r = conn.execute(
            _ATT_SEL + " WHERE id = ?", (attempt_id,),
        ).fetchone()
    return _row_to_attempt(r) if r else None


_ATT_SEL = (
    "SELECT id, paper_id, user_id, status, started_at, "
    "       submitted_at, duration_sec, raw_score, max_score, "
    "       correct_count, wrong_count, unattempted_count, "
    "       percentile, section_scores_json, topic_breakdown_json "
    "FROM mock_attempts"
)


def _row_to_attempt(r) -> Attempt:
    return Attempt(
        id=r[0], paper_id=r[1], user_id=r[2], status=r[3],
        started_at=r[4], submitted_at=r[5],
        duration_sec=r[6], raw_score=r[7], max_score=r[8],
        correct_count=r[9], wrong_count=r[10],
        unattempted_count=r[11], percentile=r[12],
        section_scores=json.loads(r[13]) if r[13] else {},
        topic_breakdown=json.loads(r[14]) if r[14] else {},
    )


def submit_response(
    *,
    attempt_id: str,
    position: int,
    chosen_answer: str | None,
    time_seconds: int | None = None,
    marked_review: bool = False,
) -> None:
    """Save a response. Caller can update mid-attempt freely;
    final grading happens at submit_attempt(). Idempotent on
    (attempt, position) — later writes overwrite."""
    a = get_attempt(attempt_id)
    if not a:
        raise ValueError("attempt not found")
    if a.status == "submitted":
        raise ValueError("attempt is submitted")
    if time_seconds is not None and (
        time_seconds < 0 or time_seconds > 36000
    ):
        raise ValueError("time_seconds must be in [0, 36000]")
    now = time.time() if chosen_answer is not None else None
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mock_responses "
            "(attempt_id, position, chosen_answer, "
            " is_correct, marks_awarded, time_seconds, "
            " marked_review, answered_at) "
            "VALUES (?, ?, ?, NULL, 0, ?, ?, ?)",
            (attempt_id, position, chosen_answer,
             time_seconds, 1 if marked_review else 0, now),
        )
        # Move 'started' → 'in_progress' on first response
        if a.status == "started" and chosen_answer is not None:
            conn.execute(
                "UPDATE mock_attempts SET status = 'in_progress' "
                "WHERE id = ? AND status = 'started'",
                (attempt_id,),
            )


def submit_attempt(attempt_id: str) -> Attempt:
    """Grade + persist. Auto-grade by comparing chosen_answer to
    mock_paper_questions.correct_answer. Computes per-section
    score + topic_breakdown."""
    a = get_attempt(attempt_id)
    if not a:
        raise ValueError("attempt not found")
    if a.status == "submitted":
        return a
    p = get_paper(a.paper_id)
    if not p:
        raise ValueError("paper missing")
    marks_by_section = {
        s["code"]: float(s["marks_per_q"]) for s in p.sections
    }
    neg = p.negative_marking
    with _conn() as conn:
        # Join questions × responses to grade
        rows = conn.execute(
            "SELECT q.position, q.section_code, "
            "       q.correct_answer, q.topic_code, "
            "       r.chosen_answer "
            "FROM mock_paper_questions q "
            "LEFT JOIN mock_responses r "
            "  ON r.attempt_id = ? AND r.position = q.position "
            "WHERE q.paper_id = ? "
            "ORDER BY q.position",
            (attempt_id, p.id),
        ).fetchall()
    correct = wrong = unattempted = 0
    raw = 0.0
    section_scores: dict[str, float] = {}
    topic_break: dict[str, dict[str, int]] = {}
    for pos, scode, expected, topic, chosen in rows:
        per_q = marks_by_section.get(scode, 0)
        is_correct = None
        delta = 0.0
        if chosen is None or chosen == "":
            unattempted += 1
        else:
            if expected is not None and str(chosen).strip() == str(expected).strip():
                is_correct = 1
                correct += 1
                delta = per_q
            else:
                is_correct = 0
                wrong += 1
                delta = -per_q * neg
        raw += delta
        section_scores[scode] = round(
            section_scores.get(scode, 0.0) + delta, 2,
        )
        if topic:
            t = topic_break.setdefault(
                topic, {"correct": 0, "wrong": 0, "unattempted": 0},
            )
            if is_correct == 1:
                t["correct"] += 1
            elif is_correct == 0:
                t["wrong"] += 1
            else:
                t["unattempted"] += 1
        # Persist per-response grade
        with _conn() as conn:
            conn.execute(
                "UPDATE mock_responses SET "
                " is_correct = ?, marks_awarded = ? "
                "WHERE attempt_id = ? AND position = ?",
                (is_correct, delta, attempt_id, pos),
            )
    now = time.time()
    duration = int(now - a.started_at)
    with _conn() as conn:
        conn.execute(
            "UPDATE mock_attempts SET "
            " status = 'submitted', submitted_at = ?, "
            " duration_sec = ?, raw_score = ?, "
            " correct_count = ?, wrong_count = ?, "
            " unattempted_count = ?, "
            " section_scores_json = ?, "
            " topic_breakdown_json = ? "
            "WHERE id = ?",
            (now, duration, round(raw, 2), correct, wrong,
             unattempted, json.dumps(section_scores),
             json.dumps(topic_break), attempt_id),
        )
    # Trigger percentile recompute (synchronous since the cohort
    # is small in tests; in prod this is a background sweep)
    _recompute_percentile_for_paper(p.id)
    return get_attempt(attempt_id)  # type: ignore[return-value]


def _recompute_percentile_for_paper(paper_id: str) -> None:
    """For each submitted attempt on this paper, recompute
    percentile rank against the full cohort. Cheap when cohort is
    small; switch to a windowed job at scale."""
    with _conn() as conn:
        scores = conn.execute(
            "SELECT id, raw_score FROM mock_attempts "
            "WHERE paper_id = ? AND status = 'submitted'",
            (paper_id,),
        ).fetchall()
    if not scores:
        return
    sorted_scores = sorted(s[1] or 0 for s in scores)
    n = len(sorted_scores)
    with _conn() as conn:
        for aid, sc in scores:
            sc = sc or 0
            # Percentile = % of scores ≤ this one
            below = sum(1 for x in sorted_scores if x < sc)
            same = sum(1 for x in sorted_scores if x == sc)
            pct = (below + same / 2) / n * 100
            conn.execute(
                "UPDATE mock_attempts SET percentile = ? "
                "WHERE id = ?", (round(pct, 2), aid),
            )


def list_attempts_for_user(
    user_id: str,
    *,
    paper_id: str | None = None,
    limit: int = 50,
) -> list[Attempt]:
    limit = max(1, min(limit, 500))
    sql = _ATT_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if paper_id:
        sql += " AND paper_id = ?"; params.append(paper_id)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_attempt(r) for r in rows]


def attempt_analysis(attempt_id: str) -> dict:
    """Per-attempt deep-dive — section scores, topic breakdown,
    timing, accuracy. Drives the post-submit review screen."""
    a = get_attempt(attempt_id)
    if not a:
        raise ValueError("attempt not found")
    if a.status != "submitted":
        return {
            "status": a.status, "ready": False,
            "message": "submit attempt to see analysis",
        }
    with _conn() as conn:
        timings = conn.execute(
            "SELECT AVG(time_seconds), MAX(time_seconds), "
            "       SUM(time_seconds) "
            "FROM mock_responses "
            "WHERE attempt_id = ? AND time_seconds IS NOT NULL",
            (attempt_id,),
        ).fetchone()
    total = (a.correct_count + a.wrong_count
             + a.unattempted_count) or 1
    attempted = a.correct_count + a.wrong_count
    return {
        "status": "submitted", "ready": True,
        "raw_score": a.raw_score, "max_score": a.max_score,
        "percentile": a.percentile,
        "correct": a.correct_count,
        "wrong": a.wrong_count,
        "unattempted": a.unattempted_count,
        "accuracy": (
            round(a.correct_count / attempted, 4)
            if attempted else 0
        ),
        "attempt_rate": round(attempted / total, 4),
        "section_scores": a.section_scores,
        "topic_breakdown": a.topic_breakdown,
        "duration_sec": a.duration_sec,
        "avg_time_per_q": round(float(timings[0] or 0), 1),
        "max_time_per_q": int(timings[1] or 0),
        "total_time_used_sec": int(timings[2] or 0),
    }


def cohort_stats(paper_id: str) -> dict:
    """Aggregate stats for a paper — mean / median / p90 score +
    attempt count. Drives the paper-landing 'cohort' card."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT raw_score FROM mock_attempts "
            "WHERE paper_id = ? AND status = 'submitted'",
            (paper_id,),
        ).fetchall()
    scores = [r[0] for r in rows if r[0] is not None]
    if not scores:
        return {
            "submissions": 0, "mean": None,
            "median": None, "p90": None,
        }
    s = sorted(scores)
    p90_idx = int(0.9 * (len(s) - 1))
    return {
        "submissions": len(scores),
        "mean": round(statistics.mean(s), 2),
        "median": round(statistics.median(s), 2),
        "p90": round(s[p90_idx], 2),
    }
