"""Curriculum router — eighteenth web.py slice.

Two endpoints covering the NCERT / state-board curriculum mapping:

  POST /lessons/{lesson_id}/curriculum    (match a lesson against catalogue)
  GET  /curriculum/index                  (browse catalogue, filterable)

`POST .../curriculum` runs `pedagogy.match_curriculum` against the
cached Lesson JSON and the static `CURRICULUM` seed list. Results are
cached idempotently in the lesson cache — second call returns the
cached match set unless `regenerate=true`. ~₹0.20/call (Haiku 4.5).

`GET /curriculum/index` returns the catalogue itself (no copyrighted
content — just chapter titles + topic tags). It merges Postgres
overrides from the `curriculum_topics` table when DATABASE_URL is
set; falls back to the static seed otherwise. Filters by `board`,
`cls`, `subject`.

Late-imports `web` for `cache` + `get_db_url`; pulls `CURRICULUM` and
`match_curriculum` directly since they're stable module-level
exports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.post("/lessons/{lesson_id}/curriculum")
def curriculum_for_lesson_route(
    lesson_id: str,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),
):
    """Match a generated lesson against the NCERT/state-board
    catalogue.

    Returns up to 3 ranked curriculum entries (board, class, subject,
    chapter) with confidence + reason. Lever for the Learning Path
    module (Phase 2) and for surfacing 'related practice from CBSE
    Class 8 Chapter 6' under a lesson.

    Per-user resource — requires auth even when PADHAI_REQUIRE_AUTH=0
    (anonymous users have no lessons of their own to match against).

    Cached idempotently. ~₹0.20/call (Haiku 4.5)."""
    from .. import web as _web
    from ..curriculum import CURRICULUM
    from ..pedagogy import match_curriculum

    if user is None:
        raise HTTPException(401, "authentication required")

    if not regenerate:
        cached = _web.cache.get_curriculum_matches(lesson_id)
        if cached is not None:
            return {
                "lesson_id": lesson_id,
                "matches": cached,
                "cached": True,
                "count": len(cached),
            }

    cached_lesson = _web.cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    matches = match_curriculum(cached_lesson, CURRICULUM)
    _web.cache.put_curriculum_matches(lesson_id, matches)
    return {
        "lesson_id": lesson_id,
        "matches": matches,
        "cached": False,
        "count": len(matches),
    }


@router.get("/curriculum/index")
def curriculum_index_route(
    board: str | None = None,
    cls: int | None = None,
    subject: str | None = None,
):
    """Return the curriculum catalogue (no copyrighted content — just
    metadata: chapter titles + topic tags). Used by the Curriculum
    Map module to render the browseable index.

    Static seed list (curriculum.py) is the base; DB rows override or
    extend it when a `curriculum_topics` table is available
    (Postgres-only)."""
    import contextlib

    from ..curriculum import CURRICULUM
    from ..db import get_db_url

    # Start with the static seed
    rows: list[dict] = [dict(r) for r in CURRICULUM]
    # Merge DB overrides / additions when Postgres is available
    db_url = get_db_url()
    if db_url:
        with contextlib.suppress(Exception):
            import json as _json

            import psycopg
            with psycopg.connect(db_url) as conn:
                db_rows = conn.execute(
                    "SELECT id, board, class, subject, chapter_no, "
                    "chapter_title, level, summary, topics "
                    "FROM curriculum_topics "
                    "ORDER BY board, class, subject, chapter_no",
                ).fetchall()
            if db_rows:
                # Build index of static rows for deduplication
                static_idx = {
                    (r["board"], r["class"], r["subject"], r["chapter_no"])
                    for r in rows
                }
                for dbr in db_rows:
                    key = (dbr[1], dbr[2], dbr[3], dbr[4])
                    entry = {
                        "id": dbr[0], "board": dbr[1], "class": dbr[2],
                        "subject": dbr[3], "chapter_no": dbr[4],
                        "chapter_title": dbr[5], "level": dbr[6],
                        "summary": dbr[7],
                        "topics": (
                            dbr[8] if isinstance(dbr[8], list)
                            else _json.loads(dbr[8] or "[]")
                        ),
                        "source": "db",
                    }
                    if key in static_idx:
                        # Override matching static entry
                        rows = [
                            entry if (
                                r["board"] == dbr[1]
                                and r["class"] == dbr[2]
                                and r["subject"] == dbr[3]
                                and r["chapter_no"] == dbr[4]
                            ) else r
                            for r in rows
                        ]
                    else:
                        rows.append(entry)
    # Apply filters after merge
    if board:
        rows = [r for r in rows if r["board"] == board]
    if cls is not None:
        rows = [r for r in rows if r["class"] == cls]
    if subject:
        rows = [r for r in rows if r["subject"] == subject]
    all_boards = (
        sorted({r["board"] for r in rows}) if not board else [board]
    )
    all_classes = (
        sorted({r["class"] for r in rows}) if cls is None else [cls]
    )
    all_subjects = (
        sorted({r["subject"] for r in rows}) if not subject else [subject]
    )
    return {
        "boards": all_boards,
        "classes": all_classes,
        "subjects": all_subjects,
        "entries": rows,
        "count": len(rows),
    }
