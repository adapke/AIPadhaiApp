"""Learning-modules router — wires HTTP routes for the six core
learner-facing modules that previously existed only as schema +
business logic:

  Essay Grader          POST /api/essay/submit       + listing/grading
  Math Vision           POST /api/math/submit        + extract/validate
  Mock Interview        POST /api/mock/start         + turn / end
  Adaptive Practice     GET  /api/adaptive/pack      + rebalance
  Practice Tests        POST /api/practice/generate  + start/submit
  Live Lectures         GET  /api/live/upcoming      + schedule/join

Every endpoint is auth-gated (current_user → require_user). Routes are
intentionally thin — the heavy lifting stays in the underlying domain
modules so unit tests there continue to exercise the real engine.

Pattern follows padhai/routers/v3.py: APIRouter at module level, lazy
imports inside handlers to keep import cost low when the app boots.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


# ============================================================================
# Essay Grader  (padhai/essay_grader.py)
# ============================================================================

@router.get("/api/essay/rubrics")
def essay_rubrics(
    exam: str | None = Query(None, description="Filter by exam key (upsc_mains, jee_adv_descriptive, ...)"),
    user=Depends(current_user),
):
    from .. import essay_grader as eg
    user = require_user(user)
    rubrics = eg.list_rubrics(exam=exam)
    return {
        "rubrics": [
            {
                "id": r.id, "exam": r.exam, "paper": r.paper,
                "topic": r.topic, "criteria": r.criteria,
                "max_marks": r.max_marks,
                "has_model_answer": bool(r.model_answer),
            }
            for r in rubrics
        ],
        "count": len(rubrics),
    }


@router.get("/api/essay/rubrics/{rubric_id}")
def essay_rubric_detail(rubric_id: str, user=Depends(current_user)):
    from .. import essay_grader as eg
    user = require_user(user)
    r = eg.get_rubric(rubric_id)
    if not r:
        raise HTTPException(404, "rubric not found")
    return {
        "id": r.id, "exam": r.exam, "paper": r.paper, "topic": r.topic,
        "criteria": r.criteria, "max_marks": r.max_marks,
        "model_answer": r.model_answer,
    }


@router.post("/api/essay/submit", status_code=201)
def essay_submit(
    rubric_id: str = Form(...),
    text: str = Form(..., min_length=50, max_length=20000),
    auto_grade: bool = Form(True, description="Run Claude grader immediately"),
    user=Depends(current_user),
):
    from .. import essay_grader as eg
    user = require_user(user)
    try:
        sub = eg.submit(user_id=user.id, rubric_id=rubric_id, text=text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    grade_result = None
    if auto_grade:
        try:
            grade_result = eg.grade(sub.id)
        except ValueError as e:
            raise HTTPException(500, f"grading failed: {e}")
    return {
        "submission_id": sub.id,
        "submitted_at": sub.submitted_at,
        "grade": _grade_to_dict(grade_result) if grade_result else None,
    }


@router.post("/api/essay/submissions/{sid}/grade")
def essay_regrade(sid: str, user=Depends(current_user)):
    """Re-run the AI grader on an existing submission (idempotent —
    overwrites prior ai_score/ai_feedback)."""
    from .. import essay_grader as eg
    user = require_user(user)
    sub = eg.get_submission(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    try:
        result = eg.grade(sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"submission_id": sid, "grade": _grade_to_dict(result)}


@router.get("/api/essay/submissions/{sid}")
def essay_get_submission(sid: str, user=Depends(current_user)):
    from .. import essay_grader as eg
    user = require_user(user)
    sub = eg.get_submission(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    return {
        "id": sub.id, "rubric_id": sub.rubric_id,
        "text": sub.text,
        "ai_score": sub.ai_score,
        "ai_feedback": sub.ai_feedback,
        "human_reviewed": sub.human_reviewed,
        "human_score": sub.human_score,
        "submitted_at": sub.submitted_at,
        "graded_at": sub.graded_at,
    }


@router.get("/api/essay/submissions")
def essay_my_submissions(
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
):
    from .. import essay_grader as eg
    user = require_user(user)
    subs = eg.list_for_user(user.id, limit=limit)
    return {
        "submissions": [
            {
                "id": s.id, "rubric_id": s.rubric_id,
                "ai_score": s.ai_score,
                "human_reviewed": s.human_reviewed,
                "submitted_at": s.submitted_at,
                "graded_at": s.graded_at,
                "preview": s.text[:200],
            }
            for s in subs
        ],
        "count": len(subs),
    }


def _grade_to_dict(g) -> dict:
    return {
        "submission_id": g.submission_id,
        "score": g.score,
        "by_criterion": g.by_criterion,
        "summary": g.summary,
        "suggestions": g.suggestions,
        "method": g.method,
    }


# ============================================================================
# Math Vision  (padhai/math_vision.py)
# ============================================================================

@router.post("/api/math/submit", status_code=201)
def math_submit(
    image_url: str = Form(..., description="Public URL or data: URI to the handwritten math image"),
    expected_language: str = Form("en"),
    auto_extract: bool = Form(True),
    auto_validate: bool = Form(True),
    user=Depends(current_user),
):
    from .. import math_vision as mv
    user = require_user(user)
    try:
        sub = mv.submit(
            user_id=user.id, image_url=image_url,
            expected_language=expected_language,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    extracted = None
    validation = None
    if auto_extract:
        sub = mv.extract(submission_id=sub.id)
        extracted = {
            "steps": sub.steps,
            "extracted_latex": sub.extracted_latex,
            "confidence": sub.confidence,
            "status": sub.status,
            "error": sub.error,
        }
    if auto_validate and sub.status == "extracted":
        v = mv.validate(sub.id)
        validation = _validation_to_dict(v)
    return {
        "submission_id": sub.id,
        "status": sub.status,
        "extracted": extracted,
        "validation": validation,
    }


@router.post("/api/math/{sid}/extract")
def math_extract_only(sid: str, user=Depends(current_user)):
    from .. import math_vision as mv
    user = require_user(user)
    sub = mv.get(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    sub = mv.extract(submission_id=sid)
    return {
        "submission_id": sub.id,
        "steps": sub.steps,
        "extracted_latex": sub.extracted_latex,
        "confidence": sub.confidence,
        "status": sub.status,
        "error": sub.error,
    }


@router.post("/api/math/{sid}/validate")
def math_validate(sid: str, user=Depends(current_user)):
    from .. import math_vision as mv
    user = require_user(user)
    sub = mv.get(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    v = mv.validate(sid)
    return _validation_to_dict(v)


@router.get("/api/math/{sid}")
def math_get(sid: str, user=Depends(current_user)):
    from .. import math_vision as mv
    user = require_user(user)
    sub = mv.get(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    return {
        "id": sub.id, "image_url": sub.image_url,
        "expected_language": sub.expected_language,
        "extracted_latex": sub.extracted_latex,
        "confidence": sub.confidence,
        "steps": sub.steps,
        "validation": sub.validation,
        "status": sub.status,
        "error": sub.error,
        "created_at": sub.created_at,
        "validated_at": sub.validated_at,
    }


@router.get("/api/math/submissions")
def math_my_submissions(
    limit: int = Query(30, ge=1, le=200),
    user=Depends(current_user),
):
    from .. import math_vision as mv
    user = require_user(user)
    subs = mv.list_for_user(user.id, limit=limit)
    return {
        "submissions": [
            {
                "id": s.id, "image_url": s.image_url,
                "status": s.status, "confidence": s.confidence,
                "step_count": len(s.steps) if s.steps else 0,
                "created_at": s.created_at,
            }
            for s in subs
        ],
        "count": len(subs),
    }


def _validation_to_dict(v) -> dict:
    return {
        "submission_id": v.submission_id,
        "overall": v.overall,
        "first_wrong_step": v.first_wrong_step,
        "per_step": v.per_step,
        "method": v.method,
    }


# ============================================================================
# Mock Interview  (padhai/mock_interview.py)
# ============================================================================

@router.post("/api/mock/start", status_code=201)
def mock_start(
    track: str = Form("generic"),
    user=Depends(current_user),
):
    from .. import mock_interview as mi
    user = require_user(user)
    try:
        interview, first_turn = mi.start(user_id=user.id, track=track)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "interview_id": interview.id,
        "track": interview.track,
        "started_at": interview.started_at,
        "current_turn": _turn_to_dict(first_turn),
    }


@router.post("/api/mock/{iid}/turn")
def mock_submit_turn(
    iid: str,
    turn_index: int = Form(...),
    answer_text: str = Form(..., min_length=1, max_length=6000),
    answer_audio_url: str | None = Form(None),
    user=Depends(current_user),
):
    from .. import mock_interview as mi
    user = require_user(user)
    interview = mi.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    try:
        result = mi.submit_answer(
            interview_id=iid, turn_index=turn_index,
            answer_text=answer_text, answer_audio_url=answer_audio_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "interview_id": iid,
        "feedback": result.feedback,
        "next_turn": _turn_to_dict(result.next_turn) if result.next_turn else None,
        "interview_ended": result.interview_ended or result.next_turn is None,
    }


@router.post("/api/mock/{iid}/end")
def mock_end(iid: str, user=Depends(current_user)):
    from .. import mock_interview as mi
    user = require_user(user)
    interview = mi.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    interview = mi.end(interview_id=iid)
    return _interview_to_dict(interview)


@router.get("/api/mock/{iid}")
def mock_get(iid: str, user=Depends(current_user)):
    from .. import mock_interview as mi
    user = require_user(user)
    interview = mi.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    turns = mi.list_turns(iid)
    return {
        **_interview_to_dict(interview),
        "turns": [_turn_to_dict(t) for t in turns],
    }


@router.get("/api/mock/sessions")
def mock_my_sessions(
    limit: int = Query(20, ge=1, le=100),
    user=Depends(current_user),
):
    from .. import mock_interview as mi
    user = require_user(user)
    items = mi.list_for_user(user.id, limit=limit)
    return {
        "interviews": [_interview_to_dict(i) for i in items],
        "count": len(items),
    }


@router.get("/api/mock/tracks")
def mock_tracks(user=Depends(current_user)):
    """Public-ish helper: return the list of supported tracks + their
    opening questions so the UI can preview before starting."""
    from .. import mock_interview as mi
    user = require_user(user)
    return {
        "tracks": [
            {"code": t, "opening_questions": mi.OPENING_QUESTIONS.get(t, [])}
            for t in sorted(mi.VALID_TRACKS)
        ],
    }


def _interview_to_dict(i) -> dict:
    return {
        "id": i.id, "track": i.track, "status": i.status,
        "started_at": i.started_at, "ended_at": i.ended_at,
        "overall_score": i.overall_score,
        "feedback": i.feedback,
        "duration_seconds": i.duration_seconds,
    }


def _turn_to_dict(t) -> dict | None:
    if t is None:
        return None
    return {
        "turn_index": t.turn_index,
        "question_text": t.question_text,
        "answer_text": t.answer_text,
        "feedback": t.feedback,
        "created_at": t.created_at,
        "answered_at": t.answered_at,
    }


# ============================================================================
# Adaptive Practice  (padhai/adaptive_packs.py)
# ============================================================================

@router.get("/api/adaptive/pack/{base_pack_code}")
def adaptive_pack_view(base_pack_code: str, user=Depends(current_user)):
    """Return the per-user personalised topic view of an Exam Pack.
    Topics carry both base + adjusted weightages so the UI can render
    a 'this is your tuning' explanation."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    try:
        topics = ap.personalised_topic_view(
            user_id=user.id, base_pack_code=base_pack_code,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    stale, reason = ap.should_re_adapt(
        user_id=user.id, base_pack_code=base_pack_code,
    )
    return {
        "base_pack_code": base_pack_code,
        "topics": topics,
        "topic_count": len(topics),
        "should_re_adapt": stale,
        "re_adapt_reason": reason,
    }


@router.post("/api/adaptive/pack/{base_pack_code}/rebalance")
def adaptive_pack_rebalance(base_pack_code: str, user=Depends(current_user)):
    """Recompute the personalised weightages from current mastery /
    mock / plan-skip signals. Caller hits this when a major signal
    changes (mock submitted, mastery shifted)."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    try:
        summary = ap.re_adapt(
            user_id=user.id, base_pack_code=base_pack_code,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return summary


@router.get("/api/adaptive/packs")
def adaptive_my_packs(user=Depends(current_user)):
    """All personalised packs this user has — useful for a 'My study
    plans' tab in the dashboard."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    packs = ap.list_user_packs(user.id)
    return {
        "packs": [
            {
                "id": p.id, "base_pack_code": p.base_pack_code,
                "title": p.title, "description": p.description,
                "last_adapted_at": p.last_adapted_at,
                "adaptation_count": p.adaptation_count,
                "created_at": p.created_at,
            }
            for p in packs
        ],
        "count": len(packs),
    }


@router.get("/api/adaptive/pack/{base_pack_code}/signals")
def adaptive_pack_signals(
    base_pack_code: str,
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
):
    """Audit trail — what events drove the most recent adaptation.
    Drives the 'why' panel in the UI ('we boosted geometry because
    mastery dropped to 0.32')."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    pp = ap.get_personalised_pack(
        user_id=user.id, base_pack_code=base_pack_code,
    )
    if not pp:
        return {"signals": [], "count": 0}
    signals = ap.list_signals(pp.id, limit=limit)
    return {
        "signals": [
            {
                "rule_code": s.rule_code,
                "topic_code": s.topic_code,
                "signal_value": s.signal_value,
                "weightage_delta": s.weightage_delta,
                "created_at": s.created_at,
            }
            for s in signals
        ],
        "count": len(signals),
    }


# ============================================================================
# Practice Tests  (padhai/practice_test.py)
# ============================================================================

@router.post("/api/practice/generate", status_code=201)
def practice_generate(
    exam: str = Form(...),
    subject: str = Form(...),
    target_minutes: int = Form(30, ge=5, le=240),
    target_questions: int | None = Form(None),
    user=Depends(current_user),
):
    from .. import practice_test as pt
    user = require_user(user)
    try:
        test = pt.generate(
            user_id=user.id, exam=exam, subject=subject,
            target_minutes=target_minutes,
            target_questions=target_questions,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _practice_to_dict(test, include_questions=True, hide_answers=True)


@router.post("/api/practice/{tid}/start")
def practice_start(tid: str, user=Depends(current_user)):
    from .. import practice_test as pt
    user = require_user(user)
    t = pt.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    pt.start(tid)
    return {"test_id": tid, "started_at": time.time()}


@router.post("/api/practice/{tid}/submit")
def practice_submit(
    tid: str,
    answers: dict[str, str] = Body(..., description="Map of question_id → chosen option letter"),
    user=Depends(current_user),
):
    from .. import practice_test as pt
    user = require_user(user)
    t = pt.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    try:
        score = pt.submit(test_id=tid, answers=answers)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"test_id": tid, "score": score}


@router.get("/api/practice/{tid}")
def practice_get(
    tid: str,
    include_answers: bool = Query(False),
    user=Depends(current_user),
):
    from .. import practice_test as pt
    user = require_user(user)
    t = pt.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    # Once submitted, the user is allowed to see answers (otherwise
    # quiz spoilers leak via the API).
    hide_answers = not (include_answers and t.status == "submitted")
    return _practice_to_dict(t, include_questions=True, hide_answers=hide_answers)


@router.get("/api/practice/tests")
def practice_my_tests(
    limit: int = Query(20, ge=1, le=100),
    user=Depends(current_user),
):
    from .. import practice_test as pt
    user = require_user(user)
    tests = pt.list_for_user(user.id, limit=limit)
    return {
        "tests": [
            _practice_to_dict(t, include_questions=False, hide_answers=True)
            for t in tests
        ],
        "count": len(tests),
    }


def _practice_to_dict(t, *, include_questions: bool, hide_answers: bool) -> dict:
    out = {
        "id": t.id, "exam": t.exam, "subject": t.subject,
        "target_minutes": t.target_minutes,
        "status": t.status,
        "started_at": t.started_at,
        "submitted_at": t.submitted_at,
        "score": t.score,
        "generation_method": t.generation_method,
        "created_at": t.created_at,
        "question_count": len(t.questions),
    }
    if include_questions:
        if hide_answers:
            out["questions"] = [
                {k: v for k, v in q.items() if k != "correct_answer"}
                for q in t.questions
            ]
        else:
            out["questions"] = t.questions
    return out


# ============================================================================
# Live Lectures  (padhai/live_classes.py)
# ============================================================================

@router.get("/api/live/upcoming")
def live_upcoming(
    window_hours: float = Query(168.0, gt=0, le=720.0),
    org_id: str | None = Query(None),
    user=Depends(current_user),
):
    from .. import live_classes as lv
    user = require_user(user)
    items = lv.list_upcoming(org_id=org_id, window_hours=window_hours)
    return {
        "classes": [_live_to_dict(lc) for lc in items],
        "count": len(items),
        "provider": lv.active_provider(),
    }


@router.get("/api/live/provider")
def live_provider_info(user=Depends(current_user)):
    from .. import live_classes as lv
    user = require_user(user)
    return lv.describe()


@router.post("/api/live/schedule", status_code=201)
def live_schedule(
    title: str = Form(..., min_length=1, max_length=200),
    scheduled_at: float = Form(..., description="Unix epoch seconds"),
    duration_min: int = Form(60, ge=5, le=480),
    subject: str | None = Form(None),
    org_id: str | None = Form(None),
    class_id: str | None = Form(None),
    max_attendees: int = Form(200, ge=2, le=5000),
    user=Depends(current_user),
):
    """Schedule a live class. Currently anyone can schedule a stand-alone
    class; org-bound classes still require teacher/admin role in that
    org — we enforce this lazily inside live_classes when the row is
    bound to an org_id."""
    from .. import live_classes as lv
    user = require_user(user)
    if org_id:
        # Org-scoped scheduling needs teacher / admin role
        from ..api_deps import require_org_role
        require_org_role(
            org_id=org_id, user_id=user.id,
            allowed={"teacher", "admin"},
        )
    try:
        lc = lv.schedule(
            teacher_user_id=user.id, title=title,
            scheduled_at=scheduled_at, duration_min=duration_min,
            subject=subject, org_id=org_id, class_id=class_id,
            max_attendees=max_attendees,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _live_to_dict(lc)


@router.get("/api/live/{lc_id}")
def live_get(lc_id: str, user=Depends(current_user)):
    from .. import live_classes as lv
    user = require_user(user)
    lc = lv.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    return _live_to_dict(lc)


@router.post("/api/live/{lc_id}/join")
def live_join(lc_id: str, user=Depends(current_user)):
    """Mint a per-user access token to join the class. Records the
    join event. Token is signed (LiveKit) or HMAC-stub (dev path)."""
    from .. import live_classes as lv
    user = require_user(user)
    lc = lv.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    if lc.status not in ("scheduled", "live"):
        raise HTTPException(409, f"class is {lc.status}; cannot join")
    role = "teacher" if lc.teacher_user_id == user.id else "student"
    try:
        token_payload = lv.issue_access_token(
            live_class_id=lc_id, user_id=user.id, role=role,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    lv.record_join(live_class_id=lc_id, user_id=user.id)
    return {
        "class": _live_to_dict(lc),
        "access": token_payload,
        "role": role,
    }


@router.post("/api/live/{lc_id}/leave")
def live_leave(lc_id: str, user=Depends(current_user)):
    from .. import live_classes as lv
    user = require_user(user)
    lc = lv.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    ok = lv.record_leave(live_class_id=lc_id, user_id=user.id)
    return {"ok": ok}


@router.post("/api/live/{lc_id}/status")
def live_set_status(
    lc_id: str,
    status: str = Form(..., description="scheduled | live | ended | cancelled"),
    user=Depends(current_user),
):
    """Only the teacher (or org admin) can change class status."""
    from .. import live_classes as lv
    user = require_user(user)
    lc = lv.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    if lc.teacher_user_id != user.id:
        if lc.org_id:
            from ..api_deps import require_org_role
            require_org_role(
                org_id=lc.org_id, user_id=user.id,
                allowed={"admin"},
            )
        else:
            raise HTTPException(403, "only the teacher can change status")
    try:
        ok = lv.set_status(live_class_id=lc_id, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok, "status": status}


def _live_to_dict(lc) -> dict:
    return {
        "id": lc.id, "title": lc.title, "subject": lc.subject,
        "teacher_user_id": lc.teacher_user_id,
        "scheduled_at": lc.scheduled_at,
        "duration_min": lc.duration_min,
        "max_attendees": lc.max_attendees,
        "provider": lc.provider,
        "room_id": lc.room_id,
        "recording_url": lc.recording_url,
        "status": lc.status,
        "started_at": lc.started_at, "ended_at": lc.ended_at,
        "org_id": lc.org_id, "class_id": lc.class_id,
        "created_at": lc.created_at,
    }
