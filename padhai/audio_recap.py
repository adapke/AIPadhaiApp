"""v3.14 — Audio recap (NotebookLM-style audio summaries).

Per gap-review §7 (StudyFetch + NotebookLM competitor gap) —
"audio recap" is a polished, on-the-go listening format: a short
narrative summary of a chapter/topic that the student can play
during a commute or workout. Existing platform has TTS
(`padhai/tts.py`) and citations + retrieval — this module
composes them into a recap.

A recap is built from one of three sources:
  • upload      — caller passes an upload_id; we pull chunks via
                  v3.3 retrieval over a topic-hint query.
  • topic       — caller passes an exam_taxonomy topic_code +
                  exam_code; we pull from any published source
                  whose section matches.
  • free_text   — caller hands us a paragraph (e.g. a tutor reply)
                  to convert directly to audio.

The script is **structured**: intro + 2-5 body sections + outro.
Each section is rendered as a separate audio segment so the UI
can show a transcript timeline that highlights the current line.

Tables:
  audio_recaps         one row per recap (status: pending/ready/failed)
  audio_recap_segments per-section transcript + audio file ref

Provider hook: caller-side. We persist the structured script +
segment audio_path strings; the actual TTS render is done by an
async worker that consumes `pending` rows. For dev / tests, the
worker is the same module's `render_pending()` function — calls
the configured TTS provider (or sandbox no-op).

Three answer modes (mirrors v3.1 citations modes):
  • cited       — segments carry citation dicts from retrieval
  • source_only — refuse to render if no source chunks
  • general     — LLM-only fallback (caller supplies script)

This module ships:
  • Storage + worker shell for audio recaps
  • generate_script_from_query() — calls retrieval + builds the
    structured script with citations
  • render_pending() — worker entry; calls TTS provider per segment
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
CREATE TABLE IF NOT EXISTS audio_recaps (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    source_kind     TEXT NOT NULL,                   -- 'upload' / 'topic' / 'free_text'
    source_id       TEXT,                             -- upload_id / topic_code / NULL
    query           TEXT,                             -- retrieval query for upload/topic
    language        TEXT NOT NULL DEFAULT 'en',
    answer_mode     TEXT NOT NULL DEFAULT 'cited',
    -- 'cited' | 'source_only' | 'general'
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'rendering' | 'ready' | 'failed'
    duration_sec    REAL,                             -- sum of segment durations
    error_reason    TEXT,
    created_at      REAL NOT NULL,
    completed_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_ar_user
    ON audio_recaps(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ar_status
    ON audio_recaps(status, created_at);

CREATE TABLE IF NOT EXISTS audio_recap_segments (
    id              TEXT PRIMARY KEY,
    recap_id        TEXT NOT NULL,
    position        INTEGER NOT NULL,                -- 0 = intro, then body, last = outro
    role            TEXT NOT NULL,                   -- 'intro' / 'body' / 'outro'
    transcript      TEXT NOT NULL,
    citations_json  TEXT,                            -- optional list of citation dicts
    audio_path      TEXT,                            -- filled in when rendered
    duration_sec    REAL,
    created_at      REAL NOT NULL,
    UNIQUE (recap_id, position)
);
CREATE INDEX IF NOT EXISTS idx_ars_recap
    ON audio_recap_segments(recap_id, position);
"""


VALID_SOURCE_KINDS = frozenset({"upload", "topic", "free_text"})
VALID_ANSWER_MODES = frozenset({"cited", "source_only", "general"})
VALID_STATUSES = frozenset({"pending", "rendering", "ready", "failed"})
VALID_ROLES = frozenset({"intro", "body", "outro"})

# How many body segments we aim for in a recap. The script
# generator picks chunks via retrieval; if there are fewer hits,
# we use what we have. If there are more, we top-k 5.
DEFAULT_BODY_SEGMENTS = 5
MAX_BODY_SEGMENTS = 8

# Rough TTS rate — used to estimate duration when the provider
# doesn't report it. 150 words/minute spoken.
WORDS_PER_MINUTE = 150

# Sandbox provider: when PADHAI_AUDIO_PROVIDER='sandbox' (default in
# tests), render_pending() does NOT call TTS — it just marks
# segments with a fake audio_path so the lifecycle is exercisable.
DEFAULT_PROVIDER = os.environ.get(
    "PADHAI_AUDIO_PROVIDER", "sandbox",
)

# Storage root for rendered audio files. The TTS provider writes
# under here. In sandbox mode we just generate the path string.
def _audio_dir() -> Path:
    custom = os.environ.get("PADHAI_AUDIO_DIR")
    if custom:
        return Path(custom).expanduser()
    output_root = os.environ.get("PADHAI_OUTPUT_DIR")
    if output_root:
        return Path(output_root).expanduser() / "audio_recaps"
    return Path.home() / ".padhai" / "audio_recaps"


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
class Segment:
    id: str
    recap_id: str
    position: int
    role: str
    transcript: str
    citations: list[dict]
    audio_path: str | None
    duration_sec: float | None
    created_at: float


@dataclass(frozen=True)
class Recap:
    id: str
    user_id: str
    title: str
    source_kind: str
    source_id: str | None
    query: str | None
    language: str
    answer_mode: str
    status: str
    duration_sec: float | None
    error_reason: str | None
    created_at: float
    completed_at: float | None
    segments: list[Segment]


# ---------- helpers ----------

def _est_duration(text: str) -> float:
    """Estimate spoken duration from word count."""
    words = len((text or "").split())
    if words == 0:
        return 0.0
    return round(words / WORDS_PER_MINUTE * 60, 1)


def _intro_line(title: str, language: str = "en") -> str:
    """Short opener. Language-aware in future; English baseline."""
    return (
        f"This is a quick audio recap of {title}. "
        "Let's walk through the key points."
    )


def _outro_line(language: str = "en") -> str:
    return (
        "That's the recap. Open the chapter to dive deeper, "
        "or take a few practice questions to test what you've heard."
    )


# ---------- recap lifecycle ----------

def create_recap(
    *,
    user_id: str,
    title: str,
    source_kind: str,
    source_id: str | None = None,
    query: str | None = None,
    language: str = "en",
    answer_mode: str = "cited",
) -> Recap:
    """Create a `pending` recap row. Caller follows up with
    `set_segments()` (or `generate_script_from_query()` if they
    want the retrieval path) before triggering `render_pending`."""
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 200:
        raise ValueError("title must be [3, 200] chars")
    if source_kind not in VALID_SOURCE_KINDS:
        raise ValueError(
            f"source_kind must be in {sorted(VALID_SOURCE_KINDS)}",
        )
    if answer_mode not in VALID_ANSWER_MODES:
        raise ValueError(
            f"answer_mode must be in {sorted(VALID_ANSWER_MODES)}",
        )
    if source_kind in ("upload", "topic") and not source_id:
        raise ValueError(
            f"source_id required when source_kind={source_kind!r}",
        )
    rid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO audio_recaps "
            "(id, user_id, title, source_kind, source_id, "
            " query, language, answer_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, user_id, title, source_kind, source_id,
             query, language, answer_mode, time.time()),
        )
    return get_recap(rid)  # type: ignore[return-value]


def set_segments(
    *,
    recap_id: str,
    user_id: str,
    body_segments: list[dict],
    custom_intro: str | None = None,
    custom_outro: str | None = None,
) -> Recap:
    """Replace all segments on the recap. body_segments is a list
    of {transcript, citations?}. We auto-prepend intro + append
    outro using the recap's title + language."""
    recap = get_recap(recap_id)
    if not recap:
        raise ValueError("recap not found")
    if recap.user_id != user_id:
        raise PermissionError("not your recap")
    if recap.status not in ("pending", "failed"):
        raise ValueError(
            f"recap status is {recap.status!r}, can't edit segments",
        )
    if not body_segments:
        raise ValueError("at least one body segment required")
    if len(body_segments) > MAX_BODY_SEGMENTS:
        raise ValueError(
            f"max {MAX_BODY_SEGMENTS} body segments",
        )
    # Strict source_only mode: every body segment must have citations
    if recap.answer_mode == "source_only":
        for i, s in enumerate(body_segments):
            cites = s.get("citations") or []
            if not cites:
                raise ValueError(
                    f"source_only mode requires citations "
                    f"on body[{i}]",
                )
    # Validate each segment shape
    for i, s in enumerate(body_segments):
        if not isinstance(s, dict):
            raise ValueError(f"body[{i}] must be dict")
        t = (s.get("transcript") or "").strip()
        if len(t) < 10 or len(t) > 4000:
            raise ValueError(
                f"body[{i}].transcript must be [10, 4000] chars",
            )
    intro = (custom_intro or _intro_line(recap.title, recap.language)).strip()
    outro = (custom_outro or _outro_line(recap.language)).strip()
    if len(intro) > 4000 or len(outro) > 4000:
        raise ValueError("intro/outro must be ≤4000 chars")
    now = time.time()
    with _conn() as conn:
        # Clear existing segments
        conn.execute(
            "DELETE FROM audio_recap_segments WHERE recap_id = ?",
            (recap_id,),
        )
        # Intro
        conn.execute(
            "INSERT INTO audio_recap_segments "
            "(id, recap_id, position, role, transcript, "
            " citations_json, created_at) "
            "VALUES (?, ?, 0, 'intro', ?, NULL, ?)",
            (uuid.uuid4().hex, recap_id, intro, now),
        )
        # Body
        for i, s in enumerate(body_segments, start=1):
            cites = s.get("citations") or []
            conn.execute(
                "INSERT INTO audio_recap_segments "
                "(id, recap_id, position, role, transcript, "
                " citations_json, created_at) "
                "VALUES (?, ?, ?, 'body', ?, ?, ?)",
                (uuid.uuid4().hex, recap_id, i,
                 s["transcript"].strip(),
                 json.dumps(cites) if cites else None, now),
            )
        # Outro
        conn.execute(
            "INSERT INTO audio_recap_segments "
            "(id, recap_id, position, role, transcript, "
            " citations_json, created_at) "
            "VALUES (?, ?, ?, 'outro', ?, NULL, ?)",
            (uuid.uuid4().hex, recap_id,
             len(body_segments) + 1, outro, now),
        )
    return get_recap(recap_id)  # type: ignore[return-value]


def generate_script_from_query(
    *,
    recap_id: str,
    user_id: str,
    query: str | None = None,
    top_k: int = DEFAULT_BODY_SEGMENTS,
) -> Recap:
    """Retrieval-driven script builder. Looks up the top-k chunks
    matching `query` (or the recap's stored query) in the recap's
    upload_id scope and turns each into a body segment with
    citations attached.

    For source_only mode: raises ValueError if retrieval returns
    zero hits (no fallback to LLM)."""
    recap = get_recap(recap_id)
    if not recap:
        raise ValueError("recap not found")
    if recap.user_id != user_id:
        raise PermissionError("not your recap")
    if recap.source_kind not in ("upload", "topic"):
        raise ValueError(
            "generate_script_from_query needs source_kind in "
            "(upload, topic)",
        )
    query = (query or recap.query or "").strip()
    if not query:
        raise ValueError("query required (or set on recap)")
    top_k = max(1, min(top_k, MAX_BODY_SEGMENTS))
    try:
        from . import retrieval as rt
    except ImportError:
        raise RuntimeError("retrieval module unavailable")
    upload_scope = (
        [recap.source_id] if recap.source_kind == "upload"
        else None
    )
    hits = rt.retrieve(
        query=query, upload_ids=upload_scope, top_k=top_k,
    )
    if not hits:
        if recap.answer_mode == "source_only":
            raise ValueError(
                "source_only mode + zero retrieval hits — "
                "upload more material",
            )
        # General/cited modes can fall back to caller-supplied
        # transcript; raise to signal "no script generated".
        raise ValueError(
            "no retrieval hits; supply body_segments manually",
        )
    body_segments = []
    for h in hits:
        # Light cleanup — collapse whitespace
        text = re.sub(r"\s+", " ", h.chunk.chunk_text or "").strip()
        body_segments.append({
            "transcript": text[:4000],
            "citations": [{
                "source_kind": "upload",
                "source_id": h.chunk.upload_id,
                "page_number": h.chunk.page_number,
                "section": h.chunk.section,
                "citation_text": h.chunk.chunk_text[:2000],
                "relevance": h.score,
            }],
        })
    return set_segments(
        recap_id=recap_id, user_id=user_id,
        body_segments=body_segments,
    )


def get_recap(recap_id: str) -> Recap | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, title, source_kind, source_id, "
            "       query, language, answer_mode, status, "
            "       duration_sec, error_reason, created_at, "
            "       completed_at "
            "FROM audio_recaps WHERE id = ?", (recap_id,),
        ).fetchone()
        if not r:
            return None
        seg_rows = conn.execute(
            "SELECT id, recap_id, position, role, transcript, "
            "       citations_json, audio_path, duration_sec, "
            "       created_at "
            "FROM audio_recap_segments WHERE recap_id = ? "
            "ORDER BY position",
            (recap_id,),
        ).fetchall()
    segments = [
        Segment(
            id=s[0], recap_id=s[1], position=s[2],
            role=s[3], transcript=s[4],
            citations=json.loads(s[5]) if s[5] else [],
            audio_path=s[6], duration_sec=s[7],
            created_at=s[8],
        )
        for s in seg_rows
    ]
    return Recap(
        id=r[0], user_id=r[1], title=r[2],
        source_kind=r[3], source_id=r[4],
        query=r[5], language=r[6], answer_mode=r[7],
        status=r[8], duration_sec=r[9],
        error_reason=r[10], created_at=r[11],
        completed_at=r[12], segments=segments,
    )


def list_user_recaps(
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[Recap]:
    limit = max(1, min(limit, 200))
    sql = (
        "SELECT id FROM audio_recaps WHERE user_id = ?"
    )
    params: list = [user_id]
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_STATUSES)}",
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        ids = [r[0] for r in conn.execute(sql, params).fetchall()]
    return [r for r in (get_recap(i) for i in ids) if r]


def cancel_recap(*, recap_id: str, user_id: str) -> bool:
    """Cancel before rendering. Once `ready`, the recap is
    fine-as-is; caller can `delete_recap` instead."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE audio_recaps SET "
            " status = 'failed', error_reason = 'cancelled' "
            "WHERE id = ? AND user_id = ? "
            "AND status IN ('pending', 'rendering')",
            (recap_id, user_id),
        )
        return cur.rowcount > 0


def delete_recap(*, recap_id: str, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM audio_recaps "
            "WHERE id = ? AND user_id = ?",
            (recap_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "DELETE FROM audio_recap_segments "
                "WHERE recap_id = ?", (recap_id,),
            )
        return cur.rowcount > 0


# ---------- worker ----------

def render_pending(*, batch_size: int = 10) -> dict:
    """Worker entry — picks pending recaps + renders each segment
    to audio. Returns counts (rendered / failed)."""
    batch_size = max(1, min(batch_size, 100))
    now = time.time()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM audio_recaps "
            "WHERE status = 'pending' "
            "ORDER BY created_at LIMIT ?",
            (batch_size,),
        ).fetchall()
    out = {"rendered": 0, "failed": 0}
    for (rid,) in rows:
        try:
            _render_recap(rid)
            out["rendered"] += 1
        except Exception as e:  # noqa: BLE001
            out["failed"] += 1
            with _conn() as conn:
                conn.execute(
                    "UPDATE audio_recaps SET "
                    " status = 'failed', "
                    " error_reason = ?, completed_at = ? "
                    "WHERE id = ?",
                    (str(e)[:1000], time.time(), rid),
                )
    return out


def _render_recap(recap_id: str) -> None:
    """Render every segment in this recap. Marks recap 'rendering'
    while in progress; 'ready' on success."""
    recap = get_recap(recap_id)
    if not recap:
        raise ValueError("recap missing")
    if not recap.segments:
        raise ValueError("recap has no segments — set them first")
    with _conn() as conn:
        conn.execute(
            "UPDATE audio_recaps SET status = 'rendering' "
            "WHERE id = ?", (recap_id,),
        )
    total_duration = 0.0
    for seg in recap.segments:
        audio_path, duration = _render_segment(
            seg, language=recap.language,
        )
        with _conn() as conn:
            conn.execute(
                "UPDATE audio_recap_segments SET "
                " audio_path = ?, duration_sec = ? "
                "WHERE id = ?",
                (audio_path, duration, seg.id),
            )
        total_duration += duration
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE audio_recaps SET "
            " status = 'ready', duration_sec = ?, "
            " completed_at = ? "
            "WHERE id = ?",
            (round(total_duration, 1), now, recap_id),
        )


def _render_segment(
    segment: Segment, *, language: str,
) -> tuple[str, float]:
    """Call the TTS provider (or sandbox no-op). Returns
    (audio_path, duration_sec)."""
    provider = DEFAULT_PROVIDER
    out_dir = _audio_dir() / segment.recap_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{segment.position:02d}_{segment.role}.mp3"
    if provider == "sandbox":
        # No real TTS — just record the path as if rendered
        return str(out_path), _est_duration(segment.transcript)
    try:
        from . import tts
        prov = tts.get_provider()
        prov.synthesise(
            text=segment.transcript,
            language_code=language,
            out_path=out_path,
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"TTS provider {provider!r} failed: {e}",
        )
    return str(out_path), _est_duration(segment.transcript)


def user_stats(user_id: str) -> dict:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*), "
            "       COALESCE(SUM(duration_sec), 0) "
            "FROM audio_recaps WHERE user_id = ? "
            "GROUP BY status",
            (user_id,),
        ).fetchall()
    total = sum(r[1] for r in rows)
    by_status = {r[0]: r[1] for r in rows}
    total_seconds = sum(r[2] for r in rows if r[0] == "ready")
    return {
        "total_recaps": total,
        "by_status": by_status,
        "total_audio_seconds": round(total_seconds, 1),
    }
