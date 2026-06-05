"""Multi-page video stitching router — first slice of the web.py split.

These endpoints stitch the per-page MP4s produced by a multi-page
`/lessons` upload into one combined MP4 via ffmpeg's concat demuxer.
Lives in its own router because:
  • The stitching logic is self-contained — only depends on the job
    store + on-disk MP4 location, not on the lesson generator or
    auth.
  • It's the natural template for follow-on web.py extractions
    (every other route group has a similar "self-contained, small
    surface" profile).

Endpoints:
  GET  /jobs/{job_id}/combined.mp4   — stitch + serve the combined MP4
  GET  /jobs/{job_id}/combined       — JSON status ("3 of 5 ready")

The actual stitching helper lives in web.py for now
(`_stitch_page_videos`) because it needs `_locate_mp4` + `_OUTPUT_DIR`
which are also web.py-internal. Moving the helper here is a follow-up
refactor; keeping these routes here at minimum stops them growing in
web.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/jobs/{job_id}/combined.mp4")
def get_combined_video(job_id: str, request: Request):  # noqa: ARG001
    """Return one stitched MP4 covering every successfully-rendered
    page of a multi-page upload. The leader job is the parent (page 1
    of the upload); siblings have payload.parent_job_id == leader.id.

    409 when no page videos are ready yet, 404 when leader_id is not a
    multi-page upload, 500 when ffmpeg isn't available. The combined
    file is cached on local disk keyed by the participating job ids,
    so partial bundles don't shadow later full bundles.
    """
    from .. import web as _web  # late import — web.py owns the store
    job = _web.store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    combined_path, _info = _web._stitch_page_videos(job_id)
    return FileResponse(
        combined_path,
        media_type="video/mp4",
        filename=f"lesson_{job_id}_combined.mp4",
    )


@router.get("/jobs/{job_id}/combined")
def get_combined_status(job_id: str):
    """JSON view of the multi-page combine status — used by the SPA to
    show "3 of 5 pages ready" before linking to the actual MP4."""
    from .. import web as _web
    job = _web.store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    pages = _web.store.find_siblings(job_id)
    if len(pages) < 2:
        return {
            "is_multi_page": False,
            "leader_job_id": job_id,
            "page_count": len(pages),
        }
    by_status: dict[str, int] = {}
    for j in pages:
        by_status[j.status] = by_status.get(j.status, 0) + 1
    return {
        "is_multi_page": True,
        "leader_job_id": job_id,
        "page_count": len(pages),
        "by_status": by_status,
        "ready_pages": by_status.get("succeeded", 0),
        "combined_video_url": (
            f"/jobs/{job_id}/combined.mp4"
            if by_status.get("succeeded", 0) > 0 else None
        ),
        "pages": [
            {
                "job_id": j.id,
                "page_number": (j.payload or {}).get("page_number"),
                "status": j.status,
                "video_url": (
                    f"/jobs/{j.id}/video" if j.status == "succeeded"
                    else None
                ),
            }
            for j in pages
        ],
    }
