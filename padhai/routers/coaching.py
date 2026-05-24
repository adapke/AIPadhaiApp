"""K3 — UPSC/JEE/NEET coaching read endpoints (public).

Extracted from web.py in v2.0.2. The write endpoints (create_track,
record_attempt, stats per user) need auth + stay in web.py until
v2.0.3 wires shared api deps."""

from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


@router.get("/api/coaching/tracks")
def list_coaching_tracks(exam: str | None = None):
    from .. import coaching
    rows = coaching.list_tracks(exam=exam)
    return {
        "tracks": [
            {"id": t.id, "exam": t.exam, "name": t.name,
             "description": t.description, "subjects": t.subjects,
             "target_year": t.target_year}
            for t in rows
        ],
    }


@router.get("/api/coaching/digest")
def coaching_daily_digest(exam: str = "upsc", limit: int = 20):
    from .. import coaching
    return {"items": coaching.daily_digest(exam=exam, limit=limit)}
