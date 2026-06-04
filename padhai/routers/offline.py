"""Offline pack router — exposes the offline_packs.py manifest engine
over HTTP so the PWA / Capacitor wrapper can download a pack for
offline study.

  GET  /api/offline/prefs               — read user's low-data prefs
  PUT  /api/offline/prefs               — update low-data prefs
  POST /api/offline/manifests           — generate a manifest from a list of files
  GET  /api/offline/manifests           — list user's manifests
  GET  /api/offline/manifests/{mid}     — fetch one manifest (with file URLs)
  DELETE /api/offline/manifests/{mid}   — delete a manifest
  POST /api/offline/downloads           — start a download for a manifest
  POST /api/offline/downloads/{did}/progress  — push progress update
  POST /api/offline/downloads/{did}/cancel    — cancel a download
  GET  /api/offline/downloads           — list user's downloads
  GET  /api/offline/usage/today         — bytes-downloaded budget for today

This is the server-side half; the PWA service worker (already mounted
at /sw.js in web.py) handles client-side caching of the URLs in the
manifest.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


# ============================================================================
# Low-data preferences
# ============================================================================

@router.get("/api/offline/prefs")
def get_prefs(user=Depends(current_user)):
    from .. import offline_packs as op
    user = require_user(user)
    p = op.get_low_data_prefs(user.id)
    return {
        "user_id": user.id,
        "quality_tier": p.quality_tier,
        "wifi_only_for_video": getattr(p, "wifi_only_for_video", True),
        "max_daily_mb": getattr(p, "max_daily_mb", None),
        "updated_at": getattr(p, "updated_at", None),
    }


@router.put("/api/offline/prefs")
def set_prefs(
    quality_tier: str = Form(..., description="text_only | standard | full"),
    wifi_only_for_video: bool = Form(True),
    max_daily_mb: int | None = Form(None, ge=0, le=10240),
    user=Depends(current_user),
):
    from .. import offline_packs as op
    user = require_user(user)
    try:
        op.set_low_data_prefs(
            user_id=user.id,
            quality_tier=quality_tier,
            wifi_only_for_video=wifi_only_for_video,
            max_daily_mb=max_daily_mb,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "quality_tier": quality_tier}


# ============================================================================
# Manifest CRUD
# ============================================================================

@router.post("/api/offline/manifests", status_code=201)
def create_manifest(
    body: dict = Body(...),
    user=Depends(current_user),
):
    """Generate an offline pack manifest. Body shape:
      {
        "title": "Class 10 — Photosynthesis (chapter)",
        "files": [
          {"ref_kind": "lesson_text", "ref_id": "lesson-1",
           "bytes": 4096, "url": "...", "priority": 1},
          ...
        ],
        "pack_code": "cbse_10_2026"  // optional
        "topic_code": "bio_photo"     // optional
        "quality_tier": "standard"    // optional; uses user pref if omitted
        "expires_in_hours": 168       // optional; default 168
      }
    """
    from .. import offline_packs as op
    user = require_user(user)
    title = (body.get("title") or "").strip()
    files = body.get("files") or []
    if not title:
        raise HTTPException(400, "title required")
    if not isinstance(files, list) or not files:
        raise HTTPException(400, "files (non-empty list) required")
    try:
        m = op.generate_manifest(
            user_id=user.id,
            title=title,
            files=files,
            pack_code=body.get("pack_code"),
            topic_code=body.get("topic_code"),
            quality_tier=body.get("quality_tier"),
            version=int(body.get("version") or 1),
            expires_in_hours=int(
                body.get("expires_in_hours") or op.DEFAULT_EXPIRY_HOURS,
            ),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _manifest_to_dict(m)


@router.get("/api/offline/manifests")
def list_manifests(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
):
    from .. import offline_packs as op
    user = require_user(user)
    items = op.list_user_manifests(user.id, limit=limit)
    return {
        "manifests": [_manifest_to_dict(m, include_files=False) for m in items],
        "count": len(items),
    }


@router.get("/api/offline/manifests/{mid}")
def get_manifest(mid: str, user=Depends(current_user)):
    from .. import offline_packs as op
    user = require_user(user)
    m = op.get_manifest(mid)
    if not m:
        raise HTTPException(404, "manifest not found")
    if m.user_id != user.id:
        raise HTTPException(403, "not your manifest")
    return _manifest_to_dict(m, include_files=True)


@router.delete("/api/offline/manifests/{mid}")
def delete_manifest(mid: str, user=Depends(current_user)):
    from .. import offline_packs as op
    user = require_user(user)
    ok = op.delete_manifest(manifest_id=mid, user_id=user.id)
    if not ok:
        raise HTTPException(404, "manifest not found or not yours")
    return {"ok": True}


# ============================================================================
# Download lifecycle
# ============================================================================

@router.post("/api/offline/downloads", status_code=201)
def start_download(
    manifest_id: str = Form(...),
    user=Depends(current_user),
):
    """Begin (or resume) a download for a manifest. The PWA fetches
    each file URL from the manifest and pushes progress events back
    here so the user can resume on reconnect."""
    from .. import offline_packs as op
    user = require_user(user)
    m = op.get_manifest(manifest_id)
    if not m:
        raise HTTPException(404, "manifest not found")
    if m.user_id != user.id:
        raise HTTPException(403, "not your manifest")
    try:
        d = op.start_download(manifest_id=manifest_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _download_to_dict(d)


@router.post("/api/offline/downloads/{did}/progress")
def push_progress(
    did: str,
    bytes_done: int = Form(..., ge=0),
    files_done: int = Form(..., ge=0),
    user=Depends(current_user),
):
    from .. import offline_packs as op
    user = require_user(user)
    try:
        d = op.update_progress(
            download_id=did, user_id=user.id,
            bytes_done=bytes_done, files_done=files_done,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if not d:
        raise HTTPException(404, "download not found")
    return _download_to_dict(d)


@router.post("/api/offline/downloads/{did}/cancel")
def cancel_download(did: str, user=Depends(current_user)):
    from .. import offline_packs as op
    user = require_user(user)
    ok = op.cancel_download(download_id=did, user_id=user.id)
    if not ok:
        raise HTTPException(404, "download not found or not yours")
    return {"ok": True}


@router.get("/api/offline/downloads")
def list_downloads(
    limit: int = Query(20, ge=1, le=100),
    user=Depends(current_user),
):
    from .. import offline_packs as op
    user = require_user(user)
    items = op.list_user_downloads(user.id, limit=limit)
    return {
        "downloads": [_download_to_dict(d) for d in items],
        "count": len(items),
    }


@router.get("/api/offline/usage/today")
def usage_today(user=Depends(current_user)):
    """How much of today's data budget the user has consumed.
    Used by the PWA to gate auto-downloads on cellular."""
    from .. import offline_packs as op
    user = require_user(user)
    return op.user_data_usage_today(user.id)


# ============================================================================
# Helpers
# ============================================================================

def _manifest_to_dict(m, *, include_files: bool = False) -> dict:
    out = {
        "id": m.id,
        "user_id": m.user_id,
        "pack_code": m.pack_code,
        "topic_code": m.topic_code,
        "title": m.title,
        "version": m.version,
        "quality_tier": m.quality_tier,
        "file_count": m.file_count,
        "total_bytes": m.total_bytes,
        "expires_at": m.expires_at,
        "created_at": m.created_at,
    }
    if include_files:
        out["files"] = getattr(m, "files", []) or []
    return out


def _download_to_dict(d) -> dict:
    return {
        "id": d.id,
        "manifest_id": d.manifest_id,
        "user_id": d.user_id,
        "status": d.status,
        "bytes_done": getattr(d, "bytes_done", 0),
        "files_done": getattr(d, "files_done", 0),
        "started_at": d.started_at,
        "completed_at": getattr(d, "completed_at", None),
        "cancelled_at": getattr(d, "cancelled_at", None),
    }
