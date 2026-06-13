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
    updated_at      REAL,
    last_verified_at REAL,
    last_played_at  REAL,
    play_count      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (concept_norm, source_url, language)
);
CREATE INDEX IF NOT EXISTS idx_cv_lookup
    ON concept_videos(concept_norm, language);
CREATE INDEX IF NOT EXISTS idx_cv_subject
    ON concept_videos(subject, grade_min, grade_max);
CREATE INDEX IF NOT EXISTS idx_cv_quality
    ON concept_videos(quality_tier);
"""


def _ensure_updated_at_column(conn: sqlite3.Connection) -> None:
    """prod-57/60 — additive migrations for older DBs. SQLite has no
    IF NOT EXISTS for ALTER TABLE, so we PRAGMA the column list and only
    ALTER when absent. Backfill from created_at so analytics queries
    don't NULL-out on old rows.
    """
    cur = conn.execute("PRAGMA table_info(concept_videos)")
    cols = {row[1] for row in cur.fetchall()}
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE concept_videos ADD COLUMN updated_at REAL")
        conn.execute(
            "UPDATE concept_videos SET updated_at = created_at "
            "WHERE updated_at IS NULL",
        )
    if "last_verified_at" not in cols:
        # prod-60 — timestamp of last curator confirmation. NULL means
        # never verified by a human. The verified seed row(s) get
        # backfilled from created_at; channel_seed and ai_fallback rows
        # stay NULL because they've never been confirmed.
        conn.execute(
            "ALTER TABLE concept_videos ADD COLUMN last_verified_at REAL",
        )
        conn.execute(
            "UPDATE concept_videos SET last_verified_at = created_at "
            "WHERE quality_tier = 'verified' AND last_verified_at IS NULL",
        )
    if "last_played_at" not in cols:
        # prod-70 — when a student last clicked play on this video.
        # Used by /popular and "trending this week" landing widgets.
        conn.execute(
            "ALTER TABLE concept_videos ADD COLUMN last_played_at REAL",
        )
    if "play_count" not in cols:
        # prod-70 — denormalised counter to avoid scanning a separate
        # play-events table for the popular sort. Bumped on every
        # POST /played. Capped server-side at a sane value (no
        # explicit cap; INTEGER overflow is theoretical for sqlite).
        conn.execute(
            "ALTER TABLE concept_videos "
            "ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0",
        )

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
    _ensure_updated_at_column(conn)
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
    updated_at: float | None = None
    last_verified_at: float | None = None


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
    "quality_tier, curator_note, created_at, updated_at, last_verified_at"
)


def _row(r) -> ConceptVideo:
    return ConceptVideo(
        id=r[0], concept=r[1], source=r[2], source_url=r[3],
        embed_url=r[4], title=r[5], channel=r[6],
        duration_sec=r[7], language=r[8], board=r[9],
        grade_min=r[10], grade_max=r[11], subject=r[12],
        quality_tier=r[13], curator_note=r[14], created_at=r[15],
        updated_at=r[16] if len(r) > 16 else None,
        last_verified_at=r[17] if len(r) > 17 else None,
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


def get_by_concept_slug(
    slug: str, *, language: str = "en", quality_tier: str = "verified",
) -> ConceptVideo | None:
    """prod-81 — RESTful lookup by normalized concept slug.

    `slug` is normalized via `_normalise_concept` so URLs like
    /by-concept/newton-first-law and /by-concept/newton%20first%20law
    both resolve to the same row.

    Returns the freshest verified row matching the exact normalised
    concept (NOT a substring match — for /search/ use the search()
    helper). None if no row matches.
    """
    if not slug:
        return None
    # URL-style slugs use '-' as separators. Normalise treats it as
    # whitespace and collapses, so the same normalisation key falls
    # out whether the user used '-' or '+' or space.
    norm = _normalise_concept(slug.replace("-", " "))
    if not norm:
        return None
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_SEL} FROM concept_videos "
            "WHERE concept_norm = ? AND language = ? "
            "AND quality_tier = ? "
            "ORDER BY COALESCE(last_verified_at, created_at) DESC "
            "LIMIT 1",
            (norm, language, quality_tier),
        ).fetchone()
    return _row(row) if row else None


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


def set_quality_tier(
    cid: str, tier: str, curator_note: str | None = None,
) -> ConceptVideo | None:
    """Admin-only mutation — flip a row's quality_tier (typically
    `channel_seed` → `verified` after a curator watches it). Append
    the note to existing curator_note so the audit trail accumulates."""
    if tier not in VALID_QUALITY_TIERS:
        raise ValueError(
            f"tier must be in {sorted(VALID_QUALITY_TIERS)}, got {tier!r}",
        )
    existing = get_by_id(cid)
    if not existing:
        return None
    new_note = existing.curator_note or ""
    if curator_note:
        sep = " | " if new_note else ""
        new_note = f"{new_note}{sep}{curator_note}"
    now = time.time()
    # prod-60 — when we flip to 'verified', stamp last_verified_at too.
    # Demoting to channel_seed/ai_fallback leaves it alone (it represents
    # "last time a human confirmed this URL was correct").
    with _conn() as conn:
        if tier == "verified":
            conn.execute(
                "UPDATE concept_videos SET quality_tier = ?, "
                "curator_note = ?, updated_at = ?, last_verified_at = ? "
                "WHERE id = ?",
                (tier, new_note, now, now, cid),
            )
        else:
            conn.execute(
                "UPDATE concept_videos SET quality_tier = ?, "
                "curator_note = ?, updated_at = ? WHERE id = ?",
                (tier, new_note, now, cid),
            )
    return get_by_id(cid)


# prod-67 — host allowlist for the iframe-block precheck. We never
# fetch arbitrary URLs from user input (SSRF prevention) — only domains
# that we already trust for video embeds.
_IFRAME_CHECK_HOSTS = frozenset({
    "www.youtube.com", "youtube.com",
    "www.youtube-nocookie.com", "youtube-nocookie.com",
    "youtu.be",
    "vimeo.com", "player.vimeo.com",
})


def check_iframe_embed(source_url: str, timeout_sec: float = 3.0) -> dict:
    """prod-67 — server-side detection of iframe-block headers
    (X-Frame-Options, Content-Security-Policy frame-ancestors).

    Returns a dict with `{embeddable, reason, status_code, ...}` so the
    curator UI can warn before saving a URL that wouldn't render on the
    student dashboard.

    SSRF-safe:
      • Only fetches URLs whose host is in `_IFRAME_CHECK_HOSTS`.
      • Uses HEAD (no body download, bounded latency).
      • Catches all exceptions — returns inconclusive result on failure.
      • Hard 3-second timeout via urllib.

    Result shape:
        {
            "embeddable": bool | None,  # None = inconclusive
            "reason": str,              # human-readable
            "status_code": int | None,
            "x_frame_options": str | None,
            "csp_frame_ancestors": str | None,
        }
    """
    out = {
        "embeddable": None,
        "reason": "not checked",
        "status_code": None,
        "x_frame_options": None,
        "csp_frame_ancestors": None,
    }
    if not source_url:
        out["reason"] = "empty url"
        return out
    try:
        import urllib.parse
        import urllib.request
        parsed = urllib.parse.urlparse(source_url)
        if parsed.scheme not in {"http", "https"}:
            out["reason"] = f"unsupported scheme: {parsed.scheme!r}"
            return out
        host = (parsed.hostname or "").lower()
        if host not in _IFRAME_CHECK_HOSTS:
            out["reason"] = f"host {host!r} not in allowlist (SSRF guard)"
            return out

        req = urllib.request.Request(
            source_url, method="HEAD",
            headers={"User-Agent": "AIPadhaiApp/curator (prod-67)"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            status = resp.status
            xfo = resp.headers.get("X-Frame-Options")
            csp = resp.headers.get("Content-Security-Policy")
            csp_fa = None
            if csp:
                for directive in csp.split(";"):
                    directive = directive.strip().lower()
                    if directive.startswith("frame-ancestors"):
                        csp_fa = directive
                        break
            out["status_code"] = status
            out["x_frame_options"] = xfo
            out["csp_frame_ancestors"] = csp_fa
            # YouTube's /embed/ URLs don't set X-Frame-Options; their
            # /watch URLs DO. If the curator pastes a /watch URL the
            # iframe will fail. We surface the raw header so the UI
            # can decide what to show.
            if xfo and xfo.upper() in {"DENY", "SAMEORIGIN"}:
                out["embeddable"] = False
                out["reason"] = f"X-Frame-Options: {xfo}"
            elif csp_fa and "'none'" in csp_fa:
                out["embeddable"] = False
                out["reason"] = "CSP frame-ancestors blocks embedding"
            elif csp_fa and "self" in csp_fa and "*" not in csp_fa:
                out["embeddable"] = False
                out["reason"] = "CSP frame-ancestors: self"
            else:
                out["embeddable"] = True
                out["reason"] = "no block headers detected"
    except Exception as e:
        out["reason"] = f"check failed: {type(e).__name__}"
    return out


def fetch_oembed_metadata(source_url: str, timeout_sec: float = 3.0) -> dict | None:
    """prod-55 — best-effort fetch of YouTube oembed metadata for a watch
    URL. The endpoint is public (no API key) and returns title + author
    (channel) + thumbnail. Returns None on any failure — never raises.

    Used by update_video() to auto-fill title/channel when the curator
    pastes just a URL, saving them a copy-paste step.
    """
    if not source_url or ("youtube.com" not in source_url and "youtu.be" not in source_url):
        return None
    try:
        import json
        import urllib.parse
        import urllib.request
        oembed_url = (
            "https://www.youtube.com/oembed?format=json&url="
            + urllib.parse.quote_plus(source_url)
        )
        req = urllib.request.Request(
            oembed_url,
            headers={"User-Agent": "AIPadhaiApp/curator (prod-55)"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
            return {
                "title": data.get("title"),
                "channel": data.get("author_name"),
                "thumbnail_url": data.get("thumbnail_url"),
            }
    except Exception:
        return None


def update_video(
    cid: str,
    *,
    title: str | None = None,
    source_url: str | None = None,
    channel: str | None = None,
    duration_sec: int | None = None,
    curator_note: str | None = None,
    auto_fetch_oembed: bool = False,
) -> ConceptVideo | None:
    """Curator workflow — replace stub URL/title with the real video the
    curator found by searching the trusted channel. If source_url is
    updated, embed_url is re-derived. curator_note appends to existing.

    Pass None to leave a field unchanged.
    """
    existing = get_by_id(cid)
    if not existing:
        return None

    # prod-55: if curator passes source_url + auto_fetch_oembed, look up
    # title/channel from YouTube oembed when they weren't explicitly
    # supplied. Caller-provided values still win.
    oembed = None
    if auto_fetch_oembed and source_url is not None:
        oembed = fetch_oembed_metadata(source_url)

    if title is not None:
        new_title = title
    elif oembed and oembed.get("title"):
        new_title = oembed["title"]
    else:
        new_title = existing.title

    new_source_url = source_url if source_url is not None else existing.source_url

    if channel is not None:
        new_channel = channel
    elif oembed and oembed.get("channel"):
        new_channel = oembed["channel"]
    else:
        new_channel = existing.channel

    new_duration = duration_sec if duration_sec is not None else existing.duration_sec
    new_embed = _derive_embed_url(new_source_url) if source_url is not None else existing.embed_url
    new_note = existing.curator_note or ""
    if curator_note:
        sep = " | " if new_note else ""
        new_note = f"{new_note}{sep}{curator_note}"
    with _conn() as conn:
        conn.execute(
            "UPDATE concept_videos SET title = ?, source_url = ?, "
            "embed_url = ?, channel = ?, duration_sec = ?, "
            "curator_note = ?, updated_at = ? WHERE id = ?",
            (new_title, new_source_url, new_embed, new_channel,
             new_duration, new_note, time.time(), cid),
        )
    return get_by_id(cid)


def record_play(cid: str) -> bool:
    """prod-70 — increment play_count and stamp last_played_at. Returns
    True if the row exists and was updated, False otherwise. Public
    write surface — does NOT do auth (caller's responsibility) and
    rate-limited at the router level."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE concept_videos SET play_count = play_count + 1, "
            "last_played_at = ? WHERE id = ?",
            (time.time(), cid),
        )
        return cur.rowcount > 0


def curator_stats(since_days: int = 30) -> dict:
    """prod-74 — aggregate counters for the curator-stats admin page.

    Returns:
        {
            "total": int,
            "by_tier": {"verified": N, "channel_seed": N, "ai_fallback": N},
            "verified_recent": int,   # verified in last `since_days`
            "updated_recent": int,    # updated in last `since_days`
            "played_recent_total": int,  # sum(play_count) for rows played recently
            "freshest_verified_iso": str | None,
            "oldest_verified_iso": str | None,
            "since_days": int,
        }
    """
    import datetime as _dt
    cutoff = time.time() - (since_days * 86400)
    out: dict = {
        "total": 0,
        "by_tier": {},
        "verified_recent": 0,
        "updated_recent": 0,
        "played_recent_total": 0,
        "freshest_verified_iso": None,
        "oldest_verified_iso": None,
        "since_days": since_days,
    }
    with _conn() as conn:
        out["total"] = conn.execute(
            "SELECT COUNT(*) FROM concept_videos",
        ).fetchone()[0]
        for tier, n in conn.execute(
            "SELECT quality_tier, COUNT(*) FROM concept_videos GROUP BY quality_tier",
        ):
            out["by_tier"][tier] = n
        out["verified_recent"] = conn.execute(
            "SELECT COUNT(*) FROM concept_videos "
            "WHERE quality_tier='verified' AND last_verified_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        out["updated_recent"] = conn.execute(
            "SELECT COUNT(*) FROM concept_videos WHERE updated_at >= ?",
            (cutoff,),
        ).fetchone()[0]
        row = conn.execute(
            "SELECT SUM(play_count) FROM concept_videos "
            "WHERE last_played_at >= ?",
            (cutoff,),
        ).fetchone()
        out["played_recent_total"] = int(row[0] or 0)
        row = conn.execute(
            "SELECT MAX(last_verified_at), MIN(last_verified_at) "
            "FROM concept_videos WHERE last_verified_at IS NOT NULL",
        ).fetchone()
        if row and row[0]:
            out["freshest_verified_iso"] = _dt.datetime.fromtimestamp(
                float(row[0]), tz=_dt.UTC,
            ).isoformat(timespec="seconds")
        if row and row[1]:
            out["oldest_verified_iso"] = _dt.datetime.fromtimestamp(
                float(row[1]), tz=_dt.UTC,
            ).isoformat(timespec="seconds")
    return out


def list_popular(
    *,
    limit: int = 10,
    language: str = "en",
    since_days: int | None = 7,
    quality_tier: str | None = "verified",
) -> list[tuple[ConceptVideo, int]]:
    """prod-70 — top-N most-played videos. Returns (row, play_count)
    pairs sorted by play_count desc, then last_played_at desc.

    since_days=N restricts to videos played at least once in the last
    N days. since_days=None disables the filter (lifetime popularity).
    quality_tier defaults to 'verified' so the landing widget never
    surfaces unconfirmed channel_seed picks.
    """
    cutoff: float | None = None
    if since_days is not None:
        cutoff = time.time() - (since_days * 86400)

    sql_parts = [
        f"SELECT {_SEL}, play_count FROM concept_videos "
        "WHERE play_count > 0 AND language = ?",
    ]
    params: list = [language]
    if quality_tier is not None:
        sql_parts.append("AND quality_tier = ?")
        params.append(quality_tier)
    if cutoff is not None:
        sql_parts.append("AND last_played_at >= ?")
        params.append(cutoff)
    sql_parts.append("ORDER BY play_count DESC, last_played_at DESC LIMIT ?")
    params.append(limit)
    sql = " ".join(sql_parts)

    with _conn() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    out: list[tuple[ConceptVideo, int]] = []
    for r in rows:
        # _SEL has 18 columns; play_count is column 18 (zero-indexed).
        out.append((_row(r[:18]), int(r[18])))
    return out


def list_curator_queue(
    *, quality_tier: str = "channel_seed", limit: int = 200,
) -> list[ConceptVideo]:
    """List rows awaiting curator action, oldest-first. Default tier
    is channel_seed — those are the "channel trusted but URL is stub"
    rows that prod-14 seeded.
    """
    with _conn() as conn:
        cur = conn.execute(
            f"SELECT {_SEL} FROM concept_videos WHERE quality_tier = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (quality_tier, limit),
        )
        return [_row(r) for r in cur.fetchall()]


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
        # prod-57/60 — surface timestamps for admin UI freshness
        "updated_at": v.updated_at,
        "last_verified_at": v.last_verified_at,
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
