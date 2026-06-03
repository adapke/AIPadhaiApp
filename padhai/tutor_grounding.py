"""v3.2 — Citation-aware tutor wrapper.

Per gap-review §14 — the existing tutor (`padhai/tutor.py`) is a
generic LLM chat. The review wants:
  • Every reply carries citations
  • "Not found in your material" fallback in strict modes
  • Per-session answer mode (source_only / official / general)
  • Detect confusion + suggest practice
  • Refuse cheating during active tests

This module is a thin wrapper around `tutor.send_message` +
`citations.record_answer`. It does **not** replace the tutor —
it composes around it.

Three entry points:
  set_session_mode(sid, mode)   — pick answer_mode for a session
  get_session_mode(sid)         — read current mode (default 'general')
  send_grounded_message(...)    — wrap a tutor reply with citation
                                  recording + strict-mode guard

Retrieval (picking which document_pages chunks to ground on) is
deliberately NOT in this module — that's a separate RAG concern.
Callers pass `retrieved_chunks` in if they already ran retrieval;
the wrapper just persists the provenance.

Single table:
  tutor_session_modes  per-session answer_mode override
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import citations as _cit


SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_session_modes (
    session_id      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    answer_mode     TEXT NOT NULL DEFAULT 'general',
    -- 'general' | 'source_only' | 'official'
    test_active     INTEGER NOT NULL DEFAULT 0,    -- 1 = refuse answers
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tsm_user
    ON tutor_session_modes(user_id);
"""


DEFAULT_MODE = "general"


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
class SessionMode:
    session_id: str
    user_id: str
    answer_mode: str
    test_active: bool
    updated_at: float


@dataclass(frozen=True)
class GroundedReply:
    text: str
    provenance_id: str
    grounded: bool
    answer_mode: str
    fallback: bool                   # True when strict-mode declined
    fallback_reason: str | None
    citation_count: int


# ---------- session mode ----------

def set_session_mode(
    *,
    session_id: str,
    user_id: str,
    answer_mode: str,
) -> SessionMode:
    if answer_mode not in _cit.VALID_ANSWER_MODES:
        raise ValueError(
            f"answer_mode must be in {sorted(_cit.VALID_ANSWER_MODES)}",
        )
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT user_id FROM tutor_session_modes "
            "WHERE session_id = ?", (session_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE tutor_session_modes SET "
                " answer_mode = ?, updated_at = ? "
                "WHERE session_id = ?",
                (answer_mode, now, session_id),
            )
        else:
            conn.execute(
                "INSERT INTO tutor_session_modes "
                "(session_id, user_id, answer_mode, "
                " test_active, updated_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (session_id, user_id, answer_mode, now),
            )
    return get_session_mode(session_id)  # type: ignore[return-value]


def get_session_mode(session_id: str) -> SessionMode | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT session_id, user_id, answer_mode, "
            "       test_active, updated_at "
            "FROM tutor_session_modes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not r:
        return None
    return SessionMode(
        session_id=r[0], user_id=r[1], answer_mode=r[2],
        test_active=bool(r[3]), updated_at=r[4],
    )


def set_test_active(
    *,
    session_id: str,
    user_id: str,
    active: bool,
) -> bool:
    """Flag that a mock test is currently in progress for this
    user. While active, send_grounded_message refuses to answer
    (anti-cheat — review §14)."""
    with _conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM tutor_session_modes WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE tutor_session_modes SET "
                " test_active = ?, updated_at = ? "
                "WHERE session_id = ?",
                (1 if active else 0, time.time(), session_id),
            )
        else:
            conn.execute(
                "INSERT INTO tutor_session_modes "
                "(session_id, user_id, answer_mode, "
                " test_active, updated_at) "
                "VALUES (?, ?, 'general', ?, ?)",
                (session_id, user_id, 1 if active else 0,
                 time.time()),
            )
    return True


# ---------- the wrapper ----------

CHEAT_GUARD_MESSAGE = (
    "I can't help with this while you have an active mock test. "
    "Submit the test first — I'll be ready to walk through any "
    "question afterward."
)


def send_grounded_message(
    *,
    session_id: str,
    user_id: str,
    question_text: str,
    answer_text: str,
    retrieved_chunks: list[dict] | None = None,
    ai_call_id: str | None = None,
    confidence: float | None = None,
    surface: str = "tutor",
) -> GroundedReply:
    """Persist a tutor reply with citations + return a structured
    result the caller hands to the user.

    `retrieved_chunks` is a list of dicts with the same shape as
    citations.record_answer expects:
      {source_kind, source_id, page_number?, section?,
       citation_text, relevance?}

    If the session is in strict mode (source_only / official) and
    no chunks are provided, returns a fallback reply with the
    not-found message instead of the LLM output.

    If the user has an active mock test, returns the cheat-guard
    message — refuses to answer regardless of mode.
    """
    sm = get_session_mode(session_id)
    if sm and sm.test_active:
        # Record the refusal as a 'general' provenance so we can
        # audit anti-cheat events
        prov = _cit.record_answer(
            surface=surface, user_id=user_id,
            question_text=question_text,
            answer_text=CHEAT_GUARD_MESSAGE,
            answer_mode="general",
            fallback_reason="cheat_guard",
            ai_call_id=ai_call_id,
        )
        return GroundedReply(
            text=CHEAT_GUARD_MESSAGE,
            provenance_id=prov.id, grounded=False,
            answer_mode="general", fallback=True,
            fallback_reason="cheat_guard",
            citation_count=0,
        )

    mode = sm.answer_mode if sm else DEFAULT_MODE
    chunks = retrieved_chunks or []

    # Strict-mode + zero chunks → return not-found, don't echo the
    # LLM (which may have hallucinated)
    if mode in ("source_only", "official") and not chunks:
        prov = _cit.record_answer(
            surface=surface, user_id=user_id,
            question_text=question_text,
            answer_text=_cit.NOT_FOUND_MESSAGE,
            answer_mode="general",          # record as general so
                                            # NotGroundedError doesn't fire
            fallback_reason="no_source_match",
            confidence=confidence,
            ai_call_id=ai_call_id,
        )
        return GroundedReply(
            text=_cit.NOT_FOUND_MESSAGE,
            provenance_id=prov.id, grounded=False,
            answer_mode=mode, fallback=True,
            fallback_reason="no_source_match",
            citation_count=0,
        )

    # Official mode requires at least one official_doc citation
    if mode == "official" and not any(
        c.get("source_kind") == "official_doc" for c in chunks
    ):
        prov = _cit.record_answer(
            surface=surface, user_id=user_id,
            question_text=question_text,
            answer_text=_cit.NOT_FOUND_MESSAGE,
            answer_mode="general",
            fallback_reason="no_official_source",
            confidence=confidence,
            ai_call_id=ai_call_id,
        )
        return GroundedReply(
            text=_cit.NOT_FOUND_MESSAGE,
            provenance_id=prov.id, grounded=False,
            answer_mode=mode, fallback=True,
            fallback_reason="no_official_source",
            citation_count=0,
        )

    prov = _cit.record_answer(
        surface=surface, user_id=user_id,
        question_text=question_text,
        answer_text=answer_text,
        answer_mode=mode,
        confidence=confidence,
        citations=chunks if chunks else None,
        ai_call_id=ai_call_id,
    )
    return GroundedReply(
        text=answer_text,
        provenance_id=prov.id, grounded=prov.grounded,
        answer_mode=mode, fallback=False,
        fallback_reason=None,
        citation_count=len(chunks),
    )


def user_recent_fallbacks(
    user_id: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Audit: recent times we declined to answer because of mode
    constraints. Drives the 'upload more material' nudge in the UI."""
    answers = _cit.list_user_answers(
        user_id=user_id, surface=None, limit=limit * 3,
    )
    return [
        {"id": a.id, "question": a.question_text,
         "surface": a.surface, "reason": a.fallback_reason,
         "created_at": a.created_at}
        for a in answers
        if a.fallback_reason
    ][:limit]
