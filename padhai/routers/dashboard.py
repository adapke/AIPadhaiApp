"""Student + Parent + Teacher dashboard router.

Surfaces the composer functions already in padhai/dashboards.py as
proper HTTP routes — they were previously unreachable.

  GET /api/me/dashboard          — student's own learning dashboard
  GET /api/parents/dashboard     — parent's view of their children
  GET /api/teacher/dashboard     — teacher's view of their students
                                    (org-bound; uses dashboards.teacher_dashboard)

The student dashboard composes:
  • Onboarding completion + next-step nudge
  • Streak (current + longest)
  • Mastery summary (weak / strong topics)
  • Adaptive pack progress (readiness, days-to-exam)
  • Recent practice tests
  • Recent mock interviews
  • Recent essay scores
  • Due flashcards count
  • Days-to-exam countdown for the target_exam if set
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()
_log = logging.getLogger("padhai.dashboard")


# ============================================================================
# Student dashboard
# ============================================================================

@router.get("/api/me/dashboard")
def my_dashboard(user=Depends(current_user)):
    """Composite dashboard for the authenticated student. Every
    sub-section is best-effort — missing data degrades silently."""
    user = require_user(user)

    return {
        "user_id": user.id,
        "computed_at": time.time(),
        "profile": _profile_block(user),
        "onboarding": _onboarding_block(user.id),
        "streak": _streak_block(user.id),
        "mastery": _mastery_block(user.id),
        "adaptive_pack": _adaptive_block(user.id),
        "practice_tests": _practice_block(user.id),
        "mock_interviews": _mock_block(user.id),
        "essays": _essay_block(user.id),
        "flashcards": _flashcards_block(user.id),
        "live_classes": _live_block(user.id),
        "tutor_sessions": _tutor_block(user.id),
    }


@router.get("/api/me/dashboard/weak-topics")
def my_weak_topics(
    limit: int = Query(10, ge=1, le=50),
    user=Depends(current_user),
):
    """Just the weak-topic list. Cheap to call on every screen so the
    UI can show 'next thing to study'."""
    user = require_user(user)
    try:
        from .. import mastery
        rows = mastery.weak_topics(user_id=user.id)
        return {
            "weak_topics": [
                {
                    "topic_key": r.topic_key,
                    "mastery": r.mastery,
                    "attempts": r.attempts,
                }
                for r in rows[:limit]
            ],
            "count": min(len(rows), limit),
        }
    except Exception as e:
        _log.warning("[dashboard] weak topics failed: %s", e)
        return {"weak_topics": [], "count": 0}


# ----------------------------------------------------------------------------
# Block helpers — each returns a dict or [], never raises
# ----------------------------------------------------------------------------

def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:
        _log.debug("[dashboard] block failed: %s", e)
        return default


def _profile_block(user) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "subscription_tier": getattr(user, "subscription_tier", "M1"),
        "subscription_level": getattr(user, "subscription_level", "L1"),
    }


def _onboarding_block(user_id: str) -> dict:
    def _go():
        from .onboarding import _ensure_onboarding_cols, _load_state, _next_step_for
        _ensure_onboarding_cols()
        state = _load_state(user_id)
        return {
            "completed": state.get("onboarding_completed_at") is not None,
            "completed_at": state.get("onboarding_completed_at"),
            "class_grade": state.get("class_grade"),
            "board": state.get("board"),
            "target_exam": state.get("target_exam"),
            "preferred_language": state.get("preferred_language"),
            "goal_minutes_daily": state.get("goal_minutes_daily"),
            "next_step": _next_step_for(state),
        }
    return _safe(_go, default={"completed": False}) or {"completed": False}


def _streak_block(user_id: str) -> dict:
    def _go():
        from .. import streaks
        s = streaks.get_streak(user_id=user_id)
        return {
            "current_days": getattr(s, "current_days", 0),
            "longest_days": getattr(s, "longest_days", 0),
            "last_visit": getattr(s, "last_visit_date", None),
        }
    return _safe(_go, default={"current_days": 0, "longest_days": 0}) or {
        "current_days": 0, "longest_days": 0,
    }


def _mastery_block(user_id: str) -> dict:
    def _go():
        from .. import mastery
        rows = mastery.list_for_user(user_id, limit=500)
        weak = sorted(
            (r for r in rows if r.attempts >= 2 and r.mastery < 0.5),
            key=lambda r: r.mastery,
        )[:5]
        strong = sorted(
            (r for r in rows if r.attempts >= 3 and r.mastery >= 0.8),
            key=lambda r: r.mastery,
            reverse=True,
        )[:5]
        return {
            "topic_count": len(rows),
            "weak": [
                {"topic_key": r.topic_key, "mastery": round(r.mastery, 3),
                 "attempts": r.attempts}
                for r in weak
            ],
            "strong": [
                {"topic_key": r.topic_key, "mastery": round(r.mastery, 3),
                 "attempts": r.attempts}
                for r in strong
            ],
        }
    return _safe(_go, default={"topic_count": 0, "weak": [], "strong": []}) or {
        "topic_count": 0, "weak": [], "strong": [],
    }


def _adaptive_block(user_id: str) -> dict:
    def _go():
        from .. import adaptive_packs as ap
        packs = ap.list_user_packs(user_id)
        return {
            "packs": [
                {
                    "id": p.id,
                    "base_pack_code": p.base_pack_code,
                    "title": p.title,
                    "last_adapted_at": p.last_adapted_at,
                    "adaptation_count": p.adaptation_count,
                }
                for p in packs[:5]
            ],
            "count": len(packs),
        }
    return _safe(_go, default={"packs": [], "count": 0}) or {
        "packs": [], "count": 0,
    }


def _practice_block(user_id: str) -> dict:
    def _go():
        from .. import practice_test as pt
        tests = pt.list_for_user(user_id, limit=5)
        return {
            "recent": [
                {
                    "id": t.id,
                    "exam": t.exam, "subject": t.subject,
                    "status": t.status,
                    "score": t.score,
                    "created_at": t.created_at,
                }
                for t in tests
            ],
            "count": len(tests),
        }
    return _safe(_go, default={"recent": [], "count": 0}) or {
        "recent": [], "count": 0,
    }


def _mock_block(user_id: str) -> dict:
    def _go():
        from .. import mock_interview as mi
        items = mi.list_for_user(user_id, limit=5)
        return {
            "recent": [
                {
                    "id": i.id,
                    "track": i.track,
                    "status": i.status,
                    "overall_score": i.overall_score,
                    "started_at": i.started_at,
                    "duration_seconds": i.duration_seconds,
                }
                for i in items
            ],
            "count": len(items),
        }
    return _safe(_go, default={"recent": [], "count": 0}) or {
        "recent": [], "count": 0,
    }


def _essay_block(user_id: str) -> dict:
    def _go():
        from .. import essay_grader as eg
        subs = eg.list_for_user(user_id, limit=5)
        return {
            "recent": [
                {
                    "id": s.id,
                    "rubric_id": s.rubric_id,
                    "ai_score": s.ai_score,
                    "submitted_at": s.submitted_at,
                    "graded_at": s.graded_at,
                }
                for s in subs
            ],
            "count": len(subs),
        }
    return _safe(_go, default={"recent": [], "count": 0}) or {
        "recent": [], "count": 0,
    }


def _flashcards_block(user_id: str) -> dict:
    def _go():
        from .. import spaced_repetition as srs
        decks = srs.list_my_decks(user_id, limit=10)
        # Due queue is the more actionable metric
        due = srs.due_queue(user_id=user_id, limit=100)
        return {
            "deck_count": len(decks),
            "due_count": len(due),
            "decks": [
                {"id": d.id, "title": d.title, "card_count": d.card_count}
                for d in decks[:5]
            ],
        }
    return _safe(_go, default={"deck_count": 0, "due_count": 0, "decks": []}) or {
        "deck_count": 0, "due_count": 0, "decks": [],
    }


def _live_block(user_id: str) -> dict:
    def _go():
        from .. import live_classes as lv
        upcoming = lv.list_upcoming(window_hours=168.0)
        return {
            "upcoming": [
                {
                    "id": lc.id, "title": lc.title,
                    "scheduled_at": lc.scheduled_at,
                    "duration_min": lc.duration_min,
                    "subject": lc.subject,
                }
                for lc in upcoming[:5]
            ],
            "count": len(upcoming),
        }
    return _safe(_go, default={"upcoming": [], "count": 0}) or {
        "upcoming": [], "count": 0,
    }


def _tutor_block(user_id: str) -> dict:
    def _go():
        from .. import tutor
        # We rely on a list_for_user; fall back to empty if not present
        if not hasattr(tutor, "list_for_user"):
            return {"sessions": [], "count": 0}
        items = tutor.list_for_user(user_id, limit=5)
        return {
            "sessions": [
                {
                    "id": s.id,
                    "started_at": s.started_at,
                    "ended_at": s.ended_at,
                    "resolved": getattr(s, "resolved", None),
                }
                for s in items
            ],
            "count": len(items),
        }
    return _safe(_go, default={"sessions": [], "count": 0}) or {
        "sessions": [], "count": 0,
    }


# ============================================================================
# Parent dashboard
# ============================================================================

@router.get("/api/parents/dashboard")
def parent_dashboard(user=Depends(current_user)):
    """Parent's view of all their linked children. Composer lives in
    padhai/dashboards.py; this route just exposes it."""
    user = require_user(user)
    try:
        from .. import dashboards as dl
        result = dl.parent_dashboard(user.id)
        return result
    except Exception as e:
        _log.warning("[parent_dashboard] failed: %s", e)
        raise HTTPException(500, "dashboard composition failed")


# ============================================================================
# Teacher dashboard
# ============================================================================

@router.get("/api/teacher/dashboard")
def teacher_dashboard(
    org_id: str = Query(..., description="Org the teacher is in"),
    class_id: str | None = Query(None, description="Optionally narrow to one class"),
    user=Depends(current_user),
):
    """Teacher / org-admin view across their students. Requires
    teacher or admin role in the given org."""
    user = require_user(user)
    from ..api_deps import require_org_role
    require_org_role(
        org_id=org_id, user_id=user.id,
        allowed={"teacher", "admin"},
    )
    try:
        from .. import dashboards as dl
        return dl.teacher_dashboard(
            teacher_user_id=user.id, org_id=org_id, class_id=class_id,
        )
    except TypeError:
        # Older signature — some forks accept different kwargs
        try:
            from .. import dashboards as dl
            return dl.teacher_dashboard(user.id, org_id, class_id)
        except Exception as e:
            _log.warning("[teacher_dashboard] failed: %s", e)
            raise HTTPException(500, "dashboard composition failed")
    except Exception as e:
        _log.warning("[teacher_dashboard] failed: %s", e)
        raise HTTPException(500, "dashboard composition failed")
