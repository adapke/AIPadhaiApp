"""Misc status router — twenty-second web.py slice.

Two small public/auth status endpoints that don't fit cleanly into
any larger subsystem:

  GET /api/exam-mode/active   (authed — is this user in an active exam?)
  GET /api/fees/config        (public — is Razorpay configured?)

`/api/exam-mode/active` is polled by the doubt-chat + voice-tutor
pages to decide whether to lock those surfaces (S4 anti-cheat).
`/api/fees/config` is public so the SPA can choose between the real
Razorpay Checkout SDK and the "Pay via mock order" sandbox
affordance — the response leaks nothing sensitive (just the public
`key_id` which is meant to be embedded in client code).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/exam-mode/active")
def get_active_exam_mode_route(
    user: AuthUser | None = Depends(current_user),
):
    """Returns the exam_id of any in-progress attempt this user has,
    else None. Client polls this on the doubt-chat / voice-tutor
    pages to know whether to lock those features."""
    from .. import web as _web
    if user is None:
        return {"active_exam_id": None}
    return {"active_exam_id": _web._orgs.has_active_exam(user.id)}


@router.get("/api/fees/config")
def get_fees_config_route():
    """Public — tells the client whether Razorpay is wired (so the
    UI can decide whether to show the real Checkout SDK or a
    "Pay via mock order" affordance for sandbox testing)."""
    from .. import web as _web
    return {
        "razorpay_configured": _web._rzp.is_configured(),
        "razorpay_key_id": (
            os.environ.get("RAZORPAY_KEY_ID")
            if _web._rzp.is_configured() else None
        ),
    }
