"""L4 — Mock interview AI.

Voice-based interview simulator. Claude plays the panel; transcribes
answers (client-side STT or audio_url + server transcription);
generates a follow-up question based on the answer; produces an
overall feedback report at the end.

Supported tracks:
  upsc_personality       — UPSC Mains interview round (200 marks)
  jee_counseling         — JEE Advanced counseling readiness
  iit_placement          — IIT campus placement (technical + HR)
  neet_pg                — NEET PG departmental interview
  mba_admission          — IIM / ISB admission interview
  generic                — fallback when track unknown

Each track has a curated opening-question bank + a scoring rubric.
Follow-ups are LLM-generated based on the prior answer.

Schema:
  mock_interviews        — one row per session, status flow
  mock_interview_turns   — one row per (question, answer) pair
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS mock_interviews (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    track           TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    status          TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress | ended | abandoned
    feedback_json   TEXT,                                  -- final report
    overall_score   REAL,
    duration_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mi_user_time
    ON mock_interviews(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS mock_interview_turns (
    id              TEXT PRIMARY KEY,
    interview_id    TEXT NOT NULL,
    turn_index      INTEGER NOT NULL,
    question_text   TEXT NOT NULL,
    answer_text     TEXT,
    answer_audio_url TEXT,
    feedback_json   TEXT,                          -- {clarity, depth, ...}
    ai_call_id      TEXT,
    created_at      REAL NOT NULL,
    answered_at     REAL,
    UNIQUE (interview_id, turn_index)
);
CREATE INDEX IF NOT EXISTS idx_mit_interview
    ON mock_interview_turns(interview_id, turn_index);
"""


VALID_TRACKS = frozenset({
    "upsc_personality", "jee_counseling", "iit_placement",
    "neet_pg", "mba_admission", "generic",
})


# Curated opening questions per track. Real-world: each track also
# has a 100+ question bank that we sample from; for v2.4 we ship the
# starter seeds.
OPENING_QUESTIONS = {
    "upsc_personality": [
        "Tell me about yourself in 90 seconds.",
        "Why do you want to join the civil services?",
        "If you were the Collector of your home district tomorrow, "
        "what would your first three priorities be?",
        "What's the most recent decision of the Supreme Court that "
        "you disagreed with, and why?",
    ],
    "jee_counseling": [
        "Walk me through your top 3 choices and why.",
        "If your rank gave you Mech at IIT-B or CS at NIT Surathkal, "
        "what would you pick?",
        "What's the most interesting engineering problem you've "
        "thought about in the last month?",
    ],
    "iit_placement": [
        "Walk me through your resume.",
        "Tell me about a project where you had to debug something "
        "non-obvious.",
        "Why this company, and why this role over the others "
        "you've shortlisted?",
    ],
    "neet_pg": [
        "Why this branch?",
        "Describe your most difficult case during the internship.",
        "Where do you see yourself in 10 years?",
    ],
    "mba_admission": [
        "Tell me about a time you led a team through ambiguity.",
        "Why MBA, and why now?",
        "What's a recent business decision you'd second-guess and why?",
    ],
    "generic": [
        "Tell me about yourself.",
        "Why this programme?",
        "What's a strength of yours that doesn't appear on your "
        "resume?",
    ],
}


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
class Interview:
    id: str
    user_id: str
    track: str
    started_at: float
    ended_at: float | None
    status: str
    feedback: dict | None
    overall_score: float | None
    duration_seconds: int | None


@dataclass(frozen=True)
class Turn:
    id: str
    interview_id: str
    turn_index: int
    question_text: str
    answer_text: str | None
    answer_audio_url: str | None
    feedback: dict | None
    ai_call_id: str | None
    created_at: float
    answered_at: float | None


def _row_to_interview(r) -> Interview:
    return Interview(
        id=r[0], user_id=r[1], track=r[2],
        started_at=r[3], ended_at=r[4],
        status=r[5],
        feedback=json.loads(r[6]) if r[6] else None,
        overall_score=r[7], duration_seconds=r[8],
    )


def _row_to_turn(r) -> Turn:
    return Turn(
        id=r[0], interview_id=r[1], turn_index=r[2],
        question_text=r[3], answer_text=r[4],
        answer_audio_url=r[5],
        feedback=json.loads(r[6]) if r[6] else None,
        ai_call_id=r[7], created_at=r[8], answered_at=r[9],
    )


# ---------- session lifecycle ----------

def start(*, user_id: str, track: str = "generic") -> tuple[Interview, Turn]:
    """Create a new interview + insert the opening question. The
    opening is from the curated bank; subsequent questions are
    LLM-generated based on the prior answer."""
    if track not in VALID_TRACKS:
        raise ValueError(f"track must be in {sorted(VALID_TRACKS)}")
    iid = uuid.uuid4().hex
    opener = random.choice(OPENING_QUESTIONS[track])
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO mock_interviews "
            "(id, user_id, track, started_at, status) "
            "VALUES (?, ?, ?, ?, 'in_progress')",
            (iid, user_id, track, now),
        )
        tid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO mock_interview_turns "
            "(id, interview_id, turn_index, question_text, created_at) "
            "VALUES (?, ?, 0, ?, ?)",
            (tid, iid, opener, now),
        )
    return get(iid), get_turn(iid, 0)  # type: ignore[return-value]


def get(interview_id: str) -> Interview | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, track, started_at, ended_at, status, "
            "       feedback_json, overall_score, duration_seconds "
            "FROM mock_interviews WHERE id = ?", (interview_id,),
        ).fetchone()
    return _row_to_interview(r) if r else None


def list_for_user(user_id: str, *, limit: int = 20) -> list[Interview]:
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, track, started_at, ended_at, status, "
            "       feedback_json, overall_score, duration_seconds "
            "FROM mock_interviews WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_interview(r) for r in rows]


def get_turn(interview_id: str, turn_index: int) -> Turn | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, interview_id, turn_index, question_text, "
            "       answer_text, answer_audio_url, feedback_json, "
            "       ai_call_id, created_at, answered_at "
            "FROM mock_interview_turns "
            "WHERE interview_id = ? AND turn_index = ?",
            (interview_id, turn_index),
        ).fetchone()
    return _row_to_turn(r) if r else None


def list_turns(interview_id: str) -> list[Turn]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, interview_id, turn_index, question_text, "
            "       answer_text, answer_audio_url, feedback_json, "
            "       ai_call_id, created_at, answered_at "
            "FROM mock_interview_turns "
            "WHERE interview_id = ? ORDER BY turn_index",
            (interview_id,),
        ).fetchall()
    return [_row_to_turn(r) for r in rows]


# ---------- answer + follow-up ----------

@dataclass(frozen=True)
class AnswerResult:
    next_turn: Turn | None        # None when interview should end
    feedback: dict
    interview_ended: bool


# Reasonable cap so a stuck student doesn't burn budget
MAX_TURNS = 12


def submit_answer(
    *,
    interview_id: str,
    turn_index: int,
    answer_text: str,
    answer_audio_url: str | None = None,
) -> AnswerResult:
    """Persist the answer + generate a follow-up question (or end the
    interview at MAX_TURNS). Returns the next Turn or None on end."""
    if not answer_text or not answer_text.strip():
        raise ValueError("answer_text required")
    answer_text = answer_text[:6000]   # hard cap
    interview = get(interview_id)
    if not interview:
        raise ValueError(f"interview {interview_id!r} not found")
    if interview.status != "in_progress":
        raise ValueError("interview already ended")
    turn = get_turn(interview_id, turn_index)
    if not turn:
        raise ValueError(f"turn {turn_index} not found")
    if turn.answer_text is not None:
        raise ValueError("turn already answered")

    feedback = _grade_answer(
        track=interview.track,
        question=turn.question_text,
        answer=answer_text,
        user_id=interview.user_id,
    )
    ai_call_id = feedback.pop("_call_id", None)

    with _conn() as conn:
        conn.execute(
            "UPDATE mock_interview_turns SET "
            " answer_text = ?, answer_audio_url = ?, "
            " feedback_json = ?, ai_call_id = ?, answered_at = ? "
            "WHERE id = ?",
            (answer_text, answer_audio_url,
             json.dumps(feedback), ai_call_id, time.time(), turn.id),
        )

    next_index = turn_index + 1
    if next_index >= MAX_TURNS:
        return AnswerResult(
            next_turn=None, feedback=feedback, interview_ended=False,
        )

    # Generate the next question
    next_q = _next_question(
        track=interview.track,
        history=list_turns(interview_id),
        user_id=interview.user_id,
    )
    with _conn() as conn:
        nid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO mock_interview_turns "
            "(id, interview_id, turn_index, question_text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (nid, interview_id, next_index, next_q, time.time()),
        )
    next_turn = get_turn(interview_id, next_index)
    return AnswerResult(
        next_turn=next_turn, feedback=feedback, interview_ended=False,
    )


def end(*, interview_id: str) -> Interview:
    """Wrap up the interview: aggregate per-turn feedback into a
    final report + overall_score, mark status=ended."""
    interview = get(interview_id)
    if not interview:
        raise ValueError(f"interview {interview_id!r} not found")
    if interview.status != "in_progress":
        return interview  # idempotent
    turns = list_turns(interview_id)
    answered = [t for t in turns if t.answer_text]
    if not answered:
        # No answers — abandoned
        with _conn() as conn:
            conn.execute(
                "UPDATE mock_interviews SET "
                " status = 'abandoned', ended_at = ?, "
                " duration_seconds = ? WHERE id = ?",
                (time.time(),
                 int(time.time() - interview.started_at),
                 interview_id),
            )
        return get(interview_id)  # type: ignore[return-value]

    overall_score, report = _aggregate(answered)
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE mock_interviews SET "
            " status = 'ended', ended_at = ?, "
            " overall_score = ?, feedback_json = ?, "
            " duration_seconds = ? "
            "WHERE id = ?",
            (now, overall_score, json.dumps(report),
             int(now - interview.started_at), interview_id),
        )
    return get(interview_id)  # type: ignore[return-value]


# ---------- AI scoring helpers ----------

_SCORE_SYSTEM_TEMPLATE = """You are an interviewer for {track}. Score
the candidate's answer on 4 criteria, each 0-10:
  - clarity         (structure + brevity)
  - depth           (substance + originality)
  - relevance       (answers the question asked)
  - confidence      (assertive without arrogance)

Return strict JSON only:
{{
  "scores": {{"clarity": <n>, "depth": <n>, "relevance": <n>, "confidence": <n>}},
  "summary": "<one paragraph>",
  "improvement": "<single specific tip>"
}}
"""


def _grade_answer(
    *, track: str, question: str, answer: str, user_id: str,
) -> dict:
    """Returns feedback dict + _call_id for L6. Falls back to a
    heuristic when no Claude key."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _heuristic_feedback(answer)
    try:
        from anthropic import Anthropic   # noqa: WPS433
    except ImportError:
        return _heuristic_feedback(answer)

    from . import llm_cache, llm_obs
    model = os.environ.get(
        "PADHAI_MOCK_INTERVIEW_MODEL", "claude-haiku-4-5-20251001",
    )
    system_text = _SCORE_SYSTEM_TEMPLATE.format(track=track)
    user_text = (
        f"Question: {question}\n\n"
        f"Candidate's answer:\n---\n{answer}\n---"
    )
    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=user_text,
    )
    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=600, **kwargs,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[mock_interview] grade failed: {e}")
        return _heuristic_feedback(answer)
    latency_ms = int((time.time() - started) * 1000)
    body = "".join(b.text for b in resp.content if b.type == "text")
    parsed = _parse_score_json(body)
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    call_id = llm_obs.record_call(
        module="mock_interview", prompt_version="v1",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=user_id, cached=cached,
    )
    parsed["_call_id"] = call_id
    parsed["method"] = "claude"
    return parsed


def _heuristic_feedback(answer: str) -> dict:
    """Cheap fallback: length + hedge-word density + simple keyword
    diversity. Crude but produces the same shape."""
    words = answer.split()
    word_count = len(words)
    hedge_words = {"maybe", "kinda", "sorta", "i guess", "i think",
                   "like", "um", "uh"}
    hedge_count = sum(1 for w in words if w.lower().rstrip(",.") in hedge_words)
    unique = len({w.lower() for w in words}) / max(1, word_count)
    clarity = max(0, min(10, round(8 - hedge_count * 0.5)))
    depth = max(0, min(10, round(min(word_count / 50, 1.0) * 8)))
    relevance = 6   # heuristic can't tell; default neutral
    confidence = max(0, min(10, round((1 - hedge_count / max(1, word_count)) * 10)))
    return {
        "scores": {
            "clarity": clarity, "depth": depth,
            "relevance": relevance, "confidence": confidence,
        },
        "summary": (
            f"Heuristic feedback (no Claude key). {word_count} words, "
            f"{hedge_count} hedges, vocabulary diversity {unique:.0%}."
        ),
        "improvement": (
            "Speak in concrete numbers + specific examples. Avoid "
            "filler words like 'maybe' and 'I think'."
        ),
        "method": "heuristic",
    }


def _parse_score_json(body: str) -> dict:
    body = body.strip()
    candidates = [body]
    if "```" in body:
        for fence in body.split("```"):
            s = fence.strip()
            if s.startswith("{") or s.startswith("json\n{"):
                candidates.append(s[5:] if s.startswith("json\n") else s)
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict) and "scores" in parsed:
                return parsed
        except (ValueError, TypeError):
            continue
    return _heuristic_feedback("")


_FOLLOWUP_SYSTEM = """You are running a structured interview. Given
the prior Q&A history, generate the SINGLE next question. Build on
the candidate's last answer — probe a claim, ask for a concrete
example, or pivot to test breadth.

Reply with just the question text. No JSON, no preamble.
"""


def _next_question(
    *, track: str, history: list[Turn], user_id: str,
) -> str:
    """Claude-driven follow-up. Falls back to the opening bank when
    no Claude key — picks a question not yet asked."""
    asked = {t.question_text for t in history}
    available = [
        q for q in OPENING_QUESTIONS.get(track, OPENING_QUESTIONS["generic"])
        if q not in asked
    ]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        if available:
            return random.choice(available)
        return "Tell me about a time you changed your mind on something important."

    try:
        from anthropic import Anthropic   # noqa: WPS433
    except ImportError:
        return random.choice(available) if available else (
            "What's your biggest weakness, and what are you doing about it?"
        )

    from . import llm_cache, llm_obs
    model = os.environ.get(
        "PADHAI_MOCK_INTERVIEW_MODEL", "claude-haiku-4-5-20251001",
    )
    system_text = _FOLLOWUP_SYSTEM
    convo = []
    for t in history:
        convo.append(f"Q: {t.question_text}")
        if t.answer_text:
            convo.append(f"A: {t.answer_text}")
    user_text = (
        f"Track: {track}\n\nPrior turns:\n" + "\n".join(convo)
    )
    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=user_text,
    )
    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=200, **kwargs,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[mock_interview] follow-up failed: {e}")
        return (random.choice(available) if available
                else "Tell me one thing about you that's not on your resume.")
    latency_ms = int((time.time() - started) * 1000)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        return random.choice(available) if available else "Why this programme?"
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    llm_obs.record_call(
        module="mock_interview", prompt_version="v1-followup",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=user_id, cached=cached,
    )
    # Trim trailing fluff
    return text.split("\n")[0].strip().strip('"').strip()


def _aggregate(turns: list[Turn]) -> tuple[float, dict]:
    """Compute overall_score (avg of per-criterion scores across
    turns) + a final report shape."""
    criteria_sums = {"clarity": 0.0, "depth": 0.0,
                     "relevance": 0.0, "confidence": 0.0}
    criteria_counts = {k: 0 for k in criteria_sums}
    summaries = []
    improvements = []
    for t in turns:
        fb = t.feedback or {}
        scores = (fb.get("scores") or {})
        for k in criteria_sums:
            if k in scores:
                criteria_sums[k] += float(scores[k])
                criteria_counts[k] += 1
        if fb.get("summary"):
            summaries.append(fb["summary"])
        if fb.get("improvement"):
            improvements.append(fb["improvement"])
    criteria_avg = {
        k: round(criteria_sums[k] / criteria_counts[k], 2)
        if criteria_counts[k] else 0.0
        for k in criteria_sums
    }
    overall = round(sum(criteria_avg.values()) / 4.0, 2)
    return overall, {
        "criteria_avg": criteria_avg,
        "turn_count": len(turns),
        "summaries": summaries[-3:],
        "top_improvements": list(dict.fromkeys(improvements))[:3],
    }
