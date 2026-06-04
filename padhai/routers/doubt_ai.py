"""Doubt-clearing AI router — exposes Claude Vision auto-answer.

  POST /api/doubts/{did}/ai-answer    — instant AI answer (Doubtnut-style)
  POST /api/doubts/submit-instant     — submit + auto-answer in one shot
  POST /api/admin/doubts/cron/escalate — cron worker entry: run AI on
                                          all stale pending doubts

The existing /api/doubts/* router in v3.py handles submit + claim +
human answer flow. This router adds the AI Vision pathway that was
described in doubt_clearing.py but not previously wired.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


@router.post("/api/doubts/{did}/ai-answer")
def doubt_ai_answer(
    did: str,
    force: bool = Form(False),
    user=Depends(current_user),
):
    """Trigger Claude Vision auto-answer for a doubt the student
    has already submitted. The owner of the doubt (or an org
    teacher/admin) can call this."""
    user = require_user(user)
    from .. import doubt_clearing as dc
    d = dc.get(did)
    if not d:
        raise HTTPException(404, "doubt not found")
    if d.user_id != user.id:
        # Educators can also trigger AI on org doubts
        try:
            from .. import orgs as _orgs
            user_orgs = _orgs.find_orgs_for_user(user.id)
            is_educator = any(
                _orgs.user_role_in_org(org_id=o.id, user_id=user.id)
                in ("teacher", "admin")
                for o in user_orgs
            )
            if not is_educator:
                raise HTTPException(403, "not your doubt")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(403, "not your doubt")
    try:
        d = dc.answer_via_ai_vision(doubt_id=did, force=force)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "doubt_id": d.id,
        "status": d.status,
        "response_text": d.response_text,
        "response_method": getattr(d, "response_method", None),
        "answered_at": getattr(d, "response_at", None),
    }


@router.post("/api/doubts/submit-instant", status_code=201)
def doubt_submit_instant(
    image_url: str | None = Form(None),
    question_text: str | None = Form(None),
    subject: str | None = Form(None),
    audio_url: str | None = Form(None),
    user=Depends(current_user),
):
    """Doubtnut-style: submit a doubt and get the AI answer back in
    one round-trip. The PWA's camera button hits this endpoint."""
    user = require_user(user)
    from .. import doubt_clearing as dc
    if not (image_url or audio_url or question_text):
        raise HTTPException(
            400,
            "at least one of image_url, audio_url, question_text required",
        )
    try:
        d = dc.submit(
            user_id=user.id,
            image_url=image_url,
            question_text=question_text,
            subject=subject,
            audio_url=audio_url,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Immediately call Claude Vision
    try:
        d = dc.answer_via_ai_vision(doubt_id=d.id)
    except Exception as e:
        # Don't fail the submission — student still has a row + a
        # human can pick it up. Surface the error to the caller.
        return {
            "doubt_id": d.id,
            "status": d.status,
            "response_text": None,
            "ai_error": str(e)[:200],
        }
    return {
        "doubt_id": d.id,
        "status": d.status,
        "response_text": d.response_text,
        "answered_at": getattr(d, "response_at", None),
    }


@router.post("/api/admin/doubts/cron/escalate")
def doubt_cron_escalate(
    minutes: float | None = Form(None),
    user=Depends(current_user),
):
    """Worker endpoint — run AI on every stale pending doubt.
    Idempotent; safe to invoke from a cron job every 5 minutes."""
    user = require_user(user)
    # Loose admin check — see commerce._require_admin_role pattern
    import os
    superusers = {
        e.strip().lower()
        for e in os.environ.get("PADHAI_SUPERUSER_EMAILS", "").split(",")
        if e.strip()
    }
    is_super = user.email and user.email.lower() in superusers
    if not is_super and os.environ.get("DATABASE_URL"):
        try:
            from .. import orgs as _orgs
            user_orgs = _orgs.find_orgs_for_user(user.id)
            is_admin = any(
                _orgs.user_role_in_org(org_id=o.id, user_id=user.id) == "admin"
                for o in user_orgs
            )
            if not is_admin:
                raise HTTPException(403, "admin role required")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(403, "admin role required")

    from .. import doubt_clearing as dc
    stale = dc.stale_for_ai_escalation(minutes=minutes)
    answered = 0
    errors = 0
    for doubt in stale:
        try:
            dc.answer_via_ai_vision(doubt_id=doubt.id)
            answered += 1
        except Exception:
            errors += 1
    return {
        "stale_found": len(stale),
        "answered": answered,
        "errors": errors,
    }
