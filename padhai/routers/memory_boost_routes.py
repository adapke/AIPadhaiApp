"""prod-139 — Memory Boost daily drill router.

Endpoints:
    GET /api/me/memory-boost?board=CBSE&grade=10
        → today's 3-item pack (idempotent — same picks if called twice
        the same day)
    POST /api/me/memory-boost/answer
        body: {pick_id, was_correct, time_seconds?}
        → record response + bump streak
    GET /api/me/memory-boost/streak
        → current/longest streak + last_active_date
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from .. import memory_boost
from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/me/memory-boost")
def get_memory_boost_pack(
    board: str = Query(..., description="CBSE / ICSE / state-board key"),
    grade: int = Query(..., ge=1, le=12),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-139 — Return today's 3-item pack. Idempotent.

    Returns:
        {
          "pack_date": "2026-06-13",
          "picks": [
            {"pick_id", "bucket": "critical"|"warmup"|"fresh",
             "item_kind": "pyq", "item_ref", "item": {...}},
            ...
          ],
          "streak": {"current_streak", "longest_streak", "last_active_date"}
        }

    Buckets:
      critical — red/yellow/decayed topic, needs revision
      warmup   — green topic, freshness check
      fresh    — untouched topic, introduce new material
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    memory_boost.migrate()
    picks = memory_boost.get_or_create_pack(
        user_id=user.id, board=board, grade=grade,
    )
    return {
        "pack_date": picks[0].pack_date if picks else None,
        "picks": memory_boost.hydrate_picks(picks),
        "streak": memory_boost.get_streak(user.id),
    }


@router.post("/api/me/memory-boost/answer")
def post_memory_boost_answer(
    payload: dict = Body(...),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-139 — Record the student's response. Bumps streak.

    Body:
        pick_id       — required (from /api/me/memory-boost picks list)
        was_correct   — required bool
        time_seconds  — optional int (telemetry)

    Returns:
        {"recorded": True, "streak": {...}}
    """
    if user is None:
        raise HTTPException(401, "authentication required")
    memory_boost.migrate()
    pick_id = (payload.get("pick_id") or "").strip()
    if not pick_id:
        raise HTTPException(400, "pick_id is required")
    was_correct_raw = payload.get("was_correct")
    if not isinstance(was_correct_raw, bool):
        raise HTTPException(400, "was_correct must be a bool")
    time_seconds = payload.get("time_seconds")
    if time_seconds is not None and not isinstance(time_seconds, int):
        raise HTTPException(400, "time_seconds must be int or null")

    try:
        result = memory_boost.record_answer(
            pick_id=pick_id,
            user_id=user.id,
            was_correct=was_correct_raw,
            time_seconds=time_seconds,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return result


@router.get("/api/me/memory-boost/streak")
def get_my_streak(
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-139 — Read-only streak feed for the dashboard widget."""
    if user is None:
        raise HTTPException(401, "authentication required")
    memory_boost.migrate()
    return memory_boost.get_streak(user.id)
