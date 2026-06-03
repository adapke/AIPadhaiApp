"""v3.1 — AI source citations.

Every AI-generated answer (tutor reply, lesson summary, generated
quiz, essay feedback) carries provenance — what source it pulled
from + which page/section + confidence + verbatim quote.

Two tables:
  ai_answer_provenance   one row per AI answer (links to ai_call_id)
  ai_citations           N rows per answer — the actual page-level
                         citations a UI renders alongside the body

The reason for the split: an answer may cite zero sources (in which
case `grounded=False` triggers the "not found in your material"
guard), or many (a topic explainer might cite 4-5 NCERT pages).

This module is the storage layer. The actual retrieval — picking
which document_pages chunks to ground an answer on — lives in
`padhai/retrieval.py` (separate, RAG-shaped).

Three answer modes:
  source_only — refuse to answer if no source supports it. Default
                for school/college user-uploaded notes.
  official    — same as source_only but restricted to officially
                ingested (board / exam body) sources. Default for
                verified exam packs.
  general     — fall back to LLM general knowledge if no source
                covers the question. Citations still attached when
                available. Default for free-tier tutor.
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
CREATE TABLE IF NOT EXISTS ai_answer_provenance (
    id              TEXT PRIMARY KEY,
    ai_call_id      TEXT,                          -- llm_obs.ai_calls.id (nullable for offline gen)
    user_id         TEXT,
    surface         TEXT NOT NULL,                  -- 'tutor' | 'lesson' | 'quiz' | 'essay' | 'doubt'
    question_text   TEXT NOT NULL,                  -- prompt / question being answered
    answer_text     TEXT NOT NULL,                  -- LLM output (trimmed)
    answer_mode     TEXT NOT NULL DEFAULT 'general',
    -- 'source_only' | 'official' | 'general'
    grounded        INTEGER NOT NULL,               -- 1 if at least one citation, else 0
    confidence      REAL,                           -- LLM-reported 0..1
    fallback_reason TEXT,                           -- 'no_source_match' | 'low_confidence' | NULL
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aap_user
    ON ai_answer_provenance(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aap_surface
    ON ai_answer_provenance(surface, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_aap_grounded
    ON ai_answer_provenance(grounded, surface);

CREATE TABLE IF NOT EXISTS ai_citations (
    id              TEXT PRIMARY KEY,
    provenance_id   TEXT NOT NULL,
    source_kind     TEXT NOT NULL,                  -- 'upload' | 'question_bank' | 'lesson' | 'official_doc'
    source_id       TEXT NOT NULL,                  -- FK depending on source_kind
    page_number     INTEGER,                        -- 1-indexed when applicable
    section         TEXT,                           -- e.g. '4.2 Linear Equations'
    citation_text   TEXT NOT NULL,                  -- verbatim quote from source
    relevance       REAL,                           -- 0..1 — retrieval score
    position        INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aic_prov
    ON ai_citations(provenance_id, position);
CREATE INDEX IF NOT EXISTS idx_aic_source
    ON ai_citations(source_kind, source_id);
"""


VALID_SURFACES = frozenset({
    "tutor", "lesson", "quiz", "essay", "doubt", "mock_interview",
})

VALID_ANSWER_MODES = frozenset({
    "source_only", "official", "general",
})

VALID_SOURCE_KINDS = frozenset({
    "upload", "question_bank", "lesson", "official_doc",
})

# What we tell the user when no source supports the answer in
# strict mode. This text is the UI-facing default; the surface
# layer can override.
NOT_FOUND_MESSAGE = (
    "I couldn't find this in your material. Try uploading the "
    "relevant chapter, or switch to general-knowledge mode."
)


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
class Citation:
    id: str
    provenance_id: str
    source_kind: str
    source_id: str
    page_number: int | None
    section: str | None
    citation_text: str
    relevance: float | None
    position: int
    created_at: float


@dataclass(frozen=True)
class AnswerProvenance:
    id: str
    ai_call_id: str | None
    user_id: str | None
    surface: str
    question_text: str
    answer_text: str
    answer_mode: str
    grounded: bool
    confidence: float | None
    fallback_reason: str | None
    created_at: float
    citations: list[Citation]


_PROV_SEL = (
    "SELECT id, ai_call_id, user_id, surface, question_text, "
    "       answer_text, answer_mode, grounded, confidence, "
    "       fallback_reason, created_at "
    "FROM ai_answer_provenance"
)


def _row_to_citation(r) -> Citation:
    return Citation(
        id=r[0], provenance_id=r[1], source_kind=r[2],
        source_id=r[3], page_number=r[4], section=r[5],
        citation_text=r[6], relevance=r[7], position=r[8],
        created_at=r[9],
    )


def _row_to_provenance(r, citations: list[Citation]) -> AnswerProvenance:
    return AnswerProvenance(
        id=r[0], ai_call_id=r[1], user_id=r[2],
        surface=r[3], question_text=r[4],
        answer_text=r[5], answer_mode=r[6],
        grounded=bool(r[7]), confidence=r[8],
        fallback_reason=r[9], created_at=r[10],
        citations=citations,
    )


# ---------- record ----------

def record_answer(
    *,
    surface: str,
    question_text: str,
    answer_text: str,
    citations: list[dict] | None = None,
    user_id: str | None = None,
    ai_call_id: str | None = None,
    answer_mode: str = "general",
    confidence: float | None = None,
    fallback_reason: str | None = None,
) -> AnswerProvenance:
    """Record an AI answer + its provenance atomically.

    `citations` is a list of dicts with keys:
      source_kind, source_id, page_number?, section?,
      citation_text, relevance?

    Caller wrappers (tutor.send_message, lesson.generate, etc) call
    this AFTER they've retrieved + chosen which chunks to ground
    on, before they hand the answer back to the user.
    """
    if surface not in VALID_SURFACES:
        raise ValueError(f"surface must be in {sorted(VALID_SURFACES)}")
    if answer_mode not in VALID_ANSWER_MODES:
        raise ValueError(
            f"answer_mode must be in {sorted(VALID_ANSWER_MODES)}",
        )
    if confidence is not None and (confidence < 0 or confidence > 1):
        raise ValueError("confidence must be in [0, 1]")
    question_text = (question_text or "").strip()
    if not question_text or len(question_text) > 8000:
        raise ValueError("question_text required (max 8000 chars)")
    answer_text = (answer_text or "").strip()
    if not answer_text or len(answer_text) > 32000:
        raise ValueError("answer_text required (max 32000 chars)")
    # Strict-mode guard: source_only / official refuse ungrounded answers
    cites = citations or []
    grounded = len(cites) > 0
    if not grounded and answer_mode in ("source_only", "official"):
        raise NotGroundedError(
            fallback_reason or "no_source_match",
        )
    pid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ai_answer_provenance "
            "(id, ai_call_id, user_id, surface, question_text, "
            " answer_text, answer_mode, grounded, confidence, "
            " fallback_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, ai_call_id, user_id, surface,
             question_text, answer_text, answer_mode,
             1 if grounded else 0, confidence,
             fallback_reason, now),
        )
        recorded: list[Citation] = []
        for i, c in enumerate(cites, start=1):
            cid = uuid.uuid4().hex
            sk = c.get("source_kind")
            if sk not in VALID_SOURCE_KINDS:
                raise ValueError(
                    f"citation[{i-1}].source_kind invalid: {sk!r}",
                )
            ctxt = (c.get("citation_text") or "").strip()
            if not ctxt or len(ctxt) > 2000:
                raise ValueError(
                    f"citation[{i-1}].citation_text required "
                    "(max 2000 chars)",
                )
            rel = c.get("relevance")
            if rel is not None and (rel < 0 or rel > 1):
                raise ValueError(
                    f"citation[{i-1}].relevance must be in [0, 1]",
                )
            conn.execute(
                "INSERT INTO ai_citations "
                "(id, provenance_id, source_kind, source_id, "
                " page_number, section, citation_text, "
                " relevance, position, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, pid, sk, c.get("source_id") or "",
                 c.get("page_number"), c.get("section"),
                 ctxt, rel, i, now),
            )
            recorded.append(Citation(
                id=cid, provenance_id=pid, source_kind=sk,
                source_id=c.get("source_id") or "",
                page_number=c.get("page_number"),
                section=c.get("section"),
                citation_text=ctxt, relevance=rel,
                position=i, created_at=now,
            ))
    return AnswerProvenance(
        id=pid, ai_call_id=ai_call_id, user_id=user_id,
        surface=surface, question_text=question_text,
        answer_text=answer_text, answer_mode=answer_mode,
        grounded=grounded, confidence=confidence,
        fallback_reason=fallback_reason,
        created_at=now, citations=recorded,
    )


class NotGroundedError(Exception):
    """Raised when strict-mode (source_only/official) answer has no
    citations. Caller surface should return NOT_FOUND_MESSAGE to
    the user."""
    def __init__(self, reason: str = "no_source_match"):
        super().__init__(reason)
        self.reason = reason


# ---------- read ----------

def get_provenance(provenance_id: str) -> AnswerProvenance | None:
    with _conn() as conn:
        r = conn.execute(
            _PROV_SEL + " WHERE id = ?", (provenance_id,),
        ).fetchone()
        if not r:
            return None
        cite_rows = conn.execute(
            "SELECT id, provenance_id, source_kind, source_id, "
            "       page_number, section, citation_text, "
            "       relevance, position, created_at "
            "FROM ai_citations WHERE provenance_id = ? "
            "ORDER BY position",
            (provenance_id,),
        ).fetchall()
    return _row_to_provenance(
        r, [_row_to_citation(c) for c in cite_rows],
    )


def list_user_answers(
    *,
    user_id: str,
    surface: str | None = None,
    grounded_only: bool = False,
    limit: int = 50,
) -> list[AnswerProvenance]:
    limit = max(1, min(limit, 500))
    sql = _PROV_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if surface:
        if surface not in VALID_SURFACES:
            raise ValueError(
                f"surface must be in {sorted(VALID_SURFACES)}",
            )
        sql += " AND surface = ?"; params.append(surface)
    if grounded_only:
        sql += " AND grounded = 1"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        # Bulk-fetch citations to avoid N+1
        if not rows:
            return []
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        cite_rows = conn.execute(
            "SELECT id, provenance_id, source_kind, source_id, "
            "       page_number, section, citation_text, "
            "       relevance, position, created_at "
            f"FROM ai_citations WHERE provenance_id IN ({placeholders}) "
            "ORDER BY position",
            ids,
        ).fetchall()
    by_prov: dict[str, list[Citation]] = {}
    for c in cite_rows:
        by_prov.setdefault(c[1], []).append(_row_to_citation(c))
    return [
        _row_to_provenance(r, by_prov.get(r[0], []))
        for r in rows
    ]


def list_citations_for_source(
    *,
    source_kind: str,
    source_id: str,
    limit: int = 50,
) -> list[Citation]:
    """How often has this source been cited? Drives the source-
    impact dashboard."""
    if source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(
            f"source_kind must be in {sorted(VALID_SOURCE_KINDS)}",
        )
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, provenance_id, source_kind, source_id, "
            "       page_number, section, citation_text, "
            "       relevance, position, created_at "
            "FROM ai_citations "
            "WHERE source_kind = ? AND source_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (source_kind, source_id, limit),
        ).fetchall()
    return [_row_to_citation(r) for r in rows]


def grounding_rate(
    *,
    surface: str | None = None,
    since: float | None = None,
) -> dict:
    """% of answers that were grounded in at least one source.
    Drives the trust dashboard. The headline metric for v3.1."""
    sql_where = "1=1"
    params: list = []
    if surface:
        if surface not in VALID_SURFACES:
            raise ValueError(
                f"surface must be in {sorted(VALID_SURFACES)}",
            )
        sql_where += " AND surface = ?"; params.append(surface)
    if since:
        sql_where += " AND created_at >= ?"; params.append(since)
    with _conn() as conn:
        r = conn.execute(
            f"SELECT COUNT(*), "
            "       COALESCE(SUM(grounded), 0) "
            f"FROM ai_answer_provenance WHERE {sql_where}",
            params,
        ).fetchone()
        by_surface = conn.execute(
            "SELECT surface, COUNT(*), COALESCE(SUM(grounded), 0) "
            f"FROM ai_answer_provenance WHERE {sql_where} "
            "GROUP BY surface",
            params,
        ).fetchall()
    total = int(r[0] or 0)
    grounded = int(r[1] or 0)
    return {
        "total_answers": total,
        "grounded_answers": grounded,
        "grounding_rate": (
            round(grounded / total, 4) if total else 0.0
        ),
        "by_surface": [
            {"surface": s[0], "total": s[1],
             "grounded": int(s[2] or 0),
             "rate": round((s[2] or 0) / s[1], 4) if s[1] else 0.0}
            for s in by_surface
        ],
    }
