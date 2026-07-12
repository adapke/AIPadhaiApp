"""L5 — Adaptive practice-test generator.

Given a student's mastery profile (J5), generate a 30-min practice
paper targeting their weak areas with a sensible difficulty mix.
Pulls from J6 question bank when available; synthesises similar-
style new questions via Claude when bank is thin.

Difficulty mix (tunable per exam, defaults here):
  30% recall   — easier, weak-topic-focused (mastery < 0.4)
  50% standard — mixed topics at student's current level
  20% stretch  — slightly above current, motivates growth

Cost path: Claude calls for synthetic-question generation go
through Q2 caching (the exam style guide is the cached system).
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import models as _models

SCHEMA = """
CREATE TABLE IF NOT EXISTS practice_tests (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    exam            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    target_minutes  INTEGER NOT NULL DEFAULT 30,
    questions_json  TEXT NOT NULL,         -- [{question_id, source, marks, difficulty, ...}]
    status          TEXT NOT NULL DEFAULT 'ready',  -- ready | in_progress | submitted | abandoned
    started_at      REAL,
    submitted_at    REAL,
    score_json      TEXT,                  -- {total, max, per_question:[...]}
    generation_method TEXT NOT NULL DEFAULT 'bank',  -- bank | synthetic | mixed
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pt_user_time
    ON practice_tests(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pt_status
    ON practice_tests(status, created_at DESC);
"""


# Default difficulty mix by stretch. Caller can override.
DIFFICULTY_MIX_DEFAULT = {
    "recall": 0.30,
    "standard": 0.50,
    "stretch": 0.20,
}

VALID_EXAMS = frozenset({
    "upsc", "upsc_pre", "upsc_mains",
    "jee_main", "jee_advanced",
    "neet", "cat", "gate", "generic",
    "sat",  # prod-192 — US Digital SAT (subjects: sat_math, sat_reading_writing)
    # prod-248 — school boards are first-class practice targets. A CBSE
    # student picking "CBSE" must not get a 400. The bank pull filters by
    # subject (board-agnostic) and synthesis uses the key as a context
    # hint, so these behave like "generic" school practice with the right
    # board label in the prompt.
    "cbse", "icse", "state",
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


@dataclass(frozen=True)
class PracticeTest:
    id: str
    user_id: str
    exam: str
    subject: str
    target_minutes: int
    questions: list[dict]
    status: str
    started_at: float | None
    submitted_at: float | None
    score: dict | None
    generation_method: str
    created_at: float


def _row_to_test(r) -> PracticeTest:
    return PracticeTest(
        id=r[0], user_id=r[1], exam=r[2], subject=r[3],
        target_minutes=r[4],
        questions=json.loads(r[5]) if r[5] else [],
        status=r[6],
        started_at=r[7], submitted_at=r[8],
        score=json.loads(r[9]) if r[9] else None,
        generation_method=r[10], created_at=r[11],
    )


_SEL = (
    "id, user_id, exam, subject, target_minutes, questions_json, "
    "status, started_at, submitted_at, score_json, "
    "generation_method, created_at"
)


# ---------- generation ----------

def generate(
    *,
    user_id: str,
    exam: str,
    subject: str,
    target_minutes: int = 30,
    target_questions: int | None = None,
    difficulty_mix: dict[str, float] | None = None,
    user_tier: str | None = None,
) -> PracticeTest:
    """Create a new practice test for this user. Reads J5 mastery to
    pick weak topics, J6 question bank for existing questions,
    synthesises via Claude when needed.

    `target_minutes` drives `target_questions` if not given: at ~90s
    per MCQ + ~3min per long-answer, 30 min ≈ 20 questions."""
    if exam not in VALID_EXAMS:
        raise ValueError(f"exam must be in {sorted(VALID_EXAMS)}")
    if target_minutes < 5 or target_minutes > 240:
        raise ValueError("target_minutes must be in [5, 240]")
    if target_questions is None:
        # 90s per MCQ-equivalent
        target_questions = max(5, min(60, round(target_minutes * 60 / 90)))
    difficulty_mix = difficulty_mix or DIFFICULTY_MIX_DEFAULT
    if abs(sum(difficulty_mix.values()) - 1.0) > 0.01:
        raise ValueError("difficulty_mix weights must sum to 1.0")

    # Resolve weak topics + general topics via J5 mastery + J6 bank
    weak_topics = _resolve_weak_topics(user_id=user_id, subject=subject)
    counts = _slot_counts(target_questions, difficulty_mix)

    questions: list[dict] = []
    method_marker = "bank"

    # Recall slots prefer weak-topic bank pulls
    for _ in range(counts["recall"]):
        q = _pull_bank_question(
            exam=exam, subject=subject,
            topic_keys=weak_topics, difficulty="easy",
        )
        if q:
            questions.append(_format_q(q, slot="recall"))

    for _ in range(counts["standard"]):
        q = _pull_bank_question(
            exam=exam, subject=subject,
            topic_keys=None, difficulty="medium",
        )
        if q:
            questions.append(_format_q(q, slot="standard"))

    for _ in range(counts["stretch"]):
        q = _pull_bank_question(
            exam=exam, subject=subject,
            topic_keys=None, difficulty="hard",
        )
        if q:
            questions.append(_format_q(q, slot="stretch"))

    # Top up via synthetic generation when the bank can't fill
    missing = target_questions - len(questions)
    if missing > 0:
        synthesised = _synthesise(
            exam=exam, subject=subject, count=missing,
            weak_topics=weak_topics, user_id=user_id,
            user_tier=user_tier,
        )
        questions.extend(synthesised)
        method_marker = "mixed" if questions else "synthetic"

    if not questions:
        # No bank, no synthesis (no Claude key) — degrade gracefully
        # with placeholders so the dev path still produces a Test.
        for i in range(target_questions):
            questions.append({
                "id": f"placeholder-{i+1}",
                "source": "placeholder",
                "slot": "standard",
                "marks": 1,
                "difficulty": "medium",
                "question_text": (
                    f"[Placeholder question {i+1} for {subject}. "
                    "Configure question bank or ANTHROPIC_API_KEY to "
                    "generate real questions.]"
                ),
                "options": None,
                "correct_answer": None,
            })
        method_marker = "placeholder"

    # Shuffle so the difficulty slots don't cluster
    random.shuffle(questions)

    tid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO practice_tests "
            "(id, user_id, exam, subject, target_minutes, "
            " questions_json, status, generation_method, created_at) "
            "VALUES (?,?,?,?,?,?, 'ready', ?, ?)",
            (tid, user_id, exam, subject, target_minutes,
             json.dumps(questions), method_marker, time.time()),
        )
    return get(tid)  # type: ignore[return-value]


def _slot_counts(target: int, mix: dict[str, float]) -> dict[str, int]:
    """Distribute target across slots; rounding error goes to standard."""
    out = {k: round(target * v) for k, v in mix.items()}
    delta = target - sum(out.values())
    out["standard"] = max(0, out.get("standard", 0) + delta)
    return out


def _resolve_weak_topics(*, user_id: str, subject: str) -> list[str]:  # noqa: ARG001
    """Return up to 5 weak-topic keys for this user. Empty list when
    the mastery table is empty (new user)."""
    try:
        from . import mastery
        weak = mastery.weak_topics(user_id=user_id)
        # Filter to subject-relevant when key looks like 'subject:topic'
        keys = [m.topic_key for m in weak if not m.topic_key.startswith(":")]
        return keys[:5]
    except Exception:
        return []


def _pull_bank_question(
    *,
    exam: str,  # noqa: ARG001
    subject: str,
    topic_keys: list[str] | None,
    difficulty: str,
) -> dict | None:
    """Single random question from J6 matching the filter. Returns
    None when the bank has no match (caller falls back to synthesis)."""
    try:
        from . import question_bank
    except ImportError:
        return None
    # question_bank.search filters by board/grade/subject; exam→board
    # mapping is loose. Caller should ideally pass board, but for
    # exam-driven flows we treat 'exam' as a soft hint and search by
    # subject only.
    rows = question_bank.search(
        subject=subject, difficulty=difficulty, limit=200,
    )
    if not rows:
        return None
    if topic_keys:
        topic_lower = [t.lower() for t in topic_keys]
        filtered = [
            q for q in rows
            if any(
                tt and tt.lower() in topic_lower
                for tt in (q.topic_tags or [])
            )
        ]
        if filtered:
            rows = filtered
    return _q_to_dict(random.choice(rows))


def _q_to_dict(q) -> dict:
    return {
        "id": q.id, "source": "bank",
        "question_text": q.question_text,
        "options": q.options, "correct_answer": q.correct_answer,
        "marks": q.marks or 1, "difficulty": q.difficulty,
        "topic_tags": q.topic_tags or [],
        "explanation": getattr(q, "explanation", None),  # prod-193
    }


def _format_q(q: dict, *, slot: str) -> dict:
    return {**q, "slot": slot}


# ---------- synthesis (Claude) ----------

_SYNTH_SYSTEM_TEMPLATE = """You are a question setter for {exam}
exams in India. Generate {count} multiple-choice questions on the
subject {subject}.

Target areas (student's weak topics): {weak_topics}

Format STRICTLY as a JSON array, no markdown:
[
  {{
    "question_text": "...",
    "options": ["a) ...", "b) ...", "c) ...", "d) ..."],
    "correct_answer": "a",
    "marks": 1,
    "difficulty": "medium",
    "topic_tags": ["..."]
  }},
  ...
]
Mix difficulty: ~half medium, quarter easy, quarter hard.
"""


def _synthesise(
    *,
    exam: str,
    subject: str,
    count: int,
    weak_topics: list[str],
    user_id: str,
    user_tier: str | None = None,
) -> list[dict]:
    """Generate `count` synthetic MCQs via Claude. Returns [] if
    Claude unavailable OR the user is over their daily budget — caller
    pads with placeholders."""
    if count <= 0:
        return []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    try:
        from anthropic import Anthropic
    except ImportError:
        return []
    from . import llm_cache, llm_obs
    try:
        llm_obs.check_daily_cap(
            user_id=user_id, subscription_tier=user_tier,
        )
    except llm_obs.BudgetExceeded as e:
        print(f"[practice_test] over budget ({e.reason}); skipping Claude call")
        return []

    model = os.environ.get(
        "PADHAI_PRACTICE_MODEL", _models.HAIKU_MODEL,
    )
    system_text = _SYNTH_SYSTEM_TEMPLATE.format(
        exam=exam, count=count, subject=subject,
        weak_topics=", ".join(weak_topics) or "(none — pick general topics)",
    )
    user_text = "Generate the questions now."
    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=user_text,
    )

    # call_claude wraps the messages.create + record_call + cost
    # accounting. Cap pre-flight already ran above (`check_daily_cap`).
    from . import llm_call
    try:
        call = llm_call.call_claude(
            module="practice_test", prompt_version="v1",
            model=model, user_id=user_id, subscription_tier=user_tier,
            enforce_cap=False,
            max_tokens=2500,
            **kwargs,
        )
    except RuntimeError as e:
        print(f"[practice_test] synth call failed: {e}")
        return []

    parsed = _parse_synth(call.text)
    out = []
    for q in parsed[:count]:
        q.setdefault("id", "synth-" + uuid.uuid4().hex[:12])
        q.setdefault("source", "synthetic")
        q.setdefault("slot", "standard")
        q.setdefault("marks", 1)
        out.append(q)
    return out


def _parse_synth(body: str) -> list[dict]:
    """Best-effort JSON array extraction. Returns [] on parse failure
    so the caller pads with placeholders rather than crashes."""
    body = body.strip()
    candidates = [body]
    if "```" in body:
        for fence in body.split("```"):
            s = fence.strip()
            if s.startswith("[") or s.startswith("json\n["):
                candidates.append(s[5:] if s.startswith("json\n") else s)
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, list):
                return [q for q in parsed if isinstance(q, dict)]
        except (ValueError, TypeError):
            continue
    return []


# ---------- read / submit ----------

def get(test_id: str) -> PracticeTest | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM practice_tests WHERE id = ?",
            (test_id,),
        ).fetchone()
    return _row_to_test(r) if r else None


def list_for_user(user_id: str, *, limit: int = 20) -> list[PracticeTest]:
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM practice_tests "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_test(r) for r in rows]


def start(test_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE practice_tests SET status = 'in_progress', "
            " started_at = ? WHERE id = ? AND status = 'ready'",
            (time.time(), test_id),
        )
        return cur.rowcount > 0


def submit(
    *,
    test_id: str,
    answers: dict[str, str],
) -> dict:
    """Grade the submitted answers. For MCQs: exact-match on
    correct_answer. Returns a score dict + persists to score_json.

    `answers` is {question_id: chosen_letter}.
    """
    t = get(test_id)
    if not t:
        raise ValueError(f"test {test_id!r} not found")
    if t.status == "submitted":
        raise ValueError("test already submitted")
    per_question = []
    total = 0
    max_total = 0
    for q in t.questions:
        qid = q["id"]
        max_total += int(q.get("marks", 1) or 1)
        chosen = (answers.get(qid) or "").strip().lower()
        correct = (q.get("correct_answer") or "").strip().lower()
        is_correct = bool(chosen) and bool(correct) and chosen.startswith(correct[:1])
        if is_correct:
            total += int(q.get("marks", 1) or 1)
        per_question.append({
            "question_id": qid,
            "chosen": chosen, "correct": correct,
            "is_correct": is_correct,
            "marks": q.get("marks", 1),
            "topic_tags": q.get("topic_tags") or [],
            "explanation": q.get("explanation"),  # prod-193 — shown post-submit
        })
    score = {
        "total": total, "max": max_total,
        "pct": (total / max_total) if max_total else 0,
        "per_question": per_question,
    }
    with _conn() as conn:
        conn.execute(
            "UPDATE practice_tests SET status = 'submitted', "
            " submitted_at = ?, score_json = ? WHERE id = ?",
            (time.time(), json.dumps(score), test_id),
        )
    # Feed back into J5 mastery — each question's correctness becomes
    # a mastery signal on the question's topic_tags.
    try:
        from . import mastery
        for r in per_question:
            for tag in (r["topic_tags"] or []):
                mastery.update(
                    user_id=t.user_id, topic_key=tag,
                    correct=r["is_correct"],
                )
    except Exception as e:
        print(f"[practice_test] mastery update non-fatal: {e}")
    return score
