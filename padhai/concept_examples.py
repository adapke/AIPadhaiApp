"""prod-137 — Real-World Examples catalog (CK-12 concept-page pattern).

CK-12 concept pages have a "Real-World Application" tab — a short
paragraph + image showing where the concept appears in daily life
(Newton's first law → passengers lurching forward when a car brakes).
These are human-curated and CC-licensed.

Pathshala adapts: use Claude Sonnet to **generate** 3 India-rooted
examples per concept on demand, then route them through a curator
approval queue before publishing. Approved examples land in the
public `/concept/{slug}` SEO page (prod-134) as a "Real-World
Examples" section.

Schema:
  concept_examples       one row per generated example
    id                   UUID
    concept_slug         normalised concept name (joins to concept_videos.concept_norm)
    example_md           Markdown body (50-400 words)
    locale               'en' / 'hi' / 'ta' / ... (default 'en')
    status               'pending' / 'approved' / 'rejected'
    created_at           epoch sec
    reviewed_at          epoch sec (null if pending)
    reviewed_by          user_id of approver (null if pending)
    review_note          optional reason for rejection / approval comment
    source               'claude' (generator) / 'human' (manual entry)
    generator_call_id    llm_obs.llm_calls.id for cost tracking

Workflow:
  1. Teacher hits `POST /api/admin/teacher-tools/generate-examples`
     with {concept_slug, locale, count=3}. Claude returns 3
     India-rooted examples → inserted as `pending`.
  2. Curator (admin) reviews via `GET /api/admin/teacher-tools/examples-queue`,
     approves/rejects per-row.
  3. Public `GET /api/concept-examples?slug=...` returns only `approved`
     rows. `/concept/{slug}` SEO page renders them inline.

The example_md is plain Markdown so we don't need a separate field
for image URLs — embed images inline via `![alt](url)` if needed.
This sidesteps prod-167 (concept_videos uploaded-images) needing
a separate table.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS concept_examples (
    id              TEXT PRIMARY KEY,
    concept_slug    TEXT NOT NULL,
    example_md      TEXT NOT NULL,
    locale          TEXT NOT NULL DEFAULT 'en',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      REAL NOT NULL,
    reviewed_at     REAL,
    reviewed_by     TEXT,
    review_note     TEXT,
    source          TEXT NOT NULL DEFAULT 'claude',
    generator_call_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_concept_examples_slug
    ON concept_examples(concept_slug, status);
CREATE INDEX IF NOT EXISTS idx_concept_examples_status
    ON concept_examples(status, created_at);
"""

VALID_STATUSES = {"pending", "approved", "rejected"}
VALID_SOURCES = {"claude", "human"}


@dataclass(frozen=True)
class ConceptExample:
    id: str
    concept_slug: str
    example_md: str
    locale: str
    status: str
    created_at: float
    reviewed_at: float | None
    reviewed_by: str | None
    review_note: str | None
    source: str
    generator_call_id: str | None


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalise_slug(s: str) -> str:
    """Same shape as concept_videos._normalise_concept so the slugs
    join cleanly."""
    if not s:
        return ""
    s = _PUNCT_RE.sub(" ", s.lower())
    s = _WS_RE.sub(" ", s).strip()
    return s


def _conn() -> sqlite3.Connection:
    path = db.sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


def _row_to_example(row: tuple) -> ConceptExample:
    return ConceptExample(
        id=row[0],
        concept_slug=row[1],
        example_md=row[2],
        locale=row[3],
        status=row[4],
        created_at=row[5],
        reviewed_at=row[6],
        reviewed_by=row[7],
        review_note=row[8],
        source=row[9],
        generator_call_id=row[10],
    )


_SEL = (
    "id, concept_slug, example_md, locale, status, created_at, "
    "reviewed_at, reviewed_by, review_note, source, generator_call_id"
)


def insert(
    *,
    concept_slug: str,
    example_md: str,
    locale: str = "en",
    source: str = "claude",
    generator_call_id: str | None = None,
    status: str = "pending",
) -> ConceptExample:
    """Insert a new example. Always lands as `pending` by default —
    curator must explicitly approve."""
    if not concept_slug or not example_md:
        raise ValueError("concept_slug + example_md required")
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if source not in VALID_SOURCES:
        raise ValueError(f"invalid source: {source}")
    slug = _normalise_slug(concept_slug)
    example_id = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO concept_examples ({_SEL}) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                example_id, slug, example_md, locale, status, now,
                None, None, None, source, generator_call_id,
            ),
        )
    return ConceptExample(
        id=example_id,
        concept_slug=slug,
        example_md=example_md,
        locale=locale,
        status=status,
        created_at=now,
        reviewed_at=None,
        reviewed_by=None,
        review_note=None,
        source=source,
        generator_call_id=generator_call_id,
    )


def get(example_id: str) -> ConceptExample | None:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_SEL} FROM concept_examples WHERE id = ?",
            (example_id,),
        ).fetchone()
    return _row_to_example(row) if row else None


def list_for_slug(
    slug: str, *, locale: str = "en", status: str = "approved", limit: int = 10,
) -> list[ConceptExample]:
    """Read-side query for `/concept/{slug}` page — defaults to
    approved-only so we don't leak pending content publicly."""
    norm = _normalise_slug(slug)
    if not norm:
        return []
    if status not in VALID_STATUSES and status != "*":
        raise ValueError(f"invalid status: {status}")
    args: list = [norm, locale]
    q = (
        f"SELECT {_SEL} FROM concept_examples "
        "WHERE concept_slug = ? AND locale = ? "
    )
    if status != "*":
        q += "AND status = ? "
        args.append(status)
    q += "ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with _conn() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_row_to_example(r) for r in rows]


def list_pending_queue(*, limit: int = 50) -> list[ConceptExample]:
    """Curator inbox — pending examples in creation order."""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM concept_examples "
            "WHERE status = 'pending' "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_example(r) for r in rows]


def review(
    *,
    example_id: str,
    reviewer_user_id: str,
    new_status: str,
    note: str | None = None,
) -> ConceptExample | None:
    """Curator action — approve or reject. Idempotent: re-reviewing
    just updates timestamps."""
    if new_status not in {"approved", "rejected"}:
        raise ValueError("new_status must be 'approved' or 'rejected'")
    now = time.time()
    with _conn() as conn:
        cursor = conn.execute(
            "UPDATE concept_examples "
            "SET status = ?, reviewed_at = ?, reviewed_by = ?, review_note = ? "
            "WHERE id = ?",
            (new_status, now, reviewer_user_id, note, example_id),
        )
        if cursor.rowcount == 0:
            return None
    return get(example_id)


def stats() -> dict:
    """Roll-up for `/admin/curator-stats` page."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM concept_examples GROUP BY status"
        ).fetchall()
    out = {"pending": 0, "approved": 0, "rejected": 0, "total": 0}
    for status, n in rows:
        out[status] = n
        out["total"] += n
    return out


def to_dict(ex: ConceptExample) -> dict:
    return {
        "id": ex.id,
        "concept_slug": ex.concept_slug,
        "example_md": ex.example_md,
        "locale": ex.locale,
        "status": ex.status,
        "created_at": ex.created_at,
        "reviewed_at": ex.reviewed_at,
        "reviewed_by": ex.reviewed_by,
        "review_note": ex.review_note,
        "source": ex.source,
    }
