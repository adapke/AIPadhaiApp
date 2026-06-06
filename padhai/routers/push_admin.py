"""Push-admin router — twenty-fourth web.py slice.

Three endpoints surfacing the push-notification log + stats:

  POST /api/push/{log_id}/opened   (public — client beacon on deep-link)
  GET  /api/push/log               (authed — recent sends, scoped)
  GET  /api/push/stats             (public — aggregate metrics)

`/opened` is intentionally unauthenticated — the opaque `log_id` is
the auth (the SPA / mobile app sends it when the user actually
tapped the push, so we get an accurate open-rate without a session
round-trip).

`/log` is scoped: a regular user sees only their own rows. A user
who is an admin in any org can pass `user_id=...` to query another
user (used by the support dashboard).

`/stats` is public — the response carries no PII, just aggregate
counts driving the public status page.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.post("/api/push/{log_id}/opened")
def mark_push_opened_route(log_id: str):
    """Client beacon: user tapped a push and the app deep-linked in.
    Drives the push open-rate metric on the admin dashboard.
    Unauthenticated (the log_id is the auth — opaque + per-send)."""
    from .. import web as _web
    ok = _web._push.mark_opened(log_id=log_id)
    return {"ok": ok}


@router.get("/api/push/log")
def list_push_log_route(
    user_id: str | None = None,
    limit: int = 100,
    user: AuthUser | None = Depends(current_user),
):
    """Diagnostic: recent push sends. Users can see only their own
    (user_id=me); admins (any role admin in any org) can pass an
    explicit user_id to query for another user."""
    from .. import web as _web
    user = _web._require_user(user)
    if user_id and user_id != user.id:
        # cross-user query → caller must be an admin in some org
        is_admin_anywhere = any(
            _web._orgs.user_role_in_org(
                org_id=o.id, user_id=user.id,
            ) == "admin"
            for o in _web._orgs.find_orgs_for_user(user.id)
        )
        if not is_admin_anywhere:
            raise HTTPException(
                403, "admin role required to query other users",
            )
        target = user_id
    else:
        target = user.id
    rows = _web._push.recent_log(user_id=target, limit=limit)
    return {
        "rows": [
            {
                "id": r.id, "category": r.category,
                "platform": r.platform,
                "title": r.title, "body": r.body,
                "sent_at": r.sent_at, "delivered_at": r.delivered_at,
                "opened_at": r.opened_at,
                "failed_reason": r.failed_reason,
                "notification_id": r.notification_id,
            }
            for r in rows
        ],
    }


@router.get("/api/push/stats")
def push_stats_route(hours: float = 24.0):
    """Aggregate metrics for the last N hours. Public (drives the
    /status page); no PII in the response."""
    from .. import web as _web
    if hours <= 0 or hours > 24 * 30:
        raise HTTPException(400, "hours must be in (0, 720]")
    return _web._push.stats_for_period(hours=hours)
