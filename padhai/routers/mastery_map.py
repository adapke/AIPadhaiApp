"""prod-135 — Concept Mastery Map router.

CK-12 BrainFlex-style endpoint that returns the student's per-topic
mastery state for their enrolled board+grade. Pure read-side
aggregation over existing tables (essay_submissions, mock_interviews,
practice_tests, flashcard_reviews, user_topic_mastery).

Endpoints:
    GET /api/me/mastery-map?board=CBSE&grade=10[&subject=Math]
       → 200 with per-topic mastery + color-state rollup
    GET /api/me/mastery-map/summary?board=CBSE&grade=10
       → 200 with only the color-state counts (cheap widget feed)

Both authed — the user_id comes from the auth dep. Caller's enrolled
board/grade is the conventional default, but the explicit query
params win (a NEET aspirant might want to check JEE prep coverage
too).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import mastery_aggregate
from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


def _serialise(row: mastery_aggregate.ConceptMastery) -> dict:
    return {
        "topic_key": row.topic_key,
        "title": row.title,
        "chapter": row.chapter,
        "subject": row.subject,
        "board": row.board,
        "grade": row.grade,
        "mastery": row.mastery,
        "raw_mastery": row.raw_mastery,
        "last_practised": row.last_practised,
        "decay_state": row.decay_state,
        "color_state": row.color_state,
        "source_attempts": row.source_attempts,
    }


@router.get("/api/me/mastery-map")
def get_mastery_map(
    board: str = Query(..., description="CBSE / ICSE / state-board key"),
    grade: int = Query(..., ge=1, le=12, description="Class 1..12"),
    subject: str | None = Query(None, description="Optional filter, e.g. Math"),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-135 — full mastery map for board+grade [+subject].

    Returns:
        {
          "rows": [
            {"topic_key":"...", "title":"...", "subject":"Math",
             "mastery":0.72, "color_state":"green",
             "decay_state":"fresh", "last_practised":1781..., ...},
            ...
          ],
          "summary": {"green": 8, "yellow": 12, "red": 4, "untouched": 6, "total": 30},
          "board": "CBSE", "grade": 10, "subject": null
        }

    Untouched topics are returned with `color_state: "untouched"` so the
    SPA can render them as "not started" tiles instead of hiding them.
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    user_id = user.id

    rows = mastery_aggregate.build_mastery_map(
        user_id=user_id, board=board, grade=grade, subject=subject,
    )
    return {
        "rows": [_serialise(r) for r in rows],
        "summary": mastery_aggregate.summarise(rows),
        "board": board,
        "grade": grade,
        "subject": subject,
    }


@router.get("/api/me/mastery-map/summary")
def get_mastery_summary(
    board: str = Query(...),
    grade: int = Query(..., ge=1, le=12),
    subject: str | None = Query(None),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-135 — cheap counts-only feed for the dashboard widget.

    Returns:
        {"green": N, "yellow": N, "red": N, "untouched": N, "total": N,
         "board": "CBSE", "grade": 10, "subject": null}
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    user_id = user.id

    rows = mastery_aggregate.build_mastery_map(
        user_id=user_id, board=board, grade=grade, subject=subject,
    )
    summary = mastery_aggregate.summarise(rows)
    summary.update({"board": board, "grade": grade, "subject": subject})
    return summary
