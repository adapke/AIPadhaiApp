"""prod-14 — /api/concept-videos/* router.

Read-API for the curated concept-video catalog. The SPA hits this
when a student asks about a concept; we return embed-friendly URLs
to professional YouTube content (Peekaboo Kidz / Khan / etc).
The AI tutor layer (`/explain/video`, `/api/tutor/*`) remains the
fallback when no curated video matches.

Routes:
  GET  /api/concept-videos              — list/search
  GET  /api/concept-videos/{id}         — single video lookup
  GET  /api/concept-videos/stats        — catalog stats (public,
                                          no PII)

POST/PATCH routes for adding videos live on the admin side (covered
by the router-level admin dep in routers/__init__.py since the path
starts with /api/admin/).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import concept_videos as _cv

router = APIRouter()


@router.get("/api/concept-videos")
def list_videos(
    concept: str | None = Query(
        None, description="exact or normalised concept name",
    ),
    language: str = Query("en"),
    subject: str | None = None,
    grade: int | None = None,
    quality_tier: str | None = Query(
        None,
        description="verified | channel_seed | ai_fallback",
    ),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    rows = _cv.search(
        concept=concept,
        language=language,
        subject=subject,
        grade=grade,
        quality_tier=quality_tier,
        limit=limit,
    )
    return {
        "rows": [_cv.to_dict(r) for r in rows],
        "count": len(rows),
    }


@router.get("/api/concept-videos/stats")
def get_stats() -> dict:
    """Public — no PII, just aggregate counts for the curator
    dashboard and the SPA's "how many concept videos do we cover"
    pitch line on the landing page."""
    return _cv.stats()


@router.get("/api/concept-videos/{video_id}")
def get_video(video_id: str) -> dict:
    v = _cv.get_by_id(video_id)
    if not v:
        raise HTTPException(404, "concept video not found")
    return _cv.to_dict(v)
