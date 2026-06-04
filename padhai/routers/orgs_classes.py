"""Org classes router — sixth web.py slice.

Two endpoints today (the core CRUD for class lists):
  GET   /api/orgs/{org_id}/classes
  POST  /api/orgs/{org_id}/classes

Scope deliberately narrow. The class subsystem also has attendance
(`/classes/{cid}/attendance` ×3), timetable (`/classes/{cid}/timetable`
×2), and leaderboard (`/classes/{class_id}/leaderboard`) endpoints
— 6 more routes scattered across web.py at lines 12200+, 12270+,
12300+, 13186+. They're not adjacent to these two so lifting them
together would mean reading + diffing four separate slabs.

Pick those up as their own router slices later, each named for the
subsystem (orgs_attendance.py, orgs_timetable.py, orgs_leaderboard.py).

Late-imports `web` for the shared globals — same pattern as
orgs_api.py, parents.py, multipage.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/classes")
def list_org_classes_route(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """List classes within an org. Admin / teacher / student can all
    read; non-members get 403."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    classes = _web._orgs.list_classes(org_id, limit=limit)
    return {
        "classes": [
            {
                "id": c.id, "name": c.name,
                "grade_level": c.grade_level, "section": c.section,
                "created_at": c.created_at,
            }
            for c in classes
        ],
    }


@router.post("/api/orgs/{org_id}/classes", status_code=201)
def create_org_class_route(
    org_id: str,
    name: str = Form(..., min_length=1),
    grade_level: str | None = Form(None),
    section: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create a class within an org. Admin + teacher only."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        c = _web._orgs.add_class(
            org_id=org_id, name=name,
            grade_level=grade_level, section=section,
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"id": c.id, "name": c.name, "grade_level": c.grade_level,
            "section": c.section}
