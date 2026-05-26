"""L2 — AI essay / answer grader with rubric matching.

Student writes an essay (UPSC mains) or descriptive answer (JEE
advanced, board paper). Claude scores it against a rubric, gives
per-criterion feedback, suggests a model answer + improvements.

Essay grading is the most expensive bottleneck in coaching — a UPSC
mains test series costs ₹15,000, ~80% of which is teacher grading
time. We replace 60% with AI; humans spot-check the rest.

Two tables:
  essay_rubrics       — board-specific rubric per (exam, paper, topic)
  essay_submissions   — student submissions with AI + optional human score

Cost path: every grade run goes through Q2's llm_cache.with_caching
because the rubric is the cached system block (1-3k tokens, identical
across N submissions for the same rubric).
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
CREATE TABLE IF NOT EXISTS essay_rubrics (
    id              TEXT PRIMARY KEY,
    exam            TEXT NOT NULL,           -- 'upsc_mains' | 'jee_adv_descriptive' | 'cbse_class10_eng'
    paper           TEXT NOT NULL,
    topic           TEXT,
    criteria_json   TEXT NOT NULL,           -- [{name, weight, description}, ...]
    max_marks       INTEGER NOT NULL,
    model_answer    TEXT,
    created_by      TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (exam, paper, topic)
);
CREATE INDEX IF NOT EXISTS idx_rubric_exam ON essay_rubrics(exam, paper);

CREATE TABLE IF NOT EXISTS essay_submissions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    rubric_id       TEXT NOT NULL,
    text            TEXT NOT NULL,
    ai_score        REAL,
    ai_feedback_json TEXT,                    -- {by_criterion: {...}, summary, suggestions}
    ai_call_id      TEXT,                     -- llm_obs.llm_calls.id
    human_reviewed  INTEGER NOT NULL DEFAULT 0,
    human_score     REAL,
    human_note      TEXT,
    submitted_at    REAL NOT NULL,
    graded_at       REAL,
    reviewed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_sub_user ON essay_submissions(user_id, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_rubric ON essay_submissions(rubric_id, submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_sub_review_queue
    ON essay_submissions(human_reviewed, submitted_at)
    WHERE human_reviewed = 0;
"""


VALID_EXAMS = frozenset({
    "upsc_mains", "upsc_essay",
    "jee_adv_descriptive",
    "cbse_class10_eng", "cbse_class12_eng",
    "icse_class10_eng",
    "neet_descriptive",
    "cat_va",
    "generic",
})


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


# ---------- rubrics ----------

@dataclass(frozen=True)
class Rubric:
    id: str
    exam: str
    paper: str
    topic: str | None
    criteria: list[dict]              # [{name, weight, description}]
    max_marks: int
    model_answer: str | None
    created_by: str | None
    created_at: float


def _row_to_rubric(r) -> Rubric:
    return Rubric(
        id=r[0], exam=r[1], paper=r[2], topic=r[3],
        criteria=json.loads(r[4]) if r[4] else [],
        max_marks=r[5], model_answer=r[6],
        created_by=r[7], created_at=r[8],
    )


def upsert_rubric(
    *,
    exam: str,
    paper: str,
    topic: str | None,
    criteria: list[dict],
    max_marks: int,
    model_answer: str | None = None,
    created_by: str | None = None,
) -> Rubric:
    """Idempotent on (exam, paper, topic)."""
    if exam not in VALID_EXAMS:
        raise ValueError(f"exam must be in {sorted(VALID_EXAMS)}")
    if max_marks <= 0 or max_marks > 1000:
        raise ValueError("max_marks must be in (0, 1000]")
    if not criteria:
        raise ValueError("at least one criterion required")
    total_w = sum(int(c.get("weight", 0)) for c in criteria)
    if total_w <= 0:
        raise ValueError("criteria weights must sum to >0")
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, created_at FROM essay_rubrics "
            "WHERE exam = ? AND paper = ? AND topic IS ?",
            (exam, paper, topic),
        ).fetchone()
        if existing:
            rid = existing[0]
            conn.execute(
                "UPDATE essay_rubrics SET "
                " criteria_json = ?, max_marks = ?, model_answer = ? "
                "WHERE id = ?",
                (json.dumps(criteria), max_marks, model_answer, rid),
            )
        else:
            rid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO essay_rubrics "
                "(id, exam, paper, topic, criteria_json, max_marks, "
                " model_answer, created_by, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (rid, exam, paper, topic, json.dumps(criteria),
                 max_marks, model_answer, created_by, time.time()),
            )
    return get_rubric(rid)  # type: ignore[return-value]


def get_rubric(rubric_id: str) -> Rubric | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, exam, paper, topic, criteria_json, max_marks, "
            "       model_answer, created_by, created_at "
            "FROM essay_rubrics WHERE id = ?", (rubric_id,),
        ).fetchone()
    return _row_to_rubric(r) if r else None


def list_rubrics(*, exam: str | None = None) -> list[Rubric]:
    sql = (
        "SELECT id, exam, paper, topic, criteria_json, max_marks, "
        "       model_answer, created_by, created_at "
        "FROM essay_rubrics"
    )
    params: list = []
    if exam:
        sql += " WHERE exam = ?"; params.append(exam)
    sql += " ORDER BY exam, paper, topic"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_rubric(r) for r in rows]


# ---------- submissions ----------

@dataclass(frozen=True)
class Submission:
    id: str
    user_id: str
    rubric_id: str
    text: str
    ai_score: float | None
    ai_feedback: dict | None
    ai_call_id: str | None
    human_reviewed: bool
    human_score: float | None
    human_note: str | None
    submitted_at: float
    graded_at: float | None
    reviewed_at: float | None


def _row_to_sub(r) -> Submission:
    return Submission(
        id=r[0], user_id=r[1], rubric_id=r[2], text=r[3],
        ai_score=r[4],
        ai_feedback=json.loads(r[5]) if r[5] else None,
        ai_call_id=r[6],
        human_reviewed=bool(r[7]),
        human_score=r[8], human_note=r[9],
        submitted_at=r[10], graded_at=r[11], reviewed_at=r[12],
    )


_SUB_SEL = (
    "id, user_id, rubric_id, text, ai_score, ai_feedback_json, "
    "ai_call_id, human_reviewed, human_score, human_note, "
    "submitted_at, graded_at, reviewed_at"
)


def submit(
    *,
    user_id: str,
    rubric_id: str,
    text: str,
) -> Submission:
    rubric = get_rubric(rubric_id)
    if not rubric:
        raise ValueError(f"rubric {rubric_id!r} not found")
    text = (text or "").strip()
    if len(text) < 50:
        raise ValueError("essay too short (min 50 chars)")
    if len(text) > 20000:
        raise ValueError("essay too long (max 20k chars)")
    sid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO essay_submissions "
            "(id, user_id, rubric_id, text, submitted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, user_id, rubric_id, text, time.time()),
        )
    return get_submission(sid)  # type: ignore[return-value]


def get_submission(sid: str) -> Submission | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SUB_SEL} FROM essay_submissions WHERE id = ?",
            (sid,),
        ).fetchone()
    return _row_to_sub(r) if r else None


def list_for_user(user_id: str, *, limit: int = 50) -> list[Submission]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SUB_SEL} FROM essay_submissions "
            "WHERE user_id = ? ORDER BY submitted_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_sub(r) for r in rows]


def review_queue(*, limit: int = 50) -> list[Submission]:
    """Pending-human-review submissions, oldest first. Drives the
    teacher review dashboard."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SUB_SEL} FROM essay_submissions "
            "WHERE human_reviewed = 0 AND ai_score IS NOT NULL "
            "ORDER BY submitted_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_sub(r) for r in rows]


def record_human_review(
    *,
    submission_id: str,
    human_score: float,
    human_note: str | None = None,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE essay_submissions SET "
            " human_reviewed = 1, human_score = ?, human_note = ?, "
            " reviewed_at = ? WHERE id = ?",
            (human_score, human_note, time.time(), submission_id),
        )
        return cur.rowcount > 0


# ---------- AI grading ----------

@dataclass(frozen=True)
class GradeResult:
    submission_id: str
    score: float
    by_criterion: dict[str, dict]    # {name: {score, weight, feedback}}
    summary: str
    suggestions: list[str]
    method: str                       # 'claude' | 'heuristic' | 'no_provider'


_SYSTEM_TEMPLATE = """You are a senior {exam} examiner. Grade the
student's submission against the rubric below.

Rubric: paper={paper}, topic={topic}, max_marks={max_marks}
Criteria (each with weight + description):
{criteria_text}

Rules:
- For EACH criterion, output a sub-score scaled to its weight.
- The TOTAL is the sum of sub-scores. Don't exceed max_marks.
- Cite specific phrases from the student's text in your feedback.
- Be honest about weak answers — over-marking helps nobody.
- Suggest 3 concrete improvements at the end.
- Reply in this JSON structure (no markdown):

{{
  "by_criterion": {{ "<criterion_name>": {{"score": <num>, "feedback": "<str>"}}, ... }},
  "summary": "<one-paragraph overall assessment>",
  "suggestions": ["<first improvement>", "<second>", "<third>"]
}}
"""


def grade(submission_id: str, *, model: str | None = None) -> GradeResult:
    """Run the AI grader on a submission. Idempotent — re-grading
    overwrites the prior ai_score/feedback.

    When ANTHROPIC_API_KEY is unset, falls back to a heuristic
    (length + criteria-keyword overlap) so the dev path produces a
    real GradeResult shape."""
    sub = get_submission(submission_id)
    if not sub:
        raise ValueError(f"submission {submission_id!r} not found")
    rubric = get_rubric(sub.rubric_id)
    if not rubric:
        raise ValueError("rubric missing")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        result = _grade_heuristic(sub, rubric)
        _persist_grade(sub.id, result, ai_call_id=None)
        return result

    # Lazy SDK import — keeps module loadable without anthropic
    try:
        from anthropic import Anthropic   # noqa: WPS433
    except ImportError:
        result = _grade_heuristic(sub, rubric)
        _persist_grade(sub.id, result, ai_call_id=None)
        return result

    from . import llm_cache, llm_obs

    model = model or os.environ.get(
        "PADHAI_ESSAY_GRADER_MODEL", "claude-sonnet-4-6",
    )
    system_text = _SYSTEM_TEMPLATE.format(
        exam=rubric.exam, paper=rubric.paper,
        topic=rubric.topic or "(general)",
        max_marks=rubric.max_marks,
        criteria_text="\n".join(
            f"- {c['name']} (weight {c.get('weight', 0)}): "
            f"{c.get('description', '')}"
            for c in rubric.criteria
        ),
    )
    user_text = f"STUDENT SUBMISSION:\n---\n{sub.text}\n---"

    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=user_text,
    )

    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=1500, **kwargs,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[essay_grader] Claude call failed: {e}")
        result = _grade_heuristic(sub, rubric)
        _persist_grade(sub.id, result, ai_call_id=None)
        return result

    latency_ms = int((time.time() - started) * 1000)
    body = "".join(
        b.text for b in resp.content if b.type == "text"
    )
    parsed = _parse_grade_response(body, rubric)
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    call_id = llm_obs.record_call(
        module="essay_grader",
        prompt_version="v1",
        model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms,
        user_id=sub.user_id,
        cached=cached,
    )
    result = GradeResult(
        submission_id=sub.id,
        score=parsed["score"],
        by_criterion=parsed["by_criterion"],
        summary=parsed["summary"],
        suggestions=parsed["suggestions"],
        method="claude",
    )
    _persist_grade(sub.id, result, ai_call_id=call_id)
    return result


def _parse_grade_response(body: str, rubric: Rubric) -> dict:
    """Best-effort JSON extraction from the Claude reply. Falls back
    to a default shape if the model hallucinates non-JSON."""
    body = body.strip()
    # Try a direct parse, then a code-fenced parse
    candidates = [body]
    if "```" in body:
        # Pull the first fenced block
        for fence in body.split("```"):
            stripped = fence.strip()
            if stripped.startswith("{") or stripped.startswith("json\n"):
                candidates.append(
                    stripped[5:] if stripped.startswith("json\n") else stripped
                )
    parsed = None
    for c in candidates:
        try:
            parsed = json.loads(c)
            break
        except (ValueError, TypeError):
            continue
    if not parsed:
        # Hallucinated structure — return zero with the body as summary
        return {
            "score": 0.0,
            "by_criterion": {},
            "summary": "(grader output was not parseable JSON; review needed)",
            "suggestions": [],
        }
    by_criterion = parsed.get("by_criterion") or {}
    # Total = sum of by_criterion scores, clamped to max_marks
    total = sum(
        float(c.get("score", 0)) for c in by_criterion.values()
        if isinstance(c, dict)
    )
    total = max(0.0, min(total, rubric.max_marks))
    suggestions = parsed.get("suggestions") or []
    if not isinstance(suggestions, list):
        suggestions = [str(suggestions)]
    return {
        "score": total,
        "by_criterion": by_criterion,
        "summary": str(parsed.get("summary") or ""),
        "suggestions": [str(s) for s in suggestions][:5],
    }


def _grade_heuristic(sub: Submission, rubric: Rubric) -> GradeResult:
    """Cheap fallback grader for the no-API-key dev path. Per-criterion:
    points = weight × (fraction of criterion's description keywords
    appearing in the essay). Crude but produces a real GradeResult."""
    import re
    text_lower = sub.text.lower()
    by_criterion = {}
    total = 0.0
    for c in rubric.criteria:
        name = c.get("name", "criterion")
        weight = float(c.get("weight", 0))
        desc = (c.get("description") or "").lower()
        keywords = [
            w for w in re.findall(r"\b[a-z]{4,}\b", desc)
            if w not in _STOPWORDS
        ]
        if not keywords:
            ratio = 0.5  # neutral
        else:
            hits = sum(1 for w in keywords if w in text_lower)
            ratio = hits / len(keywords)
        sub_score = weight * ratio
        by_criterion[name] = {
            "score": round(sub_score, 2),
            "weight": weight,
            "feedback": (
                f"(heuristic — {hits if keywords else 0}/"
                f"{len(keywords)} keywords present)"
            ) if keywords else "(heuristic — no scoring keywords)",
        }
        total += sub_score
    total = max(0.0, min(total, rubric.max_marks))
    return GradeResult(
        submission_id=sub.id,
        score=round(total, 2),
        by_criterion=by_criterion,
        summary=(
            "Heuristic-graded (no Claude key configured). Submit "
            "for human review for a real assessment."
        ),
        suggestions=[
            "Expand on specific examples from the syllabus.",
            "Tighten the introduction and conclusion.",
            "Cite at least one authoritative source.",
        ],
        method="heuristic",
    )


_STOPWORDS = frozenset({
    "this", "that", "with", "from", "they", "have", "will", "been",
    "what", "when", "where", "their", "there", "would", "should",
    "explain", "describe", "discuss", "include", "candidate",
})


def _persist_grade(
    submission_id: str,
    result: GradeResult,
    *,
    ai_call_id: str | None,
) -> None:
    feedback = {
        "by_criterion": result.by_criterion,
        "summary": result.summary,
        "suggestions": result.suggestions,
        "method": result.method,
    }
    with _conn() as conn:
        conn.execute(
            "UPDATE essay_submissions SET "
            " ai_score = ?, ai_feedback_json = ?, ai_call_id = ?, "
            " graded_at = ? WHERE id = ?",
            (result.score, json.dumps(feedback), ai_call_id,
             time.time(), submission_id),
        )


# ---------- default rubric seeding ----------

_DEFAULT_RUBRICS: list[dict] = [
    # UPSC Mains GS Papers
    {
        "exam": "upsc_mains", "paper": "GS1",
        "topic": "History, Society, Geography",
        "max_marks": 250,
        "criteria": [
            {"name": "Relevance & Coverage", "weight": 30,
             "description": "Answer addresses all dimensions of the question"},
            {"name": "Content Depth", "weight": 30,
             "description": "Factual accuracy, data, examples, case studies"},
            {"name": "Structure & Coherence", "weight": 20,
             "description": "Introduction, body paragraphs, conclusion; logical flow"},
            {"name": "Analytical Ability", "weight": 10,
             "description": "Critical reasoning, multi-perspective analysis"},
            {"name": "Language & Presentation", "weight": 10,
             "description": "Grammar, clarity, diagrams/maps where relevant"},
        ],
        "model_answer": (
            "A model GS1 answer covers historical context, societal impact, "
            "geographical dimensions, and links to current events with 2-3 "
            "relevant examples or data points per sub-part."
        ),
    },
    {
        "exam": "upsc_mains", "paper": "GS2",
        "topic": "Governance, Constitution, Polity, IR",
        "max_marks": 250,
        "criteria": [
            {"name": "Constitutional Accuracy", "weight": 30,
             "description": "Correct articles, schedules, landmark judgements cited"},
            {"name": "Policy Analysis", "weight": 25,
             "description": "Government schemes, committee recommendations referenced"},
            {"name": "IR & Security Linkage", "weight": 20,
             "description": "International organisations, bilateral ties, treaties"},
            {"name": "Structure", "weight": 15,
             "description": "Intro–Body–Conclusion, use of headings/bullets"},
            {"name": "Language", "weight": 10,
             "description": "Clarity, grammar, concise sentences"},
        ],
    },
    {
        "exam": "upsc_mains", "paper": "GS3",
        "topic": "Economy, Environment, Technology, Security",
        "max_marks": 250,
        "criteria": [
            {"name": "Economic Concepts", "weight": 30,
             "description": "Correct terminology, budget/policy data, RBI/SEBI references"},
            {"name": "Environmental Science", "weight": 25,
             "description": "Biodiversity, climate, conventions, Supreme Court orders"},
            {"name": "Technology & Innovation", "weight": 20,
             "description": "Current initiatives (Digital India, space, defence R&D)"},
            {"name": "Internal Security", "weight": 15,
             "description": "LWE, border management, cyber security frameworks"},
            {"name": "Presentation", "weight": 10,
             "description": "Legibility, diagram labels, conciseness"},
        ],
    },
    {
        "exam": "upsc_mains", "paper": "GS4",
        "topic": "Ethics, Integrity, Aptitude",
        "max_marks": 250,
        "criteria": [
            {"name": "Ethical Framework", "weight": 30,
             "description": "Thinker citations (Gandhi, Aristotle, Kant), value articulation"},
            {"name": "Case Study Analysis", "weight": 30,
             "description": "Stakeholder identification, dilemma resolution, pros/cons"},
            {"name": "Practical Suggestions", "weight": 20,
             "description": "Actionable, constitutionally sound recommendations"},
            {"name": "Emotional Intelligence", "weight": 10,
             "description": "Empathy, compassion, self-awareness demonstrated"},
            {"name": "Clarity", "weight": 10,
             "description": "Logical structure, no jargon, readable"},
        ],
    },
    {
        "exam": "upsc_essay", "paper": "Essay",
        "topic": "General Essay",
        "max_marks": 250,
        "criteria": [
            {"name": "Originality & Insight", "weight": 30,
             "description": "Fresh perspective, nuanced thesis, avoids clichés"},
            {"name": "Breadth of Ideas", "weight": 25,
             "description": "Multi-disciplinary examples: history, science, arts, policy"},
            {"name": "Coherence & Flow", "weight": 20,
             "description": "Smooth transitions, unified argument throughout"},
            {"name": "Introduction & Conclusion", "weight": 15,
             "description": "Memorable opening hook; satisfying conclusion with call to action"},
            {"name": "Language", "weight": 10,
             "description": "Vocabulary range, grammar, sentence variety"},
        ],
    },
    # JEE Advanced descriptive
    {
        "exam": "jee_adv_descriptive", "paper": "Physics",
        "topic": "Mechanics & Electromagnetism",
        "max_marks": 60,
        "criteria": [
            {"name": "Conceptual Accuracy", "weight": 40,
             "description": "Correct laws, principles, formulae applied"},
            {"name": "Mathematical Derivation", "weight": 30,
             "description": "Step-by-step algebra, correct substitutions, units"},
            {"name": "Diagram / Free-Body", "weight": 20,
             "description": "Labelled diagrams showing forces, fields, geometry"},
            {"name": "Final Answer", "weight": 10,
             "description": "Numerically correct with proper SI units"},
        ],
    },
    {
        "exam": "jee_adv_descriptive", "paper": "Chemistry",
        "topic": "Organic & Physical Chemistry",
        "max_marks": 60,
        "criteria": [
            {"name": "Reaction Mechanism", "weight": 40,
             "description": "Arrow pushing, intermediates, stereochemistry"},
            {"name": "Equations & Balancing", "weight": 30,
             "description": "Balanced equations, state symbols, conditions"},
            {"name": "Structure Drawing", "weight": 20,
             "description": "IUPAC names, correct structural formulae"},
            {"name": "Answer Accuracy", "weight": 10,
             "description": "Numerical values correct with units"},
        ],
    },
    # CBSE English
    {
        "exam": "cbse_class12_eng", "paper": "Writing Skills",
        "topic": "Letter / Article / Speech",
        "max_marks": 10,
        "criteria": [
            {"name": "Format Adherence", "weight": 30,
             "description": "Correct format for the genre (date, salutation, etc.)"},
            {"name": "Content Relevance", "weight": 30,
             "description": "All given points addressed; extra relevant points added"},
            {"name": "Expression & Vocabulary", "weight": 25,
             "description": "Idiomatic English, range of vocabulary, no repetition"},
            {"name": "Grammar & Mechanics", "weight": 15,
             "description": "Tenses, subject-verb agreement, punctuation, spelling"},
        ],
    },
    {
        "exam": "cbse_class10_eng", "paper": "Writing Skills",
        "topic": "Formal Letter / Paragraph",
        "max_marks": 10,
        "criteria": [
            {"name": "Format", "weight": 25,
             "description": "Prescribed layout for letter/paragraph"},
            {"name": "Content", "weight": 35,
             "description": "All points covered, coherent expansion"},
            {"name": "Language", "weight": 25,
             "description": "Grade-appropriate vocabulary and sentence variety"},
            {"name": "Accuracy", "weight": 15,
             "description": "Spelling, punctuation, grammar"},
        ],
    },
    # NEET descriptive (Biology)
    {
        "exam": "neet_descriptive", "paper": "Biology",
        "topic": "Human Physiology & Genetics",
        "max_marks": 60,
        "criteria": [
            {"name": "Scientific Accuracy", "weight": 40,
             "description": "Correct terminology, biological processes described correctly"},
            {"name": "Diagrams", "weight": 25,
             "description": "Labelled diagrams with clear structures"},
            {"name": "Examples & Applications", "weight": 20,
             "description": "Clinical/real-world examples connecting to concepts"},
            {"name": "Completeness", "weight": 15,
             "description": "All parts of multi-part questions answered"},
        ],
    },
    # CAT
    {
        "exam": "cat_va", "paper": "Verbal Ability",
        "topic": "Reading Comprehension & Critical Reasoning",
        "max_marks": 100,
        "criteria": [
            {"name": "Inference Quality", "weight": 35,
             "description": "Logical inferences directly supported by the passage"},
            {"name": "Argument Evaluation", "weight": 30,
             "description": "Identify assumptions, strengthen/weaken correctly"},
            {"name": "Vocabulary in Context", "weight": 20,
             "description": "Correct interpretation of highlighted words/phrases"},
            {"name": "Summary Accuracy", "weight": 15,
             "description": "Central idea captured; no distortion of tone"},
        ],
    },
    # Generic
    {
        "exam": "generic", "paper": "General",
        "topic": None,
        "max_marks": 100,
        "criteria": [
            {"name": "Relevance", "weight": 30,
             "description": "Answer directly addresses the question asked"},
            {"name": "Depth & Accuracy", "weight": 30,
             "description": "Factual correctness, examples, supporting evidence"},
            {"name": "Structure", "weight": 20,
             "description": "Introduction, logical body paragraphs, conclusion"},
            {"name": "Language", "weight": 20,
             "description": "Grammar, clarity, appropriate vocabulary"},
        ],
    },
]


def seed_default_rubrics() -> int:
    """Idempotent seed — inserts default rubrics that don't yet exist.
    Returns the count of rubrics newly created (0 = already seeded)."""
    created = 0
    for r in _DEFAULT_RUBRICS:
        try:
            existing = list_rubrics(exam=r["exam"])
            already = any(
                x.paper == r["paper"] and x.topic == r.get("topic")
                for x in existing
            )
            if not already:
                upsert_rubric(
                    exam=r["exam"],
                    paper=r["paper"],
                    topic=r.get("topic"),
                    criteria=r["criteria"],
                    max_marks=r["max_marks"],
                    model_answer=r.get("model_answer"),
                    created_by="system",
                )
                created += 1
        except Exception:  # noqa: BLE001
            pass
    return created
