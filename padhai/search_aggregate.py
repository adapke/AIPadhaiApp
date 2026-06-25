"""prod-181 — Unified content search aggregator.

A student who wants "Newton's laws" shouldn't have to know whether to
open the videos page, the practice page, or the syllabus. This module
does a single read-side search across the three content stores and
returns grouped results:

  • concept videos  — verified-tier only (never surface a dead embed)
  • past-year Qs     — question_bank, LIKE on question text + chapter
  • real-world ex.   — approved examples whose concept matches the query

Pure reads — no Claude calls, no writes, no per-user state. Safe to
call anonymously (it's a public discovery surface). Robust to any of
the source tables being missing (returns empty for that group).

Ranking is deliberately simple (SQLite LIKE + a light relevance nudge
for exact-prefix matches). Good enough for a soft-launch catalog of a
few thousand rows; swap in FTS5 / a real index when the catalog or
traffic demands it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchResults:
    query: str
    videos: list[dict]
    questions: list[dict]
    examples: list[dict]

    @property
    def total(self) -> int:
        return len(self.videos) + len(self.questions) + len(self.examples)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "total": self.total,
            "videos": self.videos,
            "questions": self.questions,
            "examples": self.examples,
        }


def _clean(q: str | None) -> str:
    if not q:
        return ""
    return _WS_RE.sub(" ", q).strip()


def _search_videos(query: str, limit: int, language: str) -> list[dict]:
    """Verified concept videos matching the query. Verified-only so a
    search result never opens a 'video unavailable' page (prod-180)."""
    try:
        from . import concept_videos as cv
        rows = cv.search(
            concept=query, language=language,
            quality_tier="verified", limit=limit,
        )
        return [cv.to_dict(r) for r in rows]
    except Exception:
        return []


def _search_questions(query: str, limit: int) -> list[dict]:
    """PYQs whose question text matches. We also surface the chapter +
    board/grade so the result is self-describing."""
    try:
        from . import question_bank as qb
        rows = qb.search(text_query=query, limit=limit)
        out = []
        for r in rows:
            out.append({
                "id": r.id,
                "board": r.board,
                "grade": r.grade,
                "subject": r.subject,
                "chapter": r.chapter,
                "year": r.year,
                "question_text": r.question_text,
                "difficulty": r.difficulty,
                "marks": r.marks,
            })
        return out
    except Exception:
        return []


def _search_examples(query: str, limit: int, locale: str) -> list[dict]:
    """Approved real-world examples whose concept slug matches the query.

    `list_for_slug` matches an exact normalised slug, so for a free-text
    query we instead scan distinct approved slugs and keep the ones that
    contain the query tokens — cheap for a few-hundred-row table."""
    try:
        from . import concept_examples as ex
        ex.migrate()
        import sqlite3

        from . import db as _db
        conn = sqlite3.connect(str(_db.sqlite_path()), timeout=10.0)
        try:
            norm = ex._normalise_slug(query)
            rows = conn.execute(
                "SELECT id, concept_slug, example_md FROM concept_examples "
                "WHERE status = 'approved' AND locale = ? "
                "AND concept_slug LIKE ? "
                "ORDER BY created_at DESC LIMIT ?",
                (locale, f"%{norm}%", limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": r[0],
                "concept_slug": r[1],
                # Trim the body to a snippet for the result card.
                "snippet": (r[2] or "")[:240]
                + ("…" if len(r[2] or "") > 240 else ""),
            }
            for r in rows
        ]
    except Exception:
        return []


def unified_search(
    query: str,
    *,
    language: str = "en",
    per_group: int = 8,
) -> SearchResults:
    """Search all content stores for `query`. Returns grouped results.

    `per_group` caps each result group so one chatty source (PYQs) can't
    drown out the others. An empty / too-short query returns no results
    (we don't want a 2-char query to LIKE-match half the catalog)."""
    q = _clean(query)
    if len(q) < 2:
        return SearchResults(query=q, videos=[], questions=[], examples=[])
    per_group = max(1, min(per_group, 50))
    return SearchResults(
        query=q,
        videos=_search_videos(q, per_group, language),
        questions=_search_questions(q, per_group),
        examples=_search_examples(q, per_group, language),
    )
