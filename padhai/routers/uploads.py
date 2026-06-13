"""Uploads router — nineteenth web.py slice.

Three endpoints implementing PRD §13.1-2 (upload → analyze → use):

  POST /api/uploads                       (persist + ingest, return id)
  POST /api/uploads/{upload_id}/analyze   (Claude vision -> topic/grade/...)
  GET  /api/uploads/{upload_id}           (look up, useful on page reload)

The router is *not* `uploads_ai.py` — that one already exists and
covers RAG chat / flashcards / quiz / summary over an already-
analyzed upload. This slice is the storage + analysis pipeline that
feeds it.

`_UPLOAD_DIR` moves with the router since this is its only call
site. `_uploads` (storage), `_rl` (rate limit), and `ingest_source`
late-import from web.py.

Per-user resource — every endpoint requires auth even when
PADHAI_REQUIRE_AUTH=0 (uploads belong to user libraries).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


_UPLOAD_DIR = Path(os.environ.get(
    "PADHAI_UPLOAD_DIR",
    str(Path.home() / ".padhai" / "uploads"),
))


@router.post("/api/uploads", status_code=201)
def create_upload_route(
    request: Request,
    # `file` is Optional so the auth gate fires BEFORE body validation
    # (FastAPI's `File(...)` validation otherwise returns 422 to
    # anonymous callers, leaking the contract). When user IS
    # authenticated and file is missing, we re-raise as 422 manually.
    file: UploadFile | None = File(None),
    is_whiteboard: bool = Form(
        False,
        description=(
            "True for handwritten content (whiteboard / notebook / "
            "blackboard photo) — uses the OCR-tolerant analyzer prompt"
        ),
    ),
    user: AuthUser | None = Depends(current_user),
):
    """Step 1 of the PRD §13 contract. Persist the upload + ingest
    into page images; do NOT run analysis yet (that's /analyze).

    `is_whiteboard=true` flags handwritten content so /analyze later
    uses the OCR-tolerant prompt (C5).

    The response carries `upload_id` + `page_count` so the UI can
    show "Uploaded 12 pages of textbook.pdf" before the user decides
    what to generate from it.

    Per-user resource — requires auth even when
    PADHAI_REQUIRE_AUTH=0 (uploads live in user's library).
    Anonymous callers get 401."""
    from .. import web as _web
    if user is None:
        raise HTTPException(401, "authentication required")
    if file is None:
        raise HTTPException(422, "file is required")
    rate_key = _web._rl.client_ip_from_request(request)
    if not _web._rl.file_upload.try_consume(rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "page.jpg").suffix.lower() or ".jpg"
    # Keep this allow-list in sync with padhai.ingest.ingest() — that
    # function does the actual dispatch and rejects anything else.
    # Adding more types here without adding ingest support causes a
    # 400 buried inside ingest with a misleading "outer accepted" UX.
    if suffix not in (
        ".jpg", ".jpeg", ".png", ".webp",        # raster images
        ".pdf",                                   # multi-page PDF
        ".pptx", ".docx",                         # office docs (LibreOffice required)
    ):
        raise HTTPException(
            400,
            f"unsupported file type {suffix!r}. Allowed: "
            ".jpg .jpeg .png .webp .pdf .pptx .docx",
        )
    raw_path = _UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    body = file.file.read()
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(413, "file too large (limit 25 MB)")
    raw_path.write_bytes(body)

    try:
        page_images = _web.ingest_source(raw_path)
    except ValueError as e:
        raw_path.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e

    # For multi-page sources (PDF/PPTX/DOCX) we keep ONLY the first
    # page image as the upload's `file_path` — that's what the
    # downstream analyzer + lesson generator look at today.
    page_path = page_images[0]
    # Don't unlink raw_path if ingest used it directly (single image).
    try:
        same_source = raw_path.samefile(page_path)
    except (FileNotFoundError, OSError):
        same_source = raw_path == page_path
    if not same_source:
        raw_path.unlink(missing_ok=True)

    # C5: whiteboard kind biases /analyze toward OCR-tolerant prompt.
    if is_whiteboard:
        kind = "whiteboard"
    elif suffix == ".pdf":
        kind = "pdf"
    elif suffix in (".pptx", ".docx"):
        kind = "document"
    else:
        kind = "image"
    rec = _web._uploads.register(
        file_path=str(page_path),
        content_kind=kind,
        original_filename=file.filename,
        size_bytes=len(body),
        page_count=len(page_images),
        user_id=(user.id if user else None),
    )
    return {
        "upload_id": rec.id,
        "page_count": rec.page_count,
        "content_kind": rec.content_kind,
        "original_filename": rec.original_filename,
        "next_step": "POST /api/uploads/{id}/analyze",
    }


@router.post("/api/uploads/{upload_id}/analyze")
def analyze_upload_route(
    upload_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Step 2 of the PRD §13 contract. Run Claude vision on the
    upload's first page → detected_topic / detected_subject /
    detected_grade / detected_language / suggested_modes.

    Result is persisted on the `uploads` row so repeat calls return
    instantly (cached). The Studio UI calls this between Source and
    Customize so the form can preselect language + grade + suggested
    modes."""
    from .. import web as _web

    rec = _web._uploads.get(upload_id)
    if rec is None:
        raise HTTPException(404, "upload not found")
    if user and rec.user_id and rec.user_id != user.id:
        raise HTTPException(403, "upload belongs to another user")

    if rec.analysis_json:
        _web._uploads.touch(upload_id)
        return {
            "upload_id": rec.id,
            "cached": True,
            **rec.analysis_json,
        }

    page_path = Path(rec.file_path)
    if not page_path.exists():
        raise HTTPException(
            410, "uploaded file no longer on disk (retention)",
        )

    try:
        analysis = _web._uploads.analyze_via_claude(
            page_path, content_kind=rec.content_kind,
        )
    except Exception as e:
        raise HTTPException(502, f"content analysis failed: {e}") from e
    _web._uploads.set_analysis(upload_id, analysis)
    return {"upload_id": rec.id, "cached": False, **analysis}


@router.get("/api/uploads/{upload_id}")
def get_upload_route(
    upload_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Look up a previously-created upload — useful for the Studio
    when the user reloads the page mid-flow."""
    from .. import web as _web

    rec = _web._uploads.get(upload_id)
    if rec is None:
        raise HTTPException(404, "upload not found")
    if user and rec.user_id and rec.user_id != user.id:
        raise HTTPException(403, "upload belongs to another user")
    return {
        "upload_id": rec.id,
        "page_count": rec.page_count,
        "content_kind": rec.content_kind,
        "original_filename": rec.original_filename,
        "status": rec.status,
        "analysis": rec.analysis_json,
        "created_at": rec.created_at,
    }
