"""L1 — Real-time AI voice tutor (always-on).

Persistent conversational tutor available 24/7 via voice + text.
Maintains context across sessions (remembers what the student is
working on, what they got wrong yesterday, what their exam is in
3 weeks).

Backed by Claude. Falls back cleanly to a "tutor not configured"
canned response when ANTHROPIC_API_KEY isn't set — the schema +
endpoint shapes stay the same so the frontend / mobile dev path
works without keys.

Two tables:
  tutor_sessions       — conversation history, token + cost tracking
  tutor_long_memory    — durable per-user (key, value, confidence)
                         compiled from session history every 24h

Every Claude call goes through llm_obs.record_call() so the cost
appears on the L6 admin dashboard, and is gated by the Q1 flag
`tutor.enabled` so we can roll it out gradually.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    started_at      REAL NOT NULL,
    ended_at        REAL,
    context_summary TEXT,                    -- LLM-condensed memory carried in from prior sessions
    messages_json   TEXT NOT NULL,           -- [{role, content, ts}, ...]
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_inr_paise  INTEGER NOT NULL DEFAULT 0,
    topic_keys      TEXT,                    -- JSON array of topics discussed
    resolved        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tutor_user_time
    ON tutor_sessions(user_id, started_at DESC);

CREATE TABLE IF NOT EXISTS tutor_long_memory (
    user_id      TEXT NOT NULL,
    memory_key   TEXT NOT NULL,              -- 'preferred_style', 'weak_topic', 'exam_date'
    value_json   TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    last_seen    REAL NOT NULL,
    PRIMARY KEY (user_id, memory_key)
);
"""


# Daily cost cap per tier (paise). Once a user exceeds their cap,
# new messages return a polite "you've used your daily AI tutor
# budget" message. Resets at UTC midnight.
DAILY_COST_CAP_PAISE = {
    "M1": 0,          # free tier: no tutor
    "M2": 2000,       # ₹20/day
    "M3": 10000,      # ₹100/day (premium)
    "M4": 0,          # enterprise: uncapped (0 sentinel = no cap)
}

# Max messages we keep verbatim in the prompt context. Older messages
# get condensed into context_summary on each turn.
MAX_VERBATIM_MESSAGES = 12


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


def is_available() -> bool:
    """Returns True when Claude is configured. False → endpoints
    return canned "not configured" replies instead of 503 — so the
    frontend dev path works."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    started_at: float
    ended_at: float | None
    context_summary: str | None
    messages: list[dict]
    tokens_in: int
    tokens_out: int
    cost_inr_paise: int
    topic_keys: list[str]
    resolved: bool


def _row_to_session(r) -> Session:
    return Session(
        id=r[0], user_id=r[1], started_at=r[2], ended_at=r[3],
        context_summary=r[4],
        messages=json.loads(r[5]) if r[5] else [],
        tokens_in=r[6] or 0, tokens_out=r[7] or 0,
        cost_inr_paise=r[8] or 0,
        topic_keys=json.loads(r[9]) if r[9] else [],
        resolved=bool(r[10]),
    )


_SEL = (
    "id, user_id, started_at, ended_at, context_summary, "
    "messages_json, tokens_in, tokens_out, cost_inr_paise, "
    "topic_keys, resolved"
)


def start_session(*, user_id: str) -> Session:
    """Create a new tutor session, hydrating context_summary from
    the user's long memory + their last 2 ended sessions."""
    context = _build_initial_context(user_id)
    sid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tutor_sessions "
            "(id, user_id, started_at, context_summary, messages_json) "
            "VALUES (?,?,?,?,?)",
            (sid, user_id, now, context, json.dumps([])),
        )
    return get_session(sid)  # type: ignore[return-value]


def get_session(sid: str) -> Session | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM tutor_sessions WHERE id = ?", (sid,),
        ).fetchone()
    return _row_to_session(r) if r else None


def list_sessions_for_user(user_id: str, *, limit: int = 50) -> list[Session]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM tutor_sessions WHERE user_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def end_session(*, sid: str, resolved: bool = True) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE tutor_sessions SET ended_at = ?, resolved = ? "
            "WHERE id = ? AND ended_at IS NULL",
            (time.time(), 1 if resolved else 0, sid),
        )
        return cur.rowcount > 0


# ---------- send_message: the hot path ----------

@dataclass(frozen=True)
class TutorReply:
    session_id: str
    reply: str
    tokens_in: int
    tokens_out: int
    cost_inr_paise: int
    cached: bool
    rate_limited: bool = False
    over_budget: bool = False
    # v3.x — source-grounded RAG. When the caller passes `upload_ids` to
    # send_message, the tutor retrieves chunks before generating the
    # reply and returns them here so the UI can render citations.
    grounded: bool = False
    citations: tuple = ()      # tuple of dicts so the frozen dataclass stays hashable


def send_message(
    *,
    sid: str,
    user_text: str,
    user_tier: str = "M2",
    upload_ids: list[str] | None = None,
    auto_ground: bool = False,
) -> TutorReply:
    """Append a user message + get an assistant reply. Side effects:
    - Updates tutor_sessions.messages_json + token/cost rollups
    - Records into llm_obs.llm_calls for L6 dashboard
    - Returns canned "not configured" if ANTHROPIC_API_KEY unset
    - Returns canned "over budget" if user exceeded daily tier cap

    The cap check happens BEFORE the Claude call so we don't burn
    money on a user we'd reject anyway.

    Source grounding (v3.x):
      - When `upload_ids` is provided, retrieve top-k chunks from those
        uploads and inject as cached context for Claude. Citations are
        returned on the TutorReply.
      - When `auto_ground=True` and no upload_ids passed, the tutor
        auto-pulls from the user's 3 most recent indexed uploads. Useful
        when the SPA wants "study my uploaded notes" without explicit
        selection.
    """
    if not user_text or not user_text.strip():
        raise ValueError("user_text required")
    user_text = user_text[:4000]  # hard cap to defend against runaway loops
    session = get_session(sid)
    if not session:
        raise ValueError(f"session {sid!r} not found")
    if session.ended_at is not None:
        raise ValueError("session already ended")

    # Tier-cap check — centralised in llm_obs so every AI surface
    # (tutor, lesson, essay, doubt, mock) draws from the same daily
    # budget. The local DAILY_COST_CAP_PAISE dict below is kept only
    # for backward-compat reads; the actual enforcement comes from
    # llm_obs.check_daily_cap().
    from . import llm_obs
    try:
        llm_obs.check_daily_cap(
            user_id=session.user_id, subscription_tier=user_tier,
        )
    except llm_obs.BudgetExceeded as e:
        if e.reason == "premium_feature":
            return _record_canned_reply(
                session,
                "AI tutor is a premium feature. Upgrade to "
                "Student Basic or Pro to chat.",
                over_budget=True,
            )
        return _record_canned_reply(
            session,
            "You've used your daily AI tutor budget for today. "
            "Try again tomorrow — or upgrade your plan.",
            over_budget=True,
        )

    if not is_available():
        return _record_canned_reply(
            session,
            "(AI tutor not configured on this deployment — "
            "ANTHROPIC_API_KEY missing.)",
        )

    # Resolve uploads for RAG
    resolved_upload_ids = list(upload_ids) if upload_ids else []
    if not resolved_upload_ids and auto_ground:
        resolved_upload_ids = _recent_indexed_uploads_for_user(session.user_id)

    retrieved_hits = []
    if resolved_upload_ids:
        retrieved_hits = _retrieve_chunks(
            query=user_text, upload_ids=resolved_upload_ids,
        )

    return _claude_reply(session, user_text, retrieved_hits=retrieved_hits)


def _recent_indexed_uploads_for_user(user_id: str, *, limit: int = 3) -> list[str]:
    """Return up to `limit` upload ids that belong to the user AND have
    retrieval chunks indexed. Used by auto_ground=True so the tutor
    can RAG against the student's latest study material without
    explicit ID selection."""
    try:
        from . import uploads as _up, retrieval as _retr
        ups = _up.list_for_user(user_id, limit=20)
        out: list[str] = []
        for u in ups:
            if _retr.chunk_count(upload_id=u.id) > 0:
                out.append(u.id)
                if len(out) >= limit:
                    break
        return out
    except Exception:
        return []


def _retrieve_chunks(
    *, query: str, upload_ids: list[str], top_k: int = 5,
) -> list:
    """TF-IDF retrieve from the user's indexed uploads. Returns
    retrieval.RetrievalHit list (or [] if retrieval module missing /
    no hits)."""
    try:
        from . import retrieval as _retr
        return _retr.retrieve(
            query=query, upload_ids=upload_ids,
            top_k=top_k, min_score=0.05,
        )
    except Exception:
        return []


def _claude_reply(
    session: Session,
    user_text: str,
    *,
    retrieved_hits: list | None = None,
) -> TutorReply:
    """Real Claude call path. Lazy imports the SDK so this module
    loads without anthropic installed.

    `retrieved_hits` (optional) — list of retrieval.RetrievalHit. When
    present, each chunk is injected into the system prompt as cached
    context, and the citations are returned on the TutorReply so the
    UI can render them inline."""
    started = time.time()
    model = os.environ.get("PADHAI_TUTOR_MODEL", "claude-haiku-4-5")
    system_prompt = _build_system_prompt(session)

    # Source grounding — inject retrieved chunks into the system prompt
    # (cacheable: same chunks for the same session won't re-tokenise).
    citations_tuple: tuple = ()
    if retrieved_hits:
        chunks_block, citations_tuple = _format_chunks_for_prompt(retrieved_hits)
        system_prompt = (
            system_prompt
            + "\n\n--- STUDENT'S UPLOADED STUDY MATERIAL (cite when used) ---\n"
            + chunks_block
            + "\n--- END OF SOURCE MATERIAL ---\n\n"
            + "When you use facts from the source material, append a "
            + "citation like [page N, section X]. If the source doesn't "
            + "cover the question, say so honestly — don't fabricate."
        )

    history = list(session.messages or [])
    history.append({"role": "user", "content": user_text, "ts": started})

    # Trim verbatim history; older messages folded into context_summary
    verbatim = history[-MAX_VERBATIM_MESSAGES:]
    api_messages = [
        {"role": m["role"], "content": m["content"]} for m in verbatim
    ]

    # One helper handles: anthropic SDK import, messages.create call,
    # text extraction, token + cost accounting, llm_obs.record_call.
    # Cap pre-flight runs upstream in send_message() — disable here so
    # we don't double-check.
    from . import llm_call
    try:
        call = llm_call.call_claude(
            module="tutor",
            prompt_version="v1-grounded" if retrieved_hits else "v1",
            model=model,
            user_id=session.user_id,
            enforce_cap=False,
            max_tokens=600,
            system=system_prompt,
            messages=api_messages,
        )
    except RuntimeError as e:
        # Covers: no key, missing SDK, Claude transport error.
        # Surface the right canned reply per cause; the wrapper's
        # error text starts with a stable prefix we can match.
        msg = str(e)
        if "ANTHROPIC_API_KEY" in msg or "not configured" in msg:
            return _record_canned_reply(
                session,
                "(AI tutor not configured on this deployment — "
                "ANTHROPIC_API_KEY missing.)",
            )
        if "SDK not installed" in msg or "anthropic" in msg.lower():
            return _record_canned_reply(
                session,
                "(AI tutor not available — anthropic SDK not installed.)",
            )
        print(f"[tutor] Claude call failed: {e}")
        return _record_canned_reply(
            session,
            "(AI tutor hit a temporary error. Try again in a minute.)",
        )

    reply_text = call.text
    tokens_in = call.tokens_in
    tokens_out = call.tokens_out
    cached = call.cached
    cost_paise = call.cost_inr_paise

    # Record provenance via tutor_grounding so /admin/grounding audits work.
    # Best-effort — failure to record is non-fatal.
    if retrieved_hits:
        try:
            from . import retrieval as _retr, tutor_grounding as _tg
            _tg.send_grounded_message(
                session_id=session.id,
                user_id=session.user_id,
                question_text=user_text,
                answer_text=reply_text,
                retrieved_chunks=_retr.hits_to_citations(retrieved_hits),
                confidence=retrieved_hits[0].score if retrieved_hits else None,
                surface="tutor",
            )
        except Exception as _e:  # noqa: BLE001
            print(f"[tutor] grounding record non-fatal: {_e}")

    # Persist the turn
    history.append({"role": "assistant", "content": reply_text, "ts": time.time()})
    _save_turn(
        sid=session.id,
        messages=history,
        delta_tokens_in=tokens_in,
        delta_tokens_out=tokens_out,
        delta_cost_paise=cost_paise,
    )

    return TutorReply(
        session_id=session.id, reply=reply_text,
        tokens_in=tokens_in, tokens_out=tokens_out,
        cost_inr_paise=cost_paise, cached=cached,
        grounded=bool(retrieved_hits),
        citations=citations_tuple,
    )


def _format_chunks_for_prompt(hits) -> tuple[str, tuple]:
    """Return (system-prompt-block, citations-tuple-for-TutorReply).

    Citations tuple shape (each dict):
      {page_number, section, preview, score, upload_id}
    """
    parts: list[str] = []
    citations: list[dict] = []
    for i, h in enumerate(hits):
        chunk = h.chunk
        label = f"[#{i+1}"
        if chunk.page_number:
            label += f" · page {chunk.page_number}"
        if chunk.section:
            label += f" · {chunk.section}"
        label += "]"
        parts.append(f"{label}\n{chunk.chunk_text[:1800]}")
        citations.append({
            "index": i + 1,
            "page_number": chunk.page_number,
            "section": chunk.section,
            "preview": chunk.chunk_text[:300],
            "score": h.score,
            "upload_id": chunk.upload_id,
        })
    return "\n\n".join(parts), tuple(citations)


def _record_canned_reply(
    session: Session,
    text: str,
    *,
    over_budget: bool = False,
) -> TutorReply:
    history = list(session.messages or [])
    history.append({"role": "assistant", "content": text, "ts": time.time(),
                    "canned": True})
    _save_turn(
        sid=session.id, messages=history,
        delta_tokens_in=0, delta_tokens_out=0, delta_cost_paise=0,
    )
    return TutorReply(
        session_id=session.id, reply=text,
        tokens_in=0, tokens_out=0, cost_inr_paise=0, cached=False,
        over_budget=over_budget,
    )


def _save_turn(
    *, sid: str, messages: list[dict],
    delta_tokens_in: int, delta_tokens_out: int, delta_cost_paise: int,
) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE tutor_sessions SET messages_json = ?, "
            " tokens_in = tokens_in + ?, "
            " tokens_out = tokens_out + ?, "
            " cost_inr_paise = cost_inr_paise + ? "
            "WHERE id = ?",
            (json.dumps(messages),
             delta_tokens_in, delta_tokens_out, delta_cost_paise, sid),
        )


# ---------- long memory ----------

def remember(
    *,
    user_id: str,
    memory_key: str,
    value,
    confidence: float = 1.0,
) -> None:
    """Upsert a durable memory entry. Caller is responsible for the
    `value` shape; we store as JSON."""
    with _conn() as conn:
        conn.execute(
            "INSERT INTO tutor_long_memory "
            "(user_id, memory_key, value_json, confidence, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, memory_key) DO UPDATE SET "
            " value_json = excluded.value_json, "
            " confidence = excluded.confidence, "
            " last_seen = excluded.last_seen",
            (user_id, memory_key, json.dumps(value), float(confidence),
             time.time()),
        )


def recall(*, user_id: str, memory_key: str | None = None) -> dict | list:
    """Read memory. With memory_key=None returns all entries as a
    dict; with a key returns the single value (or None)."""
    with _conn() as conn:
        if memory_key:
            r = conn.execute(
                "SELECT value_json FROM tutor_long_memory "
                "WHERE user_id = ? AND memory_key = ?",
                (user_id, memory_key),
            ).fetchone()
            return json.loads(r[0]) if r else None
        rows = conn.execute(
            "SELECT memory_key, value_json, confidence, last_seen "
            "FROM tutor_long_memory WHERE user_id = ? "
            "ORDER BY last_seen DESC", (user_id,),
        ).fetchall()
    return {
        r[0]: {
            "value": json.loads(r[1]),
            "confidence": r[2],
            "last_seen": r[3],
        }
        for r in rows
    }


# ---------- helpers ----------

def _build_initial_context(user_id: str) -> str:
    """Compile a brief context string from the user's long memory +
    the 2 most-recent completed sessions. Lives inside `system`
    when we call Claude."""
    mem = recall(user_id=user_id)
    bits = []
    if isinstance(mem, dict):
        for key, entry in list(mem.items())[:10]:
            bits.append(f"- {key}: {entry['value']!r}")
    recent = list_sessions_for_user(user_id, limit=2)
    for s in recent:
        if s.context_summary:
            bits.append(f"- prior: {s.context_summary[:180]}")
    return "\n".join(bits) if bits else ""


_SYSTEM_PROMPT_TEMPLATE = """You are AI Pathshala's always-on tutor.
You teach K-12 + coaching prep (UPSC, JEE, NEET) in 10 Indian languages.

Style:
- Patient + encouraging. Break problems into small steps.
- Ask one question at a time. Wait for the student's answer before
  moving on.
- Cite a source (NCERT page, Scene #, or "official syllabus") for
  factual claims. If unsure, say "I don't know — let's look it up
  together" rather than guess.
- Refuse to write essays / quiz answers FOR the student. Coach them
  to write it themselves.

You have the following context about THIS student:
{context}
"""


def _build_system_prompt(session: Session) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        context=session.context_summary or "(no prior context)",
    )
