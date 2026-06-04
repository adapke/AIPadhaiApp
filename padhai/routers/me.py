"""User-self endpoints — anything under `/api/me/*`, `/api/users/me/*`,
or `/api/coaching/practice/*` that needs `Depends(current_user)`.

Extracted from web.py in v2.0.3. Pattern:
  - `current_user` imported from web at module-load (safe because
    web.py defines it at line ~134, before router loading at ~478)
  - `require_user` imported from api_deps for the auth guard
  - Module-level deps (streaks, mastery, push, etc.) imported lazily
    inside endpoint bodies so unit tests can stub them
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException

from ..api_deps import require_user

# current_user is closed over `_user_repo` from web.py's lifespan.
# Import-from-web is safe because web.py defines it (line ~134)
# before triggering router loading (line ~478).
from ..web import current_user

router = APIRouter()


# ---------- I3: push tokens + prefs ----------

@router.post("/api/users/me/push-tokens", status_code=201)
def register_push_token(
    platform: str = Form(..., description="fcm | apns | web"),
    token: str = Form(..., min_length=8, max_length=4096),
    device_id: str | None = Form(None, max_length=200),
    app_version: str | None = Form(None, max_length=64),
    user=Depends(current_user),
):
    from .. import push
    user = require_user(user)
    try:
        tok = push.register_token(
            user_id=user.id, platform=platform, token=token,
            device_id=device_id, app_version=app_version,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "id": tok.id, "platform": tok.platform, "active": tok.active,
        "created_at": tok.created_at,
    }


@router.delete("/api/users/me/push-tokens")
def deactivate_push_token(
    token: str = Form(..., min_length=8),
    user=Depends(current_user),
):
    from .. import push
    user = require_user(user)
    if not push.deactivate_token(user_id=user.id, token=token):
        raise HTTPException(404, "token not found for this user")
    return {"ok": True}


@router.get("/api/users/me/push-prefs")
def get_push_prefs(user=Depends(current_user)):
    from .. import push
    user = require_user(user)
    return {"prefs": push.get_prefs(user.id)}


@router.post("/api/users/me/push-prefs")
def set_push_pref(
    category: str = Form(..., max_length=64),
    enabled: bool = Form(...),
    user=Depends(current_user),
):
    from .. import push
    user = require_user(user)
    try:
        push.set_pref(user_id=user.id, category=category, enabled=enabled)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"prefs": push.get_prefs(user.id)}


# ---------- I4: streaks ----------

@router.get("/api/me/streak")
def get_my_streak(user=Depends(current_user)):
    from .. import streaks
    user = require_user(user)
    s = streaks.get_streak(user.id)
    next_threshold = (
        streaks.LEVEL_THRESHOLDS[s.level]
        if s.level < len(streaks.LEVEL_THRESHOLDS) else None
    )
    return {
        "current_streak": s.current_streak,
        "longest_streak": s.longest_streak,
        "last_active_date": s.last_active_date,
        "xp_total": s.xp_total,
        "level": s.level,
        "next_level_xp": next_threshold,
        "opted_in": s.opted_in,
    }


@router.post("/api/me/streak/opt")
def opt_into_streaks(
    enabled: bool = Form(...),
    user=Depends(current_user),
):
    from .. import streaks
    user = require_user(user)
    streaks.set_opted_in(user_id=user.id, opted_in=enabled)
    return {"opted_in": enabled}


@router.get("/api/me/streak/calendar")
def my_streak_calendar(
    days: int = 90,
    user=Depends(current_user),
):
    from .. import streaks
    user = require_user(user)
    return {
        "days": days,
        "heatmap": streaks.calendar_heatmap(user_id=user.id, days=days),
    }


# ---------- J5: adaptive difficulty (mastery) ----------

@router.get("/api/me/mastery")
def get_my_mastery(
    limit: int = 50,
    user=Depends(current_user),
):
    from .. import mastery
    user = require_user(user)
    rows = mastery.list_for_user(user.id, limit=limit)
    return {
        "rows": [
            {"topic_key": m.topic_key, "mastery": round(m.mastery, 3),
             "attempts": m.attempts, "correct": m.correct,
             "last_seen": m.last_seen}
            for m in rows
        ],
    }


@router.post("/api/me/mastery/signal")
def record_mastery_signal(
    topic_key: str = Form(..., min_length=1, max_length=120),
    correct: bool = Form(...),
    user=Depends(current_user),
):
    from .. import mastery
    user = require_user(user)
    m = mastery.update(
        user_id=user.id, topic_key=topic_key, correct=correct,
    )
    return {
        "topic_key": m.topic_key,
        "mastery": round(m.mastery, 3),
        "attempts": m.attempts,
        "recommended_difficulty": mastery.recommend_difficulty(
            user_id=user.id, topic_key=topic_key,
        ),
    }


@router.get("/api/me/mastery/recommend")
def recommend_difficulty(
    topic_key: str,
    user=Depends(current_user),
):
    from .. import mastery
    user = require_user(user)
    return {
        "topic_key": topic_key,
        "recommended_difficulty": mastery.recommend_difficulty(
            user_id=user.id, topic_key=topic_key,
        ),
    }


@router.get("/api/me/mastery/weak")
def get_my_weak_topics(user=Depends(current_user)):
    from .. import mastery
    user = require_user(user)
    weak = mastery.weak_topics(user_id=user.id)
    return {
        "rows": [
            {"topic_key": m.topic_key, "mastery": round(m.mastery, 3),
             "attempts": m.attempts}
            for m in weak
        ],
    }


# ---------- K3: coaching practice (auth-gated) ----------

@router.post("/api/coaching/practice/attempts", status_code=201)
def record_practice_attempt(
    exam: str = Form(..., max_length=32),
    subject: str = Form(..., max_length=64),
    question_id: str | None = Form(None, max_length=64),
    answered: str | None = Form(None, max_length=2000),
    correct: bool = Form(False),
    time_seconds: int | None = Form(None, ge=0, le=86400),
    confidence: int | None = Form(None, ge=1, le=5),
    user=Depends(current_user),
):
    from .. import coaching
    user = require_user(user)
    try:
        aid = coaching.record_attempt(
            user_id=user.id, exam=exam, subject=subject,
            question_id=question_id, answered=answered, correct=correct,
            time_seconds=time_seconds, confidence=confidence,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": aid}


@router.get("/api/coaching/practice/stats")
def get_practice_stats(
    exam: str,
    user=Depends(current_user),
):
    from .. import coaching
    user = require_user(user)
    return {
        "by_subject": coaching.subject_stats(user_id=user.id, exam=exam),
        "weak_subjects": coaching.weak_subjects(user_id=user.id, exam=exam),
    }
