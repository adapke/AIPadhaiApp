"""Parent <-> child linking router — fourth web.py slice.

Endpoints:
  POST   /api/parents/link               — invite the other party
  POST   /api/parents/link/{id}/revoke   — either side can revoke
  GET    /api/parents/children           — my linked children
  GET    /api/parents/me/parents         — my linked parents
  GET    /api/parents/children/{uid}/stats — parent-view progress

Late-imports `web` for the user repo + notification helper + the
private `_link_to_dict` / `_compute_user_stats` helpers. Crossing
the private-name boundary deliberately — those helpers will move to
their own modules in a later cleanup.

The companion `GET /auth/parent-link/verify` page stays in web.py
because it's an HTML response that uses the shared
`_consent_result_page` template; lifting it would mean lifting that
template too.
"""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.post("/api/parents/link", status_code=201)
def create_parent_link(
    request: Request,
    other_email: str = Form(..., description="email of the other party (child if you're a parent; parent if you're a child)"),
    role: str = Form(..., description="'parent' or 'child' — your role in this link"),
    relation: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create a pending parent <-> child link. The other party must
    verify via /auth/parent-link/verify?t=<token> before the link is
    activated."""
    from .. import web as _web
    user = _web._require_user(user)
    if role not in ("parent", "child"):
        raise HTTPException(400, "role must be 'parent' or 'child'")
    if _web._get_user_repo() is None:
        raise HTTPException(503, "auth not configured — restart the server")
    other = _web._get_user_repo().find_by_email(other_email)
    if not other:
        raise HTTPException(404, f"no AI Pathshala account for {other_email}")
    other_user, _ = other

    if role == "parent":
        parent_uid, child_uid = user.id, other_user.id
        initiated_by = "parent"
    else:
        parent_uid, child_uid = other_user.id, user.id
        initiated_by = "child"

    try:
        link, token = _web._parents.invite(
            parent_user_id=parent_uid,
            child_user_id=child_uid,
            initiated_by=initiated_by,
            relation=relation,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))

    # Queue an in-app notification for the OTHER party. Best-effort:
    # notification failure shouldn't block the link creation, the
    # verify URL is still in the response.
    with contextlib.suppress(Exception):
        _web._notifs.create(
            org_id="parent-link",
            audience=f"user:{other_user.id}",
            kind="system",
            title=(
                "Verify parent link" if initiated_by == "parent"
                else "Confirm child link"
            ),
            body=(
                f"{user.email} wants to link as your {role}. "
                "Click to verify."
            ),
            link_url=str(request.url_for("parent_link_verify")) + f"?t={token}",
            sent_by=user.id,
        )

    verify_url = str(request.url_for("parent_link_verify")) + f"?t={token}"
    _web._log.info("[parent_link] verify URL for link %s: %s", link.id, verify_url)
    return {
        **_web._link_to_dict(link),
        "audience": "child" if initiated_by == "parent" else "parent",
    }


@router.post("/api/parents/link/{link_id}/revoke")
def revoke_parent_link(
    link_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Either side can revoke. Audit trail stays (status='revoked',
    revoked_at, revoked_by)."""
    from .. import web as _web
    user = _web._require_user(user)
    try:
        link = _web._parents.revoke(link_id=link_id, acting_user_id=user.id)
    except ValueError as e:
        raise HTTPException(403, str(e))
    return _web._link_to_dict(link)


@router.get("/api/parents/children")
def list_my_children(user: AuthUser | None = Depends(current_user)):
    """All children linked to the current user (any status except revoked)."""
    from .. import web as _web
    user = _web._require_user(user)
    links = _web._parents.children_of(user.id)
    out = []
    for link in links:
        child_email = None
        repo = _web._get_user_repo()
        if repo is not None:
            child = repo.find_by_id(link.child_user_id)
            child_email = child.email if child else None
        out.append({**_web._link_to_dict(link), "child_email": child_email})
    return {"links": out}


@router.get("/api/parents/me/parents")
def list_my_parents(user: AuthUser | None = Depends(current_user)):
    """For a child user: which parents are linked to me."""
    from .. import web as _web
    user = _web._require_user(user)
    links = _web._parents.parents_of(user.id)
    out = []
    for link in links:
        parent_email = None
        repo = _web._get_user_repo()
        if repo is not None:
            p = repo.find_by_id(link.parent_user_id)
            parent_email = p.email if p else None
        out.append({**_web._link_to_dict(link), "parent_email": parent_email})
    return {"links": out}


@router.get("/api/parents/children/{child_user_id}/stats")
def get_child_stats(
    child_user_id: str,
    days: int = 7,
    user: AuthUser | None = Depends(current_user),
):
    """Parent-only view of a child's progress. Same shape as /me/stats
    but requires verified parent_link first."""
    from .. import web as _web
    user = _web._require_user(user)
    if not _web._parents.is_verified_parent_of(user.id, child_user_id):
        raise HTTPException(
            403,
            "you are not a verified parent of this user. "
            "Use POST /api/parents/link to invite + the child must verify.",
        )
    return _web._compute_user_stats(child_user_id, days)
