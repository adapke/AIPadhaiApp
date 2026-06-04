"""Org core CRUD router — fifth web.py slice.

Six endpoints today (the cohesive "org lifecycle" subset):
  GET   /api/orgs/me                    — orgs I belong to
  POST  /api/orgs                       — create one (caller = owner)
  GET   /api/orgs/{id}                  — detail + stats + my_role
  GET   /api/orgs/{id}/members          — roster
  POST  /api/orgs/{id}/members          — invite by email
  POST  /api/orgs/{id}/roster           — bulk CSV import

The 30+ other /api/orgs/* endpoints (classes / assignments /
attendance / fees / exams / branding / notifications) stay in
web.py for now — they're distinct subsystems that deserve their
own router each, not one giant orgs router. This slice covers the
"org as a record + roster" core that the SPA touches on first
visit.

Late-imports `web` for the shared globals (_orgs, _audit, _rl,
_require_user, _org_or_404, _require_org_role, _org_to_dict).
Same pattern as the other extracted routers.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/me")
def list_my_orgs(user: AuthUser | None = Depends(current_user)):
    """Orgs the current user belongs to (any role). Returns an empty
    list for anonymous or unaffiliated users — the UI uses that to
    show the 'Create your school' first-time form."""
    from .. import web as _web
    if user is None:
        return {"orgs": []}
    out = [_web._org_to_dict(o) for o in _web._orgs.find_orgs_for_user(user.id)]
    return {"orgs": out}


@router.post("/api/orgs", status_code=201)
def create_my_org(
    name: str = Form(..., min_length=2, max_length=120),
    kind: str = Form("school"),
    board: str | None = Form(None),
    city: str | None = Form(None),
    contact_email: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create an organisation. The caller becomes the owner + first
    admin member automatically."""
    from .. import web as _web
    user = _web._require_user(user)
    try:
        org = _web._orgs.create_org(
            name=name, kind=kind, owner_user_id=user.id,
            board=board, city=city, contact_email=contact_email,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _web._org_to_dict(org)


@router.get("/api/orgs/{org_id}")
def get_org_detail(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    from .. import web as _web
    user = _web._require_user(user)
    org = _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    return {
        "org": _web._org_to_dict(org),
        "stats": _web._orgs.org_stats(org_id),
        "my_role": _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id),
    }


@router.get("/api/orgs/{org_id}/members")
def list_org_members(
    org_id: str,
    role: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    members = _web._orgs.list_members(org_id, role=role, limit=limit)
    return {
        "members": [
            {
                "id": m.id, "user_id": m.user_id,
                "invited_email": m.invited_email,
                "role": m.role, "class_id": m.class_id,
                "display_name": m.display_name, "joined_at": m.joined_at,
            }
            for m in members
        ],
    }


@router.post("/api/orgs/{org_id}/members", status_code=201)
def add_org_member(
    org_id: str,
    request: Request,
    email: str = Form(..., min_length=4),
    role: str = Form("student"),
    class_id: str | None = Form(None),
    display_name: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})
    try:
        m = _web._orgs.add_member(
            org_id=org_id, role=role, invited_email=email,
            class_id=class_id, display_name=display_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    _web._audit.record(
        action="org.member.invite",
        org_id=org_id, actor_user_id=user.id,
        target_type="org_member", target_id=m.id,
        after={"role": m.role, "email": m.invited_email,
               "class_id": m.class_id, "display_name": m.display_name},
        **_web._audit.actor_from_request(request),
    )
    return {
        "id": m.id, "invited_email": m.invited_email, "role": m.role,
        "class_id": m.class_id, "display_name": m.display_name,
    }


@router.post("/api/orgs/{org_id}/roster", status_code=201)
def upload_org_roster(
    org_id: str,
    request: Request,
    csv: UploadFile = File(...),
    user: AuthUser | None = Depends(current_user),
):
    """Bulk CSV roster import. Required column: email. Optional: name,
    role, class. New classes referenced by name are auto-created.
    Returns counts so the UI can show 'imported N, skipped M'."""
    from .. import web as _web
    user = _web._require_user(user)
    _rate_key = _web._rl.client_ip_from_request(request)
    if not _web._rl.file_upload.try_consume(_rate_key):
        raise HTTPException(429, "too many uploads — slow down")
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})
    suffix = Path(csv.filename or "roster.csv").suffix.lower()
    if suffix not in (".csv", ".tsv", ".txt"):
        raise HTTPException(400, "roster must be a CSV/TSV file")
    body = csv.file.read()
    if len(body) > 2 * 1024 * 1024:
        raise HTTPException(413, "CSV too large (limit 2 MB)")
    return _web._orgs.import_roster_csv(org_id=org_id, csv_bytes=body)
