"""v2.1 — first v3-roadmap release.

L1 tutor sessions, L6 LLM observability, Q1 feature flags.

Public reads + auth-gated writes coexist here because each subsystem
is small enough not to need its own router. We'll split if any one
of them grows past ~10 endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ..api_deps import require_user
from ..web import current_user


router = APIRouter()


# ---------- L1: AI tutor sessions ----------

@router.post("/api/tutor/sessions", status_code=201)
def tutor_start(user=Depends(current_user)):
    """Start a new tutor session. Hydrates context_summary from the
    user's long memory + last 2 ended sessions."""
    from .. import feature_flags, tutor
    user = require_user(user)
    # Gate behind the tutor.enabled feature flag only when the flag exists.
    # If the flag doesn't exist in DB (fresh deploy) we allow all users through.
    flag = feature_flags.get("tutor.enabled")
    if flag is not None and not feature_flags.is_enabled("tutor.enabled", user_id=user.id):
        raise HTTPException(403, "AI tutor not yet rolled out to your account")
    s = tutor.start_session(user_id=user.id)
    return {
        "session_id": s.id,
        "started_at": s.started_at,
        "context_summary": s.context_summary,
        "tutor_available": tutor.is_available(),
    }


@router.post("/api/tutor/sessions/{sid}/message")
def tutor_message(
    sid: str,
    text: str = Form(..., min_length=1, max_length=4000),
    upload_ids: str | None = Form(
        None,
        description=(
            "Comma-separated upload ids to ground the answer in. "
            "When set, the tutor retrieves from those uploads and "
            "returns citations alongside the reply."
        ),
    ),
    auto_ground: bool = Form(
        False,
        description=(
            "When true and no upload_ids supplied, the tutor "
            "auto-pulls from the user's 3 most recent indexed "
            "uploads. Lets the UI offer a 'use my notes' toggle "
            "without forcing the student to pick files."
        ),
    ),
    user=Depends(current_user),
):
    """Send a user message + get an assistant reply.

    Source grounding (v3.x): pass upload_ids (comma-separated) or
    auto_ground=true to RAG over the student's indexed uploads. The
    response will include a `citations` array.
    """
    from .. import tutor
    user = require_user(user)
    s = tutor.get_session(sid)
    if not s:
        raise HTTPException(404, "session not found")
    if s.user_id != user.id:
        raise HTTPException(403, "not your session")
    parsed_upload_ids: list[str] = []
    if upload_ids:
        parsed_upload_ids = [
            uid.strip() for uid in upload_ids.split(",") if uid.strip()
        ]
        # Authorise — every upload must belong to this user
        if parsed_upload_ids:
            from .. import uploads as _up
            for uid in parsed_upload_ids:
                u = _up.get(uid)
                if not u:
                    raise HTTPException(404, f"upload {uid!r} not found")
                if u.user_id and u.user_id != user.id:
                    raise HTTPException(403, f"upload {uid!r} not yours")
    try:
        result = tutor.send_message(
            sid=sid, user_text=text,
            user_tier=getattr(user, "subscription_tier", "M2") or "M2",
            upload_ids=parsed_upload_ids or None,
            auto_ground=auto_ground,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "session_id": result.session_id,
        "reply": result.reply,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_inr_paise": result.cost_inr_paise,
        "cached": result.cached,
        "over_budget": result.over_budget,
        "grounded": result.grounded,
        "citations": list(result.citations),
    }


@router.get("/api/tutor/sessions/{sid}")
def tutor_get(sid: str, user=Depends(current_user)):
    from .. import tutor
    user = require_user(user)
    s = tutor.get_session(sid)
    if not s:
        raise HTTPException(404, "session not found")
    if s.user_id != user.id:
        raise HTTPException(403, "not your session")
    return {
        "id": s.id, "started_at": s.started_at, "ended_at": s.ended_at,
        "messages": s.messages, "tokens_in": s.tokens_in,
        "tokens_out": s.tokens_out, "cost_inr_paise": s.cost_inr_paise,
        "resolved": s.resolved,
    }


@router.post("/api/tutor/sessions/{sid}/end")
def tutor_end(
    sid: str,
    resolved: bool = Form(True),
    user=Depends(current_user),
):
    from .. import tutor
    user = require_user(user)
    s = tutor.get_session(sid)
    if not s:
        raise HTTPException(404, "session not found")
    if s.user_id != user.id:
        raise HTTPException(403, "not your session")
    tutor.end_session(sid=sid, resolved=resolved)
    return {"ok": True}


@router.get("/api/tutor/sessions")
def tutor_list(
    limit: int = 20,
    user=Depends(current_user),
):
    from .. import tutor
    user = require_user(user)
    rows = tutor.list_sessions_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": s.id, "started_at": s.started_at,
             "ended_at": s.ended_at, "resolved": s.resolved,
             "message_count": len(s.messages),
             "tokens_out": s.tokens_out,
             "cost_inr_paise": s.cost_inr_paise}
            for s in rows
        ],
    }


# ---------- L6: LLM observability ----------

@router.get("/api/admin/llm/stats")
def llm_stats(hours: float = 24.0):
    """Aggregate Anthropic usage. Public-ish (counts + costs only,
    no prompts or PII)."""
    from .. import llm_obs
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be in (0, 720]")
    return llm_obs.stats_for_period(hours=hours)


@router.post("/api/llm/calls/{call_id}/flag")
def llm_flag_call(
    call_id: str,
    reason: str = Form(..., min_length=4, max_length=500),
    severity: str = Form("medium"),
    note: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    """User or teacher reports a wrong / hallucinated response."""
    from .. import llm_obs
    user = require_user(user)
    try:
        fid = llm_obs.flag_call(
            llm_call_id=call_id,
            reporter_user_id=user.id,
            reason=reason, severity=severity, note=note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"flag_id": fid}


@router.get("/api/admin/llm/flags")
def llm_flag_queue(limit: int = 50):
    """Reviewer queue. Highest severity first, then oldest."""
    from .. import llm_obs
    return {"rows": llm_obs.pending_flags(limit=limit)}


# ---------- Q1: feature flags ----------

@router.get("/api/me/flags")
def my_flags(user=Depends(current_user)):
    """Snapshot of every flag's resolved state for THIS user. SPA
    seeds its client-side flag cache from this."""
    from .. import feature_flags
    user = require_user(user)
    role = getattr(user, "role", None)
    return {
        "flags": feature_flags.resolve_all(user_id=user.id, role=role),
    }


@router.get("/api/admin/flags")
def list_flags(user=Depends(current_user)):
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    from .. import feature_flags
    return {
        "rows": [
            {"flag_key": f.flag_key, "description": f.description,
             "enabled_default": f.enabled_default,
             "rollout_pct": f.rollout_pct,
             "target_user_ids": f.target_user_ids,
             "target_org_ids": f.target_org_ids,
             "target_roles": f.target_roles,
             "variants": f.variants,
             "updated_at": f.updated_at}
            for f in feature_flags.list_all()
        ],
    }


@router.post("/api/admin/flags")
def upsert_flag_endpoint(
    flag_key: str = Form(..., min_length=1, max_length=120),
    description: str | None = Form(None, max_length=500),
    enabled_default: bool = Form(False),
    rollout_pct: int = Form(0, ge=0, le=100),
    user=Depends(current_user),
):
    """Admin upsert — requires is_admin (same gate as DELETE)."""
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    from .. import feature_flags
    try:
        f = feature_flags.upsert(
            flag_key=flag_key, description=description,
            enabled_default=enabled_default, rollout_pct=rollout_pct,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "flag_key": f.flag_key, "rollout_pct": f.rollout_pct,
        "enabled_default": f.enabled_default,
    }


@router.delete("/api/admin/flags/{flag_key}")
def delete_flag_endpoint(flag_key: str, user=Depends(current_user)):
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    from .. import feature_flags
    ok = feature_flags.delete(flag_key)
    if not ok:
        raise HTTPException(404, "flag not found")
    return {"ok": True}


@router.get("/api/admin/flags/{flag_key}/exposures")
def flag_exposures(flag_key: str, hours: float = 168.0):
    """A/B test exposure stats. 10% sampling; 7-day default window."""
    from .. import feature_flags
    if hours <= 0 or hours > 24 * 90:
        raise HTTPException(400, "hours must be in (0, 2160]")
    return feature_flags.exposure_stats(flag_key, hours=hours)


# ---------- L2: essay grader ----------

@router.get("/api/essay/rubrics")
def list_essay_rubrics(exam: str | None = None):
    """Public catalog of essay rubrics. UI uses this to populate the
    'pick a paper' dropdown when a student opens the essay practice
    surface."""
    from .. import essay_grader
    rows = essay_grader.list_rubrics(exam=exam)
    return {
        "rows": [
            {"id": r.id, "exam": r.exam, "paper": r.paper,
             "topic": r.topic, "criteria": r.criteria,
             "max_marks": r.max_marks,
             "has_model_answer": r.model_answer is not None}
            for r in rows
        ],
    }


@router.post("/api/essay/rubrics", status_code=201)
def upsert_essay_rubric(
    exam: str = Form(..., max_length=64),
    paper: str = Form(..., max_length=64),
    topic: str | None = Form(None, max_length=200),
    criteria_json: str = Form(..., max_length=8000),
    max_marks: int = Form(..., ge=1, le=1000),
    model_answer: str | None = Form(None, max_length=20000),
    user=Depends(current_user),
):
    """Teacher uploads or edits a rubric. Idempotent on
    (exam, paper, topic)."""
    from .. import essay_grader
    import json as _json
    user = require_user(user)
    try:
        criteria = _json.loads(criteria_json)
    except (ValueError, TypeError):
        raise HTTPException(400, "criteria_json must be valid JSON")
    try:
        r = essay_grader.upsert_rubric(
            exam=exam, paper=paper, topic=topic,
            criteria=criteria, max_marks=max_marks,
            model_answer=model_answer, created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": r.id, "exam": r.exam, "paper": r.paper,
        "topic": r.topic, "max_marks": r.max_marks,
    }


@router.post("/api/essay/submissions", status_code=201)
def submit_essay(
    rubric_id: str = Form(..., max_length=64),
    text: str = Form(..., min_length=50, max_length=20000),
    grade_now: bool = Form(True),
    user=Depends(current_user),
):
    """Student submits an essay; optionally triggers the AI grader
    inline (default). Set `grade_now=false` to queue for batch
    grading by an ops job."""
    from .. import essay_grader
    user = require_user(user)
    try:
        sub = essay_grader.submit(
            user_id=user.id, rubric_id=rubric_id, text=text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    grade = None
    if grade_now:
        try:
            r = essay_grader.grade(sub.id)
            grade = {
                "score": r.score,
                "by_criterion": r.by_criterion,
                "summary": r.summary,
                "suggestions": r.suggestions,
                "method": r.method,
            }
        except Exception as e:  # noqa: BLE001
            grade = {"error": str(e)}
    return {
        "submission_id": sub.id, "ai_grade": grade,
    }


@router.get("/api/essay/submissions/{sid}")
def get_essay_submission(sid: str, user=Depends(current_user)):
    from .. import essay_grader
    user = require_user(user)
    s = essay_grader.get_submission(sid)
    if not s:
        raise HTTPException(404, "submission not found")
    if s.user_id != user.id:
        raise HTTPException(403, "not your submission")
    return {
        "id": s.id, "rubric_id": s.rubric_id, "text": s.text,
        "ai_score": s.ai_score, "ai_feedback": s.ai_feedback,
        "human_reviewed": s.human_reviewed,
        "human_score": s.human_score, "human_note": s.human_note,
        "submitted_at": s.submitted_at, "graded_at": s.graded_at,
        "reviewed_at": s.reviewed_at,
    }


@router.get("/api/essay/submissions")
def list_my_essays(
    limit: int = 20,
    user=Depends(current_user),
):
    from .. import essay_grader
    user = require_user(user)
    rows = essay_grader.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": s.id, "rubric_id": s.rubric_id,
             "ai_score": s.ai_score, "human_reviewed": s.human_reviewed,
             "submitted_at": s.submitted_at, "graded_at": s.graded_at}
            for s in rows
        ],
    }


@router.post("/api/essay/submissions/{sid}/human-review")
def human_review_essay(
    sid: str,
    human_score: float = Form(..., ge=0),
    human_note: str | None = Form(None, max_length=4000),
    user=Depends(current_user),
):
    """Teacher endpoint — overwrites the AI score with human-verified
    marks. Audit-trail-friendly (graded_at preserved, reviewed_at
    set)."""
    from .. import essay_grader
    user = require_user(user)
    if not essay_grader.record_human_review(
        submission_id=sid, human_score=human_score,
        human_note=human_note,
    ):
        raise HTTPException(404, "submission not found")
    return {"ok": True}


# ---------- L5: adaptive practice tests ----------

@router.post("/api/practice-tests", status_code=201)
def create_practice_test(
    exam: str = Form(..., max_length=32),
    subject: str = Form(..., max_length=64),
    target_minutes: int = Form(30, ge=5, le=240),
    user=Depends(current_user),
):
    """Generate a new practice test for the current user. Pulls from
    the J6 question bank for weak topics (J5), synthesises via
    Claude when bank is thin."""
    from .. import practice_test
    user = require_user(user)
    try:
        t = practice_test.generate(
            user_id=user.id, exam=exam, subject=subject,
            target_minutes=target_minutes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": t.id, "exam": t.exam, "subject": t.subject,
        "target_minutes": t.target_minutes,
        "question_count": len(t.questions),
        "generation_method": t.generation_method,
        "status": t.status,
    }


@router.get("/api/practice-tests/{tid}")
def get_practice_test(tid: str, user=Depends(current_user)):
    from .. import practice_test
    user = require_user(user)
    t = practice_test.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    # Strip correct_answer from the response while in_progress so the
    # student can't cheat by inspecting the network tab.
    questions = t.questions
    if t.status != "submitted":
        questions = [
            {k: v for k, v in q.items() if k != "correct_answer"}
            for q in t.questions
        ]
    return {
        "id": t.id, "exam": t.exam, "subject": t.subject,
        "target_minutes": t.target_minutes,
        "questions": questions, "status": t.status,
        "started_at": t.started_at, "submitted_at": t.submitted_at,
        "score": t.score,
        "generation_method": t.generation_method,
    }


@router.get("/api/practice-tests")
def list_my_practice_tests(
    limit: int = 20,
    user=Depends(current_user),
):
    from .. import practice_test
    user = require_user(user)
    rows = practice_test.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": t.id, "exam": t.exam, "subject": t.subject,
             "status": t.status, "target_minutes": t.target_minutes,
             "question_count": len(t.questions),
             "submitted_at": t.submitted_at,
             "score": t.score.get("total") if t.score else None,
             "max": t.score.get("max") if t.score else None,
             "created_at": t.created_at}
            for t in rows
        ],
    }


@router.post("/api/practice-tests/{tid}/start")
def start_practice_test(tid: str, user=Depends(current_user)):
    from .. import practice_test
    user = require_user(user)
    t = practice_test.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    if not practice_test.start(tid):
        raise HTTPException(409, f"test status is {t.status!r}, not 'ready'")
    return {"ok": True}


@router.post("/api/practice-tests/{tid}/submit")
def submit_practice_test(
    tid: str,
    answers_json: str = Form(..., max_length=20000),
    user=Depends(current_user),
):
    """Student submits answers. Returns the score + per-question
    breakdown. Side effect: each question's correctness feeds the
    user's J5 mastery model so future tests adapt."""
    from .. import practice_test
    import json as _json
    user = require_user(user)
    t = practice_test.get(tid)
    if not t:
        raise HTTPException(404, "test not found")
    if t.user_id != user.id:
        raise HTTPException(403, "not your test")
    try:
        answers = _json.loads(answers_json)
        if not isinstance(answers, dict):
            raise ValueError("answers must be a JSON object")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"answers_json: {e}")
    try:
        score = practice_test.submit(test_id=tid, answers=answers)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"score": score}


# ---------- Q2: cost optimization status ----------

@router.get("/api/admin/llm/cost-opt")
def cost_opt_status():
    """Diagnostic — admin sees whether caching + batch are wired."""
    from .. import llm_cache
    return llm_cache.describe()


# ---------- M1: live cohort classes ----------

@router.post("/api/live-classes", status_code=201)
def schedule_live_class(
    title: str = Form(..., min_length=2, max_length=200),
    scheduled_at: float = Form(..., gt=0),
    duration_min: int = Form(60, ge=5, le=480),
    org_id: str | None = Form(None, max_length=64),
    class_id: str | None = Form(None, max_length=64),
    subject: str | None = Form(None, max_length=64),
    max_attendees: int = Form(200, ge=2, le=5000),
    user=Depends(current_user),
):
    """Teacher schedules a new live class. Returns the metadata +
    room_id; students get an access token via `/api/live-classes/
    {id}/join` once the start time approaches."""
    from .. import live_classes
    user = require_user(user)
    try:
        lc = live_classes.schedule(
            teacher_user_id=user.id, title=title,
            scheduled_at=scheduled_at, duration_min=duration_min,
            org_id=org_id, class_id=class_id, subject=subject,
            max_attendees=max_attendees,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": lc.id, "title": lc.title, "scheduled_at": lc.scheduled_at,
        "duration_min": lc.duration_min, "max_attendees": lc.max_attendees,
        "provider": lc.provider, "room_id": lc.room_id,
        "status": lc.status,
    }


@router.get("/api/live-classes/upcoming")
def upcoming_live_classes(
    org_id: str | None = None,
    class_id: str | None = None,
    window_hours: float = 168.0,
):
    """Public catalog of upcoming classes — students browse what's
    scheduled in their org / class."""
    from .. import live_classes
    rows = live_classes.list_upcoming(
        org_id=org_id, class_id=class_id, window_hours=window_hours,
    )
    return {
        "rows": [
            {"id": lc.id, "title": lc.title, "subject": lc.subject,
             "scheduled_at": lc.scheduled_at,
             "duration_min": lc.duration_min, "status": lc.status,
             "max_attendees": lc.max_attendees,
             "provider": lc.provider}
            for lc in rows
        ],
    }


@router.get("/api/live-classes/{lc_id}")
def get_live_class(lc_id: str):
    """Public read of a live class. Recording URL is exposed only
    after the class ends + the recording finalizes."""
    from .. import live_classes
    lc = live_classes.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    return {
        "id": lc.id, "title": lc.title, "subject": lc.subject,
        "teacher_user_id": lc.teacher_user_id,
        "scheduled_at": lc.scheduled_at, "duration_min": lc.duration_min,
        "max_attendees": lc.max_attendees,
        "status": lc.status, "started_at": lc.started_at,
        "ended_at": lc.ended_at,
        "recording_url": lc.recording_url if lc.status == "ended" else None,
        "provider": lc.provider,
    }


@router.post("/api/live-classes/{lc_id}/join")
def join_live_class(
    lc_id: str,
    user=Depends(current_user),
):
    """Issue a per-user access token. Records attendance row."""
    from .. import live_classes
    user = require_user(user)
    lc = live_classes.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    if lc.status not in ("scheduled", "live"):
        raise HTTPException(409, f"class status is {lc.status!r}")
    role = "teacher" if lc.teacher_user_id == user.id else "student"
    try:
        token = live_classes.issue_access_token(
            live_class_id=lc_id, user_id=user.id, role=role,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    live_classes.record_join(live_class_id=lc_id, user_id=user.id)
    return token


@router.post("/api/live-classes/{lc_id}/leave")
def leave_live_class(lc_id: str, user=Depends(current_user)):
    from .. import live_classes
    user = require_user(user)
    live_classes.record_leave(live_class_id=lc_id, user_id=user.id)
    return {"ok": True}


@router.post("/api/live-classes/{lc_id}/status")
def set_live_class_status(
    lc_id: str,
    status: str = Form(..., min_length=4, max_length=16),
    user=Depends(current_user),
):
    """Teacher transitions the class through its lifecycle."""
    from .. import live_classes
    user = require_user(user)
    lc = live_classes.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    if lc.teacher_user_id != user.id:
        raise HTTPException(403, "only the scheduling teacher can update")
    try:
        if not live_classes.set_status(live_class_id=lc_id, status=status):
            raise HTTPException(404, "live class not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "status": status}


@router.get("/api/live-classes/{lc_id}/attendees")
def list_live_class_attendees(lc_id: str, user=Depends(current_user)):
    """Teacher view — who joined + how long. Used for the
    auto-attendance feature."""
    from .. import live_classes
    user = require_user(user)
    lc = live_classes.get(lc_id)
    if not lc:
        raise HTTPException(404, "live class not found")
    if lc.teacher_user_id != user.id:
        raise HTTPException(403, "teacher only")
    return {"rows": live_classes.list_attendees(lc_id)}


@router.get("/api/admin/live-classes/provider")
def live_provider_status():
    from .. import live_classes
    return live_classes.describe()


# ---------- M2: live doubt clearing ----------

@router.post("/api/doubts", status_code=201)
def submit_doubt(
    question_text: str = Form(..., min_length=5, max_length=4000),
    image_url: str | None = Form(None, max_length=2048),
    audio_url: str | None = Form(None, max_length=2048),
    subject: str | None = Form(None, max_length=64),
    org_id: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    from .. import doubt_clearing
    user = require_user(user)
    try:
        d = doubt_clearing.submit(
            user_id=user.id, question_text=question_text,
            org_id=org_id, subject=subject,
            image_url=image_url, audio_url=audio_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": d.id, "status": d.status, "created_at": d.created_at,
    }


@router.get("/api/doubts/{did}")
def get_doubt(did: str, user=Depends(current_user)):
    from .. import doubt_clearing
    user = require_user(user)
    d = doubt_clearing.get(did)
    if not d:
        raise HTTPException(404, "doubt not found")
    if d.user_id != user.id and d.assigned_tutor_id != user.id:
        raise HTTPException(403, "not your doubt")
    return {
        "id": d.id, "user_id": d.user_id, "subject": d.subject,
        "question_text": d.question_text,
        "image_url": d.image_url, "audio_url": d.audio_url,
        "status": d.status,
        "assigned_tutor_id": d.assigned_tutor_id,
        "claimed_at": d.claimed_at,
        "response_text": d.response_text,
        "response_image_url": d.response_image_url,
        "response_audio_url": d.response_audio_url,
        "response_method": d.response_method,
        "response_at": d.response_at,
        "created_at": d.created_at,
    }


@router.get("/api/doubts")
def list_my_doubts(limit: int = 30, user=Depends(current_user)):
    from .. import doubt_clearing
    user = require_user(user)
    rows = doubt_clearing.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": d.id, "question_text": d.question_text[:120],
             "status": d.status,
             "subject": d.subject,
             "response_method": d.response_method,
             "created_at": d.created_at,
             "response_at": d.response_at}
            for d in rows
        ],
    }


@router.get("/api/doubts/queue/pending")
def doubt_queue(
    subject: str | None = None,
    org_id: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    """Tutor view — pending doubts to claim. Filters by subject +
    org so a Polity tutor doesn't see Math doubts."""
    from .. import doubt_clearing
    user = require_user(user)
    rows = doubt_clearing.queue(
        subject=subject, org_id=org_id, limit=limit,
    )
    return {
        "rows": [
            {"id": d.id, "user_id": d.user_id,
             "question_text": d.question_text,
             "image_url": d.image_url, "subject": d.subject,
             "org_id": d.org_id, "created_at": d.created_at}
            for d in rows
        ],
    }


@router.post("/api/doubts/{did}/claim")
def claim_doubt(did: str, user=Depends(current_user)):
    from .. import doubt_clearing
    user = require_user(user)
    if not doubt_clearing.claim(doubt_id=did, tutor_user_id=user.id):
        raise HTTPException(409, "doubt already claimed or not pending")
    return {"ok": True}


@router.post("/api/doubts/{did}/answer")
def answer_doubt(
    did: str,
    response_text: str = Form(..., min_length=5, max_length=8000),
    response_image_url: str | None = Form(None, max_length=2048),
    response_audio_url: str | None = Form(None, max_length=2048),
    method: str = Form("human", max_length=8),
    user=Depends(current_user),
):
    from .. import doubt_clearing
    user = require_user(user)
    d = doubt_clearing.get(did)
    if not d:
        raise HTTPException(404, "doubt not found")
    if method == "human" and d.assigned_tutor_id != user.id:
        raise HTTPException(403, "only the claiming tutor can answer")
    try:
        if not doubt_clearing.answer(
            doubt_id=did, response_text=response_text,
            response_image_url=response_image_url,
            response_audio_url=response_audio_url,
            method=method,
        ):
            raise HTTPException(409, "doubt not in answerable state")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/doubts/{did}/cancel")
def cancel_doubt(did: str, user=Depends(current_user)):
    from .. import doubt_clearing
    user = require_user(user)
    if not doubt_clearing.cancel(doubt_id=did, user_id=user.id):
        raise HTTPException(409, "doubt not cancellable (already answered?)")
    return {"ok": True}


@router.get("/api/admin/doubts/stats")
def doubt_stats(hours: float = 24.0):
    from .. import doubt_clearing
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be in (0, 720]")
    return doubt_clearing.stats(hours=hours)


# ---------- Q3: event stream + analytics ----------

@router.post("/api/events")
def log_events_endpoint(
    request: Request,
    payload: dict,
):
    """Client beacon. Accepts a single event or a list under
    `events`. Per-IP rate-limited via the v2.0.1 token-bucket pool
    so a runaway client can't flood the events table.

    Payload shape:
      {kind: 'lesson.start', user_id?, org_id?, props?}
      OR
      {events: [{kind, ...}, ...]}
    """
    from .. import analytics, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    # Reuse the math-preview bucket — same defense profile (anonymous
    # endpoint, write-heavy). A dedicated `events` bucket lands in
    # v2.3.x once we see real client traffic.
    if not rate_limit.preview_math.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be an object")
    events = payload.get("events")
    if isinstance(events, list):
        n = analytics.log_batch(events)
        return {"logged": n}
    kind = payload.get("kind")
    if not kind:
        raise HTTPException(400, "kind required")
    eid = analytics.log(
        kind=kind,
        user_id=payload.get("user_id"),
        org_id=payload.get("org_id"),
        session_id=payload.get("session_id"),
        props=payload.get("props"),
        source=payload.get("source", "web"),
    )
    return {"id": eid}


@router.get("/api/admin/metrics/dau")
def metric_dau(date: str | None = None):
    from .. import analytics
    return {"date": date, "dau": analytics.dau(date=date)}


@router.get("/api/admin/metrics/mau")
def metric_mau(date: str | None = None):
    from .. import analytics
    return {"date": date, "mau": analytics.mau(date=date)}


@router.get("/api/admin/metrics/events-by-kind")
def metric_events_by_kind(hours: float = 24.0):
    from .. import analytics
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be in (0, 720]")
    return {"hours": hours, "by_kind": analytics.event_count_by_kind(hours=hours)}


@router.post("/api/admin/metrics/funnel")
def metric_funnel(
    steps_csv: str = Form(..., max_length=2000),
    hours: float = Form(24.0, gt=0, le=720),
):
    from .. import analytics
    steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
    if not steps:
        raise HTTPException(400, "at least one step required")
    return analytics.funnel(steps, hours=hours)


@router.get("/api/admin/metrics/retention")
def metric_retention(cohort_date: str):
    """D1 / D7 / D30 retention for the signup cohort on `cohort_date`
    (YYYY-MM-DD UTC)."""
    from .. import analytics
    try:
        return analytics.retention_d1_d7_d30(cohort_date=cohort_date)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/admin/metrics/series")
def metric_series(metric: str, days: int = 30):
    from .. import analytics
    if days < 1 or days > 365:
        raise HTTPException(400, "days must be in [1, 365]")
    return {
        "metric": metric, "days": days,
        "series": analytics.get_metric_series(metric=metric, days=days),
    }


@router.post("/api/admin/metrics/rollup")
def metric_rollup(date: str | None = Form(None)):
    """Trigger the daily rollup. Cron runs this for yesterday at
    01:00 UTC; admin can call it manually for a specific date."""
    from .. import analytics
    if date:
        try:
            return analytics.rollup_for_date(date)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return analytics.rollup_yesterday()


# ---------- L3: handwritten math recognition ----------

@router.post("/api/math-vision/submit", status_code=201)
def submit_math_image(
    image_url: str = Form(..., min_length=10, max_length=2048),
    expected_language: str = Form("en", max_length=8),
    auto_extract: bool = Form(True),
    user=Depends(current_user),
):
    """Student uploads an image URL of handwritten math. `auto_extract`
    triggers vision + step extraction inline (default); set false to
    queue for a batch worker."""
    from .. import math_vision
    user = require_user(user)
    try:
        sub = math_vision.submit(
            user_id=user.id, image_url=image_url,
            expected_language=expected_language,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if auto_extract:
        try:
            sub = math_vision.extract(submission_id=sub.id)
        except Exception as e:  # noqa: BLE001
            print(f"[math_vision] extract error: {e}")
    return {
        "id": sub.id, "status": sub.status,
        "extracted_latex": sub.extracted_latex,
        "confidence": sub.confidence,
        "steps": sub.steps,
        "error": sub.error,
    }


@router.post("/api/math-vision/{sid}/validate")
def validate_math_submission(sid: str, user=Depends(current_user)):
    """Walk the extracted steps + check each pair is equivalent.
    Idempotent — re-runs overwrite the previous validation."""
    from .. import math_vision
    user = require_user(user)
    sub = math_vision.get(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    try:
        result = math_vision.validate(submission_id=sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "submission_id": result.submission_id,
        "overall": result.overall,
        "first_wrong_step": result.first_wrong_step,
        "per_step": result.per_step,
        "method": result.method,
    }


@router.get("/api/math-vision/{sid}")
def get_math_submission(sid: str, user=Depends(current_user)):
    from .. import math_vision
    user = require_user(user)
    sub = math_vision.get(sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if sub.user_id != user.id:
        raise HTTPException(403, "not your submission")
    return {
        "id": sub.id, "image_url": sub.image_url,
        "expected_language": sub.expected_language,
        "extracted_latex": sub.extracted_latex,
        "confidence": sub.confidence, "steps": sub.steps,
        "validation": sub.validation, "status": sub.status,
        "error": sub.error, "created_at": sub.created_at,
        "extracted_at": sub.extracted_at,
        "validated_at": sub.validated_at,
    }


@router.get("/api/math-vision")
def list_my_math_submissions(
    limit: int = 30, user=Depends(current_user),
):
    from .. import math_vision
    user = require_user(user)
    rows = math_vision.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": s.id, "status": s.status,
             "confidence": s.confidence,
             "steps_count": len(s.steps or []),
             "created_at": s.created_at}
            for s in rows
        ],
    }


# ---------- L4: mock interview ----------

@router.post("/api/mock-interviews", status_code=201)
def start_mock_interview(
    track: str = Form("generic", max_length=32),
    user=Depends(current_user),
):
    from .. import mock_interview
    user = require_user(user)
    try:
        interview, opener = mock_interview.start(
            user_id=user.id, track=track,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "interview_id": interview.id, "track": interview.track,
        "started_at": interview.started_at,
        "opener": {
            "turn_index": opener.turn_index,
            "question_text": opener.question_text,
        },
    }


@router.post("/api/mock-interviews/{iid}/answer")
def submit_mock_interview_answer(
    iid: str,
    turn_index: int = Form(..., ge=0, le=50),
    answer_text: str = Form(..., min_length=1, max_length=6000),
    answer_audio_url: str | None = Form(None, max_length=2048),
    user=Depends(current_user),
):
    from .. import mock_interview
    user = require_user(user)
    interview = mock_interview.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    try:
        result = mock_interview.submit_answer(
            interview_id=iid, turn_index=turn_index,
            answer_text=answer_text,
            answer_audio_url=answer_audio_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "feedback": result.feedback,
        "interview_ended": result.next_turn is None,
        "next": (
            {"turn_index": result.next_turn.turn_index,
             "question_text": result.next_turn.question_text}
            if result.next_turn else None
        ),
    }


@router.post("/api/mock-interviews/{iid}/end")
def end_mock_interview(iid: str, user=Depends(current_user)):
    from .. import mock_interview
    user = require_user(user)
    interview = mock_interview.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    try:
        ended = mock_interview.end(interview_id=iid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "interview_id": ended.id, "status": ended.status,
        "overall_score": ended.overall_score,
        "feedback": ended.feedback,
        "duration_seconds": ended.duration_seconds,
    }


@router.get("/api/mock-interviews/{iid}")
def get_mock_interview(iid: str, user=Depends(current_user)):
    from .. import mock_interview
    user = require_user(user)
    interview = mock_interview.get(iid)
    if not interview:
        raise HTTPException(404, "interview not found")
    if interview.user_id != user.id:
        raise HTTPException(403, "not your interview")
    turns = mock_interview.list_turns(iid)
    return {
        "id": interview.id, "track": interview.track,
        "status": interview.status,
        "started_at": interview.started_at,
        "ended_at": interview.ended_at,
        "overall_score": interview.overall_score,
        "feedback": interview.feedback,
        "turns": [
            {"turn_index": t.turn_index,
             "question_text": t.question_text,
             "answer_text": t.answer_text,
             "feedback": t.feedback,
             "answered_at": t.answered_at}
            for t in turns
        ],
    }


@router.get("/api/mock-interviews")
def list_my_mock_interviews(
    limit: int = 20, user=Depends(current_user),
):
    from .. import mock_interview
    user = require_user(user)
    rows = mock_interview.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"id": x.id, "track": x.track, "status": x.status,
             "overall_score": x.overall_score,
             "started_at": x.started_at, "ended_at": x.ended_at,
             "duration_seconds": x.duration_seconds}
            for x in rows
        ],
    }


# ---------- M3: live mock-test events ----------

@router.post("/api/mock-events", status_code=201)
def schedule_mock_event(
    title: str = Form(..., min_length=4, max_length=200),
    exam: str = Form(..., max_length=32),
    scheduled_at: float = Form(..., gt=0),
    duration_min: int = Form(60, ge=5, le=480),
    question_set_json: str = Form(..., max_length=200000),
    subject: str | None = Form(None, max_length=64),
    max_participants: int = Form(5000, ge=2, le=100000),
    user=Depends(current_user),
):
    """Teacher / admin schedules a live mock-test event. The
    question_set_json is the full question list as a JSON array."""
    from .. import mock_test_events
    import json as _json
    user = require_user(user)
    try:
        qs = _json.loads(question_set_json)
        if not isinstance(qs, list):
            raise ValueError("question_set_json must be a JSON array")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"question_set_json: {e}")
    try:
        e = mock_test_events.schedule_event(
            title=title, exam=exam, scheduled_at=scheduled_at,
            duration_min=duration_min, question_set=qs,
            subject=subject, max_participants=max_participants,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": e.id, "title": e.title, "exam": e.exam,
        "scheduled_at": e.scheduled_at, "duration_min": e.duration_min,
        "question_count": len(e.question_set),
        "status": e.status,
    }


@router.get("/api/mock-events/upcoming")
def upcoming_mock_events(
    exam: str | None = None, window_hours: float = 168.0,
):
    from .. import mock_test_events
    rows = mock_test_events.list_upcoming_events(
        exam=exam, window_hours=window_hours,
    )
    return {
        "rows": [
            {"id": e.id, "title": e.title, "exam": e.exam,
             "subject": e.subject, "scheduled_at": e.scheduled_at,
             "duration_min": e.duration_min,
             "question_count": len(e.question_set),
             "max_participants": e.max_participants,
             "status": e.status}
            for e in rows
        ],
    }


@router.post("/api/mock-events/{eid}/register")
def register_for_mock_event(eid: str, user=Depends(current_user)):
    from .. import mock_test_events
    user = require_user(user)
    ok = mock_test_events.register(event_id=eid, user_id=user.id)
    if not ok:
        raise HTTPException(409, "already registered or event closed")
    return {"ok": True}


@router.post("/api/mock-events/{eid}/start")
def start_mock_event_attempt(eid: str, user=Depends(current_user)):
    """Begin the attempt clock. Idempotent — re-calling returns the
    same `started_at`."""
    from .. import mock_test_events
    user = require_user(user)
    try:
        attempt = mock_test_events.start_attempt(
            event_id=eid, user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "attempt_id": attempt.id, "started_at": attempt.started_at,
    }


@router.post("/api/mock-events/{eid}/submit")
def submit_mock_event_attempt(
    eid: str,
    answers_json: str = Form(..., max_length=100000),
    user=Depends(current_user),
):
    from .. import mock_test_events
    import json as _json
    user = require_user(user)
    try:
        answers = _json.loads(answers_json)
        if not isinstance(answers, dict):
            raise ValueError("answers_json must be a JSON object")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"answers_json: {e}")
    try:
        attempt = mock_test_events.submit_attempt(
            event_id=eid, user_id=user.id, answers=answers,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "attempt_id": attempt.id,
        "score": attempt.score, "max_score": attempt.max_score,
        "rank": attempt.rank, "percentile": attempt.percentile,
    }


@router.get("/api/mock-events/{eid}/leaderboard")
def mock_event_leaderboard(eid: str, limit: int = 50):
    from .. import mock_test_events
    if limit < 1 or limit > 1000:
        raise HTTPException(400, "limit must be in [1, 1000]")
    return {"rows": mock_test_events.leaderboard(eid, limit=limit)}


@router.get("/api/mock-events/{eid}/stats")
def mock_event_stats(eid: str):
    from .. import mock_test_events
    s = mock_test_events.stats(eid)
    if not s.get("found"):
        raise HTTPException(404, "event not found")
    return s


@router.get("/api/mock-events/{eid}/me")
def my_mock_event_attempt(eid: str, user=Depends(current_user)):
    from .. import mock_test_events
    user = require_user(user)
    a = mock_test_events.get_my_attempt(event_id=eid, user_id=user.id)
    if not a:
        raise HTTPException(404, "no attempt for this event")
    return {
        "attempt_id": a.id,
        "started_at": a.started_at, "submitted_at": a.submitted_at,
        "score": a.score, "max_score": a.max_score,
        "rank": a.rank, "percentile": a.percentile,
    }


# ---------- N1: forums ----------

@router.get("/api/forums/threads")
def list_forum_threads(
    scope: str = "public",
    scope_key: str | None = None,
    limit: int = 30,
    offset: int = 0,
):
    """Public list. UI filters client-side for the scope_key the user
    has access to; org/class scope is enforced at thread-create."""
    from .. import forums
    try:
        rows = forums.list_threads(
            scope=scope, scope_key=scope_key,
            limit=limit, offset=offset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": t.id, "scope": t.scope, "scope_key": t.scope_key,
             "title": t.title, "created_by": t.created_by,
             "created_at": t.created_at,
             "last_activity_at": t.last_activity_at,
             "post_count": t.post_count,
             "locked": t.locked, "pinned": t.pinned}
            for t in rows
        ],
    }


@router.post("/api/forums/threads", status_code=201)
def create_forum_thread(
    scope: str = Form(..., max_length=16),
    title: str = Form(..., min_length=4, max_length=200),
    body: str = Form(..., min_length=1, max_length=16000),
    scope_key: str | None = Form(None, max_length=120),
    user=Depends(current_user),
):
    from .. import forums
    user = require_user(user)
    try:
        thread, post = forums.create_thread(
            scope=scope, scope_key=scope_key,
            title=title, body=body, author_user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "thread_id": thread.id, "scope": thread.scope,
        "opening_post_id": post.id, "created_at": thread.created_at,
    }


@router.get("/api/forums/threads/{tid}")
def get_forum_thread(tid: str, limit: int = 100, offset: int = 0):
    """Read a thread + its posts. Hidden posts excluded by default."""
    from .. import forums
    t = forums.get_thread(tid)
    if not t:
        raise HTTPException(404, "thread not found")
    posts = forums.list_posts(thread_id=tid, limit=limit, offset=offset)
    return {
        "thread": {
            "id": t.id, "scope": t.scope, "scope_key": t.scope_key,
            "title": t.title, "created_by": t.created_by,
            "created_at": t.created_at,
            "last_activity_at": t.last_activity_at,
            "post_count": t.post_count,
            "locked": t.locked, "pinned": t.pinned,
        },
        "posts": [
            {"id": p.id, "author_user_id": p.author_user_id,
             "body": p.body, "parent_post_id": p.parent_post_id,
             "created_at": p.created_at, "edited_at": p.edited_at,
             "flag_count": p.flag_count, "hidden": p.hidden}
            for p in posts
        ],
    }


@router.post("/api/forums/threads/{tid}/reply", status_code=201)
def reply_to_thread(
    tid: str,
    body: str = Form(..., min_length=1, max_length=16000),
    parent_post_id: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    from .. import forums
    user = require_user(user)
    try:
        post = forums.reply(
            thread_id=tid, author_user_id=user.id, body=body,
            parent_post_id=parent_post_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "post_id": post.id, "created_at": post.created_at,
    }


@router.post("/api/forums/posts/{pid}/flag")
def flag_forum_post(
    pid: str,
    reason: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import forums
    user = require_user(user)
    try:
        result = forums.flag_post(
            post_id=pid, flagger_user_id=user.id, reason=reason,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    return result


@router.delete("/api/forums/posts/{pid}")
def delete_forum_post(pid: str, user=Depends(current_user)):
    """Author deletes their own post (soft-delete)."""
    from .. import forums
    user = require_user(user)
    if not forums.delete_post(post_id=pid, author_user_id=user.id):
        raise HTTPException(404, "post not found or not yours")
    return {"ok": True}


@router.get("/api/admin/forums/flagged")
def admin_flagged_queue(limit: int = 50):
    from .. import forums
    rows = forums.flagged_queue(limit=limit)
    return {
        "rows": [
            {"id": p.id, "thread_id": p.thread_id,
             "author_user_id": p.author_user_id,
             "body": p.body, "flag_count": p.flag_count,
             "hidden": p.hidden, "created_at": p.created_at}
            for p in rows
        ],
    }


# ---------- N2: family plans ----------

@router.post("/api/families", status_code=201)
def create_family(
    name: str | None = Form(None, max_length=120),
    user=Depends(current_user),
):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.create_family(
        primary_parent_user_id=user.id, name=name,
    )
    return {
        "id": fam.id, "name": fam.name,
        "primary_parent_user_id": fam.primary_parent_user_id,
        "created_at": fam.created_at,
    }


@router.get("/api/families/me")
def my_families(user=Depends(current_user)):
    from .. import family_plans
    user = require_user(user)
    fams = family_plans.families_for_user(user.id)
    return {
        "rows": [
            {"id": f.id, "name": f.name,
             "primary_parent_user_id": f.primary_parent_user_id,
             "created_at": f.created_at,
             "is_primary": f.primary_parent_user_id == user.id}
            for f in fams
        ],
    }


@router.post("/api/families/{fid}/members", status_code=201)
def add_family_member(
    fid: str,
    member_user_id: str = Form(..., max_length=64),
    role: str = Form(..., max_length=8),
    relation: str | None = Form(None, max_length=32),
    user=Depends(current_user),
):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.get_family(fid)
    if not fam:
        raise HTTPException(404, "family not found")
    if fam.primary_parent_user_id != user.id:
        raise HTTPException(403, "only the primary parent can add members")
    try:
        m = family_plans.add_member(
            group_id=fid, user_id=member_user_id,
            role=role, relation=relation,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "group_id": m.group_id, "user_id": m.user_id,
        "role": m.role, "relation": m.relation,
    }


@router.delete("/api/families/{fid}/members/{uid}")
def remove_family_member(
    fid: str, uid: str, user=Depends(current_user),
):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.get_family(fid)
    if not fam:
        raise HTTPException(404, "family not found")
    if fam.primary_parent_user_id != user.id:
        raise HTTPException(403, "only the primary parent can remove members")
    try:
        ok = family_plans.remove_member(group_id=fid, user_id=uid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "member not found")
    return {"ok": True}


@router.get("/api/families/{fid}/members")
def list_family_members(fid: str, user=Depends(current_user)):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.get_family(fid)
    if not fam:
        raise HTTPException(404, "family not found")
    members = family_plans.list_members(fid)
    if not any(m.user_id == user.id for m in members):
        raise HTTPException(403, "not your family")
    return {
        "rows": [
            {"user_id": m.user_id, "role": m.role,
             "relation": m.relation, "joined_at": m.joined_at}
            for m in members
        ],
    }


@router.get("/api/families/quote")
def family_quote(
    tier: str, billing_cycle: str, child_count: int,
):
    """Public price quote. Anyone can preview pricing before
    creating a family — used by the marketing pricing page."""
    from .. import family_plans
    try:
        q = family_plans.quote(
            tier=tier, billing_cycle=billing_cycle,
            child_count=child_count,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "tier": q.tier, "billing_cycle": q.billing_cycle,
        "child_count": q.child_count,
        "base_price_paise": q.base_price_paise,
        "total_paise": q.total_paise,
        "savings_paise": q.savings_paise,
        "discount_pct": q.discount_pct,
        "per_child_breakdown": q.per_child_breakdown,
    }


@router.post("/api/families/{fid}/subscribe", status_code=201)
def subscribe_family(
    fid: str,
    tier: str = Form(..., max_length=4),
    billing_cycle: str = Form(..., max_length=10),
    user=Depends(current_user),
):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.get_family(fid)
    if not fam:
        raise HTTPException(404, "family not found")
    if fam.primary_parent_user_id != user.id:
        raise HTTPException(403, "only the primary parent can subscribe")
    try:
        sub = family_plans.start_subscription(
            group_id=fid, tier=tier, billing_cycle=billing_cycle,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": sub.id, "tier": sub.tier,
        "billing_cycle": sub.billing_cycle,
        "child_count": sub.child_count,
        "total_paise": sub.total_paise,
        "discount_pct": sub.discount_pct,
        "next_renewal_at": sub.next_renewal_at,
        "status": sub.status,
    }


@router.get("/api/families/{fid}/subscription")
def get_family_subscription(fid: str, user=Depends(current_user)):
    from .. import family_plans
    user = require_user(user)
    fam = family_plans.get_family(fid)
    if not fam:
        raise HTTPException(404, "family not found")
    members = family_plans.list_members(fid)
    if not any(m.user_id == user.id for m in members):
        raise HTTPException(403, "not your family")
    sub = family_plans.active_for_group(fid)
    if not sub:
        return {"active": False}
    return {
        "active": True, "id": sub.id, "tier": sub.tier,
        "billing_cycle": sub.billing_cycle,
        "child_count": sub.child_count,
        "total_paise": sub.total_paise,
        "discount_pct": sub.discount_pct,
        "started_at": sub.started_at,
        "next_renewal_at": sub.next_renewal_at,
    }


# ---------- N3: study buddies ----------

@router.get("/api/buddies/me")
def get_my_buddy_profile(user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    p = study_buddies.get_profile(user.id)
    if not p:
        return {"opted_in": False, "profile": None}
    return {
        "opted_in": p.opted_in,
        "profile": {
            "exam": p.exam, "grade": p.grade,
            "language": p.language,
            "study_hours_week": p.study_hours_week,
            "available_windows": p.available_windows,
            "bio": p.bio, "updated_at": p.updated_at,
        },
    }


@router.post("/api/buddies/me")
def upsert_my_buddy_profile(
    exam: str | None = Form(None, max_length=32),
    grade: int | None = Form(None, ge=1, le=16),
    language: str = Form("en", max_length=8),
    study_hours_week: int = Form(10, ge=0, le=168),
    available_windows_csv: str | None = Form(None, max_length=200),
    bio: str | None = Form(None, max_length=2000),
    opted_in: bool = Form(True),
    user=Depends(current_user),
):
    from .. import study_buddies
    user = require_user(user)
    windows = (
        [w.strip() for w in available_windows_csv.split(",") if w.strip()]
        if available_windows_csv else None
    )
    try:
        p = study_buddies.upsert_profile(
            user_id=user.id, exam=exam, grade=grade,
            language=language,
            study_hours_week=study_hours_week,
            available_windows=windows, bio=bio, opted_in=opted_in,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "opted_in": p.opted_in,
        "exam": p.exam, "grade": p.grade,
    }


@router.post("/api/buddies/me/opt-out")
def opt_out_buddy(user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    study_buddies.opt_out(user.id)
    return {"ok": True}


@router.get("/api/buddies/matches")
def find_buddy_matches(limit: int = 5, user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    matches = study_buddies.find_matches(user_id=user.id, limit=limit)
    return {
        "rows": [
            {"candidate_user_id": m.candidate_user_id,
             "score": m.score, "reasons": m.reasons}
            for m in matches
        ],
    }


@router.post("/api/buddies/pairs", status_code=201)
def propose_buddy_pair(
    candidate_user_id: str = Form(..., max_length=64),
    user=Depends(current_user),
):
    from .. import study_buddies
    user = require_user(user)
    try:
        pair = study_buddies.propose_pair(
            user_a_id=user.id, user_b_id=candidate_user_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": pair.id, "user_a_id": pair.user_a_id,
        "user_b_id": pair.user_b_id, "status": pair.status,
    }


@router.post("/api/buddies/pairs/{pid}/accept")
def accept_buddy_pair(pid: str, user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    try:
        pair = study_buddies.accept_pair(pair_id=pid, user_id=user.id)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400 if isinstance(e, ValueError) else 403, str(e))
    return {
        "id": pair.id, "status": pair.status,
        "accepted_a_at": pair.accepted_a_at,
        "accepted_b_at": pair.accepted_b_at,
    }


@router.post("/api/buddies/pairs/{pid}/decline")
def decline_buddy_pair(pid: str, user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    if not study_buddies.decline_pair(pair_id=pid, user_id=user.id):
        raise HTTPException(404, "pair not found or already actioned")
    return {"ok": True}


@router.post("/api/buddies/pairs/{pid}/dissolve")
def dissolve_buddy_pair(pid: str, user=Depends(current_user)):
    from .. import study_buddies
    user = require_user(user)
    if not study_buddies.dissolve_pair(pair_id=pid, user_id=user.id):
        raise HTTPException(404, "pair not found")
    return {"ok": True}


@router.get("/api/buddies/pairs")
def list_my_buddy_pairs(
    statuses: str | None = None,
    user=Depends(current_user),
):
    from .. import study_buddies
    user = require_user(user)
    status_list = (
        [s.strip() for s in statuses.split(",") if s.strip()]
        if statuses else None
    )
    try:
        rows = study_buddies.my_pairs(user.id, statuses=status_list)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": p.id, "user_a_id": p.user_a_id,
             "user_b_id": p.user_b_id,
             "score": p.score, "status": p.status,
             "matched_at": p.matched_at,
             "accepted_a_at": p.accepted_a_at,
             "accepted_b_at": p.accepted_b_at,
             "last_interaction_at": p.last_interaction_at}
            for p in rows
        ],
    }


@router.post("/api/buddies/pairs/{pid}/messages", status_code=201)
def send_buddy_message(
    pid: str,
    body: str = Form(..., min_length=1, max_length=4000),
    user=Depends(current_user),
):
    from .. import study_buddies
    user = require_user(user)
    try:
        msg = study_buddies.send_message(
            pair_id=pid, sender_user_id=user.id, body=body,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": msg.id, "created_at": msg.created_at,
    }


@router.get("/api/buddies/pairs/{pid}/messages")
def list_buddy_messages(
    pid: str, limit: int = 100, user=Depends(current_user),
):
    from .. import study_buddies
    user = require_user(user)
    # Authz: only the two parties can read
    try:
        pair = study_buddies._get_pair_by_id(pid)
    except ValueError:
        raise HTTPException(404, "pair not found")
    if user.id not in (pair.user_a_id, pair.user_b_id):
        raise HTTPException(403, "not your pair")
    rows = study_buddies.list_messages(pair_id=pid, limit=limit)
    return {
        "rows": [
            {"id": m.id, "sender_user_id": m.sender_user_id,
             "body": m.body, "created_at": m.created_at}
            for m in rows
        ],
    }


# ---------- O1: teacher publishing ----------

@router.post("/api/publishing/creators", status_code=201)
def apply_as_creator(
    display_name: str = Form(..., min_length=2, max_length=80),
    bio: str | None = Form(None, max_length=2000),
    avatar_url: str | None = Form(None, max_length=2048),
    user=Depends(current_user),
):
    from .. import teacher_publishing
    user = require_user(user)
    try:
        c = teacher_publishing.apply_as_creator(
            user_id=user.id, display_name=display_name,
            bio=bio, avatar_url=avatar_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": c.id, "user_id": c.user_id,
        "display_name": c.display_name,
        "status": c.status, "verified": c.verified,
    }


@router.get("/api/publishing/creators/me")
def get_my_creator_profile(user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    c = teacher_publishing.get_creator_by_user(user.id)
    if not c:
        return {"profile": None}
    return {
        "profile": {
            "id": c.id, "display_name": c.display_name,
            "bio": c.bio, "verified": c.verified,
            "status": c.status,
            "platform_fee_pct": c.platform_fee_pct,
            "total_earnings_paise": c.total_earnings_paise,
        },
    }


@router.post("/api/admin/publishing/creators/{cid}/approve")
def admin_approve_creator(
    cid: str, verified: bool = Form(True),
):
    from .. import teacher_publishing
    if not teacher_publishing.approve_creator(
        creator_id=cid, verified=verified,
    ):
        raise HTTPException(404, "creator not pending")
    return {"ok": True}


@router.post("/api/publishing/series", status_code=201)
def create_publishing_series(
    title: str = Form(..., min_length=4, max_length=200),
    price_paise: int = Form(..., ge=4900, le=9999900),
    description: str | None = Form(None, max_length=4000),
    exam: str | None = Form(None, max_length=32),
    subject: str | None = Form(None, max_length=64),
    language: str = Form("en", max_length=8),
    cover_url: str | None = Form(None, max_length=2048),
    user=Depends(current_user),
):
    from .. import teacher_publishing
    user = require_user(user)
    try:
        s = teacher_publishing.create_series(
            creator_user_id=user.id, title=title,
            price_paise=price_paise, description=description,
            exam=exam, subject=subject, language=language,
            cover_url=cover_url,
        )
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    return {
        "id": s.id, "title": s.title, "status": s.status,
        "price_paise": s.price_paise,
    }


@router.post("/api/publishing/series/{sid}/lessons", status_code=201)
def add_publishing_lesson(
    sid: str,
    title: str = Form(..., min_length=2, max_length=200),
    duration_seconds: int = Form(0, ge=0, le=14400),
    video_url: str | None = Form(None, max_length=2048),
    free_preview: bool = Form(False),
    user=Depends(current_user),
):
    from .. import teacher_publishing
    user = require_user(user)
    try:
        lsn = teacher_publishing.add_lesson(
            series_id=sid, creator_user_id=user.id,
            title=title, duration_seconds=duration_seconds,
            video_url=video_url, free_preview=free_preview,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": lsn.id, "position": lsn.position, "title": lsn.title,
        "duration_seconds": lsn.duration_seconds,
    }


@router.post("/api/publishing/series/{sid}/publish")
def publish_publishing_series(sid: str, user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    try:
        ok = teacher_publishing.publish_series(
            series_id=sid, creator_user_id=user.id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(409, "series already published or archived")
    return {"ok": True}


@router.get("/api/publishing/storefront")
def publishing_storefront(
    exam: str | None = None,
    subject: str | None = None,
    language: str | None = None,
    limit: int = 30,
    offset: int = 0,
):
    from .. import teacher_publishing
    rows = teacher_publishing.list_storefront(
        exam=exam, subject=subject, language=language,
        limit=limit, offset=offset,
    )
    return {
        "rows": [
            {"id": s.id, "title": s.title,
             "creator_user_id": s.creator_user_id,
             "exam": s.exam, "subject": s.subject,
             "language": s.language,
             "price_paise": s.price_paise,
             "lesson_count": s.lesson_count,
             "total_minutes": s.total_minutes,
             "purchase_count": s.purchase_count,
             "cover_url": s.cover_url}
            for s in rows
        ],
    }


@router.get("/api/publishing/series/{sid}")
def get_publishing_series(sid: str, user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    s = teacher_publishing.get_series(sid)
    if not s:
        raise HTTPException(404, "series not found")
    is_owner = (s.creator_user_id == user.id)
    if s.status != "published" and not is_owner:
        raise HTTPException(404, "series not found")
    has = teacher_publishing.has_access(user_id=user.id, series_id=sid)
    lessons = teacher_publishing.list_lessons(sid)
    return {
        "id": s.id, "title": s.title, "description": s.description,
        "exam": s.exam, "subject": s.subject,
        "language": s.language, "price_paise": s.price_paise,
        "status": s.status,
        "lesson_count": s.lesson_count,
        "total_minutes": s.total_minutes,
        "purchase_count": s.purchase_count,
        "has_access": has,
        "is_owner": is_owner,
        "lessons": [
            {"id": l.id, "position": l.position, "title": l.title,
             "duration_seconds": l.duration_seconds,
             "free_preview": l.free_preview,
             "video_url": l.video_url if (has or l.free_preview) else None}
            for l in lessons
        ],
    }


@router.post("/api/publishing/series/{sid}/purchase", status_code=201)
def purchase_publishing_series(sid: str, user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    try:
        p = teacher_publishing.purchase(user_id=user.id, series_id=sid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": p.id, "price_paise": p.price_paise,
        "purchased_at": p.purchased_at,
    }


@router.get("/api/publishing/me/purchases")
def my_publishing_purchases(user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    rows = teacher_publishing.my_purchases(user.id)
    return {
        "rows": [
            {"id": p.id, "series_id": p.series_id,
             "price_paise": p.price_paise,
             "purchased_at": p.purchased_at}
            for p in rows
        ],
    }


@router.get("/api/publishing/me/earnings")
def my_publishing_earnings(user=Depends(current_user)):
    from .. import teacher_publishing
    user = require_user(user)
    return teacher_publishing.creator_earnings(user.id)


# ---------- O2: content marketplace ----------

@router.post("/api/admin/content/publishers", status_code=201)
def admin_register_publisher(
    name: str = Form(..., min_length=2, max_length=200),
    kind: str = Form(..., max_length=16),
    contact_email: str | None = Form(None, max_length=200),
):
    from .. import content_market
    try:
        p = content_market.register_publisher(
            name=name, kind=kind, contact_email=contact_email,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": p.id, "name": p.name, "kind": p.kind,
            "verified": p.verified}


@router.post("/api/admin/content/publishers/{pid}/verify")
def admin_verify_publisher(pid: str, verified: bool = Form(True)):
    from .. import content_market
    if not content_market.verify_publisher(pid, verified=verified):
        raise HTTPException(404, "publisher not found")
    return {"ok": True}


@router.get("/api/content/publishers")
def list_content_publishers(kind: str | None = None):
    from .. import content_market
    rows = content_market.list_publishers(kind=kind)
    return {
        "rows": [
            {"id": p.id, "name": p.name, "kind": p.kind,
             "verified": p.verified}
            for p in rows
        ],
    }


@router.post("/api/content/packs", status_code=201)
def create_content_pack(
    publisher_id: str = Form(..., max_length=64),
    title: str = Form(..., min_length=4, max_length=200),
    price_inr_per_seat_year: int = Form(..., ge=1, le=50000),
    description: str | None = Form(None, max_length=4000),
    board: str | None = Form(None, max_length=32),
    grade: int | None = Form(None, ge=1, le=16),
    subject: str | None = Form(None, max_length=64),
    chapter_count: int = Form(0, ge=0, le=500),
    language: str = Form("en", max_length=8),
    manifest_url: str | None = Form(None, max_length=2048),
):
    from .. import content_market
    try:
        p = content_market.create_pack(
            publisher_id=publisher_id, title=title,
            price_inr_per_seat_year=price_inr_per_seat_year,
            description=description, board=board, grade=grade,
            subject=subject, chapter_count=chapter_count,
            language=language, manifest_url=manifest_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": p.id, "title": p.title, "status": p.status}


@router.post("/api/content/packs/{pid}/publish")
def publish_content_pack(
    pid: str, publisher_id: str = Form(..., max_length=64),
):
    from .. import content_market
    try:
        ok = content_market.publish_pack(
            pack_id=pid, publisher_id=publisher_id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(409, "pack already published or archived")
    return {"ok": True}


@router.get("/api/content/packs")
def list_content_packs(
    publisher_id: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    language: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    from .. import content_market
    rows = content_market.list_packs(
        publisher_id=publisher_id, board=board, grade=grade,
        subject=subject, language=language,
        limit=limit, offset=offset,
    )
    return {
        "rows": [
            {"id": p.id, "publisher_id": p.publisher_id,
             "title": p.title, "description": p.description,
             "board": p.board, "grade": p.grade, "subject": p.subject,
             "chapter_count": p.chapter_count,
             "price_inr_per_seat_year": p.price_inr_per_seat_year,
             "language": p.language,
             "subscription_count": p.subscription_count}
            for p in rows
        ],
    }


@router.get("/api/content/packs/{pid}")
def get_content_pack(pid: str):
    from .. import content_market
    p = content_market.get_pack(pid)
    if not p or p.status not in ("published", "draft"):
        raise HTTPException(404, "pack not found")
    return {
        "id": p.id, "publisher_id": p.publisher_id,
        "title": p.title, "description": p.description,
        "board": p.board, "grade": p.grade, "subject": p.subject,
        "chapter_count": p.chapter_count,
        "price_inr_per_seat_year": p.price_inr_per_seat_year,
        "language": p.language, "manifest_url": p.manifest_url,
        "status": p.status,
        "subscription_count": p.subscription_count,
    }


@router.post("/api/content/packs/{pid}/subscribe", status_code=201)
def subscribe_to_content_pack(
    pid: str,
    org_id: str = Form(..., max_length=64),
    seats: int = Form(..., ge=1, le=100000),
    duration_days: int = Form(365, ge=30, le=1095),
    user=Depends(current_user),
):
    from .. import content_market
    user = require_user(user)
    # Caller is responsible for proving org-admin status; for v2.6
    # we don't gate that here (admin SPA does its own check).
    try:
        s = content_market.subscribe(
            org_id=org_id, pack_id=pid,
            seats=seats, duration_days=duration_days,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": s.id, "seats": s.seats,
        "total_paid_paise": s.total_paid_paise,
        "expires_at": s.expires_at, "status": s.status,
    }


@router.get("/api/content/orgs/{org_id}/subscriptions")
def list_org_content_subscriptions(
    org_id: str, user=Depends(current_user),
):
    from .. import content_market
    user = require_user(user)
    rows = content_market.list_subscriptions_for_org(org_id)
    return {
        "rows": [
            {"id": s.id, "pack_id": s.pack_id, "seats": s.seats,
             "total_paid_paise": s.total_paid_paise,
             "started_at": s.started_at,
             "expires_at": s.expires_at, "status": s.status}
            for s in rows
        ],
    }


@router.get("/api/content/publishers/{pid}/earnings")
def content_publisher_earnings(pid: str):
    from .. import content_market
    return content_market.publisher_earnings(pid)


# ---------- N4: mentor program ----------

@router.post("/api/mentors/apply", status_code=201)
def apply_as_mentor(
    bio: str | None = Form(None, max_length=2000),
    expertise_subjects_csv: str | None = Form(None, max_length=500),
    expertise_exams_csv: str | None = Form(None, max_length=500),
    available_hours_week: int = Form(2, ge=1, le=40),
    year_of_passing: int | None = Form(None, ge=2000, le=2100),
    college: str | None = Form(None, max_length=200),
    languages_csv: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import mentorship
    user = require_user(user)
    subs = (
        [s.strip() for s in expertise_subjects_csv.split(",") if s.strip()]
        if expertise_subjects_csv else None
    )
    exams = (
        [s.strip() for s in expertise_exams_csv.split(",") if s.strip()]
        if expertise_exams_csv else None
    )
    langs = (
        [s.strip() for s in languages_csv.split(",") if s.strip()]
        if languages_csv else None
    )
    try:
        p = mentorship.apply(
            user_id=user.id, bio=bio,
            expertise_subjects=subs, expertise_exams=exams,
            available_hours_week=available_hours_week,
            year_of_passing=year_of_passing, college=college,
            languages=langs,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "user_id": p.user_id, "status": p.status,
        "expertise_subjects": p.expertise_subjects,
        "expertise_exams": p.expertise_exams,
    }


@router.get("/api/mentors/me")
def get_my_mentor_profile(user=Depends(current_user)):
    from .. import mentorship
    user = require_user(user)
    p = mentorship.get_profile(user.id)
    if not p:
        return {"profile": None}
    return {
        "profile": {
            "status": p.status, "bio": p.bio,
            "expertise_subjects": p.expertise_subjects,
            "expertise_exams": p.expertise_exams,
            "available_hours_week": p.available_hours_week,
            "year_of_passing": p.year_of_passing,
            "college": p.college,
            "languages": p.languages,
            "hours_logged": p.hours_logged,
            "free_months_earned": p.free_months_earned,
            "rating_avg": p.rating_avg,
            "rating_count": p.rating_count,
        },
    }


@router.post("/api/admin/mentors/{uid}/status")
def admin_set_mentor_status(
    uid: str, status: str = Form(..., max_length=12),
):
    from .. import mentorship
    try:
        ok = mentorship.set_mentor_status(user_id=uid, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "mentor not found")
    return {"ok": True}


@router.get("/api/mentors")
def list_mentors_endpoint(
    subject: str | None = None,
    exam: str | None = None,
    limit: int = 30,
):
    from .. import mentorship
    rows = mentorship.list_active_mentors(
        subject=subject, exam=exam, limit=limit,
    )
    return {
        "rows": [
            {"user_id": p.user_id, "bio": p.bio,
             "expertise_subjects": p.expertise_subjects,
             "expertise_exams": p.expertise_exams,
             "available_hours_week": p.available_hours_week,
             "year_of_passing": p.year_of_passing,
             "college": p.college,
             "languages": p.languages,
             "rating_avg": p.rating_avg,
             "rating_count": p.rating_count,
             "hours_logged": p.hours_logged}
            for p in rows
        ],
    }


@router.post("/api/mentors/{mentor_user_id}/sessions", status_code=201)
def request_mentor_session(
    mentor_user_id: str,
    scheduled_at: float = Form(..., gt=0),
    duration_min: int = Form(30, ge=15, le=180),
    topic: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import mentorship
    user = require_user(user)
    try:
        s = mentorship.request_session(
            mentor_user_id=mentor_user_id,
            mentee_user_id=user.id,
            scheduled_at=scheduled_at,
            duration_min=duration_min, topic=topic,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": s.id, "scheduled_at": s.scheduled_at,
        "duration_min": s.duration_min, "status": s.status,
    }


@router.post("/api/mentor-sessions/{sid}/complete")
def complete_mentor_session(
    sid: str,
    actual_duration_min: int | None = Form(None, ge=0, le=300),
    notes: str | None = Form(None, max_length=8000),
    user=Depends(current_user),
):
    from .. import mentorship
    user = require_user(user)
    try:
        s = mentorship.complete_session(
            session_id=sid, mentor_user_id=user.id,
            actual_duration_min=actual_duration_min, notes=notes,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": s.id, "status": s.status,
        "actual_duration_min": s.actual_duration_min,
    }


@router.post("/api/mentor-sessions/{sid}/cancel")
def cancel_mentor_session(sid: str, user=Depends(current_user)):
    from .. import mentorship
    user = require_user(user)
    if not mentorship.cancel_session(
        session_id=sid, user_id=user.id,
    ):
        raise HTTPException(409, "cannot cancel (wrong user or not scheduled)")
    return {"ok": True}


@router.get("/api/mentor-sessions")
def list_my_mentor_sessions(
    role: str | None = None, limit: int = 30,
    user=Depends(current_user),
):
    from .. import mentorship
    user = require_user(user)
    rows = mentorship.list_sessions_for_user(
        user.id, role=role, limit=limit,
    )
    return {
        "rows": [
            {"id": s.id, "mentor_user_id": s.mentor_user_id,
             "mentee_user_id": s.mentee_user_id,
             "scheduled_at": s.scheduled_at,
             "duration_min": s.duration_min, "topic": s.topic,
             "status": s.status, "completed_at": s.completed_at,
             "actual_duration_min": s.actual_duration_min}
            for s in rows
        ],
    }


@router.post("/api/mentor-sessions/{sid}/review", status_code=201)
def review_mentor_session(
    sid: str,
    rating: int = Form(..., ge=1, le=5),
    feedback: str | None = Form(None, max_length=4000),
    user=Depends(current_user),
):
    from .. import mentorship
    user = require_user(user)
    try:
        rid = mentorship.review_session(
            session_id=sid, reviewer_user_id=user.id,
            rating=rating, feedback=feedback,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"review_id": rid}


@router.get("/api/mentors/{mentor_user_id}/reviews")
def list_mentor_reviews(mentor_user_id: str, limit: int = 20):
    from .. import mentorship
    return {"rows": mentorship.list_reviews(
        mentor_user_id=mentor_user_id, limit=limit,
    )}


# ---------- P1: NEP 2020 + NCF 2023 alignment ----------

@router.get("/api/frameworks/nep")
def list_nep_competencies(category: str | None = None):
    from .. import nep_alignment
    rows = nep_alignment.list_nep(category=category)
    return {
        "rows": [
            {"key": c.key, "label": c.label,
             "description": c.description,
             "category": c.category, "keywords": c.keywords}
            for c in rows
        ],
    }


@router.get("/api/frameworks/ncf")
def list_ncf_competencies(
    stage: str | None = None, area: str | None = None,
):
    from .. import nep_alignment
    try:
        rows = nep_alignment.list_ncf(stage=stage, area=area)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"key": c.key, "label": c.label,
             "description": c.description,
             "stage": c.stage, "area": c.area,
             "keywords": c.keywords}
            for c in rows
        ],
    }


@router.post("/api/frameworks/score")
def score_text_against_framework(
    request: Request,
    text: str = Form(..., min_length=20, max_length=40000),
    framework: str = Form(..., max_length=8),
    stage: str | None = Form(None, max_length=16),
    lesson_id: str | None = Form(None, max_length=64),
):
    """Public + rate-limited (reuses curriculum-scorer bucket).
    Returns matched competencies + overall coverage score. When
    `lesson_id` provided, persists for later coverage reports."""
    from .. import nep_alignment, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    if not rate_limit.preview_scorer.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    try:
        result = nep_alignment.score_text(
            text=text, framework=framework, stage=stage,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if lesson_id:
        try:
            nep_alignment.persist_lesson_alignment(
                lesson_id=lesson_id, framework=framework,
                matches=result.matches,
            )
        except ValueError:
            pass
    return {
        "framework": result.framework,
        "overall_score": result.overall_score,
        "total_competencies": result.total_competencies,
        "matches": result.matches,
    }


@router.get("/api/frameworks/lessons/{lesson_id}/alignment")
def get_lesson_framework_alignment(
    lesson_id: str,
    framework: str | None = None,
):
    from .. import nep_alignment
    rows = nep_alignment.get_lesson_alignment(
        lesson_id=lesson_id, framework=framework,
    )
    return {"rows": rows}


@router.post("/api/frameworks/coverage")
def framework_coverage_summary(
    lesson_ids_csv: str = Form(..., max_length=4000),
    framework: str = Form(..., max_length=8),
):
    from .. import nep_alignment
    ids = [s.strip() for s in lesson_ids_csv.split(",") if s.strip()]
    if not ids:
        raise HTTPException(400, "lesson_ids_csv required (non-empty)")
    return nep_alignment.coverage_summary(
        lesson_ids=ids, framework=framework,
    )


# ---------- P2: DIKSHA + NDEAR interoperability ----------

@router.get("/api/diksha/status")
def diksha_status():
    from .. import diksha
    return {
        "api_configured": diksha.is_api_configured(),
        "ndear_version": diksha.NDEAR_VERSION,
    }


@router.post("/api/diksha/import")
def diksha_import(
    diksha_id: str = Form(..., max_length=200),
    title: str | None = Form(None, max_length=500),
    description: str | None = Form(None, max_length=4000),
    board: str | None = Form(None, max_length=32),
    grade: str | None = Form(None, max_length=8),
    subject: str | None = Form(None, max_length=64),
    medium: str | None = Form(None, max_length=16),
    content_type: str | None = Form(None, max_length=16),
    content_url: str | None = Form(None, max_length=2048),
    use_api: bool = Form(False),
    user=Depends(current_user),
):
    """Admin imports DIKSHA content. With `use_api=true` (and DIKSHA
    API key configured), we fetch the manifest from DIKSHA. Otherwise
    we accept the manifest fields inline."""
    from .. import diksha
    user = require_user(user)
    try:
        if use_api:
            ref = diksha.import_from_api(
                diksha_id=diksha_id, imported_by=user.id,
            )
        else:
            if not title:
                raise HTTPException(
                    400, "title required when not using API path",
                )
            ref = diksha.import_from_manifest(
                diksha_id=diksha_id, title=title,
                description=description, board=board, grade=grade,
                subject=subject, medium=medium,
                content_type=content_type, content_url=content_url,
                imported_by=user.id,
            )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": ref.id, "diksha_id": ref.diksha_id,
        "title": ref.title, "content_type": ref.content_type,
        "imported_at": ref.imported_at,
    }


@router.get("/api/diksha/refs")
def list_diksha_refs(
    board: str | None = None, grade: str | None = None,
    subject: str | None = None, limit: int = 50,
):
    from .. import diksha
    rows = diksha.list_refs(
        board=board, grade=grade, subject=subject, limit=limit,
    )
    return {
        "rows": [
            {"id": r.id, "diksha_id": r.diksha_id,
             "title": r.title, "description": r.description,
             "board": r.board, "grade": r.grade,
             "subject": r.subject, "medium": r.medium,
             "content_type": r.content_type,
             "content_url": r.content_url,
             "imported_at": r.imported_at}
            for r in rows
        ],
    }


@router.get("/api/diksha/refs/{ref_id}")
def get_diksha_ref(ref_id: str):
    from .. import diksha
    r = diksha.get_ref(ref_id)
    if not r:
        raise HTTPException(404, "ref not found")
    return {
        "id": r.id, "diksha_id": r.diksha_id, "title": r.title,
        "description": r.description, "board": r.board,
        "grade": r.grade, "subject": r.subject, "medium": r.medium,
        "content_type": r.content_type, "content_url": r.content_url,
        "metadata": r.metadata, "imported_at": r.imported_at,
    }


@router.post("/api/diksha/export-ndear")
def export_lesson_ndear(
    lesson_id: str = Form(..., max_length=64),
    title: str = Form(..., min_length=4, max_length=500),
    content_url: str = Form(..., max_length=2048),
    language: str = Form("en", max_length=8),
    description: str | None = Form(None, max_length=4000),
    board: str | None = Form(None, max_length=32),
    grade: int | None = Form(None, ge=1, le=16),
    subject: str | None = Form(None, max_length=64),
    duration_seconds: int | None = Form(None, ge=0, le=86400),
    license: str = Form("CC-BY-SA-4.0", max_length=64),
    user=Depends(current_user),
):
    from .. import diksha, nep_alignment
    user = require_user(user)
    # Pull alignment if persisted
    nep = nep_alignment.get_lesson_alignment(
        lesson_id=lesson_id, framework="nep",
    )
    ncf = nep_alignment.get_lesson_alignment(
        lesson_id=lesson_id, framework="ncf",
    )
    manifest = diksha.build_ndear_manifest(
        lesson_id=lesson_id, title=title, description=description,
        board=board, grade=grade, subject=subject,
        language=language, content_url=content_url,
        duration_seconds=duration_seconds, license=license,
        nep_alignment=nep, ncf_alignment=ncf,
    )
    rec = diksha.record_export(
        lesson_id=lesson_id, manifest=manifest,
        exported_by=user.id,
    )
    return {
        "export_id": rec.id, "manifest_sha": rec.manifest_sha,
        "ndear_version": rec.ndear_version,
        "manifest": manifest,
    }


@router.get("/api/diksha/lessons/{lesson_id}/latest-export")
def latest_ndear_export(lesson_id: str):
    from .. import diksha
    e = diksha.latest_export(lesson_id)
    if not e:
        return {"export": None}
    return {
        "export": {
            "id": e.id, "manifest_sha": e.manifest_sha,
            "ndear_version": e.ndear_version,
            "exported_at": e.exported_at,
            "exported_by": e.exported_by,
        },
    }


# ---------- Q4: customer success ----------

@router.post("/api/admin/cs/health/compute")
def cs_compute_health(
    org_id: str = Form(..., max_length=64),
    period_days: int = Form(30, ge=1, le=365),
):
    from .. import customer_success
    try:
        hs = customer_success.compute_health(
            org_id=org_id, period_days=period_days,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": hs.id, "org_id": hs.org_id, "score": hs.score,
        "band": hs.band, "components": hs.components,
        "computed_at": hs.computed_at,
        "period_days": hs.period_days,
    }


@router.get("/api/admin/cs/health/{org_id}")
def cs_latest_health(org_id: str):
    from .. import customer_success
    hs = customer_success.latest_health(org_id)
    if not hs:
        return {"health": None}
    return {
        "health": {
            "score": hs.score, "band": hs.band,
            "components": hs.components,
            "computed_at": hs.computed_at,
            "period_days": hs.period_days,
        },
    }


@router.get("/api/admin/cs/at-risk")
def cs_at_risk(threshold: float = 50.0, limit: int = 50):
    from .. import customer_success
    if threshold < 0 or threshold > 100:
        raise HTTPException(400, "threshold must be in [0, 100]")
    rows = customer_success.at_risk_orgs(
        threshold=threshold, limit=limit,
    )
    return {
        "rows": [
            {"org_id": h.org_id, "score": h.score,
             "band": h.band, "components": h.components,
             "computed_at": h.computed_at}
            for h in rows
        ],
    }


@router.post("/api/admin/cs/events", status_code=201)
def cs_log_event(
    kind: str = Form(..., max_length=24),
    title: str = Form(..., min_length=2, max_length=200),
    org_id: str | None = Form(None, max_length=64),
    user_id: str | None = Form(None, max_length=64),
    severity: str = Form("info", max_length=12),
    body: str | None = Form(None, max_length=8000),
):
    from .. import customer_success
    try:
        e = customer_success.log_event(
            kind=kind, title=title, org_id=org_id,
            user_id=user_id, severity=severity, body=body,
        )
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {
        "id": e.id, "kind": e.kind, "severity": e.severity,
        "created_at": e.created_at,
    }


@router.post("/api/admin/cs/events/{eid}/resolve")
def cs_resolve_event(eid: str):
    from .. import customer_success
    if not customer_success.resolve_event(eid):
        raise HTTPException(404, "event not found or already resolved")
    return {"ok": True}


@router.get("/api/admin/cs/events")
def cs_unresolved_alerts(limit: int = 50):
    """Global CSM queue — unresolved alerts + escalations."""
    from .. import customer_success
    rows = customer_success.unresolved_alerts(limit=limit)
    return {
        "rows": [
            {"id": e.id, "org_id": e.org_id, "kind": e.kind,
             "severity": e.severity, "title": e.title,
             "body": e.body, "created_at": e.created_at}
            for e in rows
        ],
    }


@router.get("/api/admin/cs/orgs/{org_id}/events")
def cs_org_events(
    org_id: str,
    unresolved_only: bool = False,
    limit: int = 50,
):
    from .. import customer_success
    rows = customer_success.list_events_for_org(
        org_id=org_id, unresolved_only=unresolved_only, limit=limit,
    )
    return {
        "rows": [
            {"id": e.id, "kind": e.kind, "severity": e.severity,
             "title": e.title, "body": e.body,
             "resolved_at": e.resolved_at,
             "created_at": e.created_at}
            for e in rows
        ],
    }


@router.get("/api/admin/cs/onboarding/steps")
def cs_onboarding_steps():
    from .. import customer_success
    return {"steps": customer_success.get_onboarding_steps()}


@router.post("/api/admin/cs/onboarding/emit")
def cs_emit_onboarding(
    org_id: str = Form(..., max_length=64),
    step_key: str = Form(..., max_length=64),
):
    from .. import customer_success
    try:
        e = customer_success.emit_onboarding_step(
            org_id=org_id, step_key=step_key,
        )
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"event_id": e.id, "title": e.title}


@router.post("/api/admin/cs/renewals/upsert")
def cs_upsert_renewal(
    org_id: str = Form(..., max_length=64),
    plan_tier: str = Form(..., max_length=8),
    current_period_end: float = Form(..., gt=0),
    notes: str | None = Form(None, max_length=4000),
):
    from .. import customer_success
    try:
        r = customer_success.upsert_renewal(
            org_id=org_id, plan_tier=plan_tier,
            current_period_end=current_period_end, notes=notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "org_id": r.org_id, "plan_tier": r.plan_tier,
        "current_period_end": r.current_period_end,
        "predicted_renewal": r.predicted_renewal,
        "churn_risk": r.churn_risk,
    }


@router.get("/api/admin/cs/renewals")
def cs_upcoming_renewals(days_ahead: int = 90, limit: int = 100):
    from .. import customer_success
    if days_ahead < 1 or days_ahead > 730:
        raise HTTPException(400, "days_ahead must be in [1, 730]")
    rows = customer_success.upcoming_renewals(
        days_ahead=days_ahead, limit=limit,
    )
    return {
        "rows": [
            {"org_id": r.org_id, "plan_tier": r.plan_tier,
             "current_period_end": r.current_period_end,
             "predicted_renewal": r.predicted_renewal,
             "churn_risk": r.churn_risk,
             "last_action_at": r.last_action_at,
             "last_action_kind": r.last_action_kind,
             "notes": r.notes}
            for r in rows
        ],
    }


@router.post("/api/admin/cs/renewals/{org_id}/action")
def cs_record_renewal_action(
    org_id: str, action_kind: str = Form(..., max_length=32),
):
    from .. import customer_success
    if not customer_success.record_renewal_action(
        org_id=org_id, action_kind=action_kind,
    ):
        raise HTTPException(404, "renewal entry not found")
    return {"ok": True}


# ---------- P3: state partnerships ----------

@router.get("/api/states")
def list_state_partnerships(
    status: str | None = None, region: str | None = None,
):
    from .. import state_partnerships
    try:
        rows = state_partnerships.list_all(status=status, region=region)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"state_code": p.state_code, "name": p.name,
             "region": p.region,
             "primary_language": p.primary_language,
             "student_population": p.student_population,
             "status": p.status,
             "pilot_org_ids": p.pilot_org_ids,
             "syllabus_pack_ids": p.syllabus_pack_ids,
             "contract_value_inr": p.contract_value_inr,
             "start_date": p.start_date,
             "end_date": p.end_date,
             "contact_email": p.contact_email,
             "updated_at": p.updated_at}
            for p in rows
        ],
    }


@router.get("/api/states/{state_code}")
def get_state_partnership(state_code: str):
    from .. import state_partnerships
    p = state_partnerships.get(state_code)
    if not p:
        raise HTTPException(404, "state not found")
    return {
        "state_code": p.state_code, "name": p.name,
        "region": p.region, "primary_language": p.primary_language,
        "student_population": p.student_population,
        "status": p.status,
        "pilot_org_ids": p.pilot_org_ids,
        "syllabus_pack_ids": p.syllabus_pack_ids,
        "contract_value_inr": p.contract_value_inr,
        "start_date": p.start_date, "end_date": p.end_date,
        "contact_name": p.contact_name,
        "contact_email": p.contact_email,
        "contact_phone": p.contact_phone,
        "branding": p.branding, "notes": p.notes,
        "created_at": p.created_at, "updated_at": p.updated_at,
    }


@router.post("/api/admin/states/upsert")
def admin_upsert_state(
    state_code: str = Form(..., min_length=2, max_length=4),
    name: str | None = Form(None, max_length=200),
    region: str | None = Form(None, max_length=12),
    primary_language: str | None = Form(None, max_length=8),
    student_population: int | None = Form(None, ge=0, le=200_000_000),
    status: str | None = Form(None, max_length=16),
    contract_value_inr: int | None = Form(None, ge=0, le=10_000_000_000),
    contact_name: str | None = Form(None, max_length=200),
    contact_email: str | None = Form(None, max_length=200),
    notes: str | None = Form(None, max_length=10000),
):
    from .. import state_partnerships
    try:
        p = state_partnerships.upsert(
            state_code=state_code, name=name, region=region,
            primary_language=primary_language,
            student_population=student_population, status=status,
            contract_value_inr=contract_value_inr,
            contact_name=contact_name,
            contact_email=contact_email, notes=notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "state_code": p.state_code, "status": p.status,
        "updated_at": p.updated_at,
    }


@router.post("/api/admin/states/{state_code}/status")
def admin_set_state_status(
    state_code: str, status: str = Form(..., max_length=16),
):
    from .. import state_partnerships
    try:
        ok = state_partnerships.set_status(
            state_code=state_code, status=status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "state not found")
    return {"ok": True}


@router.get("/api/admin/states/pipeline")
def admin_state_pipeline():
    from .. import state_partnerships
    return state_partnerships.pipeline_summary()


# ---------- R1: corporate training ----------

@router.post("/api/admin/corporate/orgs", status_code=201)
def admin_register_corp_org(
    name: str = Form(..., min_length=2, max_length=200),
    industry: str | None = Form(None, max_length=64),
    headcount: int | None = Form(None, ge=0, le=10_000_000),
    contact_name: str | None = Form(None, max_length=200),
    contact_email: str | None = Form(None, max_length=200),
    integration_kind: str = Form("api", max_length=12),
    seat_limit: int = Form(100, ge=1, le=1_000_000),
):
    from .. import corporate
    try:
        c = corporate.register_corp_org(
            name=name, industry=industry, headcount=headcount,
            contact_name=contact_name, contact_email=contact_email,
            integration_kind=integration_kind, seat_limit=seat_limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": c.id, "name": c.name,
        "integration_kind": c.integration_kind,
        "seat_limit": c.seat_limit, "status": c.status,
    }


@router.get("/api/admin/corporate/orgs")
def admin_list_corp_orgs(status: str | None = None):
    from .. import corporate
    try:
        rows = corporate.list_corp_orgs(status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": c.id, "name": c.name, "industry": c.industry,
             "headcount": c.headcount,
             "integration_kind": c.integration_kind,
             "seat_limit": c.seat_limit, "status": c.status,
             "created_at": c.created_at}
            for c in rows
        ],
    }


@router.post("/api/corporate/paths", status_code=201)
def create_training_path(
    corp_org_id: str = Form(..., max_length=64),
    title: str = Form(..., min_length=4, max_length=200),
    modules_json: str = Form(..., max_length=100000),
    description: str | None = Form(None, max_length=4000),
    category: str | None = Form(None, max_length=24),
    duration_min: int | None = Form(None, ge=1, le=10000),
    user=Depends(current_user),
):
    from .. import corporate
    import json as _json
    user = require_user(user)
    try:
        modules = _json.loads(modules_json)
        if not isinstance(modules, list):
            raise ValueError("modules_json must be a JSON array")
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"modules_json: {e}")
    try:
        p = corporate.create_path(
            corp_org_id=corp_org_id, title=title,
            modules=modules, description=description,
            category=category, duration_min=duration_min,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": p.id, "title": p.title, "status": p.status,
        "module_count": len(p.modules),
        "duration_min": p.duration_min,
    }


@router.get("/api/corporate/paths/{path_id}")
def get_training_path(path_id: str):
    from .. import corporate
    p = corporate.get_path(path_id)
    if not p:
        raise HTTPException(404, "path not found")
    return {
        "id": p.id, "corp_org_id": p.corp_org_id,
        "title": p.title, "description": p.description,
        "category": p.category, "modules": p.modules,
        "duration_min": p.duration_min, "status": p.status,
        "created_at": p.created_at, "published_at": p.published_at,
    }


@router.get("/api/corporate/paths")
def list_training_paths(
    corp_org_id: str | None = None,
    status: str | None = None,
    category: str | None = None,
):
    from .. import corporate
    try:
        rows = corporate.list_paths(
            corp_org_id=corp_org_id, status=status, category=category,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": p.id, "corp_org_id": p.corp_org_id,
             "title": p.title, "category": p.category,
             "module_count": len(p.modules),
             "duration_min": p.duration_min, "status": p.status}
            for p in rows
        ],
    }


@router.post("/api/corporate/paths/{path_id}/publish")
def publish_training_path(path_id: str):
    from .. import corporate
    try:
        ok = corporate.publish_path(path_id=path_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(409, "path not in draft or not found")
    return {"ok": True}


@router.post("/api/corporate/paths/{path_id}/enroll", status_code=201)
def enroll_in_path(
    path_id: str,
    employee_user_id: str = Form(..., max_length=64),
):
    from .. import corporate
    try:
        e = corporate.enroll(
            path_id=path_id, employee_user_id=employee_user_id,
        )
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {
        "id": e.id, "path_id": e.path_id,
        "employee_user_id": e.employee_user_id,
        "status": e.status, "enrolled_at": e.enrolled_at,
    }


@router.get("/api/corporate/enrollments/{enrollment_id}")
def get_enrollment_endpoint(enrollment_id: str):
    from .. import corporate
    e = corporate.get_enrollment(enrollment_id)
    if not e:
        raise HTTPException(404, "enrollment not found")
    return {
        "id": e.id, "path_id": e.path_id,
        "employee_user_id": e.employee_user_id,
        "enrolled_at": e.enrolled_at,
        "started_at": e.started_at,
        "completed_at": e.completed_at,
        "completion_pct": e.completion_pct,
        "status": e.status, "final_score": e.final_score,
    }


@router.post("/api/corporate/enrollments/{enrollment_id}/progress")
def update_enrollment_progress(
    enrollment_id: str,
    completion_pct: float = Form(..., ge=0, le=100),
    final_score: float | None = Form(None, ge=0, le=100),
):
    from .. import corporate
    try:
        e = corporate.update_progress(
            enrollment_id=enrollment_id,
            completion_pct=completion_pct,
            final_score=final_score,
        )
    except ValueError as ex:
        raise HTTPException(404, str(ex))
    return {
        "id": e.id, "completion_pct": e.completion_pct,
        "status": e.status, "completed_at": e.completed_at,
    }


@router.post("/api/corporate/enrollments/{enrollment_id}/xapi")
def emit_xapi_statement(
    enrollment_id: str,
    actor_user_id: str = Form(..., max_length=64),
    verb: str = Form(..., max_length=24),
    object_id: str = Form(..., max_length=200),
    object_kind: str | None = Form(None, max_length=32),
    result_json: str | None = Form(None, max_length=4000),
):
    from .. import corporate
    import json as _json
    result = None
    if result_json:
        try:
            result = _json.loads(result_json)
        except (ValueError, TypeError):
            raise HTTPException(400, "result_json must be valid JSON")
    try:
        sid = corporate.emit_xapi(
            enrollment_id=enrollment_id,
            actor_user_id=actor_user_id, verb=verb,
            object_id=object_id, object_kind=object_kind,
            result=result,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"statement_id": sid}


@router.get("/api/corporate/enrollments/{enrollment_id}/xapi")
def list_xapi_statements(enrollment_id: str, limit: int = 100):
    from .. import corporate
    rows = corporate.xapi_statements_for_enrollment(
        enrollment_id, limit=limit,
    )
    return {"rows": rows}


@router.get("/api/admin/corporate/orgs/{corp_id}/stats")
def corp_completion_stats(corp_id: str):
    from .. import corporate
    return corporate.org_completion_stats(corp_id)


# ---------- Q5: sales pipeline ----------

@router.post("/api/sales/leads", status_code=201)
def create_sales_lead(
    request: Request,
    source: str = Form(..., max_length=24),
    org_name: str = Form(..., min_length=2, max_length=200),
    org_kind: str | None = Form(None, max_length=24),
    contact_name: str | None = Form(None, max_length=200),
    contact_email: str | None = Form(None, max_length=200),
    contact_phone: str | None = Form(None, max_length=24),
    state_code: str | None = Form(None, max_length=4),
    estimated_seats: int | None = Form(None, ge=0, le=10_000_000),
    expected_value_inr: int | None = Form(None, ge=0, le=10_000_000_000),
    notes: str | None = Form(None, max_length=10000),
    owner_user_id: str | None = Form(None, max_length=64),
):
    """Open endpoint — the "request a demo" form posts here. Rate-
    limited per IP via the preview_scorer bucket so a bot can't
    spam-create leads."""
    from .. import sales_pipeline, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    if not rate_limit.preview_scorer.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    try:
        lead = sales_pipeline.create_lead(
            source=source, org_name=org_name, org_kind=org_kind,
            contact_name=contact_name, contact_email=contact_email,
            contact_phone=contact_phone, state_code=state_code,
            estimated_seats=estimated_seats,
            expected_value_inr=expected_value_inr,
            notes=notes, owner_user_id=owner_user_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": lead.id, "stage": lead.stage, "score": lead.score,
    }


@router.get("/api/admin/sales/leads")
def admin_list_leads(
    stage: str | None = None,
    owner_user_id: str | None = None,
    source: str | None = None,
    limit: int = 50, offset: int = 0,
):
    from .. import sales_pipeline
    try:
        rows = sales_pipeline.list_leads(
            stage=stage, owner_user_id=owner_user_id,
            source=source, limit=limit, offset=offset,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": l.id, "source": l.source,
             "org_name": l.org_name, "org_kind": l.org_kind,
             "contact_email": l.contact_email,
             "state_code": l.state_code,
             "estimated_seats": l.estimated_seats,
             "expected_value_inr": l.expected_value_inr,
             "stage": l.stage, "score": l.score,
             "owner_user_id": l.owner_user_id,
             "crm_external_id": l.crm_external_id,
             "created_at": l.created_at,
             "updated_at": l.updated_at}
            for l in rows
        ],
    }


@router.get("/api/admin/sales/leads/{lead_id}")
def admin_get_lead(lead_id: str):
    from .. import sales_pipeline
    l = sales_pipeline.get_lead(lead_id)
    if not l:
        raise HTTPException(404, "lead not found")
    return {
        "id": l.id, "source": l.source,
        "org_name": l.org_name, "org_kind": l.org_kind,
        "contact_name": l.contact_name,
        "contact_email": l.contact_email,
        "contact_phone": l.contact_phone,
        "state_code": l.state_code,
        "estimated_seats": l.estimated_seats,
        "expected_value_inr": l.expected_value_inr,
        "stage": l.stage, "score": l.score,
        "owner_user_id": l.owner_user_id,
        "crm_external_id": l.crm_external_id,
        "notes": l.notes,
        "created_at": l.created_at, "updated_at": l.updated_at,
    }


@router.post("/api/admin/sales/leads/{lead_id}/stage")
def admin_update_lead_stage(
    lead_id: str,
    new_stage: str = Form(..., max_length=16),
    note: str | None = Form(None, max_length=4000),
    user=Depends(current_user),
):
    from .. import sales_pipeline
    user = require_user(user)
    try:
        l = sales_pipeline.update_stage(
            lead_id=lead_id, new_stage=new_stage,
            user_id=user.id, note=note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": l.id, "stage": l.stage, "score": l.score,
    }


@router.post("/api/admin/sales/leads/{lead_id}/assign")
def admin_assign_lead(
    lead_id: str,
    owner_user_id: str = Form(..., max_length=64),
):
    from .. import sales_pipeline
    if not sales_pipeline.assign(
        lead_id=lead_id, owner_user_id=owner_user_id,
    ):
        raise HTTPException(404, "lead not found")
    return {"ok": True}


@router.post("/api/admin/sales/leads/{lead_id}/activities", status_code=201)
def admin_log_activity(
    lead_id: str,
    kind: str = Form(..., max_length=24),
    title: str = Form(..., min_length=2, max_length=200),
    body: str | None = Form(None, max_length=10000),
    user=Depends(current_user),
):
    from .. import sales_pipeline
    user = require_user(user)
    try:
        a = sales_pipeline.log_activity(
            lead_id=lead_id, kind=kind, title=title,
            body=body, created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": a.id, "kind": a.kind, "title": a.title,
        "created_at": a.created_at,
    }


@router.get("/api/admin/sales/leads/{lead_id}/activities")
def admin_list_activities(lead_id: str, limit: int = 100):
    from .. import sales_pipeline
    rows = sales_pipeline.list_activities(lead_id, limit=limit)
    return {
        "rows": [
            {"id": a.id, "kind": a.kind, "title": a.title,
             "body": a.body, "created_by": a.created_by,
             "created_at": a.created_at}
            for a in rows
        ],
    }


@router.get("/api/admin/sales/pipeline")
def admin_sales_pipeline():
    from .. import sales_pipeline
    return sales_pipeline.pipeline_summary()


@router.post("/api/admin/sales/leads/{lead_id}/recompute-score")
def admin_recompute_lead_score(lead_id: str):
    from .. import sales_pipeline
    score = sales_pipeline.compute_score(lead_id)
    sales_pipeline._set_score(lead_id, score)
    return {"score": score}


@router.post("/api/admin/sales/crm/{lead_id}/external-id")
def admin_set_crm_external_id(
    lead_id: str,
    crm_external_id: str = Form(..., max_length=200),
):
    """Called by the CRM-side webhook handler after the CRM creates
    its own record + returns its id. Closes the sync loop."""
    from .. import sales_pipeline
    if not sales_pipeline.set_crm_id(
        lead_id=lead_id, crm_external_id=crm_external_id,
    ):
        raise HTTPException(404, "lead not found")
    return {"ok": True}


# ---------- M4: 1:1 tutor marketplace ----------

def _tutor_to_dict(t) -> dict:
    return {
        "id": t.id, "user_id": t.user_id,
        "display_name": t.display_name, "bio": t.bio,
        "avatar_url": t.avatar_url, "exams": t.exams,
        "subjects": t.subjects, "languages": t.languages,
        "rate_inr_per_30min": t.rate_inr_per_30min,
        "platform_fee_pct": t.platform_fee_pct,
        "verified": t.verified, "status": t.status,
        "total_earnings_paise": t.total_earnings_paise,
        "booking_count": t.booking_count,
        "rating_avg": t.rating_avg,
        "rating_count": t.rating_count,
    }


def _booking_to_dict(b) -> dict:
    return {
        "id": b.id, "tutor_user_id": b.tutor_user_id,
        "student_user_id": b.student_user_id,
        "scheduled_at": b.scheduled_at,
        "duration_min": b.duration_min, "topic": b.topic,
        "price_paise": b.price_paise,
        "platform_fee_paise": b.platform_fee_paise,
        "tutor_payout_paise": b.tutor_payout_paise,
        "status": b.status, "payment_status": b.payment_status,
        "created_at": b.created_at,
        "confirmed_at": b.confirmed_at,
        "started_at": b.started_at,
        "completed_at": b.completed_at,
        "cancelled_at": b.cancelled_at,
        "cancellation_reason": b.cancellation_reason,
    }


@router.post("/api/marketplace/tutors/apply", status_code=201)
def mkt_tutor_apply(
    display_name: str = Form(..., min_length=2, max_length=80),
    rate_inr_per_30min: int = Form(..., ge=50, le=5000),
    bio: str | None = Form(None, max_length=2000),
    avatar_url: str | None = Form(None, max_length=500),
    exams: str | None = Form(None, max_length=300),
    subjects: str | None = Form(None, max_length=300),
    languages: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    """Apply to be a paid 1:1 tutor on the marketplace. Comma-sep
    lists for exams/subjects/languages. Status starts 'applied' →
    admin approves to 'active'."""
    from .. import tutor_marketplace as tm
    user = require_user(user)

    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()] or None

    try:
        t = tm.apply_as_tutor(
            user_id=user.id, display_name=display_name,
            rate_inr_per_30min=rate_inr_per_30min,
            bio=bio, avatar_url=avatar_url,
            exams=_split(exams), subjects=_split(subjects),
            languages=_split(languages),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _tutor_to_dict(t)


@router.get("/api/marketplace/tutors/me")
def mkt_tutor_me(user=Depends(current_user)):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    t = tm.get_tutor_by_user(user.id)
    if not t:
        raise HTTPException(404, "no tutor profile")
    return _tutor_to_dict(t)


@router.post("/api/admin/marketplace/tutors/{tutor_user_id}/approve")
def mkt_admin_approve_tutor(
    tutor_user_id: str,
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    ok = tm.approve_tutor(user_id=tutor_user_id)
    if not ok:
        raise HTTPException(404, "tutor not found or not in 'applied' state")
    return {"ok": True}


@router.post("/api/admin/marketplace/tutors/{tutor_user_id}/status")
def mkt_admin_set_tutor_status(
    tutor_user_id: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = tm.set_tutor_status(user_id=tutor_user_id, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "tutor not found")
    return {"ok": True}


@router.get("/api/marketplace/tutors")
def mkt_search_tutors(
    exam: str | None = None,
    subject: str | None = None,
    language: str | None = None,
    max_rate: int | None = None,
    limit: int = 30,
):
    """Public — search active tutors. No auth (browsing)."""
    from .. import tutor_marketplace as tm
    rows = tm.search_tutors(
        exam=exam, subject=subject, language=language,
        max_rate=max_rate, limit=limit,
    )
    return {"tutors": [_tutor_to_dict(t) for t in rows]}


@router.post("/api/marketplace/bookings", status_code=201)
def mkt_book(
    tutor_user_id: str = Form(..., max_length=64),
    scheduled_at: float = Form(...),
    duration_min: int = Form(30, ge=30, le=120),
    topic: str | None = Form(None, max_length=300),
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        b = tm.book_session(
            tutor_user_id=tutor_user_id,
            student_user_id=user.id,
            scheduled_at=scheduled_at,
            duration_min=duration_min, topic=topic,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _booking_to_dict(b)


@router.get("/api/marketplace/bookings")
def mkt_list_bookings(
    role: str | None = None,
    status: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        rows = tm.list_bookings(
            user_id=user.id, role=role, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"bookings": [_booking_to_dict(b) for b in rows]}


@router.post("/api/marketplace/bookings/{booking_id}/confirm")
def mkt_confirm(booking_id: str, user=Depends(current_user)):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        b = tm.confirm_booking(
            booking_id=booking_id, tutor_user_id=user.id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _booking_to_dict(b)


@router.post("/api/marketplace/bookings/{booking_id}/start")
def mkt_start(booking_id: str, user=Depends(current_user)):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        b = tm.start_session(booking_id=booking_id, user_id=user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _booking_to_dict(b)


@router.post("/api/marketplace/bookings/{booking_id}/complete")
def mkt_complete(booking_id: str, user=Depends(current_user)):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        b = tm.complete_booking(
            booking_id=booking_id, tutor_user_id=user.id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _booking_to_dict(b)


@router.post("/api/marketplace/bookings/{booking_id}/cancel")
def mkt_cancel(
    booking_id: str,
    reason: str | None = Form(None, max_length=500),
    refund: bool = Form(True),
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        b = tm.cancel_booking(
            booking_id=booking_id, user_id=user.id,
            reason=reason, refund=refund,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _booking_to_dict(b)


@router.post("/api/marketplace/bookings/{booking_id}/review", status_code=201)
def mkt_review(
    booking_id: str,
    rating: int = Form(..., ge=1, le=5),
    feedback: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    try:
        rid = tm.review_booking(
            booking_id=booking_id, reviewer_user_id=user.id,
            rating=rating, feedback=feedback,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": rid}


@router.get("/api/marketplace/tutors/{tutor_user_id}/reviews")
def mkt_list_reviews(tutor_user_id: str, limit: int = 20):
    from .. import tutor_marketplace as tm
    return {
        "reviews": tm.list_reviews(
            tutor_user_id=tutor_user_id, limit=limit,
        ),
    }


@router.get("/api/marketplace/tutors/me/earnings")
def mkt_tutor_earnings(user=Depends(current_user)):
    from .. import tutor_marketplace as tm
    user = require_user(user)
    return tm.tutor_earnings_summary(user.id)


# ---------- O3: question pack marketplace ----------

def _qp_to_dict(p) -> dict:
    return {
        "id": p.id, "setter_user_id": p.setter_user_id,
        "title": p.title, "description": p.description,
        "exam": p.exam, "board": p.board, "grade": p.grade,
        "subject": p.subject, "difficulty": p.difficulty,
        "question_count": p.question_count,
        "price_paise": p.price_paise, "status": p.status,
        "purchase_count": p.purchase_count,
        "rating_avg": p.rating_avg,
        "rating_count": p.rating_count,
        "preview_question_ids": p.preview_question_ids,
        "created_at": p.created_at,
        "published_at": p.published_at,
    }


def _setter_to_dict(s) -> dict:
    return {
        "id": s.id, "user_id": s.user_id,
        "display_name": s.display_name, "bio": s.bio,
        "credentials": s.credentials,
        "verified": s.verified,
        "platform_fee_pct": s.platform_fee_pct,
        "total_earnings_paise": s.total_earnings_paise,
        "pack_count": s.pack_count,
        "created_at": s.created_at,
    }


@router.post("/api/qb-market/setters/apply", status_code=201)
def qb_setter_apply(
    display_name: str = Form(..., min_length=2, max_length=80),
    bio: str | None = Form(None, max_length=2000),
    credentials: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        s = qpm.apply_as_setter(
            user_id=user.id, display_name=display_name,
            bio=bio, credentials=credentials,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _setter_to_dict(s)


@router.get("/api/qb-market/setters/me")
def qb_setter_me(user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    s = qpm.get_setter(user.id)
    if not s:
        raise HTTPException(404, "no setter profile")
    return _setter_to_dict(s)


@router.post("/api/admin/qb-market/setters/{setter_user_id}/verify")
def qb_admin_verify_setter(
    setter_user_id: str,
    user=Depends(current_user),
):
    from .. import question_pack_market as qpm
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    if not qpm.verify_setter(user_id=setter_user_id):
        raise HTTPException(404, "setter not found")
    return {"ok": True}


@router.get("/api/admin/qb-market/setters")
def qb_admin_list_setters(
    verified_only: bool = False,
    user=Depends(current_user),
):
    from .. import question_pack_market as qpm
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return {
        "setters": [
            _setter_to_dict(s)
            for s in qpm.list_setters(verified_only=verified_only)
        ],
    }


@router.post("/api/qb-market/packs", status_code=201)
def qb_create_pack(
    title: str = Form(..., min_length=4, max_length=200),
    price_paise: int = Form(..., ge=1900, le=500000),
    description: str | None = Form(None, max_length=2000),
    exam: str | None = Form(None, max_length=40),
    board: str | None = Form(None, max_length=40),
    grade: int | None = Form(None, ge=1, le=16),
    subject: str | None = Form(None, max_length=60),
    difficulty: str | None = Form(None, max_length=10),
    user=Depends(current_user),
):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        p = qpm.create_pack(
            setter_user_id=user.id, title=title,
            price_paise=price_paise, description=description,
            exam=exam, board=board, grade=grade,
            subject=subject, difficulty=difficulty,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _qp_to_dict(p)


@router.post("/api/qb-market/packs/{pack_id}/questions")
def qb_add_question(
    pack_id: str,
    question_id: str = Form(..., max_length=64),
    user=Depends(current_user),
):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        count = qpm.add_question(
            pack_id=pack_id, setter_user_id=user.id,
            question_id=question_id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"question_count": count}


@router.post("/api/qb-market/packs/{pack_id}/publish")
def qb_publish_pack(pack_id: str, user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        ok = qpm.publish_pack(pack_id=pack_id, setter_user_id=user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}


@router.post("/api/qb-market/packs/{pack_id}/archive")
def qb_archive_pack(pack_id: str, user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        ok = qpm.archive_pack(pack_id=pack_id, setter_user_id=user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    return {"ok": ok}


@router.get("/api/qb-market/packs")
def qb_browse_packs(
    exam: str | None = None,
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    max_price_paise: int | None = None,
    limit: int = 30,
    offset: int = 0,
):
    """Public — browse published question packs."""
    from .. import question_pack_market as qpm
    rows = qpm.browse_packs(
        exam=exam, board=board, grade=grade, subject=subject,
        max_price_paise=max_price_paise,
        limit=limit, offset=offset,
    )
    return {"packs": [_qp_to_dict(p) for p in rows]}


@router.get("/api/qb-market/packs/{pack_id}")
def qb_get_pack(pack_id: str):
    from .. import question_pack_market as qpm
    p = qpm.get_pack(pack_id)
    if not p:
        raise HTTPException(404, "pack not found")
    return _qp_to_dict(p)


@router.post("/api/qb-market/packs/{pack_id}/purchase", status_code=201)
def qb_purchase(pack_id: str, user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    try:
        p = qpm.purchase_pack(pack_id=pack_id, buyer_user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": p.id, "pack_id": p.pack_id,
        "price_paise": p.price_paise,
        "platform_fee_paise": p.platform_fee_paise,
        "setter_payout_paise": p.setter_payout_paise,
        "purchased_at": p.purchased_at,
    }


@router.get("/api/qb-market/packs/{pack_id}/questions")
def qb_pack_question_ids(pack_id: str, user=Depends(current_user)):
    """Full question list — paywalled. Caller must have purchased
    the pack (or be the setter)."""
    from .. import question_pack_market as qpm
    user = require_user(user)
    p = qpm.get_pack(pack_id)
    if not p:
        raise HTTPException(404, "pack not found")
    if p.setter_user_id != user.id and not qpm.user_has_pack(
        pack_id=pack_id, user_id=user.id,
    ):
        raise HTTPException(403, "purchase required")
    return {"question_ids": qpm.list_pack_questions(pack_id)}


@router.get("/api/qb-market/me/purchases")
def qb_my_purchases(user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    return {
        "purchases": [
            {"id": p.id, "pack_id": p.pack_id,
             "price_paise": p.price_paise,
             "purchased_at": p.purchased_at,
             "refunded_at": p.refunded_at}
            for p in qpm.list_user_purchases(user.id)
        ],
    }


@router.get("/api/qb-market/setters/me/packs")
def qb_my_packs(user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    return {
        "packs": [_qp_to_dict(p)
                  for p in qpm.list_setter_packs(user.id)],
    }


@router.get("/api/qb-market/setters/me/earnings")
def qb_setter_earnings(user=Depends(current_user)):
    from .. import question_pack_market as qpm
    user = require_user(user)
    return qpm.setter_earnings_summary(user.id)


# ---------- R3: vouchers + bundles ----------

def _voucher_to_dict(v) -> dict:
    return {
        "code": v.code, "kind": v.kind, "value": v.value,
        "applies_to": v.applies_to, "max_uses": v.max_uses,
        "max_uses_per_user": v.max_uses_per_user,
        "redeemed_count": v.redeemed_count,
        "starts_at": v.starts_at, "expires_at": v.expires_at,
        "min_order_paise": v.min_order_paise,
        "status": v.status, "description": v.description,
        "created_at": v.created_at,
    }


def _bundle_to_dict(b) -> dict:
    return {
        "id": b.id, "title": b.title,
        "sku_codes": b.sku_codes,
        "bundle_discount_pct": b.bundle_discount_pct,
        "status": b.status, "description": b.description,
        "created_at": b.created_at,
    }


@router.post("/api/admin/vouchers", status_code=201)
def vch_admin_create(
    code: str = Form(..., min_length=3, max_length=32),
    kind: str = Form(..., max_length=10),
    value: int = Form(..., ge=1),
    applies_to: str | None = Form(None, max_length=500),
    max_uses: int | None = Form(None, ge=1),
    max_uses_per_user: int = Form(1, ge=1, le=100),
    starts_at: float | None = Form(None),
    expires_at: float | None = Form(None),
    min_order_paise: int = Form(0, ge=0),
    description: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import vouchers as vch
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    sku_list = (
        [x.strip() for x in (applies_to or "").split(",") if x.strip()]
        or None
    )
    try:
        v = vch.create_voucher(
            code=code, kind=kind, value=value,
            applies_to=sku_list, max_uses=max_uses,
            max_uses_per_user=max_uses_per_user,
            starts_at=starts_at, expires_at=expires_at,
            min_order_paise=min_order_paise,
            description=description,
            created_by_user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _voucher_to_dict(v)


@router.get("/api/admin/vouchers")
def vch_admin_list(
    status: str | None = None,
    user=Depends(current_user),
):
    from .. import vouchers as vch
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = vch.list_vouchers(status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"vouchers": [_voucher_to_dict(v) for v in rows]}


@router.post("/api/admin/vouchers/{code}/status")
def vch_admin_set_status(
    code: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import vouchers as vch
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = vch.set_voucher_status(code=code, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "voucher not found")
    return {"ok": True}


@router.post("/api/vouchers/validate")
def vch_validate(
    code: str = Form(..., min_length=3, max_length=32),
    order_paise: int = Form(..., ge=1),
    sku: str | None = Form(None, max_length=80),
    user=Depends(current_user),
):
    """Dry-run a voucher against an order. Returns discount or 400
    with the user-facing reason it can't be applied."""
    from .. import vouchers as vch
    user = require_user(user)
    try:
        r = vch.validate_voucher(
            code=code, user_id=user.id,
            order_paise=order_paise, sku=sku,
        )
    except vch.VoucherError as e:
        raise HTTPException(400, str(e))
    return {
        "voucher_code": r.voucher_code,
        "discount_paise": r.discount_paise,
        "final_paise": r.final_paise,
        "reason": r.reason,
    }


@router.post("/api/vouchers/redeem")
def vch_redeem(
    code: str = Form(..., min_length=3, max_length=32),
    order_paise: int = Form(..., ge=1),
    sku: str | None = Form(None, max_length=80),
    user=Depends(current_user),
):
    """Record the redemption. Caller charges discounted amount."""
    from .. import vouchers as vch
    user = require_user(user)
    try:
        r = vch.redeem_voucher(
            code=code, user_id=user.id,
            order_paise=order_paise, sku=sku,
        )
    except vch.VoucherError as e:
        raise HTTPException(400, str(e))
    return {
        "voucher_code": r.voucher_code,
        "discount_paise": r.discount_paise,
        "final_paise": r.final_paise,
    }


@router.get("/api/vouchers/me/redemptions")
def vch_my_redemptions(
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import vouchers as vch
    user = require_user(user)
    return {
        "redemptions": vch.list_user_redemptions(user.id, limit=limit),
    }


@router.post("/api/admin/bundles", status_code=201)
def bun_admin_create(
    title: str = Form(..., min_length=4, max_length=200),
    sku_codes: str = Form(..., max_length=1000),
    bundle_discount_pct: int = Form(..., ge=1, le=80),
    description: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import vouchers as vch
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    skus = [x.strip() for x in sku_codes.split(",") if x.strip()]
    try:
        b = vch.create_bundle(
            title=title, sku_codes=skus,
            bundle_discount_pct=bundle_discount_pct,
            description=description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _bundle_to_dict(b)


@router.get("/api/bundles")
def bun_list(active_only: bool = True):
    """Public — browse active bundles."""
    from .. import vouchers as vch
    return {
        "bundles": [
            _bundle_to_dict(b)
            for b in vch.list_bundles(active_only=active_only)
        ],
    }


@router.post("/api/bundles/match")
def bun_match(
    sku_codes: str = Form(..., max_length=1000),
    total_paise: int = Form(..., ge=1),
):
    """Given a cart of SKUs + total, return the best bundle discount
    that applies. Public — no auth needed; pricing is deterministic."""
    from .. import vouchers as vch
    skus = [x.strip() for x in sku_codes.split(",") if x.strip()]
    if not skus:
        raise HTTPException(400, "sku_codes required")
    try:
        r = vch.apply_bundle(skus=skus, total_paise=total_paise)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "bundle_id": r.bundle_id,
        "discount_paise": r.discount_paise,
        "final_paise": r.final_paise,
        "reason": r.reason,
    }


# ---------- R2: university / NPTEL extension ----------

def _partner_to_dict(p) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind,
        "integration_kind": p.integration_kind,
        "contact_email": p.contact_email,
        "contract_value_inr": p.contract_value_inr,
        "contracted_students": p.contracted_students,
        "revenue_share_pct": p.revenue_share_pct,
        "status": p.status,
        "lti_client_id": p.lti_client_id,
        "lti_deployment_id": p.lti_deployment_id,
        "created_at": p.created_at,
        "contracted_at": p.contracted_at,
    }


def _u_course_to_dict(c) -> dict:
    return {
        "id": c.id, "partner_id": c.partner_id,
        "course_code": c.course_code, "title": c.title,
        "description": c.description,
        "duration_weeks": c.duration_weeks, "credits": c.credits,
        "lesson_manifest_url": c.lesson_manifest_url,
        "status": c.status,
        "enrollment_count": c.enrollment_count,
        "completion_count": c.completion_count,
        "created_at": c.created_at,
        "published_at": c.published_at,
    }


def _u_enroll_to_dict(e) -> dict:
    return {
        "id": e.id, "course_id": e.course_id,
        "partner_id": e.partner_id,
        "partner_student_id": e.partner_student_id,
        "our_user_id": e.our_user_id,
        "status": e.status, "completion_pct": e.completion_pct,
        "final_score": e.final_score,
        "enrolled_at": e.enrolled_at,
        "started_at": e.started_at,
        "completed_at": e.completed_at,
    }


@router.post("/api/admin/university/partners", status_code=201)
def univ_register(
    name: str = Form(..., min_length=2, max_length=200),
    kind: str = Form(..., max_length=20),
    integration_kind: str = Form(..., max_length=20),
    contact_email: str | None = Form(None, max_length=200),
    contract_value_inr: int | None = Form(None, ge=0),
    contracted_students: int | None = Form(None, ge=0),
    revenue_share_pct: float = Form(0.30, ge=0.10, le=0.70),
    notes: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        p = upart.register_partner(
            name=name, kind=kind, integration_kind=integration_kind,
            contact_email=contact_email,
            contract_value_inr=contract_value_inr,
            contracted_students=contracted_students,
            revenue_share_pct=revenue_share_pct, notes=notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _partner_to_dict(p)


@router.get("/api/admin/university/partners")
def univ_list(
    status: str | None = None,
    kind: str | None = None,
    user=Depends(current_user),
):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = upart.list_partners(status=status, kind=kind)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"partners": [_partner_to_dict(p) for p in rows]}


@router.post("/api/admin/university/partners/{partner_id}/status")
def univ_set_status(
    partner_id: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = upart.set_partner_status(
            partner_id=partner_id, status=status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "partner not found")
    return {"ok": True}


@router.post("/api/admin/university/partners/{partner_id}/lti")
def univ_set_lti(
    partner_id: str,
    lti_client_id: str = Form(..., max_length=200),
    lti_deployment_id: str = Form(..., max_length=200),
    user=Depends(current_user),
):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = upart.set_lti_config(
            partner_id=partner_id,
            lti_client_id=lti_client_id,
            lti_deployment_id=lti_deployment_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(
            404, "partner not found or not lti13-integrated",
        )
    return {"ok": True}


@router.post("/api/university/courses", status_code=201)
def univ_create_course(
    partner_id: str = Form(..., max_length=64),
    course_code: str = Form(..., max_length=60),
    title: str = Form(..., min_length=4, max_length=200),
    description: str | None = Form(None, max_length=2000),
    duration_weeks: int | None = Form(None, ge=1, le=104),
    credits: int | None = Form(None, ge=0, le=20),
    lesson_manifest_url: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        c = upart.create_course(
            partner_id=partner_id, course_code=course_code,
            title=title, description=description,
            duration_weeks=duration_weeks, credits=credits,
            lesson_manifest_url=lesson_manifest_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _u_course_to_dict(c)


@router.get("/api/university/courses")
def univ_list_courses(
    partner_id: str | None = None,
    status: str | None = None,
):
    """Public — partner courses are intended for partner LMSes to
    browse + their students to enroll into."""
    from .. import university_partners as upart
    try:
        rows = upart.list_courses(
            partner_id=partner_id, status=status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"courses": [_u_course_to_dict(c) for c in rows]}


@router.post("/api/university/courses/{course_id}/publish")
def univ_publish_course(course_id: str, user=Depends(current_user)):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = upart.publish_course(course_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}


@router.post("/api/university/courses/{course_id}/enroll", status_code=201)
def univ_enroll(
    course_id: str,
    partner_student_id: str = Form(..., max_length=100),
    our_user_id: str | None = Form(None, max_length=64),
):
    """Open — partner LMS posts here w/ their student id. Auth
    happens via the partner's api_key_hash check that gates LTI/
    REST traffic upstream (out of scope for this endpoint)."""
    from .. import university_partners as upart
    try:
        e = upart.enroll_student(
            course_id=course_id,
            partner_student_id=partner_student_id,
            our_user_id=our_user_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _u_enroll_to_dict(e)


@router.post("/api/university/enrollments/{enrollment_id}/progress")
def univ_progress(
    enrollment_id: str,
    completion_pct: float = Form(..., ge=0, le=100),
    final_score: float | None = Form(None, ge=0, le=100),
):
    from .. import university_partners as upart
    try:
        e = upart.update_progress(
            enrollment_id=enrollment_id,
            completion_pct=completion_pct,
            final_score=final_score,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _u_enroll_to_dict(e)


@router.get("/api/admin/university/partners/{partner_id}/stats")
def univ_stats(partner_id: str, user=Depends(current_user)):
    from .. import university_partners as upart
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return upart.partner_stats(partner_id)


# ---------- R4: affiliate program ----------

def _aff_to_dict(a) -> dict:
    return {
        "code": a.code, "user_id": a.user_id,
        "display_name": a.display_name, "email": a.email,
        "kind": a.kind, "commission_pct": a.commission_pct,
        "status": a.status,
        "total_clicks": a.total_clicks,
        "total_conversions": a.total_conversions,
        "total_earned_paise": a.total_earned_paise,
        "created_at": a.created_at,
    }


@router.post("/api/affiliates/register", status_code=201)
def aff_register(
    code: str = Form(..., min_length=3, max_length=32),
    display_name: str = Form(..., min_length=2, max_length=100),
    kind: str = Form(..., max_length=20),
    email: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import affiliates as aff_mod
    user = require_user(user)
    try:
        a = aff_mod.register_affiliate(
            user_id=user.id, code=code, display_name=display_name,
            kind=kind, email=email,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _aff_to_dict(a)


@router.get("/api/affiliates/me")
def aff_me(user=Depends(current_user)):
    from .. import affiliates as aff_mod
    user = require_user(user)
    a = aff_mod.get_affiliate_by_user(user.id)
    if not a:
        raise HTTPException(404, "not registered as affiliate")
    return _aff_to_dict(a)


@router.get("/api/affiliates/me/earnings")
def aff_me_earnings(user=Depends(current_user)):
    from .. import affiliates as aff_mod
    user = require_user(user)
    a = aff_mod.get_affiliate_by_user(user.id)
    if not a:
        raise HTTPException(404, "not registered as affiliate")
    return aff_mod.affiliate_earnings(a.code)


@router.post("/api/affiliates/track-visit")
def aff_track_visit(
    request: Request,
    code: str = Form(..., min_length=3, max_length=32),
    landing_path: str | None = Form(None, max_length=500),
    utm_source: str | None = Form(None, max_length=80),
    utm_medium: str | None = Form(None, max_length=80),
    utm_campaign: str | None = Form(None, max_length=80),
):
    """Open — affiliate landing pixel. Returns ok regardless of
    code validity (don't leak which codes are real)."""
    from .. import affiliates as aff_mod
    import hashlib as _h
    ua = request.headers.get("user-agent", "")[:300]
    ip = request.client.host if request.client else ""
    ip_hash = _h.sha256(ip.encode("utf-8")).hexdigest() if ip else None
    vid = aff_mod.record_visit(
        code=code, landing_path=landing_path,
        utm_source=utm_source, utm_medium=utm_medium,
        utm_campaign=utm_campaign,
        user_agent=ua, ip_hash=ip_hash,
    )
    return {"visit_id": vid}


@router.post("/api/affiliates/attribute")
def aff_attribute(
    code: str = Form(..., min_length=3, max_length=32),
    landing_visit_id: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    """Caller posts here on user signup if a `ref=` was present in
    their landing URL. Idempotent — first attribution wins."""
    from .. import affiliates as aff_mod
    user = require_user(user)
    attr = aff_mod.attribute_user(
        user_id=user.id, affiliate_code=code,
        landing_visit_id=landing_visit_id,
    )
    if not attr:
        raise HTTPException(400, "affiliate code unknown or inactive")
    return {
        "user_id": attr.user_id,
        "affiliate_code": attr.affiliate_code,
        "attributed_at": attr.attributed_at,
        "commission_until": attr.commission_until,
    }


@router.get("/api/admin/affiliates")
def aff_admin_list(
    status: str | None = None,
    user=Depends(current_user),
):
    from .. import affiliates as aff_mod
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = aff_mod.list_affiliates(status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"affiliates": [_aff_to_dict(a) for a in rows]}


@router.post("/api/admin/affiliates/{code}/status")
def aff_admin_set_status(
    code: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import affiliates as aff_mod
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = aff_mod.set_affiliate_status(code=code, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "affiliate not found")
    return {"ok": True}


@router.post("/api/admin/affiliates/commissions/{commission_id}/paid")
def aff_admin_mark_paid(
    commission_id: str,
    user=Depends(current_user),
):
    from .. import affiliates as aff_mod
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    ok = aff_mod.mark_commission_paid(commission_id)
    if not ok:
        raise HTTPException(404, "commission not found or already paid")
    return {"ok": True}


@router.get("/api/admin/affiliates/commissions")
def aff_admin_list_commissions(
    affiliate_code: str | None = None,
    pending_only: bool = False,
    limit: int = 100,
    user=Depends(current_user),
):
    from .. import affiliates as aff_mod
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    rows = aff_mod.list_commission_events(
        affiliate_code=affiliate_code,
        pending_only=pending_only, limit=limit,
    )
    return {
        "commissions": [
            {"id": e.id, "affiliate_code": e.affiliate_code,
             "user_id": e.user_id, "invoice_id": e.invoice_id,
             "invoice_paise": e.invoice_paise,
             "commission_paise": e.commission_paise,
             "booked_at": e.booked_at, "paid_at": e.paid_at}
            for e in rows
        ],
    }


@router.get("/api/admin/affiliates/program-summary")
def aff_admin_summary(user=Depends(current_user)):
    from .. import affiliates as aff_mod
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return aff_mod.program_summary()


# ---------- P4: DigiLocker integration ----------

@router.get("/api/digilocker/doc-types")
def dl_doc_types():
    """Public — what we're allowed to issue via DigiLocker."""
    from .. import digilocker as dl
    return {
        "doc_types": [
            {"code": dt.code, "title": dt.title,
             "description": dt.description}
            for dt in dl.list_doc_types()
        ],
    }


@router.post("/api/digilocker/consent", status_code=201)
def dl_consent(
    aadhaar: str = Form(..., min_length=12, max_length=12),
    consent_purposes: str = Form(..., max_length=500),
    consent_text: str = Form(..., min_length=20, max_length=2000),
    user=Depends(current_user),
):
    """DPDP §6 explicit consent. consent_purposes is comma-sep doc
    type codes. consent_text is the exact text shown to the user
    at the consent UI — we store it verbatim for audit."""
    from .. import digilocker as dl
    user = require_user(user)
    purposes = [
        x.strip() for x in consent_purposes.split(",") if x.strip()
    ]
    try:
        c = dl.record_consent(
            user_id=user.id, aadhaar_raw=aadhaar,
            consent_purposes=purposes,
            consent_text=consent_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": c.id, "consent_purposes": c.consent_purposes,
        "consented_at": c.consented_at,
    }


@router.get("/api/digilocker/consent/me")
def dl_consent_me(user=Depends(current_user)):
    from .. import digilocker as dl
    user = require_user(user)
    c = dl.get_consent(user.id)
    if not c:
        raise HTTPException(404, "no consent on record")
    return {
        "id": c.id, "consent_purposes": c.consent_purposes,
        "consent_text": c.consent_text,
        "consented_at": c.consented_at,
        "revoked_at": c.revoked_at,
    }


@router.post("/api/digilocker/consent/me/revoke")
def dl_consent_revoke(user=Depends(current_user)):
    """DPDP §13 — right to withdraw consent."""
    from .. import digilocker as dl
    user = require_user(user)
    ok = dl.revoke_consent(user.id)
    if not ok:
        raise HTTPException(404, "no active consent to revoke")
    return {"ok": True}


@router.get("/api/digilocker/issuances/me")
def dl_my_issuances(user=Depends(current_user)):
    from .. import digilocker as dl
    user = require_user(user)
    rows = dl.list_user_issuances(user.id)
    return {
        "issuances": [
            {"id": i.id, "doc_type_code": i.doc_type_code,
             "doc_title": i.doc_title, "status": i.status,
             "digilocker_uri": i.digilocker_uri,
             "created_at": i.created_at,
             "issued_at": i.issued_at}
            for i in rows
        ],
    }


@router.post("/api/admin/digilocker/orgs", status_code=201)
def dl_admin_register_org(
    org_id: str = Form(..., max_length=64),
    issuer_name: str = Form(..., min_length=2, max_length=200),
    issuer_id: str = Form(..., max_length=80),
    api_key: str | None = Form(None, max_length=500),
    callback_url: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import digilocker as dl
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        o = dl.register_org_issuer(
            org_id=org_id, issuer_name=issuer_name,
            issuer_id=issuer_id, api_key=api_key,
            callback_url=callback_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": o.id, "org_id": o.org_id, "issuer_id": o.issuer_id,
        "status": o.status, "created_at": o.created_at,
    }


@router.post("/api/admin/digilocker/orgs/{org_id}/activate")
def dl_admin_activate(org_id: str, user=Depends(current_user)):
    from .. import digilocker as dl
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    ok = dl.activate_org_issuer(org_id=org_id)
    if not ok:
        raise HTTPException(404, "no sandbox issuer to activate")
    return {"ok": True}


@router.post("/api/admin/digilocker/issuances", status_code=201)
def dl_admin_enqueue(
    org_id: str = Form(..., max_length=64),
    target_user_id: str = Form(..., max_length=64),
    doc_type_code: str = Form(..., max_length=60),
    doc_title: str = Form(..., min_length=4, max_length=200),
    payload_json: str = Form(..., max_length=10000),
    user=Depends(current_user),
):
    from .. import digilocker as dl
    import json as _json
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        payload = _json.loads(payload_json)
    except _json.JSONDecodeError:
        raise HTTPException(400, "payload_json must be valid JSON")
    try:
        i = dl.enqueue_issuance(
            org_id=org_id, user_id=target_user_id,
            doc_type_code=doc_type_code, doc_title=doc_title,
            payload=payload,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": i.id, "status": i.status,
        "body_sha256": i.body_sha256,
        "created_at": i.created_at,
    }


@router.post("/api/admin/digilocker/issuances/{iid}/issued")
def dl_admin_mark_issued(
    iid: str,
    digilocker_uri: str = Form(..., max_length=500),
    user=Depends(current_user),
):
    """DigiLocker callback handler — after their backend confirms
    the doc landed in the citizen's vault, mark our local row."""
    from .. import digilocker as dl
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = dl.mark_issued(
            issuance_id=iid, digilocker_uri=digilocker_uri,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, "issuance not found or not pending")
    return {"ok": True}


@router.post("/api/admin/digilocker/issuances/{iid}/failed")
def dl_admin_mark_failed(
    iid: str,
    reason: str = Form(..., min_length=4, max_length=500),
    user=Depends(current_user),
):
    from .. import digilocker as dl
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    if not dl.mark_failed(issuance_id=iid, reason=reason):
        raise HTTPException(404, "issuance not found")
    return {"ok": True}


@router.get("/api/admin/digilocker/stats")
def dl_admin_stats(
    org_id: str | None = None,
    user=Depends(current_user),
):
    from .. import digilocker as dl
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return dl.issuance_stats(org_id=org_id)


# ---------- v3.1 trust + accuracy: citations ----------

def _citation_to_dict(c) -> dict:
    return {
        "id": c.id, "source_kind": c.source_kind,
        "source_id": c.source_id,
        "page_number": c.page_number, "section": c.section,
        "citation_text": c.citation_text,
        "relevance": c.relevance, "position": c.position,
    }


def _provenance_to_dict(p) -> dict:
    return {
        "id": p.id, "ai_call_id": p.ai_call_id,
        "surface": p.surface, "answer_mode": p.answer_mode,
        "question_text": p.question_text,
        "answer_text": p.answer_text,
        "grounded": p.grounded, "confidence": p.confidence,
        "fallback_reason": p.fallback_reason,
        "created_at": p.created_at,
        "citations": [_citation_to_dict(c) for c in p.citations],
    }


@router.get("/api/citations/me")
def cit_my_answers(
    surface: str | None = None,
    grounded_only: bool = False,
    limit: int = 50,
    user=Depends(current_user),
):
    """List the caller's recent AI answers + their citations.
    Drives the 'why did the tutor say that?' UX."""
    from .. import citations as cit
    user = require_user(user)
    try:
        rows = cit.list_user_answers(
            user_id=user.id, surface=surface,
            grounded_only=grounded_only, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"answers": [_provenance_to_dict(p) for p in rows]}


@router.get("/api/citations/{provenance_id}")
def cit_get(provenance_id: str, user=Depends(current_user)):
    """Read one provenance record. Users see their own;
    admins see all (org context out of scope for this endpoint)."""
    from .. import citations as cit
    user = require_user(user)
    p = cit.get_provenance(provenance_id)
    if not p:
        raise HTTPException(404, "provenance not found")
    if p.user_id and p.user_id != user.id and not getattr(
        user, "is_admin", False,
    ):
        raise HTTPException(403, "not your answer")
    return _provenance_to_dict(p)


@router.get("/api/admin/citations/grounding-rate")
def cit_admin_grounding_rate(
    surface: str | None = None,
    since: float | None = None,
    user=Depends(current_user),
):
    """Headline trust metric — what fraction of AI answers carry
    at least one citation. Filterable by surface + time window."""
    from .. import citations as cit
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        return cit.grounding_rate(surface=surface, since=since)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/admin/citations/source-impact")
def cit_admin_source_impact(
    source_kind: str,
    source_id: str,
    limit: int = 50,
    user=Depends(current_user),
):
    """How often has this source been cited? Drives publisher /
    teacher payout signals + 'most-trusted-source' rankings."""
    from .. import citations as cit
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        cites = cit.list_citations_for_source(
            source_kind=source_kind, source_id=source_id,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"citations": [_citation_to_dict(c) for c in cites]}


# ---------- v3.1 exam taxonomy + Exam Packs ----------

def _exam_to_dict(e) -> dict:
    return {
        "code": e.code, "body_code": e.body_code,
        "segment_code": e.segment_code, "title": e.title,
        "short_title": e.short_title, "level": e.level,
        "languages": e.languages, "is_active": e.is_active,
    }


def _topic_to_dict(t) -> dict:
    return {
        "id": t.id, "code": t.code, "title": t.title,
        "depth": t.depth, "weightage_pct": t.weightage_pct,
        "learning_objectives": t.learning_objectives,
        "parent_id": t.parent_id, "sort_order": t.sort_order,
    }


def _pack_to_dict(p) -> dict:
    return {
        "code": p.code, "exam_code": p.exam_code,
        "title": p.title, "year": p.year,
        "description": p.description,
        "syllabus_url": p.syllabus_url,
        "pattern_summary": p.pattern_summary,
        "cutoff_summary": p.cutoff_summary,
        "estimated_hours": p.estimated_hours,
        "status": p.status,
        "enrollment_count": p.enrollment_count,
    }


@router.get("/api/exam-taxonomy/segments")
def tax_segments():
    """Public — top-level segment catalog (kinder → research)."""
    from .. import exam_taxonomy as et
    return {
        "segments": [
            {"code": s.code, "title": s.title,
             "description": s.description,
             "sort_order": s.sort_order}
            for s in et.list_segments()
        ],
    }


@router.get("/api/exam-taxonomy/bodies")
def tax_bodies(country: str | None = None):
    from .. import exam_taxonomy as et
    return {
        "bodies": [
            {"code": b.code, "title": b.title,
             "country": b.country,
             "description": b.description,
             "website": b.website}
            for b in et.list_bodies(country=country)
        ],
    }


@router.get("/api/exam-taxonomy/exams")
def tax_exams(
    segment_code: str | None = None,
    body_code: str | None = None,
    active_only: bool = True,
):
    from .. import exam_taxonomy as et
    try:
        rows = et.list_exams(
            segment_code=segment_code, body_code=body_code,
            active_only=active_only,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"exams": [_exam_to_dict(x) for x in rows]}


@router.get("/api/exam-taxonomy/exams/{exam_code}")
def tax_exam_detail(exam_code: str):
    from .. import exam_taxonomy as et
    e = et.get_exam(exam_code)
    if not e:
        raise HTTPException(404, "exam not found")
    return _exam_to_dict(e)


@router.get("/api/exam-taxonomy/exams/{exam_code}/topics")
def tax_exam_topics(
    exam_code: str,
    parent_id: str | None = None,
    depth: int | None = None,
):
    from .. import exam_taxonomy as et
    return {
        "topics": [
            _topic_to_dict(t)
            for t in et.list_topics(
                exam_code, parent_id=parent_id, depth=depth,
            )
        ],
    }


@router.post(
    "/api/admin/exam-taxonomy/exams/{exam_code}/topics",
    status_code=201,
)
def tax_admin_add_topic(
    exam_code: str,
    code: str = Form(..., max_length=80),
    title: str = Form(..., min_length=2, max_length=200),
    parent_id: str | None = Form(None, max_length=64),
    depth: int = Form(0, ge=0, le=2),
    weightage_pct: float | None = Form(None, ge=0, le=100),
    sort_order: int = Form(100, ge=0, le=10000),
    user=Depends(current_user),
):
    from .. import exam_taxonomy as et
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        t = et.add_topic(
            exam_code=exam_code, code=code, title=title,
            parent_id=parent_id, depth=depth,
            weightage_pct=weightage_pct, sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _topic_to_dict(t)


@router.get("/api/exam-packs")
def packs_list(
    exam_code: str | None = None,
    active_only: bool = True,
):
    """Public — browse the Exam Packs catalog. The 5 deep packs
    from review §Phase 2 are seeded on migrate."""
    from .. import exam_taxonomy as et
    return {
        "packs": [
            _pack_to_dict(p)
            for p in et.list_packs(
                exam_code=exam_code, active_only=active_only,
            )
        ],
    }


@router.get("/api/exam-packs/{code}")
def packs_get(code: str):
    from .. import exam_taxonomy as et
    p = et.get_pack(code)
    if not p:
        raise HTTPException(404, "pack not found")
    return _pack_to_dict(p)


@router.post("/api/admin/exam-packs", status_code=201)
def packs_admin_create(
    code: str = Form(..., max_length=80),
    exam_code: str = Form(..., max_length=80),
    title: str = Form(..., min_length=4, max_length=200),
    year: int | None = Form(None, ge=2020, le=2050),
    description: str | None = Form(None, max_length=2000),
    syllabus_url: str | None = Form(None, max_length=500),
    pattern_summary: str | None = Form(None, max_length=500),
    cutoff_summary: str | None = Form(None, max_length=500),
    estimated_hours: int | None = Form(None, ge=1, le=10000),
    user=Depends(current_user),
):
    from .. import exam_taxonomy as et
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        p = et.create_pack(
            code=code, exam_code=exam_code, title=title,
            year=year, description=description,
            syllabus_url=syllabus_url,
            pattern_summary=pattern_summary,
            cutoff_summary=cutoff_summary,
            estimated_hours=estimated_hours,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _pack_to_dict(p)


@router.post("/api/exam-packs/{code}/enroll", status_code=201)
def packs_enroll(
    code: str,
    target_date: float | None = Form(None),
    daily_minutes: int = Form(60, ge=10, le=720),
    user=Depends(current_user),
):
    """Student enrolls into an Exam Pack. The pack becomes their
    daily-plan / mock-list / community 'home'."""
    from .. import exam_taxonomy as et
    user = require_user(user)
    try:
        e = et.enroll(
            pack_code=code, user_id=user.id,
            target_date=target_date,
            daily_minutes=daily_minutes,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "id": e.id, "pack_code": e.pack_code,
        "target_date": e.target_date,
        "daily_minutes": e.daily_minutes,
        "status": e.status, "enrolled_at": e.enrolled_at,
    }


@router.get("/api/exam-packs/me/enrollments")
def packs_my_enrollments(user=Depends(current_user)):
    from .. import exam_taxonomy as et
    user = require_user(user)
    rows = et.list_user_enrollments(user.id)
    return {
        "enrollments": [
            {"id": e.id, "pack_code": e.pack_code,
             "target_date": e.target_date,
             "daily_minutes": e.daily_minutes,
             "status": e.status,
             "enrolled_at": e.enrolled_at,
             "completed_at": e.completed_at}
            for e in rows
        ],
    }


@router.post("/api/exam-packs/me/enrollments/{eid}/status")
def packs_set_enrollment_status(
    eid: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import exam_taxonomy as et
    user = require_user(user)
    e = et.get_enrollment(eid)
    if not e:
        raise HTTPException(404, "enrollment not found")
    if e.user_id != user.id:
        raise HTTPException(403, "not your enrollment")
    try:
        ok = et.set_enrollment_status(
            enrollment_id=eid, status=status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "no row updated")
    return {"ok": True}


@router.get("/api/admin/exam-packs/{code}/stats")
def packs_admin_stats(code: str, user=Depends(current_user)):
    from .. import exam_taxonomy as et
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return et.pack_stats(code)


# ---------- v3.1 accuracy benchmark ----------

def _ds_to_dict(d) -> dict:
    return {
        "id": d.id, "code": d.code, "title": d.title,
        "domain": d.domain, "task_kind": d.task_kind,
        "description": d.description,
        "item_count": d.item_count,
        "reviewed_by": d.reviewed_by, "version": d.version,
        "status": d.status, "created_at": d.created_at,
        "published_at": d.published_at,
    }


def _run_to_dict(r) -> dict:
    return {
        "id": r.id, "dataset_id": r.dataset_id, "judge": r.judge,
        "target": r.target, "model_version": r.model_version,
        "item_count": r.item_count,
        "pass_count": r.pass_count, "fail_count": r.fail_count,
        "skipped_count": r.skipped_count,
        "mean_score": r.mean_score,
        "p50_score": r.p50_score, "p90_score": r.p90_score,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


@router.post("/api/admin/bench/datasets", status_code=201)
def bench_admin_create_dataset(
    code: str = Form(..., max_length=80),
    title: str = Form(..., min_length=4, max_length=200),
    domain: str = Form(..., max_length=60),
    task_kind: str = Form(..., max_length=40),
    description: str | None = Form(None, max_length=2000),
    version: int = Form(1, ge=1, le=999),
    reviewed_by: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        d = ab.create_dataset(
            code=code, title=title, domain=domain,
            task_kind=task_kind, description=description,
            version=version, reviewed_by=reviewed_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _ds_to_dict(d)


@router.get("/api/admin/bench/datasets")
def bench_admin_list_datasets(
    domain: str | None = None,
    status: str | None = None,
    user=Depends(current_user),
):
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = ab.list_datasets(domain=domain, status=status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"datasets": [_ds_to_dict(d) for d in rows]}


@router.post(
    "/api/admin/bench/datasets/{dataset_id}/items",
    status_code=201,
)
def bench_admin_add_item(
    dataset_id: str,
    prompt: str = Form(..., min_length=4, max_length=8000),
    expected_json: str = Form(..., max_length=10000),
    rubric: str | None = Form(None, max_length=2000),
    difficulty: str | None = Form(None, max_length=10),
    tags_json: str | None = Form(None, max_length=1000),
    weight: float = Form(1.0, gt=0.0, le=10.0),
    user=Depends(current_user),
):
    from .. import accuracy_bench as ab
    import json as _json
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        expected = _json.loads(expected_json)
        tags = _json.loads(tags_json) if tags_json else None
    except _json.JSONDecodeError:
        raise HTTPException(400, "expected_json/tags_json must be JSON")
    try:
        it = ab.add_item(
            dataset_id=dataset_id, prompt=prompt,
            expected=expected, rubric=rubric,
            difficulty=difficulty, tags=tags, weight=weight,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": it.id, "dataset_id": it.dataset_id,
        "prompt": it.prompt,
        "difficulty": it.difficulty, "weight": it.weight,
    }


@router.post("/api/admin/bench/datasets/{dataset_id}/publish")
def bench_admin_publish_dataset(
    dataset_id: str,
    user=Depends(current_user),
):
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = ab.publish_dataset(dataset_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}


@router.get("/api/admin/bench/runs")
def bench_admin_list_runs(
    target: str | None = None,
    dataset_id: str | None = None,
    limit: int = 20,
    user=Depends(current_user),
):
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return {
        "runs": [
            _run_to_dict(r)
            for r in ab.list_runs(
                target=target, dataset_id=dataset_id, limit=limit,
            )
        ],
    }


@router.get("/api/admin/bench/runs/{run_id}")
def bench_admin_get_run(run_id: str, user=Depends(current_user)):
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    r = ab.get_run(run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return _run_to_dict(r)


@router.get("/api/admin/bench/trust-dashboard")
def bench_admin_trust_dashboard(
    since: float | None = None,
    user=Depends(current_user),
):
    """Headline trust dashboard from review §24. Pass rate +
    mean score per target across recent runs."""
    from .. import accuracy_bench as ab
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return ab.trust_dashboard(since=since)


# ---------- v3.2 mock engine ----------

def _paper_to_dict(p) -> dict:
    return {
        "id": p.id, "pack_code": p.pack_code,
        "exam_code": p.exam_code, "title": p.title,
        "mode": p.mode, "sections": p.sections,
        "total_questions": p.total_questions,
        "total_marks": p.total_marks,
        "total_time_min": p.total_time_min,
        "negative_marking": p.negative_marking,
        "status": p.status,
        "created_at": p.created_at,
        "published_at": p.published_at,
    }


def _attempt_to_dict(a) -> dict:
    return {
        "id": a.id, "paper_id": a.paper_id,
        "user_id": a.user_id, "status": a.status,
        "started_at": a.started_at,
        "submitted_at": a.submitted_at,
        "duration_sec": a.duration_sec,
        "raw_score": a.raw_score, "max_score": a.max_score,
        "correct_count": a.correct_count,
        "wrong_count": a.wrong_count,
        "unattempted_count": a.unattempted_count,
        "percentile": a.percentile,
        "section_scores": a.section_scores,
        "topic_breakdown": a.topic_breakdown,
    }


@router.post("/api/admin/mock/papers", status_code=201)
def mock_admin_create_paper(
    title: str = Form(..., min_length=4, max_length=200),
    sections_json: str = Form(..., max_length=10000),
    total_time_min: int = Form(..., ge=1, le=600),
    pack_code: str | None = Form(None, max_length=80),
    exam_code: str | None = Form(None, max_length=80),
    mode: str = Form("full", max_length=20),
    negative_marking: float = Form(0.0, ge=0.0, le=1.0),
    user=Depends(current_user),
):
    """Create a mock paper template. sections_json is a JSON array
    of {code, title, time_min, marks_per_q}."""
    from .. import mock_engine as me
    import json as _json
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        sections = _json.loads(sections_json)
    except _json.JSONDecodeError:
        raise HTTPException(400, "sections_json must be JSON array")
    try:
        p = me.create_paper(
            title=title, sections=sections,
            total_time_min=total_time_min, pack_code=pack_code,
            exam_code=exam_code, mode=mode,
            negative_marking=negative_marking,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _paper_to_dict(p)


@router.post("/api/admin/mock/papers/{paper_id}/questions")
def mock_admin_add_question(
    paper_id: str,
    position: int = Form(..., ge=1, le=1000),
    section_code: str = Form(..., max_length=40),
    question_id: str = Form(..., max_length=64),
    correct_answer: str | None = Form(None, max_length=2000),
    topic_code: str | None = Form(None, max_length=80),
    user=Depends(current_user),
):
    from .. import mock_engine as me
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        count = me.add_question(
            paper_id=paper_id, position=position,
            section_code=section_code, question_id=question_id,
            correct_answer=correct_answer, topic_code=topic_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"question_count": count}


@router.post("/api/admin/mock/papers/{paper_id}/publish")
def mock_admin_publish(paper_id: str, user=Depends(current_user)):
    from .. import mock_engine as me
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = me.publish_paper(paper_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}


@router.get("/api/mock/papers")
def mock_list_papers(
    pack_code: str | None = None,
    exam_code: str | None = None,
    mode: str | None = None,
    limit: int = 50,
):
    """Public — browse published mock papers. Filter by pack/exam/mode."""
    from .. import mock_engine as me
    try:
        rows = me.list_papers(
            pack_code=pack_code, exam_code=exam_code,
            mode=mode, status="published", limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"papers": [_paper_to_dict(p) for p in rows]}


@router.get("/api/mock/papers/{paper_id}")
def mock_get_paper(paper_id: str):
    from .. import mock_engine as me
    p = me.get_paper(paper_id)
    if not p:
        raise HTTPException(404, "paper not found")
    return _paper_to_dict(p)


@router.get("/api/mock/papers/{paper_id}/cohort-stats")
def mock_cohort_stats(paper_id: str):
    """Public — paper-level cohort stats (mean / median / p90)."""
    from .. import mock_engine as me
    return me.cohort_stats(paper_id)


@router.post("/api/mock/papers/{paper_id}/start", status_code=201)
def mock_start(paper_id: str, user=Depends(current_user)):
    from .. import mock_engine as me
    user = require_user(user)
    try:
        a = me.start_attempt(paper_id=paper_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _attempt_to_dict(a)


@router.post("/api/mock/attempts/{attempt_id}/respond")
def mock_respond(
    attempt_id: str,
    position: int = Form(..., ge=1, le=1000),
    chosen_answer: str | None = Form(None, max_length=2000),
    time_seconds: int | None = Form(None, ge=0, le=36000),
    marked_review: bool = Form(False),
    user=Depends(current_user),
):
    from .. import mock_engine as me
    user = require_user(user)
    a = me.get_attempt(attempt_id)
    if not a:
        raise HTTPException(404, "attempt not found")
    if a.user_id != user.id:
        raise HTTPException(403, "not your attempt")
    try:
        me.submit_response(
            attempt_id=attempt_id, position=position,
            chosen_answer=chosen_answer,
            time_seconds=time_seconds,
            marked_review=marked_review,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/api/mock/attempts/{attempt_id}/submit")
def mock_submit(attempt_id: str, user=Depends(current_user)):
    from .. import mock_engine as me
    user = require_user(user)
    a = me.get_attempt(attempt_id)
    if not a:
        raise HTTPException(404, "attempt not found")
    if a.user_id != user.id:
        raise HTTPException(403, "not your attempt")
    try:
        graded = me.submit_attempt(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _attempt_to_dict(graded)


@router.get("/api/mock/attempts/{attempt_id}")
def mock_get_attempt(attempt_id: str, user=Depends(current_user)):
    from .. import mock_engine as me
    user = require_user(user)
    a = me.get_attempt(attempt_id)
    if not a:
        raise HTTPException(404, "attempt not found")
    if a.user_id != user.id:
        raise HTTPException(403, "not your attempt")
    return _attempt_to_dict(a)


@router.get("/api/mock/attempts/{attempt_id}/analysis")
def mock_attempt_analysis(
    attempt_id: str, user=Depends(current_user),
):
    from .. import mock_engine as me
    user = require_user(user)
    a = me.get_attempt(attempt_id)
    if not a:
        raise HTTPException(404, "attempt not found")
    if a.user_id != user.id:
        raise HTTPException(403, "not your attempt")
    try:
        return me.attempt_analysis(attempt_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/mock/me/attempts")
def mock_my_attempts(
    paper_id: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import mock_engine as me
    user = require_user(user)
    rows = me.list_attempts_for_user(
        user.id, paper_id=paper_id, limit=limit,
    )
    return {"attempts": [_attempt_to_dict(a) for a in rows]}


# ---------- v3.2 exam readiness ----------

def _readiness_to_dict(r) -> dict:
    return {
        "pack_code": r.pack_code, "score": r.score,
        "mastery_score": r.mastery_score,
        "mock_score": r.mock_score,
        "coverage_score": r.coverage_score,
        "consistency_score": r.consistency_score,
        "trust_score": r.trust_score,
        "weak_topics": r.weak_topics,
        "components": r.components,
        "computed_at": r.computed_at,
    }


@router.get("/api/readiness/me/{pack_code}")
def readiness_me_get(
    pack_code: str,
    refresh: bool = True,
    user=Depends(current_user),
):
    """Get readiness for (caller, pack). Auto-refreshes if stale."""
    from .. import readiness as rd
    user = require_user(user)
    r = rd.get_readiness(
        user_id=user.id, pack_code=pack_code,
        refresh_if_stale=refresh,
    )
    if not r:
        raise HTTPException(404, "no readiness data for pack")
    return _readiness_to_dict(r)


@router.post("/api/readiness/me/{pack_code}/recompute")
def readiness_me_recompute(
    pack_code: str, user=Depends(current_user),
):
    """Force a recompute regardless of staleness."""
    from .. import readiness as rd
    user = require_user(user)
    try:
        r = rd.compute_readiness(
            user_id=user.id, pack_code=pack_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _readiness_to_dict(r)


@router.get("/api/readiness/me")
def readiness_me_list(user=Depends(current_user)):
    """List all packs the caller has a readiness row for."""
    from .. import readiness as rd
    user = require_user(user)
    rows = rd.list_user_readiness(user.id)
    return {"readiness": [_readiness_to_dict(r) for r in rows]}


@router.get("/api/exam-packs/{code}/leaderboard")
def readiness_pack_leaderboard(code: str, limit: int = 20):
    """Public-ish — top readiness scorers per pack (drives the
    community room). User IDs are returned raw; anonymise at UI."""
    from .. import readiness as rd
    return {"entries": rd.pack_leaderboard(code, limit=limit)}


# ---------- v3.2 tutor grounding ----------

@router.post("/api/tutor/sessions/{sid}/mode")
def tutor_set_mode(
    sid: str,
    answer_mode: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    """Set source_only / official / general for a tutor session.
    source_only refuses LLM-only answers; official requires an
    official_doc citation."""
    from .. import tutor_grounding as tg
    user = require_user(user)
    try:
        sm = tg.set_session_mode(
            session_id=sid, user_id=user.id,
            answer_mode=answer_mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "session_id": sm.session_id,
        "answer_mode": sm.answer_mode,
        "test_active": sm.test_active,
    }


@router.get("/api/tutor/sessions/{sid}/mode")
def tutor_get_mode(sid: str, user=Depends(current_user)):
    from .. import tutor_grounding as tg
    user = require_user(user)
    sm = tg.get_session_mode(sid)
    if not sm:
        # Default mode
        return {
            "session_id": sid, "answer_mode": tg.DEFAULT_MODE,
            "test_active": False,
        }
    return {
        "session_id": sm.session_id,
        "answer_mode": sm.answer_mode,
        "test_active": sm.test_active,
    }


@router.post("/api/tutor/sessions/{sid}/test-active")
def tutor_set_test_active(
    sid: str,
    active: bool = Form(...),
    user=Depends(current_user),
):
    """Mock-test anti-cheat — flip on when a mock starts, off when
    it submits. While on, grounded replies return the cheat-guard
    message."""
    from .. import tutor_grounding as tg
    user = require_user(user)
    tg.set_test_active(
        session_id=sid, user_id=user.id, active=active,
    )
    return {"ok": True}


@router.get("/api/tutor/me/fallbacks")
def tutor_my_fallbacks(
    limit: int = 20, user=Depends(current_user),
):
    """Show the caller their recent fallbacks ('not found' / cheat
    guard). Drives the 'upload more material' nudge in the UI."""
    from .. import tutor_grounding as tg
    user = require_user(user)
    return {
        "fallbacks": tg.user_recent_fallbacks(user.id, limit=limit),
    }


# ---------- v3.3 retrieval (RAG) ----------

def _chunk_to_dict(c) -> dict:
    return {
        "id": c.id, "upload_id": c.upload_id,
        "page_number": c.page_number, "section": c.section,
        "chunk_text": c.chunk_text,
        "token_count": c.token_count,
        "chunk_index": c.chunk_index,
    }


def _hit_to_dict(h) -> dict:
    return {
        "chunk": _chunk_to_dict(h.chunk),
        "score": h.score,
        "matched_tokens": h.matched_tokens,
    }


@router.post("/api/retrieval/uploads/{upload_id}/index")
def retr_index_upload(upload_id: str, user=Depends(current_user)):
    """Trigger chunking + embedding of a previously-uploaded
    document. Idempotent. Returns the chunk count after indexing."""
    from .. import retrieval as rt
    user = require_user(user)
    written = rt.index_upload(upload_id)
    return {
        "indexed_chunks": written,
        "total_chunks": rt.chunk_count(upload_id=upload_id),
    }


@router.get("/api/retrieval/uploads/{upload_id}/chunks")
def retr_list_chunks(upload_id: str, user=Depends(current_user)):
    """List the chunks for an upload. Useful for debugging citations
    + 'why did the AI cite this passage?' UX."""
    from .. import retrieval as rt
    user = require_user(user)
    return {
        "chunks": [
            _chunk_to_dict(c)
            for c in rt.list_chunks_for_upload(upload_id)
        ],
    }


@router.delete("/api/retrieval/uploads/{upload_id}/chunks")
def retr_delete_chunks(upload_id: str, user=Depends(current_user)):
    """Remove all chunks + embeddings for an upload. Called when
    the source document is deleted."""
    from .. import retrieval as rt
    user = require_user(user)
    n = rt.delete_upload_chunks(upload_id)
    return {"deleted_chunks": n}


@router.post("/api/retrieval/query")
def retr_query(
    query: str = Form(..., min_length=2, max_length=1000),
    upload_ids: str | None = Form(None, max_length=2000),
    top_k: int = Form(5, ge=1, le=50),
    min_score: float = Form(0.0, ge=0.0, le=1.0),
    user=Depends(current_user),
):
    """Run a retrieval against the caller's chunk index. Returns
    top-k hits with scores normalised to [0, 1] — drop these
    straight into `citations.record_answer`."""
    from .. import retrieval as rt
    user = require_user(user)
    uids = (
        [x.strip() for x in upload_ids.split(",") if x.strip()]
        if upload_ids else None
    )
    hits = rt.retrieve(
        query=query, upload_ids=uids,
        top_k=top_k, min_score=min_score,
    )
    return {
        "hits": [_hit_to_dict(h) for h in hits],
        "citations_format": rt.hits_to_citations(hits),
    }


@router.get("/api/retrieval/stats")
def retr_stats(
    upload_id: str | None = None,
    user=Depends(current_user),
):
    from .. import retrieval as rt
    user = require_user(user)
    return {
        "chunk_count": rt.chunk_count(upload_id=upload_id),
        "provider": rt.DEFAULT_PROVIDER,
    }


# ---------- v3.4 daily plan ----------

def _block_to_dict(b) -> dict:
    return {
        "id": b.id, "position": b.position, "kind": b.kind,
        "title": b.title, "topic_code": b.topic_code,
        "estimated_min": b.estimated_min,
        "ref_kind": b.ref_kind, "ref_id": b.ref_id,
        "payload": b.payload,
        "completed": b.completed,
        "completed_at": b.completed_at,
    }


def _plan_to_dict(p) -> dict:
    return {
        "id": p.id, "user_id": p.user_id,
        "pack_code": p.pack_code, "plan_date": p.plan_date,
        "total_minutes": p.total_minutes, "status": p.status,
        "completion_pct": p.completion_pct,
        "generation_reason": p.generation_reason,
        "created_at": p.created_at,
        "completed_at": p.completed_at,
        "blocks": [_block_to_dict(b) for b in p.blocks],
    }


@router.get("/api/daily-plan/me/{pack_code}")
def plan_get_today(
    pack_code: str,
    plan_date: str | None = None,
    user=Depends(current_user),
):
    """Return today's plan for (caller, pack). Auto-generates if
    missing. The default entry point — UI calls this on home
    screen load."""
    from .. import daily_plan as dp
    user = require_user(user)
    try:
        p = dp.get_or_generate(
            user_id=user.id, pack_code=pack_code,
            plan_date=plan_date,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _plan_to_dict(p)


@router.post("/api/daily-plan/me/{pack_code}/regenerate")
def plan_regenerate(
    pack_code: str,
    plan_date: str | None = Form(None, max_length=10),
    total_minutes: int | None = Form(None, ge=10, le=720),
    reason: str = Form("manual", max_length=40),
    user=Depends(current_user),
):
    """Force a regen. Useful after a mock submission or when the
    student manually edits their daily budget."""
    from .. import daily_plan as dp
    user = require_user(user)
    try:
        p = dp.generate_plan(
            user_id=user.id, pack_code=pack_code,
            plan_date=plan_date,
            total_minutes=total_minutes, reason=reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _plan_to_dict(p)


@router.get("/api/daily-plan/me/{pack_code}/should-regenerate")
def plan_should_regen(
    pack_code: str,
    last_readiness_score: float | None = None,
    user=Depends(current_user),
):
    """Probe — does the current plan need a refresh? Dashboard
    polls this so it can show a 'Refresh plan' button when the
    student's mock/readiness shifted things."""
    from .. import daily_plan as dp
    user = require_user(user)
    needs, reason = dp.should_regenerate(
        user_id=user.id, pack_code=pack_code,
        last_readiness_score=last_readiness_score,
    )
    return {"should_regenerate": needs, "reason": reason}


@router.post("/api/daily-plan/blocks/{block_id}/done")
def plan_block_done(block_id: str, user=Depends(current_user)):
    """Tick off a block."""
    from .. import daily_plan as dp
    user = require_user(user)
    if not dp.mark_block_done(
        block_id=block_id, user_id=user.id,
    ):
        raise HTTPException(404, "block not found or not yours")
    return {"ok": True}


@router.post("/api/daily-plan/{plan_id}/skip")
def plan_skip(plan_id: str, user=Depends(current_user)):
    from .. import daily_plan as dp
    user = require_user(user)
    if not dp.skip_plan(plan_id=plan_id, user_id=user.id):
        raise HTTPException(
            404, "plan not found, not yours, or already inactive",
        )
    return {"ok": True}


@router.get("/api/daily-plan/me/recent")
def plan_recent(
    pack_code: str | None = None,
    limit: int = 14,
    user=Depends(current_user),
):
    """Last N plans for the caller. Drives the calendar / habit
    view in the UI."""
    from .. import daily_plan as dp
    user = require_user(user)
    rows = dp.list_recent_plans(
        user_id=user.id, pack_code=pack_code, limit=limit,
    )
    return {"plans": [_plan_to_dict(p) for p in rows]}


@router.get("/api/daily-plan/me/{pack_code}/stats")
def plan_stats(
    pack_code: str,
    last_n_days: int = 14,
    user=Depends(current_user),
):
    from .. import daily_plan as dp
    user = require_user(user)
    return dp.pack_completion_stats(
        user_id=user.id, pack_code=pack_code,
        last_n_days=last_n_days,
    )


# ---------- v3.5 moderation queue ----------

def _flagged_to_dict(f) -> dict:
    return {
        "id": f.id, "content_kind": f.content_kind,
        "content_id": f.content_id,
        "author_user_id": f.author_user_id,
        "body_snippet": f.body_snippet,
        "score": f.score,
        "rules_triggered": f.rules_triggered,
        "severity": f.severity, "status": f.status,
        "sla_due_at": f.sla_due_at,
        "created_at": f.created_at,
    }


@router.get("/api/admin/moderation/queue")
def mod_list_queue(
    status: str = "open",
    severity: str | None = None,
    content_kind: str | None = None,
    sla_breached_only: bool = False,
    limit: int = 50,
    user=Depends(current_user),
):
    """Reviewer queue. Admins + reviewers see everything; ordinary
    users get 403."""
    from .. import moderation_queue as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = mq.list_queue(
            status=status, severity=severity,
            content_kind=content_kind,
            sla_breached_only=sla_breached_only, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": [_flagged_to_dict(f) for f in rows]}


@router.get("/api/admin/moderation/queue/{item_id}")
def mod_get_item(item_id: str, user=Depends(current_user)):
    from .. import moderation_queue as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    item = mq.get_item(item_id)
    if not item:
        raise HTTPException(404, "item not found")
    return {
        **_flagged_to_dict(item),
        "actions": mq.list_actions_for_item(item_id),
    }


@router.post("/api/admin/moderation/queue/{item_id}/decide")
def mod_decide(
    item_id: str,
    action: str = Form(..., max_length=20),
    reason: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import moderation_queue as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = mq.decide(
            item_id=item_id, action=action,
            reviewer_user_id=user.id, reason=reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(
            404, "item not found or not in actionable state",
        )
    return {"ok": True}


@router.get("/api/admin/moderation/stats")
def mod_stats(user=Depends(current_user)):
    from .. import moderation_queue as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return mq.queue_stats()


@router.post("/api/admin/moderation/scan")
def mod_admin_scan(
    content_kind: str = Form(..., max_length=40),
    content_id: str = Form(..., max_length=64),
    body: str = Form(..., max_length=100_000),
    author_user_id: str = Form(..., max_length=64),
    user=Depends(current_user),
):
    """Admin-side dry-run of the scanner. Useful for tuning the
    blocklist + checking false positives without persisting rows."""
    from .. import moderation_queue as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        result = mq.scan(
            content_kind=content_kind, content_id=content_id,
            body=body, author_user_id=author_user_id,
            persist=False,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "score": result.score,
        "rules_triggered": result.rules_triggered,
        "severity": result.severity,
    }


# ---------- Public reactions ----------

@router.post("/api/reactions")
def reactions_react(
    target_kind: str = Form(..., max_length=40),
    target_id: str = Form(..., max_length=64),
    kind: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    """React to a piece of content. Idempotent on
    (user, target, kind). `report` reactions feed into the
    moderation queue automatically."""
    from .. import moderation_queue as mq
    user = require_user(user)
    try:
        ok = mq.react(
            target_kind=target_kind, target_id=target_id,
            user_id=user.id, kind=kind,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": ok}    # False = already reacted


@router.delete("/api/reactions")
def reactions_unreact(
    target_kind: str,
    target_id: str,
    kind: str,
    user=Depends(current_user),
):
    from .. import moderation_queue as mq
    user = require_user(user)
    ok = mq.unreact(
        target_kind=target_kind, target_id=target_id,
        user_id=user.id, kind=kind,
    )
    return {"ok": ok}


@router.get("/api/reactions/{target_kind}/{target_id}")
def reactions_counts(target_kind: str, target_id: str):
    """Public — aggregate reaction counts. UI shows these next
    to content."""
    from .. import moderation_queue as mq
    try:
        counts = mq.reaction_counts(
            target_kind=target_kind, target_id=target_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"counts": counts}


@router.get("/api/reactions/me/{target_kind}/{target_id}")
def reactions_my_for_target(
    target_kind: str, target_id: str,
    user=Depends(current_user),
):
    """Which reactions has *this* caller made on this target?
    Drives the highlighted-state in the UI."""
    from .. import moderation_queue as mq
    user = require_user(user)
    return {
        "kinds": mq.user_reactions_for_target(
            target_kind=target_kind, target_id=target_id,
            user_id=user.id,
        ),
    }


# ---------- v3.6 parent dashboard ----------

@router.get("/api/parent/me/dashboard")
def dash_parent_me(user=Depends(current_user)):
    """Parent's view of all linked children. Caller must be a
    verified parent of at least one child (otherwise the
    children list will be empty)."""
    from .. import dashboards
    user = require_user(user)
    return dashboards.parent_dashboard(user.id)


@router.get("/api/parent/me/children/{child_user_id}")
def dash_parent_child_detail(
    child_user_id: str,
    user=Depends(current_user),
):
    """Drill-down view for a specific child. Caller must be a
    verified parent of that child."""
    from .. import dashboards
    from .. import parents
    user = require_user(user)
    if not parents.is_verified_parent_of(
        parent_user_id=user.id, child_user_id=child_user_id,
    ):
        raise HTTPException(
            403, "you are not a verified parent of this user",
        )
    import time as _time
    return {
        "child_user_id": child_user_id,
        "detail": dashboards._child_summary(child_user_id),
        "computed_at": _time.time(),
    }


# ---------- v3.6 teacher dashboard ----------

def _require_teacher(*, user, org_id: str):
    """Caller must be a teacher (or admin) of the named org."""
    from .. import orgs
    try:
        orgs.require_role(
            org_id=org_id, user_id=user.id,
            allowed={"teacher", "admin"},
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))


@router.get("/api/teacher/orgs/{org_id}/dashboard")
def dash_teacher_org(
    org_id: str,
    class_id: str | None = None,
    user=Depends(current_user),
):
    """Class-or-org-wide teacher view. `class_id` scopes to a class
    if supplied; otherwise aggregates the whole org."""
    from .. import dashboards
    user = require_user(user)
    _require_teacher(user=user, org_id=org_id)
    return dashboards.teacher_dashboard(
        teacher_user_id=user.id, org_id=org_id, class_id=class_id,
    )


@router.get(
    "/api/teacher/orgs/{org_id}/students/{student_user_id}",
)
def dash_teacher_student(
    org_id: str,
    student_user_id: str,
    user=Depends(current_user),
):
    """Per-student deep-dive for a teacher. Verifies the student
    is actually in the teacher's org."""
    from .. import dashboards
    user = require_user(user)
    _require_teacher(user=user, org_id=org_id)
    try:
        return dashboards.teacher_student_detail(
            teacher_user_id=user.id, org_id=org_id,
            student_user_id=student_user_id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))


# ---------- v3.7 expert review ----------

def _expert_to_dict(e) -> dict:
    return {
        "user_id": e.user_id, "display_name": e.display_name,
        "bio": e.bio, "credentials": e.credentials,
        "subjects": e.subjects, "exam_codes": e.exam_codes,
        "languages": e.languages, "status": e.status,
        "rate_per_review_paise": e.rate_per_review_paise,
        "total_reviews": e.total_reviews,
        "total_earned_paise": e.total_earned_paise,
        "rating_avg": e.rating_avg,
        "rating_count": e.rating_count,
        "activated_at": e.activated_at,
    }


def _review_to_dict(r) -> dict:
    return {
        "id": r.id, "target_kind": r.target_kind,
        "target_id": r.target_id,
        "requested_by": r.requested_by,
        "priority": r.priority,
        "subject_hint": r.subject_hint, "status": r.status,
        "reviewer_user_id": r.reviewer_user_id,
        "corrected_answer": r.corrected_answer,
        "reason": r.reason,
        "commission_paise": r.commission_paise,
        "requested_at": r.requested_at,
        "claimed_at": r.claimed_at,
        "decided_at": r.decided_at,
        "sla_due_at": r.sla_due_at,
    }


@router.post("/api/experts/apply", status_code=201)
def er_apply(
    display_name: str = Form(..., min_length=2, max_length=100),
    subjects: str = Form(..., max_length=500),
    credentials: str | None = Form(None, max_length=1000),
    bio: str | None = Form(None, max_length=2000),
    exam_codes: str | None = Form(None, max_length=500),
    languages: str | None = Form(None, max_length=200),
    rate_per_review_paise: int = Form(5000, ge=1000, le=50000),
    user=Depends(current_user),
):
    """Apply to become a verified subject-matter expert.
    Comma-sep lists for subjects / exam_codes / languages.
    Status starts 'applied' → admin approves to 'active'."""
    from .. import expert_review as ex
    user = require_user(user)

    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()] or None

    try:
        e = ex.apply_as_expert(
            user_id=user.id, display_name=display_name,
            subjects=_split(subjects) or [],
            credentials=credentials, bio=bio,
            exam_codes=_split(exam_codes),
            languages=_split(languages),
            rate_per_review_paise=rate_per_review_paise,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _expert_to_dict(e)


@router.get("/api/experts/me")
def er_me(user=Depends(current_user)):
    from .. import expert_review as ex
    user = require_user(user)
    e = ex.get_expert(user.id)
    if not e:
        raise HTTPException(404, "not registered as expert")
    return _expert_to_dict(e)


@router.get("/api/experts/me/earnings")
def er_me_earnings(user=Depends(current_user)):
    from .. import expert_review as ex
    user = require_user(user)
    e = ex.get_expert(user.id)
    if not e:
        raise HTTPException(404, "not registered as expert")
    return ex.expert_earnings_summary(user.id)


@router.get("/api/experts")
def er_list_public(
    subject: str | None = None,
    limit: int = 50,
):
    """Public — directory of active experts, optionally filtered
    by subject."""
    from .. import expert_review as ex
    rows = ex.list_experts(
        status="active", subject=subject, limit=limit,
    )
    # Strip earnings-internal fields for public view
    return {
        "experts": [
            {"user_id": e.user_id, "display_name": e.display_name,
             "bio": e.bio, "credentials": e.credentials,
             "subjects": e.subjects, "exam_codes": e.exam_codes,
             "languages": e.languages,
             "total_reviews": e.total_reviews,
             "rating_avg": e.rating_avg,
             "rating_count": e.rating_count}
            for e in rows
        ],
    }


@router.post("/api/admin/experts/{expert_user_id}/approve")
def er_admin_approve(
    expert_user_id: str, user=Depends(current_user),
):
    from .. import expert_review as ex
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    if not ex.approve_expert(expert_user_id):
        raise HTTPException(
            404, "expert not found or not in 'applied' state",
        )
    return {"ok": True}


@router.post("/api/admin/experts/{expert_user_id}/status")
def er_admin_set_status(
    expert_user_id: str,
    status: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    from .. import expert_review as ex
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        ok = ex.set_expert_status(
            user_id=expert_user_id, status=status,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "expert not found")
    return {"ok": True}


@router.post("/api/expert-reviews/request", status_code=201)
def er_request(
    target_kind: str = Form(..., max_length=20),
    target_id: str = Form(..., max_length=64),
    priority: int = Form(5, ge=1, le=10),
    subject_hint: str | None = Form(None, max_length=60),
    user=Depends(current_user),
):
    """Student or system requests expert review of content.
    Idempotent on (target_kind, target_id) while a pending /
    in_review request exists."""
    from .. import expert_review as ex
    user = require_user(user)
    try:
        r = ex.request_review(
            target_kind=target_kind, target_id=target_id,
            requested_by=user.id, priority=priority,
            subject_hint=subject_hint,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _review_to_dict(r)


@router.get("/api/expert-reviews/queue")
def er_queue(
    status: str = "pending",
    subject: str | None = None,
    target_kind: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    """Active experts see the review queue. Admins see all."""
    from .. import expert_review as ex
    user = require_user(user)
    expert = ex.get_expert(user.id)
    is_admin = getattr(user, "is_admin", False)
    if not (is_admin or (expert and expert.status == "active")):
        raise HTTPException(
            403, "active expert or admin only",
        )
    try:
        rows = ex.list_review_queue(
            status=status, subject=subject,
            target_kind=target_kind, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"reviews": [_review_to_dict(r) for r in rows]}


@router.post("/api/expert-reviews/{review_id}/claim")
def er_claim(review_id: str, user=Depends(current_user)):
    """Expert claims an item off the queue."""
    from .. import expert_review as ex
    user = require_user(user)
    try:
        r = ex.claim_review(
            review_id=review_id, reviewer_user_id=user.id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _review_to_dict(r)


@router.post("/api/expert-reviews/{review_id}/decide")
def er_decide(
    review_id: str,
    action: str = Form(..., max_length=20),
    corrected_answer: str | None = Form(None, max_length=10000),
    reason: str | None = Form(None, max_length=1000),
    user=Depends(current_user),
):
    """Expert decides on a claimed review. action ∈
    {approve, correct, reject}. correct requires
    corrected_answer."""
    from .. import expert_review as ex
    user = require_user(user)
    try:
        r = ex.decide_review(
            review_id=review_id, reviewer_user_id=user.id,
            action=action, corrected_answer=corrected_answer,
            reason=reason,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _review_to_dict(r)


@router.get("/api/expert-reviews/{review_id}")
def er_get(review_id: str, user=Depends(current_user)):
    from .. import expert_review as ex
    user = require_user(user)
    r = ex.get_review(review_id)
    if not r:
        raise HTTPException(404, "review not found")
    return _review_to_dict(r)


@router.get("/api/verifications/{target_kind}/{target_id}")
def er_verification_lookup(target_kind: str, target_id: str):
    """Public — has this content been verified by an expert?
    Hit on every content render to decide whether to show the
    'verified by teacher' badge."""
    from .. import expert_review as ex
    return {
        "is_verified": ex.is_verified(
            target_kind=target_kind, target_id=target_id,
        ),
        "verification": ex.get_verification(
            target_kind=target_kind, target_id=target_id,
        ),
    }


@router.post("/api/experts/{expert_user_id}/rate")
def er_rate(
    expert_user_id: str,
    rating: int = Form(..., ge=1, le=5),
    user=Depends(current_user),
):
    """Student rates an expert's review (1-5)."""
    from .. import expert_review as ex
    user = require_user(user)
    try:
        ok = ex.rate_expert(
            expert_user_id=expert_user_id,
            rating=rating, rater_user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not ok:
        raise HTTPException(404, "expert not found")
    return {"ok": True}


@router.get("/api/admin/expert-reviews/stats")
def er_admin_stats(user=Depends(current_user)):
    from .. import expert_review as ex
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return ex.queue_stats()


@router.post("/api/admin/expert-reviews/expire-stale")
def er_admin_expire_stale(user=Depends(current_user)):
    """Cron — sweeps pending items past SLA into 'expired'."""
    from .. import expert_review as ex
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return {"expired": ex.expire_stale_reviews()}


# ---------- v3.8 spaced repetition ----------

def _deck_to_dict(d) -> dict:
    return {
        "id": d.id, "owner_user_id": d.owner_user_id,
        "title": d.title, "description": d.description,
        "pack_code": d.pack_code, "topic_code": d.topic_code,
        "language": d.language, "visibility": d.visibility,
        "card_count": d.card_count,
        "created_at": d.created_at,
        "updated_at": d.updated_at,
    }


def _card_to_dict(c) -> dict:
    return {
        "id": c.id, "deck_id": c.deck_id,
        "position": c.position,
        "front": c.front, "back": c.back, "hint": c.hint,
        "source_ref": c.source_ref,
        "citation": c.citation,
        "created_at": c.created_at,
    }


@router.post("/api/srs/decks", status_code=201)
def srs_create_deck(
    title: str = Form(..., min_length=3, max_length=200),
    description: str | None = Form(None, max_length=2000),
    pack_code: str | None = Form(None, max_length=80),
    topic_code: str | None = Form(None, max_length=80),
    language: str = Form("en", max_length=10),
    visibility: str = Form("private", max_length=20),
    user=Depends(current_user),
):
    from .. import spaced_repetition as srs
    user = require_user(user)
    try:
        d = srs.create_deck(
            owner_user_id=user.id, title=title,
            description=description, pack_code=pack_code,
            topic_code=topic_code, language=language,
            visibility=visibility,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _deck_to_dict(d)


@router.get("/api/srs/decks/me")
def srs_my_decks(user=Depends(current_user)):
    from .. import spaced_repetition as srs
    user = require_user(user)
    return {
        "decks": [_deck_to_dict(d)
                  for d in srs.list_my_decks(user.id)],
    }


@router.get("/api/srs/decks/public")
def srs_public_decks(
    pack_code: str | None = None,
    topic_code: str | None = None,
    limit: int = 50,
):
    """Public — browse community/shared decks."""
    from .. import spaced_repetition as srs
    return {
        "decks": [
            _deck_to_dict(d)
            for d in srs.list_public_decks(
                pack_code=pack_code, topic_code=topic_code,
                limit=limit,
            )
        ],
    }


@router.get("/api/srs/decks/{deck_id}")
def srs_get_deck(deck_id: str, user=Depends(current_user)):
    from .. import spaced_repetition as srs
    user = require_user(user)
    d = srs.get_deck(deck_id)
    if not d:
        raise HTTPException(404, "deck not found")
    # Visibility check
    if d.visibility == "private" and d.owner_user_id != user.id:
        raise HTTPException(403, "deck is private")
    return {
        "deck": _deck_to_dict(d),
        "cards": [
            _card_to_dict(c) for c in srs.list_cards_for_deck(deck_id)
        ],
    }


@router.delete("/api/srs/decks/{deck_id}")
def srs_delete_deck(deck_id: str, user=Depends(current_user)):
    from .. import spaced_repetition as srs
    user = require_user(user)
    if not srs.delete_deck(
        deck_id=deck_id, owner_user_id=user.id,
    ):
        raise HTTPException(404, "deck not found or not yours")
    return {"ok": True}


@router.post("/api/srs/decks/{deck_id}/cards", status_code=201)
def srs_add_card(
    deck_id: str,
    front: str = Form(..., min_length=1, max_length=4000),
    back: str = Form(..., min_length=1, max_length=8000),
    hint: str | None = Form(None, max_length=1000),
    source_ref: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import spaced_repetition as srs
    user = require_user(user)
    try:
        card = srs.add_card(
            deck_id=deck_id, owner_user_id=user.id,
            front=front, back=back, hint=hint,
            source_ref=source_ref,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _card_to_dict(card)


@router.post("/api/srs/decks/from-retrieval", status_code=201)
def srs_generate_from_retrieval(
    deck_title: str = Form(..., min_length=3, max_length=200),
    query: str = Form(..., min_length=2, max_length=1000),
    upload_ids: str | None = Form(None, max_length=2000),
    top_k: int = Form(10, ge=1, le=50),
    pack_code: str | None = Form(None, max_length=80),
    topic_code: str | None = Form(None, max_length=80),
    user=Depends(current_user),
):
    """One-shot — run a retrieval + create a deck where each
    chunk becomes a card. Closes the loop from v3.3 retrieval to
    student-facing flashcards."""
    from .. import retrieval as rt
    from .. import spaced_repetition as srs
    user = require_user(user)
    uids = (
        [x.strip() for x in upload_ids.split(",") if x.strip()]
        if upload_ids else None
    )
    hits = rt.retrieve(
        query=query, upload_ids=uids, top_k=top_k,
    )
    if not hits:
        raise HTTPException(
            400, "no retrieval hits — try a broader query",
        )
    chunks = rt.hits_to_citations(hits)
    try:
        deck = srs.generate_from_chunks(
            owner_user_id=user.id, deck_title=deck_title,
            chunks=chunks, pack_code=pack_code,
            topic_code=topic_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _deck_to_dict(deck)


@router.post("/api/srs/cards/{card_id}/review")
def srs_review_card(
    card_id: str,
    grade: int = Form(..., ge=0, le=5),
    time_seconds: int | None = Form(None, ge=0, le=3600),
    user=Depends(current_user),
):
    """Submit a review — SM-2 schedules the next due date."""
    from .. import spaced_repetition as srs
    user = require_user(user)
    try:
        outcome = srs.review_card(
            card_id=card_id, user_id=user.id,
            grade=grade, time_seconds=time_seconds,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "card_id": outcome.card_id,
        "new_interval_days": outcome.new_interval,
        "new_ease": outcome.new_ease,
        "new_due_at": outcome.new_due_at,
        "repetitions": outcome.repetitions,
        "lapses": outcome.lapses,
    }


@router.get("/api/srs/queue")
def srs_due_queue(
    deck_id: str | None = None,
    limit: int = 20,
    include_new: bool = True,
    user=Depends(current_user),
):
    """Today's review queue for the caller."""
    from .. import spaced_repetition as srs
    user = require_user(user)
    cards = srs.due_queue(
        user_id=user.id, deck_id=deck_id,
        limit=limit, include_new=include_new,
    )
    return {"cards": [_card_to_dict(c) for c in cards]}


@router.get("/api/srs/cards/{card_id}/state")
def srs_card_state(card_id: str, user=Depends(current_user)):
    from .. import spaced_repetition as srs
    user = require_user(user)
    s = srs.get_card_state(card_id=card_id, user_id=user.id)
    if not s:
        return {"state": None}
    return {
        "state": {
            "card_id": s.card_id, "user_id": s.user_id,
            "ease_factor": s.ease_factor,
            "interval_days": s.interval_days,
            "repetitions": s.repetitions,
            "last_reviewed_at": s.last_reviewed_at,
            "due_at": s.due_at,
            "lapses": s.lapses,
        },
    }


@router.get("/api/srs/me/stats")
def srs_my_stats(user=Depends(current_user)):
    from .. import spaced_repetition as srs
    user = require_user(user)
    return srs.user_stats(user.id)


# ---------- v3.9 Socratic tutor ----------

def _exchange_to_dict(e) -> dict:
    return {
        "id": e.id, "session_id": e.session_id,
        "user_id": e.user_id,
        "student_question": e.student_question,
        "state": e.state, "turns": e.turns,
        "target_depth": e.target_depth,
        "final_answer": e.final_answer,
        "final_provenance_id": e.final_provenance_id,
        "confusion_count": e.confusion_count,
        "created_at": e.created_at,
        "completed_at": e.completed_at,
    }


@router.post("/api/socratic/exchanges", status_code=201)
def soc_start(
    session_id: str = Form(..., max_length=64),
    student_question: str = Form(..., min_length=5, max_length=4000),
    target_depth: int = Form(3, ge=1, le=8),
    user=Depends(current_user),
):
    """Begin a Socratic exchange. State starts 'diagnose' — caller
    (tutor wrapper) generates a probing sub-question instead of
    answering directly."""
    from .. import socratic_tutor as st
    user = require_user(user)
    try:
        ex = st.start_exchange(
            session_id=session_id, user_id=user.id,
            student_question=student_question,
            target_depth=target_depth,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _exchange_to_dict(ex)


@router.post("/api/socratic/exchanges/{eid}/tutor-turn")
def soc_append_tutor(
    eid: str,
    tutor_text: str = Form(..., min_length=1, max_length=8000),
    user=Depends(current_user),
):
    """Append the tutor's generated reply to the turn log."""
    from .. import socratic_tutor as st
    user = require_user(user)
    ex = st.get_exchange(eid)
    if not ex:
        raise HTTPException(404, "exchange not found")
    if ex.user_id != user.id:
        raise HTTPException(403, "not your exchange")
    try:
        ex2 = st.append_tutor_turn(
            exchange_id=eid, tutor_text=tutor_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _exchange_to_dict(ex2)


@router.post("/api/socratic/exchanges/{eid}/advance")
def soc_advance(
    eid: str,
    student_text: str = Form(..., min_length=1, max_length=4000),
    time_seconds: int | None = Form(None, ge=0, le=36000),
    user=Depends(current_user),
):
    """Student replies. We detect confusion + reveal-demand,
    advance state, return what the tutor should do next."""
    from .. import socratic_tutor as st
    user = require_user(user)
    try:
        result = st.advance(
            exchange_id=eid, user_id=user.id,
            student_text=student_text,
            time_seconds=time_seconds,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "exchange_id": result.exchange_id,
        "next_state": result.next_state,
        "next_action": result.next_action,
        "suggested_depth": result.suggested_depth,
        "student_confused": result.student_confused,
        "student_demanded_reveal": result.student_demanded_reveal,
        "turn_index": result.turn_index,
    }


@router.post("/api/socratic/exchanges/{eid}/reveal")
def soc_reveal(
    eid: str,
    final_answer: str = Form(..., min_length=1, max_length=32000),
    citations_json: str | None = Form(None, max_length=20000),
    surface: str = Form("tutor", max_length=20),
    user=Depends(current_user),
):
    """Final reveal — records citation provenance + marks
    exchange complete."""
    from .. import socratic_tutor as st
    import json as _json
    user = require_user(user)
    cits = None
    if citations_json:
        try:
            cits = _json.loads(citations_json)
        except _json.JSONDecodeError:
            raise HTTPException(400, "citations_json must be JSON")
    try:
        ex = st.reveal(
            exchange_id=eid, user_id=user.id,
            final_answer=final_answer,
            citations=cits, surface=surface,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _exchange_to_dict(ex)


@router.post("/api/socratic/exchanges/{eid}/abandon")
def soc_abandon(eid: str, user=Depends(current_user)):
    from .. import socratic_tutor as st
    user = require_user(user)
    if not st.abandon(exchange_id=eid, user_id=user.id):
        raise HTTPException(
            404, "exchange not found, not yours, or already done",
        )
    return {"ok": True}


@router.get("/api/socratic/exchanges/{eid}")
def soc_get(eid: str, user=Depends(current_user)):
    from .. import socratic_tutor as st
    user = require_user(user)
    ex = st.get_exchange(eid)
    if not ex:
        raise HTTPException(404, "exchange not found")
    if ex.user_id != user.id:
        raise HTTPException(403, "not your exchange")
    return _exchange_to_dict(ex)


@router.get("/api/socratic/me")
def soc_my_exchanges(
    state: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import socratic_tutor as st
    user = require_user(user)
    try:
        rows = st.list_user_exchanges(
            user.id, state=state, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "exchanges": [_exchange_to_dict(e) for e in rows],
    }


@router.get("/api/socratic/me/stats")
def soc_my_stats(user=Depends(current_user)):
    from .. import socratic_tutor as st
    user = require_user(user)
    return st.user_stats(user.id)


# ---------- v3.10 research / PhD tools ----------

def _research_paper_to_dict(p) -> dict:
    return {
        "id": p.id, "upload_id": p.upload_id,
        "title": p.title, "authors": p.authors,
        "year": p.year, "venue": p.venue,
        "doi": p.doi, "arxiv_id": p.arxiv_id,
        "abstract": p.abstract, "keywords": p.keywords,
        "citation_count": p.citation_count,
        "created_at": p.created_at,
    }


def _collection_to_dict(c) -> dict:
    return {
        "id": c.id, "user_id": c.user_id,
        "title": c.title, "description": c.description,
        "paper_count": c.paper_count,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _research_citation_to_dict(c) -> dict:
    return {
        "id": c.id, "paper_id": c.paper_id,
        "page_number": c.page_number, "section": c.section,
        "citation_text": c.citation_text,
        "note": c.note, "tags": c.tags,
        "created_at": c.created_at,
    }


def _gap_to_dict(g) -> dict:
    return {
        "id": g.id, "collection_id": g.collection_id,
        "theme": g.theme, "rationale": g.rationale,
        "coverage_score": g.coverage_score,
        "suggested_keywords": g.suggested_keywords,
        "created_at": g.created_at,
    }


@router.post("/api/research/papers", status_code=201)
def res_ingest_paper(
    title: str = Form(..., min_length=3, max_length=500),
    upload_id: str | None = Form(None, max_length=64),
    authors: str | None = Form(None, max_length=2000),
    year: int | None = Form(None, ge=1900, le=2100),
    venue: str | None = Form(None, max_length=200),
    doi: str | None = Form(None, max_length=200),
    arxiv_id: str | None = Form(None, max_length=40),
    abstract: str | None = Form(None, max_length=8000),
    keywords: str | None = Form(None, max_length=1000),
    citation_count: int | None = Form(None, ge=0),
    user=Depends(current_user),
):
    """Ingest a research paper. authors / keywords are comma-sep."""
    from .. import research_tools as rt
    user = require_user(user)

    def _split(s):
        return [x.strip() for x in (s or "").split(",") if x.strip()] or None

    try:
        p = rt.ingest_paper(
            user_id=user.id, title=title, upload_id=upload_id,
            authors=_split(authors),
            year=year, venue=venue, doi=doi, arxiv_id=arxiv_id,
            abstract=abstract,
            keywords=_split(keywords),
            citation_count=citation_count,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _research_paper_to_dict(p)


@router.get("/api/research/papers/me")
def res_my_papers(user=Depends(current_user)):
    from .. import research_tools as rt
    user = require_user(user)
    return {
        "papers": [_research_paper_to_dict(p)
                   for p in rt.list_user_papers(user.id)],
    }


@router.get("/api/research/papers/{paper_id}")
def res_get_paper(paper_id: str, user=Depends(current_user)):
    from .. import research_tools as rt
    user = require_user(user)
    p = rt.get_paper(paper_id)
    if not p:
        raise HTTPException(404, "paper not found")
    if p.user_id != user.id:
        raise HTTPException(403, "not your paper")
    summary = rt.get_summary(paper_id)
    return {
        "paper": _research_paper_to_dict(p),
        "summary": ({
            "short_summary": summary.short_summary,
            "key_findings": summary.key_findings,
            "methods": summary.methods,
            "limitations": summary.limitations,
            "future_work": summary.future_work,
            "generated_at": summary.generated_at,
        } if summary else None),
    }


@router.post("/api/research/papers/{paper_id}/summary",
             status_code=201)
def res_save_summary(
    paper_id: str,
    short_summary: str = Form(..., min_length=20, max_length=8000),
    key_findings: str | None = Form(None, max_length=8000),
    methods: str | None = Form(None, max_length=4000),
    limitations: str | None = Form(None, max_length=4000),
    future_work: str | None = Form(None, max_length=4000),
    ai_call_id: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    """Persist an LLM-generated summary. Caller does the LLM call
    + posts the structured result here for caching.
    key_findings is semicolon-separated."""
    from .. import research_tools as rt
    user = require_user(user)
    kf = (
        [x.strip() for x in key_findings.split(";") if x.strip()]
        if key_findings else None
    )
    try:
        s = rt.save_summary(
            paper_id=paper_id, user_id=user.id,
            short_summary=short_summary,
            key_findings=kf, methods=methods,
            limitations=limitations, future_work=future_work,
            ai_call_id=ai_call_id,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "paper_id": s.paper_id,
        "short_summary": s.short_summary,
        "key_findings": s.key_findings,
        "generated_at": s.generated_at,
    }


@router.post("/api/research/collections", status_code=201)
def res_create_collection(
    title: str = Form(..., min_length=3, max_length=200),
    description: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    try:
        c = rt.create_collection(
            user_id=user.id, title=title, description=description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _collection_to_dict(c)


@router.get("/api/research/collections/me")
def res_my_collections(user=Depends(current_user)):
    from .. import research_tools as rt
    user = require_user(user)
    return {
        "collections": [
            _collection_to_dict(c)
            for c in rt.list_user_collections(user.id)
        ],
    }


@router.get("/api/research/collections/{cid}")
def res_get_collection(cid: str, user=Depends(current_user)):
    from .. import research_tools as rt
    user = require_user(user)
    c = rt.get_collection(cid)
    if not c:
        raise HTTPException(404, "collection not found")
    if c.user_id != user.id:
        raise HTTPException(403, "not your collection")
    return {
        "collection": _collection_to_dict(c),
        "papers": rt.list_collection_papers(cid),
    }


@router.post("/api/research/collections/{cid}/papers")
def res_add_to_collection(
    cid: str,
    paper_id: str = Form(..., max_length=64),
    notes: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    try:
        pos = rt.add_to_collection(
            collection_id=cid, paper_id=paper_id,
            user_id=user.id, notes=notes,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"position": pos}


@router.delete(
    "/api/research/collections/{cid}/papers/{paper_id}",
)
def res_remove_from_collection(
    cid: str, paper_id: str, user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    if not rt.remove_from_collection(
        collection_id=cid, paper_id=paper_id, user_id=user.id,
    ):
        raise HTTPException(
            404, "not found, not yours, or not in collection",
        )
    return {"ok": True}


@router.get("/api/research/collections/{cid}/map")
def res_lit_map(cid: str, user=Depends(current_user)):
    """Literature graph for a collection — nodes + edges based
    on shared keywords / authors."""
    from .. import research_tools as rt
    user = require_user(user)
    c = rt.get_collection(cid)
    if not c:
        raise HTTPException(404, "collection not found")
    if c.user_id != user.id:
        raise HTTPException(403, "not your collection")
    return rt.literature_map(cid)


@router.post("/api/research/collections/{cid}/detect-gaps")
def res_detect_gaps(
    cid: str,
    proposed_themes: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    """Run gap detection. Pass comma-sep proposed_themes to test
    specific hypotheses. Returns the freshly-detected gaps."""
    from .. import research_tools as rt
    user = require_user(user)
    themes = (
        [x.strip() for x in proposed_themes.split(",") if x.strip()]
        if proposed_themes else None
    )
    try:
        gaps = rt.detect_gaps(
            collection_id=cid, user_id=user.id,
            proposed_themes=themes,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"gaps": [_gap_to_dict(g) for g in gaps]}


@router.get("/api/research/collections/{cid}/gaps")
def res_list_gaps(cid: str, user=Depends(current_user)):
    from .. import research_tools as rt
    user = require_user(user)
    c = rt.get_collection(cid)
    if not c:
        raise HTTPException(404, "collection not found")
    if c.user_id != user.id:
        raise HTTPException(403, "not your collection")
    return {
        "gaps": [_gap_to_dict(g) for g in rt.list_gaps(cid)],
    }


@router.post("/api/research/citations", status_code=201)
def res_flag_citation(
    paper_id: str = Form(..., max_length=64),
    citation_text: str = Form(..., min_length=10, max_length=4000),
    page_number: int | None = Form(None, ge=1, le=10000),
    section: str | None = Form(None, max_length=200),
    note: str | None = Form(None, max_length=2000),
    tags: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    tag_list = (
        [t.strip() for t in tags.split(",") if t.strip()]
        if tags else None
    )
    try:
        c = rt.flag_citation(
            user_id=user.id, paper_id=paper_id,
            citation_text=citation_text,
            page_number=page_number, section=section,
            note=note, tags=tag_list,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _research_citation_to_dict(c)


@router.get("/api/research/citations/me")
def res_list_my_citations(
    paper_id: str | None = None,
    tag: str | None = None,
    limit: int = 100,
    user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    rows = rt.list_citations(
        user_id=user.id, paper_id=paper_id, tag=tag, limit=limit,
    )
    return {
        "citations": [
            _research_citation_to_dict(c) for c in rows
        ],
    }


@router.delete("/api/research/citations/{citation_id}")
def res_delete_citation(
    citation_id: str, user=Depends(current_user),
):
    from .. import research_tools as rt
    user = require_user(user)
    if not rt.delete_citation(
        citation_id=citation_id, user_id=user.id,
    ):
        raise HTTPException(404, "citation not found or not yours")
    return {"ok": True}


# ---------- v3.11 marketplace quality controls ----------

def _market_rating_to_dict(r) -> dict:
    return {
        "id": r.id, "item_kind": r.item_kind,
        "item_id": r.item_id, "user_id": r.user_id,
        "rating": r.rating, "review_text": r.review_text,
        "helpful_count": r.helpful_count,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


def _refund_to_dict(r) -> dict:
    return {
        "id": r.id, "item_kind": r.item_kind,
        "item_id": r.item_id, "purchase_id": r.purchase_id,
        "user_id": r.user_id, "amount_paise": r.amount_paise,
        "reason": r.reason, "status": r.status,
        "reviewer_user_id": r.reviewer_user_id,
        "decision_reason": r.decision_reason,
        "requested_at": r.requested_at,
        "decided_at": r.decided_at,
        "sla_due_at": r.sla_due_at,
    }


def _claim_to_dict(c) -> dict:
    return {
        "id": c.id, "item_kind": c.item_kind,
        "item_id": c.item_id,
        "claimant_user_id": c.claimant_user_id,
        "claimant_kind": c.claimant_kind,
        "claim_type": c.claim_type, "severity": c.severity,
        "evidence_text": c.evidence_text,
        "evidence_url": c.evidence_url,
        "status": c.status,
        "reviewer_user_id": c.reviewer_user_id,
        "decision_reason": c.decision_reason,
        "filed_at": c.filed_at,
        "decided_at": c.decided_at,
    }


def _quality_to_dict(q) -> dict:
    return {
        "item_kind": q.item_kind, "item_id": q.item_id,
        "score": q.score, "rating_avg": q.rating_avg,
        "rating_count": q.rating_count,
        "refund_count": q.refund_count,
        "copyright_claims_open": q.copyright_claims_open,
        "copyright_claims_upheld": q.copyright_claims_upheld,
        "last_rated_at": q.last_rated_at,
        "computed_at": q.computed_at,
    }


@router.post("/api/market/ratings", status_code=201)
def mq_rate(
    item_kind: str = Form(..., max_length=40),
    item_id: str = Form(..., max_length=64),
    rating: int = Form(..., ge=1, le=5),
    review_text: str | None = Form(None, max_length=4000),
    user=Depends(current_user),
):
    """Rate any marketplace item (course / content_pack /
    question_pack / tutor). Idempotent on (item, user)."""
    from .. import marketplace_quality as mq
    user = require_user(user)
    try:
        r = mq.rate(
            item_kind=item_kind, item_id=item_id,
            user_id=user.id, rating=rating,
            review_text=review_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _market_rating_to_dict(r)


@router.get("/api/market/ratings/{item_kind}/{item_id}")
def mq_list_ratings(
    item_kind: str, item_id: str, limit: int = 50,
):
    """Public — list ratings for an item, helpful-count sorted."""
    from .. import marketplace_quality as mq
    try:
        rows = mq.list_ratings_for_item(
            item_kind=item_kind, item_id=item_id, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ratings": [_market_rating_to_dict(r) for r in rows],
    }


@router.post("/api/market/ratings/{rating_id}/helpful")
def mq_mark_helpful(rating_id: str, user=Depends(current_user)):
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not mq.mark_review_helpful(rating_id=rating_id):
        raise HTTPException(404, "rating not found")
    return {"ok": True}


@router.get("/api/market/quality/{item_kind}/{item_id}")
def mq_get_quality(item_kind: str, item_id: str):
    """Public — current quality score for an item. Drives the
    UI's quality badge."""
    from .. import marketplace_quality as mq
    try:
        q = mq.get_quality(item_kind=item_kind, item_id=item_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not q:
        return {"quality": None}
    return {"quality": _quality_to_dict(q)}


@router.get("/api/market/quality/top")
def mq_top_quality(
    item_kind: str | None = None,
    min_rating_count: int = 3,
    limit: int = 20,
):
    """Public — top-quality items for the marketplace home."""
    from .. import marketplace_quality as mq
    try:
        rows = mq.top_items_by_quality(
            item_kind=item_kind,
            min_rating_count=min_rating_count, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": [_quality_to_dict(q) for q in rows]}


@router.get("/api/market/status/{item_kind}/{item_id}")
def mq_get_item_status(item_kind: str, item_id: str):
    """Public — item visibility status."""
    from .. import marketplace_quality as mq
    try:
        return mq.get_item_status(
            item_kind=item_kind, item_id=item_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/market/refunds", status_code=201)
def mq_refund(
    item_kind: str = Form(..., max_length=40),
    item_id: str = Form(..., max_length=64),
    purchase_id: str = Form(..., max_length=64),
    amount_paise: int = Form(..., ge=1),
    reason: str = Form(..., min_length=5, max_length=1000),
    user=Depends(current_user),
):
    from .. import marketplace_quality as mq
    user = require_user(user)
    try:
        r = mq.request_refund(
            item_kind=item_kind, item_id=item_id,
            purchase_id=purchase_id, user_id=user.id,
            amount_paise=amount_paise, reason=reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _refund_to_dict(r)


@router.get("/api/admin/market/refunds")
def mq_refund_queue(
    status: str = "pending",
    item_kind: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = mq.list_refund_queue(
            status=status, item_kind=item_kind, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"refunds": [_refund_to_dict(r) for r in rows]}


@router.post("/api/admin/market/refunds/{refund_id}/decide")
def mq_refund_decide(
    refund_id: str,
    action: str = Form(..., max_length=20),
    decision_reason: str | None = Form(None, max_length=1000),
    user=Depends(current_user),
):
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        r = mq.decide_refund(
            refund_id=refund_id, action=action,
            reviewer_user_id=user.id,
            decision_reason=decision_reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _refund_to_dict(r)


@router.post("/api/admin/market/refunds/expire-stale")
def mq_refund_expire_stale(user=Depends(current_user)):
    """Cron — pending refunds past SLA flip to auto_expired."""
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return {"expired": mq.expire_stale_refunds()}


@router.post("/api/market/copyright-claims", status_code=201)
def mq_file_claim(
    item_kind: str = Form(..., max_length=40),
    item_id: str = Form(..., max_length=64),
    claim_type: str = Form(..., max_length=40),
    severity: str = Form("moderate", max_length=20),
    claimant_kind: str = Form("individual", max_length=40),
    evidence_text: str | None = Form(None, max_length=8000),
    evidence_url: str | None = Form(None, max_length=500),
    user=Depends(current_user),
):
    """File a copyright/plagiarism claim against an item."""
    from .. import marketplace_quality as mq
    user = require_user(user)
    try:
        c = mq.file_copyright_claim(
            item_kind=item_kind, item_id=item_id,
            claimant_user_id=user.id,
            claim_type=claim_type, severity=severity,
            claimant_kind=claimant_kind,
            evidence_text=evidence_text,
            evidence_url=evidence_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _claim_to_dict(c)


@router.get("/api/admin/market/copyright-claims")
def mq_list_claims(
    status: str = "open",
    item_kind: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        rows = mq.list_claims(
            status=status, item_kind=item_kind, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"claims": [_claim_to_dict(c) for c in rows]}


@router.post("/api/admin/market/copyright-claims/{claim_id}/decide")
def mq_decide_claim(
    claim_id: str,
    action: str = Form(..., max_length=20),
    decision_reason: str | None = Form(None, max_length=1000),
    user=Depends(current_user),
):
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        c = mq.decide_claim(
            claim_id=claim_id, action=action,
            reviewer_user_id=user.id,
            decision_reason=decision_reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _claim_to_dict(c)


@router.post("/api/admin/market/status")
def mq_admin_set_status(
    item_kind: str = Form(..., max_length=40),
    item_id: str = Form(..., max_length=64),
    status: str = Form(..., max_length=20),
    reason: str | None = Form(None, max_length=1000),
    user=Depends(current_user),
):
    """Admin override of item status."""
    from .. import marketplace_quality as mq
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        mq.set_item_status(
            item_kind=item_kind, item_id=item_id,
            status=status, reason=reason,
            updated_by_user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------- v3.12 offline packs + low-data ----------

def _manifest_to_dict(m) -> dict:
    return {
        "id": m.id, "user_id": m.user_id,
        "pack_code": m.pack_code, "topic_code": m.topic_code,
        "title": m.title, "version": m.version,
        "quality_tier": m.quality_tier,
        "file_count": m.file_count,
        "total_bytes": m.total_bytes,
        "files": m.files,
        "expires_at": m.expires_at,
        "created_at": m.created_at,
    }


def _download_to_dict(d) -> dict:
    return {
        "id": d.id, "manifest_id": d.manifest_id,
        "bytes_downloaded": d.bytes_downloaded,
        "files_completed": d.files_completed,
        "status": d.status,
        "started_at": d.started_at,
        "completed_at": d.completed_at,
        "last_progress_at": d.last_progress_at,
        "network": d.network,
    }


@router.get("/api/offline/prefs")
def off_get_prefs(user=Depends(current_user)):
    from .. import offline_packs as off
    user = require_user(user)
    p = off.get_low_data_prefs(user.id)
    return {
        "quality_tier": p.quality_tier,
        "auto_downgrade_on_cellular": p.auto_downgrade_on_cellular,
        "max_daily_mb": p.max_daily_mb,
        "updated_at": p.updated_at,
    }


@router.post("/api/offline/prefs")
def off_set_prefs(
    quality_tier: str | None = Form(None, max_length=20),
    auto_downgrade_on_cellular: bool | None = Form(None),
    max_daily_mb: int | None = Form(None, ge=0, le=100_000),
    user=Depends(current_user),
):
    """Update low-data prefs. Pass only the fields you're
    changing."""
    from .. import offline_packs as off
    user = require_user(user)
    try:
        p = off.set_low_data_prefs(
            user_id=user.id,
            quality_tier=quality_tier,
            auto_downgrade_on_cellular=auto_downgrade_on_cellular,
            max_daily_mb=max_daily_mb,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "quality_tier": p.quality_tier,
        "auto_downgrade_on_cellular": p.auto_downgrade_on_cellular,
        "max_daily_mb": p.max_daily_mb,
    }


@router.post("/api/offline/manifests", status_code=201)
def off_generate_manifest(
    title: str = Form(..., min_length=3, max_length=200),
    files_json: str = Form(..., max_length=200_000),
    pack_code: str | None = Form(None, max_length=80),
    topic_code: str | None = Form(None, max_length=80),
    quality_tier: str | None = Form(None, max_length=20),
    version: int = Form(1, ge=1, le=999),
    expires_in_hours: int = Form(168, ge=1, le=720),
    user=Depends(current_user),
):
    """Generate a manifest. files_json is a JSON array of
    {ref_kind, ref_id, priority?, bytes?, url?, title?}."""
    from .. import offline_packs as off
    import json as _json
    user = require_user(user)
    try:
        files = _json.loads(files_json)
    except _json.JSONDecodeError:
        raise HTTPException(400, "files_json must be JSON array")
    if not isinstance(files, list):
        raise HTTPException(400, "files_json must be array")
    try:
        m = off.generate_manifest(
            user_id=user.id, title=title, files=files,
            pack_code=pack_code, topic_code=topic_code,
            quality_tier=quality_tier, version=version,
            expires_in_hours=expires_in_hours,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _manifest_to_dict(m)


@router.get("/api/offline/manifests/me")
def off_list_manifests(
    active_only: bool = True,
    user=Depends(current_user),
):
    from .. import offline_packs as off
    user = require_user(user)
    rows = off.list_user_manifests(
        user.id, active_only=active_only,
    )
    return {
        "manifests": [_manifest_to_dict(m) for m in rows],
    }


@router.get("/api/offline/manifests/{mid}")
def off_get_manifest(mid: str, user=Depends(current_user)):
    from .. import offline_packs as off
    user = require_user(user)
    m = off.get_manifest(mid)
    if not m:
        raise HTTPException(404, "manifest not found")
    if m.user_id != user.id:
        raise HTTPException(403, "not your manifest")
    return _manifest_to_dict(m)


@router.delete("/api/offline/manifests/{mid}")
def off_delete_manifest(mid: str, user=Depends(current_user)):
    from .. import offline_packs as off
    user = require_user(user)
    if not off.delete_manifest(
        manifest_id=mid, user_id=user.id,
    ):
        raise HTTPException(404, "manifest not found or not yours")
    return {"ok": True}


@router.post(
    "/api/offline/manifests/{mid}/download",
    status_code=201,
)
def off_start_download(
    mid: str,
    network: str | None = Form(None, max_length=20),
    user=Depends(current_user),
):
    from .. import offline_packs as off
    user = require_user(user)
    try:
        d = off.start_download(
            manifest_id=mid, user_id=user.id, network=network,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _download_to_dict(d)


@router.post("/api/offline/downloads/{did}/progress")
def off_update_progress(
    did: str,
    bytes_downloaded: int = Form(..., ge=0),
    files_completed: int = Form(..., ge=0),
    user=Depends(current_user),
):
    from .. import offline_packs as off
    user = require_user(user)
    try:
        d = off.update_progress(
            download_id=did, user_id=user.id,
            bytes_downloaded=bytes_downloaded,
            files_completed=files_completed,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _download_to_dict(d)


@router.post("/api/offline/downloads/{did}/cancel")
def off_cancel_download(did: str, user=Depends(current_user)):
    from .. import offline_packs as off
    user = require_user(user)
    if not off.cancel_download(
        download_id=did, user_id=user.id,
    ):
        raise HTTPException(
            404, "download not found, not yours, or not in_progress",
        )
    return {"ok": True}


@router.get("/api/offline/downloads/me")
def off_my_downloads(
    status: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import offline_packs as off
    user = require_user(user)
    try:
        rows = off.list_user_downloads(
            user.id, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "downloads": [_download_to_dict(d) for d in rows],
    }


@router.get("/api/offline/usage/today")
def off_usage_today(user=Depends(current_user)):
    from .. import offline_packs as off
    user = require_user(user)
    return off.user_data_usage_today(user.id)


# ---------- v3.13 messaging (WhatsApp / SMS) ----------

def _msg_channel_to_dict(c) -> dict:
    return {
        "id": c.id, "phone_number": c.phone_number,
        "channel": c.channel,
        "opt_in_status": c.opt_in_status,
        "consented_at": c.consented_at,
        "revoked_at": c.revoked_at,
    }


def _msg_template_to_dict(t) -> dict:
    return {
        "id": t.id, "template_code": t.template_code,
        "channel": t.channel, "language": t.language,
        "title": t.title, "body_template": t.body_template,
        "variables": t.variables,
        "approval_status": t.approval_status,
        "daily_max_per_user": t.daily_max_per_user,
        "created_at": t.created_at,
        "approved_at": t.approved_at,
    }


def _msg_to_dict(m) -> dict:
    return {
        "id": m.id, "user_id": m.user_id,
        "template_code": m.template_code,
        "channel": m.channel, "phone_number": m.phone_number,
        "rendered_body": m.rendered_body,
        "variables": m.variables,
        "scheduled_at": m.scheduled_at, "status": m.status,
        "sent_at": m.sent_at, "error_reason": m.error_reason,
        "retries": m.retries,
        "provider_msg_id": m.provider_msg_id,
    }


@router.post("/api/messaging/opt-in", status_code=201)
def msg_opt_in(
    phone_number: str = Form(..., max_length=20),
    channel: str = Form(..., max_length=20),
    consent_text: str = Form(..., min_length=20, max_length=2000),
    user=Depends(current_user),
):
    """DPDP §6 explicit consent. consent_text is the exact text
    shown to the user at opt-in time."""
    from .. import messaging as msg
    user = require_user(user)
    try:
        c = msg.opt_in_channel(
            user_id=user.id, phone_number=phone_number,
            channel=channel, consent_text=consent_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _msg_channel_to_dict(c)


@router.post("/api/messaging/opt-out")
def msg_opt_out(
    phone_number: str = Form(..., max_length=20),
    channel: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    """DPDP §13 — withdraw consent."""
    from .. import messaging as msg
    user = require_user(user)
    ok = msg.opt_out_channel(
        user_id=user.id, phone_number=phone_number,
        channel=channel,
    )
    if not ok:
        raise HTTPException(
            404, "active opt-in record not found",
        )
    return {"ok": True}


@router.get("/api/messaging/channels/me")
def msg_my_channels(
    channel: str | None = None,
    opt_in_status: str | None = None,
    user=Depends(current_user),
):
    from .. import messaging as msg
    user = require_user(user)
    try:
        rows = msg.list_user_channels(
            user.id, channel=channel,
            opt_in_status=opt_in_status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "channels": [_msg_channel_to_dict(c) for c in rows],
    }


@router.post("/api/admin/messaging/templates", status_code=201)
def msg_admin_create_template(
    template_code: str = Form(..., max_length=80),
    channel: str = Form(..., max_length=20),
    language: str = Form("en", max_length=10),
    title: str = Form(..., min_length=3, max_length=200),
    body_template: str = Form(..., min_length=5, max_length=2000),
    daily_max_per_user: int = Form(1, ge=1, le=100),
    provider_template_id: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import messaging as msg
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    try:
        t = msg.create_template(
            template_code=template_code, channel=channel,
            language=language, title=title,
            body_template=body_template,
            daily_max_per_user=daily_max_per_user,
            provider_template_id=provider_template_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _msg_template_to_dict(t)


@router.post("/api/admin/messaging/templates/{code}/approve")
def msg_admin_approve_template(
    code: str,
    channel: str = Form(..., max_length=20),
    language: str = Form("en", max_length=10),
    provider_template_id: str | None = Form(None, max_length=200),
    user=Depends(current_user),
):
    from .. import messaging as msg
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    if not msg.approve_template(
        template_code=code, channel=channel, language=language,
        provider_template_id=provider_template_id,
    ):
        raise HTTPException(
            404, "template not found or not pending",
        )
    return {"ok": True}


@router.get("/api/messaging/templates")
def msg_list_templates(
    channel: str | None = None,
    approval_status: str | None = None,
    user=Depends(current_user),
):
    """Authed — list templates for the caller's UI to pick from."""
    from .. import messaging as msg
    user = require_user(user)
    try:
        rows = msg.list_templates(
            channel=channel,
            approval_status=approval_status,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "templates": [_msg_template_to_dict(t) for t in rows],
    }


@router.post("/api/messaging/schedule", status_code=201)
def msg_schedule(
    template_code: str = Form(..., max_length=80),
    variables_json: str = Form("{}", max_length=8000),
    scheduled_at: float | None = Form(None),
    language: str = Form("en", max_length=10),
    channel: str | None = Form(None, max_length=20),
    user=Depends(current_user),
):
    """Schedule a message to the caller. Caller passes the
    template variables as JSON."""
    from .. import messaging as msg
    import json as _json
    user = require_user(user)
    try:
        variables = _json.loads(variables_json)
    except _json.JSONDecodeError:
        raise HTTPException(
            400, "variables_json must be JSON object",
        )
    if not isinstance(variables, dict):
        raise HTTPException(
            400, "variables_json must be JSON object",
        )
    try:
        m = msg.schedule_message(
            user_id=user.id, template_code=template_code,
            variables=variables, scheduled_at=scheduled_at,
            language=language, channel=channel,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _msg_to_dict(m)


@router.post("/api/messaging/messages/{message_id}/cancel")
def msg_cancel(message_id: str, user=Depends(current_user)):
    from .. import messaging as msg
    user = require_user(user)
    if not msg.cancel_message(
        message_id=message_id, user_id=user.id,
    ):
        raise HTTPException(
            404, "message not found, not yours, or already sent",
        )
    return {"ok": True}


@router.get("/api/messaging/messages/me")
def msg_my_messages(
    status: str | None = None,
    limit: int = 100,
    user=Depends(current_user),
):
    from .. import messaging as msg
    user = require_user(user)
    try:
        rows = msg.list_user_messages(
            user.id, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"messages": [_msg_to_dict(m) for m in rows]}


@router.get("/api/messaging/me/stats")
def msg_my_stats(user=Depends(current_user)):
    from .. import messaging as msg
    user = require_user(user)
    return msg.user_message_stats(user.id)


@router.post("/api/admin/messaging/send-due")
def msg_admin_send_due(
    batch_size: int = Form(50, ge=1, le=500),
    user=Depends(current_user),
):
    """Worker entry — pulls a batch of due scheduled messages
    and sends them via the configured providers (or sandbox)."""
    from .. import messaging as msg
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return msg.send_due(batch_size=batch_size)


@router.post("/api/admin/messaging/bounce")
def msg_admin_bounce(
    phone_number: str = Form(..., max_length=20),
    channel: str = Form(..., max_length=20),
    user=Depends(current_user),
):
    """Provider webhook — phone bounced; stop sending."""
    from .. import messaging as msg
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    n = msg.mark_bounced(
        phone_number=phone_number, channel=channel,
    )
    return {"affected": n}


# ---------- v3.14 audio recap ----------

def _audio_seg_to_dict(s) -> dict:
    return {
        "id": s.id, "position": s.position, "role": s.role,
        "transcript": s.transcript,
        "citations": s.citations,
        "audio_path": s.audio_path,
        "duration_sec": s.duration_sec,
    }


def _recap_to_dict(r) -> dict:
    return {
        "id": r.id, "user_id": r.user_id, "title": r.title,
        "source_kind": r.source_kind,
        "source_id": r.source_id,
        "query": r.query, "language": r.language,
        "answer_mode": r.answer_mode, "status": r.status,
        "duration_sec": r.duration_sec,
        "error_reason": r.error_reason,
        "created_at": r.created_at,
        "completed_at": r.completed_at,
        "segments": [
            _audio_seg_to_dict(s) for s in r.segments
        ],
    }


@router.post("/api/audio-recaps", status_code=201)
def ar_create(
    title: str = Form(..., min_length=3, max_length=200),
    source_kind: str = Form(..., max_length=20),
    source_id: str | None = Form(None, max_length=64),
    query: str | None = Form(None, max_length=1000),
    language: str = Form("en", max_length=10),
    answer_mode: str = Form("cited", max_length=20),
    user=Depends(current_user),
):
    """Create a pending audio recap. Follow up with set-segments
    (manual) or generate-script (retrieval-driven)."""
    from .. import audio_recap as ar
    user = require_user(user)
    try:
        r = ar.create_recap(
            user_id=user.id, title=title,
            source_kind=source_kind, source_id=source_id,
            query=query, language=language,
            answer_mode=answer_mode,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _recap_to_dict(r)


@router.post("/api/audio-recaps/{rid}/segments")
def ar_set_segments(
    rid: str,
    body_segments_json: str = Form(..., max_length=200_000),
    custom_intro: str | None = Form(None, max_length=4000),
    custom_outro: str | None = Form(None, max_length=4000),
    user=Depends(current_user),
):
    """Replace body segments. body_segments_json is a JSON array
    of {transcript, citations?}."""
    from .. import audio_recap as ar
    import json as _json
    user = require_user(user)
    try:
        body = _json.loads(body_segments_json)
    except _json.JSONDecodeError:
        raise HTTPException(
            400, "body_segments_json must be JSON array",
        )
    if not isinstance(body, list):
        raise HTTPException(
            400, "body_segments_json must be array",
        )
    try:
        r = ar.set_segments(
            recap_id=rid, user_id=user.id,
            body_segments=body,
            custom_intro=custom_intro,
            custom_outro=custom_outro,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _recap_to_dict(r)


@router.post("/api/audio-recaps/{rid}/generate-script")
def ar_generate_script(
    rid: str,
    query: str | None = Form(None, max_length=1000),
    top_k: int = Form(5, ge=1, le=8),
    user=Depends(current_user),
):
    """Retrieval-driven script generation. Pulls top-k chunks +
    turns each into a body segment with citation attached."""
    from .. import audio_recap as ar
    user = require_user(user)
    try:
        r = ar.generate_script_from_query(
            recap_id=rid, user_id=user.id,
            query=query, top_k=top_k,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _recap_to_dict(r)


@router.get("/api/audio-recaps/{rid}")
def ar_get(rid: str, user=Depends(current_user)):
    from .. import audio_recap as ar
    user = require_user(user)
    r = ar.get_recap(rid)
    if not r:
        raise HTTPException(404, "recap not found")
    if r.user_id != user.id:
        raise HTTPException(403, "not your recap")
    return _recap_to_dict(r)


@router.get("/api/audio-recaps/me")
def ar_my_recaps(
    status: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import audio_recap as ar
    user = require_user(user)
    try:
        rows = ar.list_user_recaps(
            user.id, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"recaps": [_recap_to_dict(r) for r in rows]}


@router.post("/api/audio-recaps/{rid}/cancel")
def ar_cancel(rid: str, user=Depends(current_user)):
    from .. import audio_recap as ar
    user = require_user(user)
    if not ar.cancel_recap(recap_id=rid, user_id=user.id):
        raise HTTPException(
            404, "recap not found, not yours, or already done",
        )
    return {"ok": True}


@router.delete("/api/audio-recaps/{rid}")
def ar_delete(rid: str, user=Depends(current_user)):
    from .. import audio_recap as ar
    user = require_user(user)
    if not ar.delete_recap(recap_id=rid, user_id=user.id):
        raise HTTPException(404, "recap not found or not yours")
    return {"ok": True}


@router.get("/api/audio-recaps/me/stats")
def ar_my_stats(user=Depends(current_user)):
    from .. import audio_recap as ar
    user = require_user(user)
    return ar.user_stats(user.id)


@router.post("/api/admin/audio-recaps/render-pending")
def ar_admin_render(
    batch_size: int = Form(10, ge=1, le=100),
    user=Depends(current_user),
):
    """Worker entry — render queued recaps. In sandbox mode this
    is a no-op marking segments rendered without calling TTS."""
    from .. import audio_recap as ar
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return ar.render_pending(batch_size=batch_size)


# ---------- v3.15 adaptive personalised packs ----------

def _adaptive_pack_to_dict(p) -> dict:
    return {
        "id": p.id, "user_id": p.user_id,
        "base_pack_code": p.base_pack_code,
        "title": p.title, "description": p.description,
        "last_adapted_at": p.last_adapted_at,
        "adaptation_count": p.adaptation_count,
        "created_at": p.created_at,
    }


def _override_to_dict(o) -> dict:
    return {
        "topic_code": o.topic_code,
        "base_weightage": o.base_weightage,
        "adjusted_weightage": o.adjusted_weightage,
        "reasons": o.reasons,
        "updated_at": o.updated_at,
    }


def _adapt_signal_to_dict(s) -> dict:
    return {
        "id": s.id, "rule_code": s.rule_code,
        "topic_code": s.topic_code,
        "signal_value": s.signal_value,
        "weightage_delta": s.weightage_delta,
        "created_at": s.created_at,
    }


@router.post("/api/adaptive-packs", status_code=201)
def adapt_create(
    base_pack_code: str = Form(..., max_length=80),
    title: str | None = Form(None, max_length=200),
    description: str | None = Form(None, max_length=2000),
    user=Depends(current_user),
):
    """Create a per-user personalised overlay of a base Exam Pack.
    Idempotent — re-creating returns the existing row."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    try:
        p = ap.create_personalised_pack(
            user_id=user.id,
            base_pack_code=base_pack_code,
            title=title, description=description,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _adaptive_pack_to_dict(p)


@router.get("/api/adaptive-packs/me")
def adapt_my_packs(user=Depends(current_user)):
    from .. import adaptive_packs as ap
    user = require_user(user)
    return {
        "packs": [
            _adaptive_pack_to_dict(p)
            for p in ap.list_user_packs(user.id)
        ],
    }


@router.get("/api/adaptive-packs/{base_pack_code}/me")
def adapt_get(base_pack_code: str, user=Depends(current_user)):
    from .. import adaptive_packs as ap
    user = require_user(user)
    p = ap.get_personalised_pack(
        user_id=user.id, base_pack_code=base_pack_code,
    )
    if not p:
        raise HTTPException(404, "no personalised pack yet")
    return _adaptive_pack_to_dict(p)


@router.post("/api/adaptive-packs/{base_pack_code}/re-adapt")
def adapt_re_adapt(
    base_pack_code: str, user=Depends(current_user),
):
    """Recompute personalised weightages from latest signals."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    try:
        return ap.re_adapt(
            user_id=user.id, base_pack_code=base_pack_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/adaptive-packs/{base_pack_code}/topics")
def adapt_topic_view(
    base_pack_code: str, user=Depends(current_user),
):
    """Personalised topic list — adjusted weightages + reasons.
    Falls back to base weightages when no overrides exist."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    try:
        return {
            "topics": ap.personalised_topic_view(
                user_id=user.id,
                base_pack_code=base_pack_code,
            ),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/adaptive-packs/{pack_id}/overrides")
def adapt_overrides(pack_id: str, user=Depends(current_user)):
    """Audit — sorted list of adjusted_weightages."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    p = ap._get_personalised(pack_id)
    if not p:
        raise HTTPException(404, "personalised pack not found")
    if p.user_id != user.id:
        raise HTTPException(403, "not your pack")
    rows = ap.get_overrides(pack_id)
    return {
        "overrides": [_override_to_dict(o) for o in rows],
    }


@router.get("/api/adaptive-packs/{pack_id}/signals")
def adapt_signals(
    pack_id: str, limit: int = 100, user=Depends(current_user),
):
    """Audit — what events fired which rules during the last
    adaptation."""
    from .. import adaptive_packs as ap
    user = require_user(user)
    p = ap._get_personalised(pack_id)
    if not p:
        raise HTTPException(404, "personalised pack not found")
    if p.user_id != user.id:
        raise HTTPException(403, "not your pack")
    return {
        "signals": [
            _adapt_signal_to_dict(s)
            for s in ap.list_signals(pack_id, limit=limit)
        ],
    }


@router.get("/api/adaptive-packs/{base_pack_code}/should-re-adapt")
def adapt_should(
    base_pack_code: str, user=Depends(current_user),
):
    """Probe — does the current personalised pack need a refresh?"""
    from .. import adaptive_packs as ap
    user = require_user(user)
    needs, reason = ap.should_re_adapt(
        user_id=user.id, base_pack_code=base_pack_code,
    )
    return {"should_re_adapt": needs, "reason": reason}


@router.delete("/api/adaptive-packs/{pack_id}")
def adapt_delete(pack_id: str, user=Depends(current_user)):
    from .. import adaptive_packs as ap
    user = require_user(user)
    if not ap.delete_personalised_pack(
        pack_id=pack_id, user_id=user.id,
    ):
        raise HTTPException(
            404, "personalised pack not found or not yours",
        )
    return {"ok": True}


# ---------- v3.16 step-by-step math ----------

def _step_to_dict(s) -> dict:
    return {
        "id": s.id, "position": s.position,
        "latex": s.latex,
        "explanation": s.explanation,
        "validated": s.validated,
        "flagged_count": s.flagged_count,
        "created_at": s.created_at,
    }


def _step_problem_to_dict(p) -> dict:
    return {
        "id": p.id, "user_id": p.user_id,
        "submission_id": p.submission_id,
        "problem_latex": p.problem_latex,
        "problem_kind": p.problem_kind,
        "language": p.language, "status": p.status,
        "final_answer": p.final_answer,
        "error_reason": p.error_reason,
        "solver": p.solver,
        "created_at": p.created_at,
        "solved_at": p.solved_at,
        "steps": [_step_to_dict(s) for s in p.steps],
    }


@router.post("/api/step-math/problems", status_code=201)
def sm_create(
    problem_latex: str = Form(..., min_length=3, max_length=4000),
    submission_id: str | None = Form(None, max_length=64),
    language: str = Form("en", max_length=10),
    user=Depends(current_user),
):
    """Create a step-math problem. Caller can pass an existing
    math_submissions.id to link image-based problems."""
    from .. import step_math as sm
    user = require_user(user)
    try:
        p = sm.create_problem(
            user_id=user.id, problem_latex=problem_latex,
            submission_id=submission_id, language=language,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _step_problem_to_dict(p)


@router.post("/api/step-math/problems/{pid}/solve-sympy")
def sm_solve_sympy(pid: str, user=Depends(current_user)):
    """Try the deterministic SymPy solver. 400 if the problem
    isn't in the supported class — caller falls back to LLM."""
    from .. import step_math as sm
    user = require_user(user)
    p = sm.get_problem(pid)
    if not p:
        raise HTTPException(404, "problem not found")
    if p.user_id != user.id:
        raise HTTPException(403, "not your problem")
    try:
        p = sm.solve_with_sympy(pid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _step_problem_to_dict(p)


@router.post("/api/step-math/problems/{pid}/llm-steps")
def sm_accept_llm_steps(
    pid: str,
    steps_json: str = Form(..., max_length=80_000),
    final_answer: str = Form(..., min_length=1, max_length=2000),
    solver: str = Form("llm", max_length=20),
    user=Depends(current_user),
):
    """Caller (tutor wrapper) posts LLM-generated steps + the
    final answer. steps_json is a JSON array of
    {latex, explanation?}."""
    from .. import step_math as sm
    import json as _json
    user = require_user(user)
    try:
        steps = _json.loads(steps_json)
    except _json.JSONDecodeError:
        raise HTTPException(400, "steps_json must be JSON array")
    if not isinstance(steps, list):
        raise HTTPException(400, "steps_json must be array")
    try:
        p = sm.accept_llm_steps(
            problem_id=pid, user_id=user.id,
            steps=steps, final_answer=final_answer,
            solver=solver,
        )
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _step_problem_to_dict(p)


@router.get("/api/step-math/problems/{pid}")
def sm_get(pid: str, user=Depends(current_user)):
    from .. import step_math as sm
    user = require_user(user)
    p = sm.get_problem(pid)
    if not p:
        raise HTTPException(404, "problem not found")
    if p.user_id != user.id:
        raise HTTPException(403, "not your problem")
    return _step_problem_to_dict(p)


@router.get("/api/step-math/problems/me")
def sm_my_problems(
    status: str | None = None,
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import step_math as sm
    user = require_user(user)
    try:
        rows = sm.list_user_problems(
            user.id, status=status, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "problems": [_step_problem_to_dict(p) for p in rows],
    }


@router.post("/api/step-math/steps/{step_id}/flag")
def sm_flag_step(step_id: str, user=Depends(current_user)):
    """Student says 'didn't follow' on this step."""
    from .. import step_math as sm
    user = require_user(user)
    if not sm.flag_step(step_id=step_id, user_id=user.id):
        raise HTTPException(404, "step not found")
    return {"ok": True}


@router.post(
    "/api/step-math/steps/{step_id}/explanation",
    status_code=201,
)
def sm_add_explanation(
    step_id: str,
    explanation: str = Form(..., min_length=10, max_length=8000),
    citations_json: str | None = Form(None, max_length=20000),
    ai_call_id: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    """Tutor explanation for a flagged step. Caller (tutor
    wrapper) generates the explanation + posts here for caching."""
    from .. import step_math as sm
    import json as _json
    user = require_user(user)
    citations = None
    if citations_json:
        try:
            citations = _json.loads(citations_json)
        except _json.JSONDecodeError:
            raise HTTPException(
                400, "citations_json must be JSON",
            )
    try:
        eid = sm.add_step_explanation(
            step_id=step_id, user_id=user.id,
            explanation=explanation,
            citations=citations, ai_call_id=ai_call_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": eid}


@router.get("/api/step-math/steps/{step_id}/explanations")
def sm_list_explanations(
    step_id: str, user=Depends(current_user),
):
    from .. import step_math as sm
    user = require_user(user)
    return {
        "explanations": sm.list_step_explanations(step_id),
    }


@router.get("/api/admin/step-math/high-flagged")
def sm_admin_high_flagged(
    threshold: int = 3,
    limit: int = 50,
    user=Depends(current_user),
):
    """Admin queue — steps with high flagged_count for editorial
    rewrite."""
    from .. import step_math as sm
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin only")
    return {
        "steps": [
            _step_to_dict(s)
            for s in sm.high_flagged_steps(
                threshold=threshold, limit=limit,
            )
        ],
    }


@router.get("/api/step-math/me/stats")
def sm_my_stats(user=Depends(current_user)):
    from .. import step_math as sm
    user = require_user(user)
    return sm.user_stats(user.id)


# ---------- v3.17 navigation manifest + student home ----------

@router.get("/api/navigation/manifest")
def nav_manifest(role: str | None = None):
    """Public — the goal-led navigation. Optional role filter
    (student / teacher / parent / admin) drops features whose
    visible_to_role doesn't match."""
    from .. import navigation as nav
    return nav.get_manifest(role=role)


@router.get("/api/navigation/sections/{slug}")
def nav_section(slug: str):
    """Single-section detail — features + descriptions."""
    from .. import navigation as nav
    section = nav.get_section(slug)
    if not section:
        raise HTTPException(404, "section not found")
    return section


@router.get("/api/navigation/sections")
def nav_list_sections():
    """List section slugs (cheap discovery)."""
    from .. import navigation as nav
    return {"sections": nav.list_section_slugs()}


@router.get("/api/home/me/dashboard")
def home_my_dashboard(user=Depends(current_user)):
    """Goal-led home view. Composes Exam Pack + readiness +
    today's plan + next mock + community + trust + recent
    fallbacks + module catalog into a single payload. Drives
    the §26 mockup's home screen."""
    from .. import student_home as sh
    user = require_user(user)
    return sh.build_dashboard(user_id=user.id)


# ---------- Admin: curriculum topics DB CRUD ----------

_CURRICULUM_TOPICS_DDL = """
CREATE TABLE IF NOT EXISTS curriculum_topics (
    id          SERIAL PRIMARY KEY,
    board       TEXT NOT NULL,
    class       INTEGER NOT NULL,
    subject     TEXT NOT NULL,
    chapter_no  INTEGER NOT NULL,
    chapter_title TEXT NOT NULL,
    level       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    topics      JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (board, class, subject, chapter_no)
);
"""

_curriculum_table_created = False


def _ensure_curriculum_table() -> bool:
    """Create curriculum_topics table if it does not exist. Returns True
    if a DB connection is available, False in dev/no-DB mode."""
    global _curriculum_table_created
    if _curriculum_table_created:
        return True
    try:
        from ..web import get_db_url
        db_url = get_db_url()
        if not db_url:
            return False
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(_CURRICULUM_TOPICS_DDL)
        _curriculum_table_created = True
        return True
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("[curriculum_table] non-fatal: %s", exc)
        return False


@router.get("/api/admin/curriculum")
def admin_list_curriculum(
    board: str | None = None,
    cls: int | None = None,
    subject: str | None = None,
    user=Depends(current_user),
):
    """List curriculum topics from the DB (admin-only).
    Falls back to the static CURRICULUM list when no DB is available."""
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    if not _ensure_curriculum_table():
        # No DB — return the static seed list
        from ..curriculum import CURRICULUM
        rows = CURRICULUM
        if board:
            rows = [r for r in rows if r["board"] == board]
        if cls is not None:
            rows = [r for r in rows if r["class"] == cls]
        if subject:
            rows = [r for r in rows if r["subject"] == subject]
        return {"source": "static", "count": len(rows), "rows": rows}
    try:
        from ..web import get_db_url
        import psycopg
        filters, params = [], []
        if board:
            filters.append("board = %s"); params.append(board)
        if cls is not None:
            filters.append("class = %s"); params.append(cls)
        if subject:
            filters.append("subject = %s"); params.append(subject)
        where = ("WHERE " + " AND ".join(filters)) if filters else ""
        with psycopg.connect(get_db_url()) as conn:
            rows = conn.execute(
                f"SELECT id, board, class, subject, chapter_no, chapter_title, "
                f"level, summary, topics FROM curriculum_topics {where} "
                f"ORDER BY board, class, subject, chapter_no",
                params,
            ).fetchall()
        return {
            "source": "db",
            "count": len(rows),
            "rows": [
                {"id": r[0], "board": r[1], "class": r[2], "subject": r[3],
                 "chapter_no": r[4], "chapter_title": r[5],
                 "level": r[6], "summary": r[7], "topics": r[8]}
                for r in rows
            ],
        }
    except Exception as exc:
        raise HTTPException(500, f"db error: {exc}")


@router.post("/api/admin/curriculum", status_code=201)
def admin_add_curriculum_topic(
    board: str = Form(...),
    cls: int = Form(..., alias="class"),
    subject: str = Form(...),
    chapter_no: int = Form(...),
    chapter_title: str = Form(...),
    level: str = Form(...),
    summary: str = Form(""),
    topics: str = Form("[]", description="JSON array of topic tag strings"),
    user=Depends(current_user),
):
    """Add a curriculum topic to the DB. Admin-only."""
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    import json as _json
    from ..pedagogy import LEVEL_GUIDANCE
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of {sorted(LEVEL_GUIDANCE)}")
    try:
        topics_list = _json.loads(topics)
        if not isinstance(topics_list, list):
            raise ValueError
    except Exception:
        raise HTTPException(400, "topics must be a JSON array of strings")
    if not _ensure_curriculum_table():
        raise HTTPException(503, "database not available")
    try:
        from ..web import get_db_url
        import psycopg
        with psycopg.connect(get_db_url(), autocommit=True) as conn:
            row = conn.execute(
                "INSERT INTO curriculum_topics "
                "(board, class, subject, chapter_no, chapter_title, level, summary, topics) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (board, class, subject, chapter_no) DO UPDATE "
                "SET chapter_title=EXCLUDED.chapter_title, level=EXCLUDED.level, "
                "summary=EXCLUDED.summary, topics=EXCLUDED.topics, updated_at=NOW() "
                "RETURNING id",
                (board, cls, subject, chapter_no, chapter_title, level,
                 summary, _json.dumps(topics_list)),
            ).fetchone()
        return {"id": row[0], "ok": True}
    except Exception as exc:
        raise HTTPException(500, f"db error: {exc}")


@router.put("/api/admin/curriculum/{topic_id}")
def admin_update_curriculum_topic(
    topic_id: int,
    chapter_title: str | None = Form(None),
    level: str | None = Form(None),
    summary: str | None = Form(None),
    topics: str | None = Form(None, description="JSON array of topic tag strings"),
    user=Depends(current_user),
):
    """Update a curriculum topic in the DB. Admin-only. Only supplied
    fields are updated."""
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    import json as _json
    from ..pedagogy import LEVEL_GUIDANCE
    if level is not None and level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of {sorted(LEVEL_GUIDANCE)}")
    topics_list = None
    if topics is not None:
        try:
            topics_list = _json.loads(topics)
            if not isinstance(topics_list, list):
                raise ValueError
        except Exception:
            raise HTTPException(400, "topics must be a JSON array of strings")
    if not _ensure_curriculum_table():
        raise HTTPException(503, "database not available")
    sets, params = [], []
    if chapter_title is not None:
        sets.append("chapter_title = %s"); params.append(chapter_title)
    if level is not None:
        sets.append("level = %s"); params.append(level)
    if summary is not None:
        sets.append("summary = %s"); params.append(summary)
    if topics_list is not None:
        sets.append("topics = %s"); params.append(_json.dumps(topics_list))
    if not sets:
        raise HTTPException(400, "no fields to update")
    sets.append("updated_at = NOW()")
    params.append(topic_id)
    try:
        from ..web import get_db_url
        import psycopg
        with psycopg.connect(get_db_url(), autocommit=True) as conn:
            n = conn.execute(
                f"UPDATE curriculum_topics SET {', '.join(sets)} WHERE id = %s",
                params,
            ).rowcount
        if n == 0:
            raise HTTPException(404, "topic not found")
        return {"ok": True, "updated": n}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"db error: {exc}")


@router.delete("/api/admin/curriculum/{topic_id}")
def admin_delete_curriculum_topic(topic_id: int, user=Depends(current_user)):
    """Delete a curriculum topic from the DB. Admin-only."""
    user = require_user(user)
    if not getattr(user, "is_admin", False):
        raise HTTPException(403, "admin access required")
    if not _ensure_curriculum_table():
        raise HTTPException(503, "database not available")
    try:
        from ..web import get_db_url
        import psycopg
        with psycopg.connect(get_db_url(), autocommit=True) as conn:
            n = conn.execute(
                "DELETE FROM curriculum_topics WHERE id = %s", (topic_id,)
            ).rowcount
        if n == 0:
            raise HTTPException(404, "topic not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"db error: {exc}")
