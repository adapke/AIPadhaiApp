"""Org attendance router — eighth web.py slice.

Four endpoints covering the daily-roll + per-student attendance API:
  GET  /api/orgs/{org_id}/classes/{cid}/attendance              (one date)
  POST /api/orgs/{org_id}/classes/{cid}/attendance              (bulk mark)
  GET  /api/orgs/{org_id}/students/{uid}/attendance             (per-student)
  GET  /api/orgs/{org_id}/classes/{cid}/attendance/summary      (range rollup)

Role gates differ across the four — daily-roll filters to the
student's own row when the caller is a student; bulk-mark and the
range summary are admin/teacher only; per-student lets students see
their own history. The student-row filter is done in this router
(not delegated to `_orgs.class_attendance_for_date`) because the
underlying SQL returns the whole roster — the gate is policy, not
storage.

Late-imports `web` for the shared globals — same pattern as
orgs_leaderboard.py, orgs_classes.py, parents.py, multipage.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/classes/{cid}/attendance")
def get_class_attendance_route(
    org_id: str, cid: str,
    date: str,
    user: AuthUser | None = Depends(current_user),
):
    """Daily class roll for one date. Students appear in the response
    even if not yet marked (status: null) so the teacher UI can render
    the full roster.

    Access: admin, teacher (any), or the student themselves seeing
    only their own row."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    roll = _web._orgs.class_attendance_for_date(cid, date)
    if my_role == "student":
        # Students see only their own row
        roll = [r for r in roll if r["user_id"] == user.id]
    return {"date": date, "class_id": cid, "students": roll}


@router.post(
    "/api/orgs/{org_id}/classes/{cid}/attendance", status_code=201,
)
def post_class_attendance_route(
    org_id: str, cid: str,
    records_json: str = Form(
        ..., description='JSON array of {user_id, date, status, notes?}',
    ),
    user: AuthUser | None = Depends(current_user),
):
    """Bulk-mark attendance. Teachers + admins only."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        records = json.loads(records_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "records_json must be valid JSON") from None
    if not isinstance(records, list):
        raise HTTPException(400, "records_json must be a JSON array")
    return _web._orgs.mark_attendance(
        org_id=org_id, class_id=cid,
        marked_by=user.id, records=records,
    )


@router.get("/api/orgs/{org_id}/students/{uid}/attendance")
def get_student_attendance_route(
    org_id: str, uid: str,
    from_date: str | None = None, to_date: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    """Per-student attendance record. Admin/teacher see anyone;
    students see only their own (parents inherit via E8 — not yet)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    if my_role == "student" and user.id != uid:
        raise HTTPException(403, "students may only view their own attendance")
    return {
        "user_id": uid,
        "records": _web._orgs.student_attendance_history(
            org_id=org_id, user_id=uid,
            from_date=from_date, to_date=to_date,
        ),
    }


@router.get("/api/orgs/{org_id}/classes/{cid}/attendance/summary")
def get_class_attendance_summary_route(
    org_id: str, cid: str,
    from_date: str | None = None, to_date: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    """Per-student rollup over a date range. Admin/teacher only."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    return _web._orgs.class_attendance_summary(
        class_id=cid, from_date=from_date, to_date=to_date,
    )
