"""prod-14 — Curated concept-video catalog.

Indexes external explainer videos (YouTube, mostly) by concept so the
SPA can embed studio-quality content (Peekaboo Kidz, Khan Academy,
CrashCourse, FuseSchool, 3Blue1Brown, etc.) instead of generating its
own. The AI tutor layer adds the actual differentiator — personalised
practice, doubt-clearing, mock interviews — on top of professional
concept content.

Why embed not host:
  * Studio-quality content already exists and is free to embed.
  * Production-cost of matching Peekaboo Kidz quality (~₹50K-2L per
    video × studio cost) is prohibitive for a startup.
  * Embed legal model: YouTube's embed iframe is the standard,
    creators consent to it via TOS. No licensing deal needed.
  * Bandwidth + storage is YouTube's problem, not ours.

Schema:
  concept_videos   one row per (concept, source_url) pair

Curation workflow:
  1. Add a row via `upsert()` — store the concept name, the source
     URL, grade/subject/board metadata, the source channel name.
  2. SPA calls `search(concept=..., grade=..., language=...)` →
     returns top-N matching videos with embed URLs.
  3. When no curated video exists for a concept, the SPA falls back
     to the existing `/explain/video` AI pipeline.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS concept_videos (
    id              TEXT PRIMARY KEY,
    concept         TEXT NOT NULL,                 -- 'Newton's first law'
    concept_norm    TEXT NOT NULL,                 -- normalised lookup key (lowercase, no punctuation)
    source          TEXT NOT NULL,                 -- 'youtube' | 'khan_academy' | 'ck12' | 'vimeo'
    source_url      TEXT NOT NULL,                 -- canonical watch URL
    embed_url       TEXT NOT NULL,                 -- iframe-friendly embed URL
    title           TEXT NOT NULL,
    channel         TEXT,                          -- 'Peekaboo Kidz', 'Khan Academy India'
    duration_sec    INTEGER,                       -- if known
    language        TEXT NOT NULL DEFAULT 'en',
    board           TEXT,                          -- optional curriculum tag
    grade_min       INTEGER,                       -- recommended grade floor
    grade_max       INTEGER,                       -- recommended grade ceiling
    subject         TEXT,                          -- 'physics' | 'biology' | ...
    quality_tier    TEXT NOT NULL DEFAULT 'verified',
    -- 'verified' = human-reviewed URL, plays without 404
    -- 'channel_seed' = channel verified, specific URL needs curator confirmation
    -- 'ai_fallback' = stub for the AI pipeline to fill
    curator_note    TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (concept_norm, source_url, language)
);
CREATE INDEX IF NOT EXISTS idx_cv_lookup
    ON concept_videos(concept_norm, language);
CREATE INDEX IF NOT EXISTS idx_cv_subject
    ON concept_videos(subject, grade_min, grade_max);
CREATE INDEX IF NOT EXISTS idx_cv_quality
    ON concept_videos(quality_tier);
"""

VALID_SOURCES = frozenset({
    "youtube", "khan_academy", "ck12", "vimeo", "internal",
})

VALID_QUALITY_TIERS = frozenset({
    "verified",         # human-confirmed URL renders correctly
    "channel_seed",     # channel verified, URL needs curator confirm
    "ai_fallback",      # placeholder for AI-pipeline fallback
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
class ConceptVideo:
    id: str
    concept: str
    source: str
    source_url: str
    embed_url: str
    title: str
    channel: str | None
    duration_sec: int | None
    language: str
    board: str | None
    grade_min: int | None
    grade_max: int | None
    subject: str | None
    quality_tier: str
    curator_note: str | None
    created_at: float


def _normalise_concept(name: str) -> str:
    """Lowercase, strip English possessive 's, strip punctuation,
    collapse whitespace. So 'Newton's First Law of Motion!' and
    'newton first law motion' both map to the same lookup key."""
    s = (name or "").lower().strip()
    # English possessive: 's after a letter → drop both apostrophe + s.
    # (Order matters — must run before the generic apostrophe strip.)
    s = re.sub(r"([a-z])['‘’]s\b", r"\1", s)
    # Any remaining apostrophes (non-possessive)
    s = re.sub(r"['‘’]", "", s)
    # Strip non-alphanumeric ASCII; keep Devanagari range for Hindi.
    s = re.sub(r"[^a-z0-9ऀ-ॿ\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_YT_VIDEO_ID = re.compile(
    r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})",
)


def _derive_embed_url(source_url: str) -> str:
    """For YouTube URLs, convert watch?v=XYZ → embed/XYZ.
    Other sources: return as-is."""
    m = _YT_VIDEO_ID.search(source_url or "")
    if m:
        return f"https://www.youtube.com/embed/{m.group(1)}"
    return source_url


_SEL = (
    "id, concept, source, source_url, embed_url, title, channel, "
    "duration_sec, language, board, grade_min, grade_max, subject, "
    "quality_tier, curator_note, created_at"
)


def _row(r) -> ConceptVideo:
    return ConceptVideo(
        id=r[0], concept=r[1], source=r[2], source_url=r[3],
        embed_url=r[4], title=r[5], channel=r[6],
        duration_sec=r[7], language=r[8], board=r[9],
        grade_min=r[10], grade_max=r[11], subject=r[12],
        quality_tier=r[13], curator_note=r[14], created_at=r[15],
    )


def upsert(
    *,
    concept: str,
    source: str,
    source_url: str,
    title: str,
    channel: str | None = None,
    duration_sec: int | None = None,
    language: str = "en",
    board: str | None = None,
    grade_min: int | None = None,
    grade_max: int | None = None,
    subject: str | None = None,
    quality_tier: str = "verified",
    curator_note: str | None = None,
    embed_url: str | None = None,
) -> ConceptVideo:
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be in {sorted(VALID_SOURCES)}")
    if quality_tier not in VALID_QUALITY_TIERS:
        raise ValueError(
            f"quality_tier must be in {sorted(VALID_QUALITY_TIERS)}",
        )
    if not concept or not source_url or not title:
        raise ValueError("concept, source_url, title required")
    if embed_url is None:
        embed_url = _derive_embed_url(source_url)

    concept_norm = _normalise_concept(concept)
    now = time.time()
    with _conn() as conn:
        try:
            cid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO concept_videos "
                "(id, concept, concept_norm, source, source_url, "
                " embed_url, title, channel, duration_sec, language, "
                " board, grade_min, grade_max, subject, quality_tier, "
                " curator_note, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, concept, concept_norm, source, source_url,
                 embed_url, title, channel, duration_sec, language,
                 board, grade_min, grade_max, subject, quality_tier,
                 curator_note, now),
            )
        except sqlite3.IntegrityError:
            # Already exists — update everything except the natural key
            conn.execute(
                "UPDATE concept_videos SET "
                " concept = ?, embed_url = ?, title = ?, channel = ?, "
                " duration_sec = ?, board = ?, grade_min = ?, "
                " grade_max = ?, subject = ?, quality_tier = ?, "
                " curator_note = ? "
                "WHERE concept_norm = ? AND source_url = ? "
                "AND language = ?",
                (concept, embed_url, title, channel, duration_sec,
                 board, grade_min, grade_max, subject, quality_tier,
                 curator_note, concept_norm, source_url, language),
            )
        r = conn.execute(
            f"SELECT {_SEL} FROM concept_videos "
            "WHERE concept_norm = ? AND source_url = ? AND language = ?",
            (concept_norm, source_url, language),
        ).fetchone()
    return _row(r)


def search(
    *,
    concept: str | None = None,
    language: str = "en",
    subject: str | None = None,
    grade: int | None = None,
    quality_tier: str | None = None,
    limit: int = 10,
) -> list[ConceptVideo]:
    """Look up curated concept videos. If `concept` is given, matches
    on normalised concept name (so 'newton first law' finds
    'Newton's First Law of Motion'). If grade is given, returns only
    videos whose [grade_min, grade_max] range includes it."""
    limit = max(1, min(limit, 100))
    where: list[str] = ["language = ?"]
    params: list = [language]
    if concept:
        # Substring-LIKE on normalised name so "newton first law"
        # matches "newton first law of motion". Wrap with % so any
        # token-prefix in the stored concept matches the query.
        where.append("concept_norm LIKE ?")
        params.append(f"%{_normalise_concept(concept)}%")
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if grade is not None:
        # Grade falls within video's recommended range
        where.append(
            "(grade_min IS NULL OR grade_min <= ?) "
            "AND (grade_max IS NULL OR grade_max >= ?)",
        )
        params.extend([grade, grade])
    if quality_tier:
        where.append("quality_tier = ?")
        params.append(quality_tier)
    sql = (
        f"SELECT {_SEL} FROM concept_videos "
        f"WHERE {' AND '.join(where)} "
        # Verified first, then channel_seed, then ai_fallback
        "ORDER BY "
        "  CASE quality_tier "
        "    WHEN 'verified' THEN 0 "
        "    WHEN 'channel_seed' THEN 1 "
        "    ELSE 2 "
        "  END, "
        "  created_at DESC "
        "LIMIT ?"
    )
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows]


def get_by_id(cid: str) -> ConceptVideo | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM concept_videos WHERE id = ?", (cid,),
        ).fetchone()
    return _row(r) if r else None


def list_concepts(*, language: str = "en") -> list[str]:
    """Distinct concept names currently in the catalog."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT concept FROM concept_videos "
            "WHERE language = ? ORDER BY concept",
            (language,),
        ).fetchall()
    return [r[0] for r in rows]


def stats() -> dict:
    """For admin dashboards + the prod-14 audit script."""
    with _conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM concept_videos",
        ).fetchone()[0]
        by_quality = conn.execute(
            "SELECT quality_tier, COUNT(*) FROM concept_videos "
            "GROUP BY quality_tier",
        ).fetchall()
        by_subject = conn.execute(
            "SELECT subject, COUNT(*) FROM concept_videos "
            "GROUP BY subject",
        ).fetchall()
        by_language = conn.execute(
            "SELECT language, COUNT(*) FROM concept_videos "
            "GROUP BY language",
        ).fetchall()
        by_source = conn.execute(
            "SELECT source, COUNT(*) FROM concept_videos "
            "GROUP BY source",
        ).fetchall()
    return {
        "total": total,
        "by_quality_tier": {r[0]: r[1] for r in by_quality},
        "by_subject": {r[0]: r[1] for r in by_subject},
        "by_language": {r[0]: r[1] for r in by_language},
        "by_source": {r[0]: r[1] for r in by_source},
    }


def bulk_load(rows: list[dict]) -> tuple[int, list[str]]:
    """Upsert many rows. Returns (count_loaded, errors).
    Each row must carry the kwargs `upsert` accepts. Used by the
    seed loader and the admin CSV-import endpoint."""
    loaded = 0
    errors: list[str] = []
    for i, row in enumerate(rows):
        try:
            upsert(**row)
            loaded += 1
        except (ValueError, TypeError, KeyError) as e:
            errors.append(f"row {i} ({row.get('concept','?')}): {e}")
    return loaded, errors


# Expose JSON-serialisable view of a ConceptVideo dataclass for API
# routes (FastAPI doesn't auto-serialise dataclasses with all our
# fields populated cleanly).
def to_dict(v: ConceptVideo) -> dict:
    return {
        "id": v.id, "concept": v.concept,
        "source": v.source, "source_url": v.source_url,
        "embed_url": v.embed_url, "title": v.title,
        "channel": v.channel, "duration_sec": v.duration_sec,
        "language": v.language, "board": v.board,
        "grade_min": v.grade_min, "grade_max": v.grade_max,
        "subject": v.subject, "quality_tier": v.quality_tier,
        "curator_note": v.curator_note,
    }


# JSON-roundtrip helper for the admin import path (CSV → list[dict])
def from_csv_row(row: dict) -> dict:
    """Normalise a CSV row into the upsert kwargs shape."""
    out = dict(row)
    for k in ("duration_sec", "grade_min", "grade_max"):
        if k in out and out[k] not in (None, ""):
            out[k] = int(out[k])
        elif k in out:
            out[k] = None
    return out


# Module sanity check — fail fast if schema drifts
assert "concept_norm" in SCHEMA, "schema missing concept_norm column"


def _selftest() -> bool:
    """Lightweight smoke test invokable from a script: ensures the
    table can be created + a row inserted + read back."""
    migrate()
    v = upsert(
        concept="__selftest__",
        source="youtube",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="self-test row",
    )
    assert v.embed_url.startswith("https://www.youtube.com/embed/"), v
    found = search(concept="__selftest__")
    assert found, "selftest row not found"
    with _conn() as conn:
        conn.execute("DELETE FROM concept_videos WHERE concept = ?",
                     ("__selftest__",))
    return True


def _ensure_json(payload) -> str:
    """For modules that want to persist a topic_tags-style list."""
    if payload is None:
        return ""
    return json.dumps(payload, ensure_ascii=False)
