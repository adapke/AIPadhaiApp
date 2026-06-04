"""Org assignments router — ninth web.py slice.

Four endpoints covering the assignments + per-student completion API:
  GET  /api/orgs/{org_id}/assignments               (list)
  POST /api/orgs/{org_id}/assignments               (create)
  POST /api/orgs/{org_id}/assignments/{aid}/completion  (student beacon)
  GET  /api/orgs/{org_id}/assignments/{aid}/stats   (class rollup)

The completion-beacon POST is intentionally permissive for students —
they can only write their own row (enforced by passing `user.id` to
`_orgs.record_completion`), but the role gate allows `student` because
this is how the watch-progress beacon updates state. Admin and teacher
can also POST completions (e.g., manual corrections from the dashboard).

Late-imports `web` for the shared globals — same pattern as
orgs_attendance.py, orgs_classes.py, parents.py, multipage.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/assignments")
def list_org_assignments_route(
    org_id: str,
    class_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """List assignments in an org (optionally filtered by class).
    Admin / teacher / student can all read."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    items = _web._orgs.list_assignments(org_id, class_id=class_id, limit=limit)
    return {
        "assignments": [
            {
                "id": a.id, "class_id": a.class_id, "title": a.title,
                "topic": a.topic, "language": a.language, "level": a.level,
                "due_date": a.due_date, "notes": a.notes,
                "created_at": a.created_at,
            }
            for a in items
        ],
    }


@router.post("/api/orgs/{org_id}/assignments", status_code=201)
def create_org_assignment_route(
    org_id: str,
    class_id: str = Form(...),
    title: str = Form(..., min_length=2, max_length=120),
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    due_date: str | None = Form(None),
    notes: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create an assignment. Admin + teacher only."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    a = _web._orgs.create_assignment(
        org_id=org_id, class_id=class_id, title=title, topic=topic,
        language=language, level=level, due_date=due_date, notes=notes,
        created_by=user.id,
    )
    return {
        "id": a.id, "title": a.title, "topic": a.topic,
        "language": a.language, "level": a.level, "due_date": a.due_date,
        "class_id": a.class_id,
    }


@router.post("/api/orgs/{org_id}/assignments/{aid}/completion")
def post_completion_route(
    org_id: str, aid: str,
    watch_pct: int | None = Form(None, ge=0, le=100),
    quiz_score: int | None = Form(None, ge=0, le=100),
    quiz_attempt: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Student progress beacon — called by the player every ~30s
    (timeupdate) and once at quiz finish. Idempotent on (aid, user).

    Students can only write their own completions; admins/teachers
    cannot fake progress for a student (use the grading API for that)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    c = _web._orgs.record_completion(
        assignment_id=aid, user_id=user.id,
        watch_pct=watch_pct, quiz_score=quiz_score,
        quiz_attempt=quiz_attempt,
    )
    return {
        "assignment_id": c.assignment_id, "watch_pct": c.watch_pct,
        "quiz_score": c.quiz_score, "quiz_attempts": c.quiz_attempts,
        "watched_at": c.watched_at, "updated_at": c.updated_at,
    }


@router.get("/api/orgs/{org_id}/assignments/{aid}/stats")
def get_assignment_stats_route(
    org_id: str, aid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Per-assignment class rollup. Admin or teacher only — students
    don't see other students' scores."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    # Look up class_id from the assignment
    assignments = _web._orgs.list_assignments(org_id)
    a = next((x for x in assignments if x.id == aid), None)
    if a is None:
        raise HTTPException(404, "assignment not found")
    return _web._orgs.assignment_class_stats(aid, a.class_id)
