"""Org exams router — eleventh web.py slice.

Six endpoints covering the school-exam subsystem (create, distribute,
take, submit, review, manually grade):
  POST /api/orgs/{org_id}/exams                          (create)
  GET  /api/orgs/{org_id}/exams                          (list)
  POST /api/orgs/{org_id}/exams/{eid}/begin              (student start)
  POST /api/orgs/{org_id}/exams/{eid}/submit             (student submit)
  GET  /api/orgs/{org_id}/exams/{eid}/attempts           (teacher review)
  POST /api/orgs/{org_id}/exams/{eid}/attempts/{aid}/grade  (manual override)

Anti-cheat counters (tab-blur, fullscreen exit, flag events) flow
through `submit`. The `grade` endpoint is high-trust — it overrides
auto-scoring and is recorded in the audit log because manual override
is a known fraud vector in school deployments.

The companion `/api/exam-mode/active` (anti-cheat status surface) stays
in web.py — it's a top-level endpoint, not under `/api/orgs/`.

`_exam_to_dict` and `_attempt_to_dict` helpers lifted with the routes
since this is their only call site.

Late-imports `web` for the shared globals — same pattern as
orgs_fees.py, orgs_assignments.py, orgs_attendance.py, parents.py.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


def _exam_to_dict(e, *, include_answers: bool = False) -> dict:
    """Serialize an exam for API responses. When `include_answers=False`
    we strip the `answer` field from each question — that's the
    student-facing view (no peeking at the correct answer)."""
    questions = []
    for q in e.questions:
        clean = dict(q)
        if not include_answers:
            clean.pop("answer", None)
        questions.append(clean)
    return {
        "id": e.id, "org_id": e.org_id, "class_id": e.class_id,
        "title": e.title, "subject": e.subject, "topic": e.topic,
        "scheduled_at": e.scheduled_at, "duration_min": e.duration_min,
        "max_marks": e.max_marks, "status": e.status,
        "questions": questions, "created_at": e.created_at,
    }


def _attempt_to_dict(a) -> dict:
    return {
        "id": a.id, "exam_id": a.exam_id, "user_id": a.user_id,
        "started_at": a.started_at, "submitted_at": a.submitted_at,
        "answers": a.answers, "auto_score": a.auto_score,
        "manual_score": a.manual_score, "total_score": a.total_score,
        "feedback": a.feedback,
        "tab_blur_count": a.tab_blur_count,
        "fullscreen_exit_count": a.fullscreen_exit_count,
        "flags": a.flags,
    }


@router.post("/api/orgs/{org_id}/exams", status_code=201)
def create_org_exam_route(
    org_id: str,
    class_id: str = Form(...),
    title: str = Form(..., min_length=2, max_length=160),
    topic: str = Form(..., min_length=2, max_length=200),
    questions_json: str = Form(
        ..., description="JSON array of {q, options:{A,B,C,D}, answer, marks, kind}",
    ),
    duration_min: int = Form(30, ge=1, le=480),
    subject: str | None = Form(None),
    scheduled_at: float | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create an exam. The teacher provides the question set (in v0.15.1
    the UI will offer "AI-generate from topic" via the existing quiz
    generator, but that needs Claude in production — see padhai/pedagogy.py).

    Returns the exam with answers INCLUDED (teacher view)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        questions = json.loads(questions_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "questions_json must be valid JSON") from None
    if not isinstance(questions, list):
        raise HTTPException(400, "questions_json must be a JSON array")
    try:
        exam = _web._orgs.create_exam(
            org_id=org_id, class_id=class_id, title=title, topic=topic,
            questions=questions, duration_min=duration_min,
            subject=subject, scheduled_at=scheduled_at,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _exam_to_dict(exam, include_answers=True)


@router.get("/api/orgs/{org_id}/exams")
def list_org_exams_route(
    org_id: str,
    class_id: str | None = None,
    user: AuthUser | None = Depends(current_user),
):
    """List exams. Students see the exam list WITHOUT answers; admins
    and teachers see everything."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    exams = _web._orgs.list_exams(org_id, class_id=class_id)
    # Students see exams WITHOUT correct answers; teachers see all.
    include_answers = my_role in ("admin", "teacher")
    return {
        "exams": [
            _exam_to_dict(e, include_answers=include_answers)
            for e in exams
        ],
    }


@router.post("/api/orgs/{org_id}/exams/{eid}/begin")
def begin_exam_route(
    org_id: str, eid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Student starts the exam — locks in the start_time so the timer
    can't be reset by refreshing. Idempotent: re-begin returns the
    same started_at, not a new clock."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"student", "admin", "teacher"})
    exam = _web._orgs.get_exam(eid)
    if not exam or exam.org_id != org_id:
        raise HTTPException(404, "exam not found")
    attempt = _web._orgs.begin_attempt(exam_id=eid, user_id=user.id)
    return {
        "attempt": _attempt_to_dict(attempt),
        "exam": _exam_to_dict(exam, include_answers=False),
        "deadline_at": attempt.started_at + exam.duration_min * 60,
    }


@router.post("/api/orgs/{org_id}/exams/{eid}/submit")
def submit_exam_route(
    org_id: str, eid: str,
    answers_json: str = Form(
        ..., description='JSON object: {question_index_str: answer}',
    ),
    tab_blur_count: int = Form(0, ge=0),
    fullscreen_exit_count: int = Form(0, ge=0),
    flags_json: str = Form(
        "[]", description="JSON array of {t, at} anti-cheat event records",
    ),
    user: AuthUser | None = Depends(current_user),
):
    """Student submits. Auto-grades MCQs immediately; free-form
    questions wait for teacher grading. Anti-cheat counters captured."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"student", "admin", "teacher"})
    try:
        answers = json.loads(answers_json)
        flags = json.loads(flags_json or "[]")
    except (ValueError, TypeError):
        raise HTTPException(
            400, "answers_json / flags_json must be valid JSON",
        ) from None
    try:
        attempt = _web._orgs.submit_attempt(
            exam_id=eid, user_id=user.id, answers=answers,
            tab_blur_count=tab_blur_count,
            fullscreen_exit_count=fullscreen_exit_count,
            flags=flags,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return _attempt_to_dict(attempt)


@router.get("/api/orgs/{org_id}/exams/{eid}/attempts")
def list_exam_attempts_route(
    org_id: str, eid: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """All attempts on this exam — teacher review surface."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    attempts = _web._orgs.list_attempts(eid, limit=limit)
    return {"attempts": [_attempt_to_dict(a) for a in attempts]}


@router.post("/api/orgs/{org_id}/exams/{eid}/attempts/{aid}/grade")
def grade_exam_attempt_route(
    org_id: str, eid: str, aid: str,
    request: Request,
    manual_score: int = Form(..., ge=0),
    feedback: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Teacher posts manual marks (for free-form questions). Recomputes
    total_score = auto + manual.

    High-value audit log entry — manual grade override is a known fraud
    vector in school deployments."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    try:
        attempt = _web._orgs.grade_attempt(
            attempt_id=aid, manual_score=manual_score, feedback=feedback,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    _web._audit.record(
        action="org.exam.grade.override",
        org_id=org_id, actor_user_id=user.id,
        target_type="exam_attempt", target_id=aid,
        after={"exam_id": eid, "manual_score": manual_score,
               "feedback": feedback, "total_score": attempt.total_score},
        **_web._audit.actor_from_request(request),
    )
    return _attempt_to_dict(attempt)
