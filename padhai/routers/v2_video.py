"""v2 video-request router — third slice of the web.py split.

Two endpoints today:
  GET /api/v2/video-requests/{request_id}/status   — progress + step
  GET /api/v2/video-requests/{request_id}/result   — final artifacts

The two POST endpoints (`/api/v2/video-requests` itself and
`/regenerate`) stay in web.py for now — they pull in too many
helpers (PersonalizationProfile builder, moderation, render-tier
clamping, multipart-upload handling) to be a clean lift. Splitting
them is a follow-up; this router takes the two read endpoints that
make up the bulk of v2 polling traffic.

Both routes are public — the v2 client identifies requests by job
id (already a UUID), no auth required to read status. Mirrors how
`/jobs/{id}` works today.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/v2/video-requests/{request_id}/status")
def v2_request_status(request_id: str):
    """Track generation with per-step progress."""
    from .. import web as _web
    job = _web.store.get(request_id)
    if not job:
        raise HTTPException(404, "request not found")
    out = _web._progress_for_job(job)
    out["video_request_id"] = request_id
    if job.error:
        out["error"] = job.error
    return out


@router.get("/api/v2/video-requests/{request_id}/result")
def v2_request_result(request_id: str):
    """Fetch the final artifacts + actions."""
    from .. import web as _web
    job = _web.store.get(request_id)
    if not job:
        raise HTTPException(404, "request not found")
    if job.status != "succeeded":
        raise HTTPException(
            409, f"request not ready (status={job.status})",
        )
    r = job.result or {}
    profile_dict = job.payload.get("profile_json")
    return {
        "video_request_id": request_id,
        "video_url": r.get("video_url") or f"/jobs/{request_id}/video",
        "thumbnail_url": r.get("thumbnail_url"),
        # PRD §11 output_assets — every artifact addressable separately
        # so apps can pull only what they need (e.g. audio for
        # walk-to-school mode, srt for the classroom projector).
        "subtitle_url":     r.get("subtitle_url")     or f"/jobs/{request_id}/subtitles.srt",
        "subtitle_vtt_url": r.get("subtitle_vtt_url") or f"/jobs/{request_id}/subtitles.vtt",
        "audio_url":    r.get("audio_url")    or f"/jobs/{request_id}/audio.mp3",
        "lesson_id": r.get("lesson_id"),
        "chat_endpoint": (
            f"/chat/{r['lesson_id']}" if r.get("lesson_id") else None
        ),
        "profile": profile_dict,
        # PRD §13.5 — actions surface
        "actions": [
            "ask_doubt",
            "make_easier",
            "make_advanced",
            "change_language",
            "shorten",
            "exam_focused",
            "create_short",
            "download",
            "share",
        ],
    }
