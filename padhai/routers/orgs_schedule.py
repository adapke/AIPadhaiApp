"""Org schedule router — fifteenth web.py slice.

Four routes covering the per-class timetable + per-user "what's on
today" + per-student assignment history surfaces. They're grouped
because they all answer "what's happening / what has happened in this
class for this user":

  GET  /api/orgs/{org_id}/students/{uid}/history             (assignments)
  GET  /api/orgs/{org_id}/classes/{cid}/timetable            (weekly grid)
  POST /api/orgs/{org_id}/classes/{cid}/timetable            (bulk replace)
  GET  /api/orgs/{org_id}/today                              (today's slots)

Role gates:
- student history: admin/teacher see anyone; students see only their own
- timetable read: any org member
- timetable write: admin or teacher only
- today: any org member (filtered to caller by `_orgs.today_for_user`)

Late-imports `web` for the shared globals — same pattern as
notifications.py, scim.py, branding.py, orgs_exams.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/students/{uid}/history")
def get_student_history_route(
    org_id: str, uid: str,
    user: AuthUser | None = Depends(current_user),
):
    """All assignments + completion state for one student.

    Access rules:
      - admin/teacher: can view any student in the org
      - student: can view ONLY their own history
    """
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    if my_role == "student" and user.id != uid:
        raise HTTPException(
            403, "students may only view their own history",
        )
    return {
        "assignments": _web._orgs.student_assignment_history(
            org_id=org_id, user_id=uid,
        ),
    }


@router.get("/api/orgs/{org_id}/classes/{cid}/timetable")
def get_class_timetable_route(
    org_id: str, cid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Weekly grid for a class. Anyone in the org can read."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(
        org_id, user.id, {"admin", "teacher", "student"},
    )
    return {"slots": _web._orgs.class_timetable(cid)}


@router.post(
    "/api/orgs/{org_id}/classes/{cid}/timetable", status_code=201,
)
def replace_class_timetable_route(
    org_id: str, cid: str,
    slots_json: str = Form(
        ...,
        description=(
            "JSON array of {day_of_week, start_time, end_time, subject, "
            "teacher_user_id?, room?}"
        ),
    ),
    user: AuthUser | None = Depends(current_user),
):
    """Atomic bulk-replace of a class's timetable. Admin or teacher.

    Format: JSON array. day_of_week 1-7 (1=Monday). start_time +
    end_time 'HH:MM'. subject required. teacher_user_id + room optional.

    Returns {added: N, errors: [(row, reason)]} — partial success is
    fine; bad rows are reported individually."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        slots = json.loads(slots_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "slots_json must be valid JSON") from None
    if not isinstance(slots, list):
        raise HTTPException(400, "slots_json must be a JSON array")
    return _web._orgs.replace_class_timetable(
        org_id=org_id, class_id=cid, slots=slots,
    )


@router.get("/api/orgs/{org_id}/today")
def get_today_for_user_route(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """What's on for the current user today. Students see their class
    schedule; teachers see slots they're teaching. Both lists are
    merged + sorted by start_time."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(
        org_id, user.id, {"admin", "teacher", "student"},
    )
    return {
        "slots": _web._orgs.today_for_user(
            org_id=org_id, user_id=user.id,
        ),
    }
