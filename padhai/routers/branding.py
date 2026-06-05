"""Branding router — twelfth web.py slice.

Three related endpoints for white-label / per-org branding:
  GET  /api/branding/resolve            (public — SPA boot lookup by host)
  POST /api/orgs/{org_id}/branding/logo (admin — upload logo image)
  GET  /branding/logo/{filename}        (public — serve a stored logo)

The resolve + serve endpoints are deliberately public — the SPA calls
them on page load before the user authenticates, and the served logos
are referenced from HTML/CSS. The upload endpoint is admin-only and
rate-limited.

Storage: PADHAI_LOGO_DIR env var (default `~/.padhai/logos`). Future
work (E9.1) will route to R2 when S3_BUCKET is set.

Late-imports `web` for the shared globals — same pattern as
orgs_fees.py, orgs_exams.py, parents.py, multipage.py.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


_BRANDING_LOGO_DIR = Path(os.environ.get(
    "PADHAI_LOGO_DIR",
    str(Path.home() / ".padhai" / "logos"),
))


def _branding_to_dict(b) -> dict:
    return {
        "brand_name": b.brand_name,
        "brand_color": b.brand_color,
        "brand_accent": b.brand_accent,
        "brand_logo_url": b.brand_logo_url,
        "brand_subdomain": b.brand_subdomain,
    }


@router.get("/api/branding/resolve")
def resolve_branding_route(request: Request):
    """Public endpoint — the SPA calls this on page load to pick up
    org-specific branding when served on a custom subdomain.

    Returns the platform defaults when there's no subdomain match,
    so the client can always assume a valid response."""
    from .. import web as _web
    host = (request.headers.get("x-forwarded-host") or
            request.url.netloc or "")
    branding = _web._branding.resolve_by_subdomain(host)
    if branding is None:
        branding = _web._branding.platform_default()
    return _branding_to_dict(branding)


@router.post("/api/orgs/{org_id}/branding/logo")
def upload_org_logo_route(
    org_id: str,
    request: Request,
    logo: UploadFile = File(...),
    user: AuthUser | None = Depends(current_user),
):
    """Upload a logo. Stored under PADHAI_LOGO_DIR (local) or R2 when
    S3_BUCKET is configured. URL returned + persisted on the org.

    Size cap: 2 MB. Type: PNG / JPG / SVG / WebP only."""
    from .. import web as _web
    user = _web._require_user(user)
    rate_key = _web._rl.client_ip_from_request(request)
    if not _web._rl.file_upload.try_consume(rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})

    suffix = Path(logo.filename or "logo.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".svg", ".webp"):
        raise HTTPException(400, "logo must be PNG/JPG/SVG/WebP")
    body = logo.file.read()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "logo too large (limit 2 MB)")

    # Filename includes org_id so multiple uploads from the same org
    # don't collide across orgs. We do replace within an org —
    # the latest upload wins; old logos linger on disk for retention.
    _BRANDING_LOGO_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"{org_id}_{int(time.time())}{suffix}"
    out_path = _BRANDING_LOGO_DIR / safe_name
    out_path.write_bytes(body)

    # Build a URL — local serving via /branding/logo; R2 path skipped
    # in v0.17 for scope (E9.1 will add it).
    logo_url = f"/branding/logo/{safe_name}"

    b = _web._branding.update_branding(
        org_id=org_id, brand_logo_url=logo_url,
    )
    return _branding_to_dict(b)


@router.get("/branding/logo/{filename}")
def serve_branding_logo_route(filename: str):
    """Public serving of uploaded logos. Filename is derived
    deterministically from org_id+timestamp, so no path traversal
    even without an extra check — but we validate anyway."""
    # Reject path traversal explicitly
    if "/" in filename or ".." in filename or filename.startswith("."):
        raise HTTPException(400, "invalid filename")
    path = _BRANDING_LOGO_DIR / filename
    if not path.exists():
        raise HTTPException(404, "logo not found")
    media = "image/png"
    if filename.endswith((".jpg", ".jpeg")):
        media = "image/jpeg"
    elif filename.endswith(".svg"):
        media = "image/svg+xml"
    elif filename.endswith(".webp"):
        media = "image/webp"
    return FileResponse(path, media_type=media)
