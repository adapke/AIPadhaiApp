"""v3.9 — Socratic tutor mode.

Per review §7 (Khanmigo gap) + §14 — the generic tutor answers
questions. Khanmigo's wedge is **Socratic tutoring**: ask
diagnostic questions, give hints before the answer, detect
confusion, guide rather than tell.

This module extends `tutor_grounding.py`'s answer modes with a
4th: `socratic`. The state machine for a Socratic exchange:

  diagnose → hint → check → reveal

  diagnose  Tutor responds to student question with a probing
            sub-question ("Before I tell you, what do you think
            happens when...?")
  hint      Student answers; tutor gives a hint that nudges
            toward the answer without revealing it
  check     Tutor asks the student to apply the hint
  reveal    Only after 2-3 turns or explicit "tell me" → full
            answer with citations

We track per-session Socratic state so the tutor knows what step
it's in. Single table:

  socratic_exchanges   one row per Socratic exchange (question +
                       hint chain + final answer)

A Socratic exchange ties together multiple tutor messages with a
single `student_question`. Each turn appends to `turns_json`
(student response + tutor reply). When `state` flips to `reveal`,
the final grounded answer is recorded.

Detection signals — `confusion` boolean on student responses:
  • Response shorter than ~10 chars ("idk", "no", "?")
  • Contains explicit confusion markers ("don't understand",
    "what?", "help")
  • Time-on-page > 60s before reply (caller passes hint)

If confusion fires, the next turn switches from `check` back to
`hint` with a simpler hint instead of escalating to `reveal`.

Public APIs:
  start_exchange(session_id, user_id, question, target_depth=3)
  advance(exchange_id, user_id, student_text, time_seconds?)
  reveal(exchange_id, user_id, final_answer, citations?)
  get_exchange(exchange_id)
  list_user_exchanges(user_id)
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
CREATE TABLE IF NOT EXISTS socratic_exchanges (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    student_question TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'diagnose',
    -- 'diagnose' | 'hint' | 'check' | 'reveal' | 'abandoned'
    turns_json      TEXT NOT NULL DEFAULT '[]',
    -- list of {role: 'tutor'|'student', text, confusion?, time_seconds?, ts}
    target_depth    INTEGER NOT NULL DEFAULT 3,
    final_answer    TEXT,
    final_provenance_id TEXT,
    confusion_count INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_se_user
    ON socratic_exchanges(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_se_state
    ON socratic_exchanges(state, created_at DESC);
"""


VALID_STATES = frozenset({
    "diagnose", "hint", "check", "reveal", "abandoned",
})

# Min depth before we'll allow reveal — 1 means "as soon as
# student asks tell me you can answer". 2 is the minimum for an
# actual Socratic loop.
MIN_DEPTH = 1
DEFAULT_DEPTH = 3
MAX_DEPTH = 8


# Explicit confusion markers — when the student's text contains
# any of these, treat the response as confused.
_CONFUSION_PATTERNS = [
    re.compile(r"\bidk\b", re.IGNORECASE),
    re.compile(r"\bi (don'?t|do not) (know|understand|get it)\b",
               re.IGNORECASE),
    re.compile(r"\b(no idea|not sure|lost|confused|huh|what\?)\b",
               re.IGNORECASE),
    re.compile(r"^[\s\?]*$"),                # whitespace or just ?s
]

# Threshold below which a short response counts as 'maybe
# confused' (lazy reply / disengaged). We don't count it as
# strong confusion by itself; combine with timer.
SHORT_REPLY_CHARS = 10

# Time on page (seconds) above which we treat a short reply as
# a confusion signal — student stared at the screen and gave up.
SLOW_REPLY_THRESHOLD_SEC = 60

# How many confusions before we soften the next step (back to
# 'hint' instead of advancing).
CONFUSION_TOLERANCE = 2

# Explicit "tell me the answer" markers — student gives up on
# Socratic and demands the answer.
_REVEAL_REQUEST_PATTERNS = [
    re.compile(r"\b(tell|give) me (the )?answer\b", re.IGNORECASE),
    re.compile(r"\bjust (the )?answer\b", re.IGNORECASE),
    re.compile(r"\breveal\b", re.IGNORECASE),
    re.compile(r"\bi give up\b", re.IGNORECASE),
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
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Exchange:
    id: str
    session_id: str
    user_id: str
    student_question: str
    state: str
    turns: list[dict]
    target_depth: int
    final_answer: str | None
    final_provenance_id: str | None
    confusion_count: int
    created_at: float
    completed_at: float | None


@dataclass(frozen=True)
class AdvanceResult:
    """What the next turn should be — caller (router or tutor
    wrapper) generates the actual tutor reply using this."""
    exchange_id: str
    next_state: str
    next_action: str          # 'diagnose' / 'hint' / 'check' / 'reveal'
    suggested_depth: int       # how 'deep' the hint should be
    student_confused: bool
    student_demanded_reveal: bool
    turn_index: int


# ---------- detection ----------

def _is_confused(text: str, time_seconds: int | None) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    for pat in _CONFUSION_PATTERNS:
        if pat.search(text):
            return True
    # Short + slow → confusion
    return bool(len(text) <= SHORT_REPLY_CHARS and (time_seconds or 0) >= SLOW_REPLY_THRESHOLD_SEC)


def _wants_reveal(text: str) -> bool:
    return any(pat.search(text or "") for pat in _REVEAL_REQUEST_PATTERNS)


# ---------- lifecycle ----------

def start_exchange(
    *,
    session_id: str,
    user_id: str,
    student_question: str,
    target_depth: int = DEFAULT_DEPTH,
) -> Exchange:
    """Begin a new Socratic exchange. The first turn is the
    student's question — caller generates the diagnose response
    using the AdvanceResult we return implicitly via state."""
    student_question = (student_question or "").strip()
    if len(student_question) < 5 or len(student_question) > 4000:
        raise ValueError("student_question must be [5, 4000] chars")
    if target_depth < MIN_DEPTH or target_depth > MAX_DEPTH:
        raise ValueError(
            f"target_depth must be in [{MIN_DEPTH}, {MAX_DEPTH}]",
        )
    eid = uuid.uuid4().hex
    now = time.time()
    initial_turn = [
        {"role": "student", "text": student_question, "ts": now},
    ]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO socratic_exchanges "
            "(id, session_id, user_id, student_question, "
            " state, turns_json, target_depth, created_at) "
            "VALUES (?, ?, ?, ?, 'diagnose', ?, ?, ?)",
            (eid, session_id, user_id, student_question,
             json.dumps(initial_turn), target_depth, now),
        )
    return get_exchange(eid)  # type: ignore[return-value]


def get_exchange(exchange_id: str) -> Exchange | None:
    with _conn() as conn:
        r = conn.execute(
            _E_SEL + " WHERE id = ?", (exchange_id,),
        ).fetchone()
    return _row_to_exchange(r) if r else None


_E_SEL = (
    "SELECT id, session_id, user_id, student_question, state, "
    "       turns_json, target_depth, final_answer, "
    "       final_provenance_id, confusion_count, "
    "       created_at, completed_at "
    "FROM socratic_exchanges"
)


def _row_to_exchange(r) -> Exchange:
    return Exchange(
        id=r[0], session_id=r[1], user_id=r[2],
        student_question=r[3], state=r[4],
        turns=json.loads(r[5]) if r[5] else [],
        target_depth=r[6], final_answer=r[7],
        final_provenance_id=r[8],
        confusion_count=r[9], created_at=r[10],
        completed_at=r[11],
    )


def append_tutor_turn(
    *,
    exchange_id: str,
    tutor_text: str,
) -> Exchange:
    """Caller pushes the tutor's generated reply into the turn log.
    Doesn't change state — that happens on `advance()`."""
    ex = get_exchange(exchange_id)
    if not ex:
        raise ValueError("exchange not found")
    if ex.state in ("reveal", "abandoned"):
        raise ValueError(f"exchange is {ex.state!r}; can't append")
    tutor_text = (tutor_text or "").strip()
    if not tutor_text or len(tutor_text) > 8000:
        raise ValueError("tutor_text required (max 8000 chars)")
    turns = list(ex.turns)
    turns.append({
        "role": "tutor", "text": tutor_text,
        "step": ex.state, "ts": time.time(),
    })
    with _conn() as conn:
        conn.execute(
            "UPDATE socratic_exchanges SET turns_json = ? "
            "WHERE id = ?",
            (json.dumps(turns), exchange_id),
        )
    return get_exchange(exchange_id)  # type: ignore[return-value]


def advance(
    *,
    exchange_id: str,
    user_id: str,
    student_text: str,
    time_seconds: int | None = None,
) -> AdvanceResult:
    """Student replies. We detect confusion + reveal-demand,
    append the turn, advance state. Returns the next action the
    caller (tutor wrapper) should take.

    State transitions:
      diagnose → hint        (student gave any answer)
      hint     → check       (student attempted; not confused)
      hint     → hint        (student confused — drop a simpler hint)
      check    → reveal      (student got it or reached depth)
      check    → hint        (student confused; back off)
      *        → reveal      (student demanded answer)
    """
    ex = get_exchange(exchange_id)
    if not ex:
        raise ValueError("exchange not found")
    if ex.user_id != user_id:
        raise PermissionError("not your exchange")
    if ex.state in ("reveal", "abandoned"):
        raise ValueError(
            f"exchange is {ex.state!r}; can't advance",
        )
    student_text = (student_text or "").strip()
    if len(student_text) < 1 or len(student_text) > 4000:
        raise ValueError("student_text must be [1, 4000] chars")
    confused = _is_confused(student_text, time_seconds)
    demanded = _wants_reveal(student_text)
    turn_index = len(
        [t for t in ex.turns if t.get("role") == "tutor"]
    )
    # Build turn entry
    turns = list(ex.turns)
    turns.append({
        "role": "student", "text": student_text,
        "confusion": confused,
        "time_seconds": time_seconds, "ts": time.time(),
    })
    # State machine
    current = ex.state
    new_confusion_count = ex.confusion_count + (1 if confused else 0)
    if demanded:
        next_state = "reveal"
    elif current == "diagnose":
        next_state = "hint" if not confused else "hint"
    elif current == "hint":
        if confused:  # noqa: SIM108
            next_state = "hint"   # stay, drop simpler hint
        else:
            next_state = "check"
    elif current == "check":
        if confused:
            next_state = "hint"
        elif turn_index >= ex.target_depth:
            next_state = "reveal"
        else:
            next_state = "check"
    else:
        next_state = current
    # Force reveal if confusion tolerance breached AND we've
    # made at least 1 attempt — student is stuck; stop torturing them.
    if (new_confusion_count > CONFUSION_TOLERANCE
            and turn_index >= 1):
        next_state = "reveal"
    suggested_depth = (
        max(1, ex.target_depth - new_confusion_count)
    )
    with _conn() as conn:
        conn.execute(
            "UPDATE socratic_exchanges SET "
            " state = ?, turns_json = ?, confusion_count = ? "
            "WHERE id = ?",
            (next_state, json.dumps(turns),
             new_confusion_count, exchange_id),
        )
    return AdvanceResult(
        exchange_id=exchange_id, next_state=next_state,
        next_action=next_state,
        suggested_depth=suggested_depth,
        student_confused=confused,
        student_demanded_reveal=demanded,
        turn_index=turn_index + 1,
    )


def reveal(
    *,
    exchange_id: str,
    user_id: str,
    final_answer: str,
    citations: list[dict] | None = None,
    surface: str = "tutor",
) -> Exchange:
    """Final step — tutor reveals the full answer. We record a
    provenance row via `citations.record_answer` so the student's
    'why did the tutor say that?' UX still works. Marks the
    exchange complete."""
    ex = get_exchange(exchange_id)
    if not ex:
        raise ValueError("exchange not found")
    if ex.user_id != user_id:
        raise PermissionError("not your exchange")
    if ex.state == "abandoned":
        raise ValueError("exchange is abandoned; can't reveal")
    final_answer = (final_answer or "").strip()
    if len(final_answer) < 1 or len(final_answer) > 32000:
        raise ValueError("final_answer required (max 32000 chars)")
    # Record citation provenance
    provenance_id = None
    try:
        from . import citations as cit
        prov = cit.record_answer(
            surface=surface, user_id=user_id,
            question_text=ex.student_question,
            answer_text=final_answer,
            answer_mode="general",
            citations=citations,
        )
        provenance_id = prov.id
    except Exception:  # noqa: BLE001
        pass
    now = time.time()
    turns = list(ex.turns)
    turns.append({
        "role": "tutor", "text": final_answer,
        "step": "reveal", "provenance_id": provenance_id,
        "ts": now,
    })
    with _conn() as conn:
        conn.execute(
            "UPDATE socratic_exchanges SET "
            " state = 'reveal', turns_json = ?, "
            " final_answer = ?, final_provenance_id = ?, "
            " completed_at = ? "
            "WHERE id = ?",
            (json.dumps(turns), final_answer,
             provenance_id, now, exchange_id),
        )
    return get_exchange(exchange_id)  # type: ignore[return-value]


def abandon(
    *, exchange_id: str, user_id: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE socratic_exchanges SET "
            " state = 'abandoned', completed_at = ? "
            "WHERE id = ? AND user_id = ? "
            "AND state NOT IN ('reveal', 'abandoned')",
            (time.time(), exchange_id, user_id),
        )
        return cur.rowcount > 0


def list_user_exchanges(
    user_id: str,
    *,
    state: str | None = None,
    limit: int = 50,
) -> list[Exchange]:
    if state and state not in VALID_STATES:
        raise ValueError(f"state must be in {sorted(VALID_STATES)}")
    limit = max(1, min(limit, 500))
    sql = _E_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if state:
        sql += " AND state = ?"; params.append(state)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_exchange(r) for r in rows]


def user_stats(user_id: str) -> dict:
    """Per-student Socratic engagement signal — high
    confusion_count_per_exchange suggests harder topics; high
    reveal-demand rate suggests low engagement."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*), "
            "       COALESCE(SUM(confusion_count), 0), "
            "       SUM(CASE WHEN state = 'reveal' THEN 1 ELSE 0 END), "
            "       SUM(CASE WHEN state = 'abandoned' THEN 1 ELSE 0 END) "
            "FROM socratic_exchanges WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    total = int(r[0] or 0)
    if total == 0:
        return {
            "exchanges": 0, "completed_rate": 0.0,
            "avg_confusion_per_exchange": 0.0,
        }
    confusion = int(r[1] or 0)
    completed = int(r[2] or 0)
    abandoned = int(r[3] or 0)
    return {
        "exchanges": total,
        "completed": completed,
        "abandoned": abandoned,
        "completed_rate": round(completed / total, 3),
        "avg_confusion_per_exchange": round(confusion / total, 2),
    }
