"""Notifications router — fourteenth web.py slice.

Five endpoints covering the per-user notification feed + org-side
broadcast composer:

  GET  /api/notifications/me                  (current user's feed)
  POST /api/notifications/{nid}/read          (mark one notification read)
  POST /api/notifications/read-all            (mark all read across orgs)
  POST /api/orgs/{org_id}/notifications       (admin/teacher composes)
  GET  /api/orgs/{org_id}/notifications       (admin/teacher reads org log)

The user-facing reads call `_notifs.feed_for_user` which scopes the
result by the caller's org memberships + role + class_id (resolved
via `_web._resolve_user_org_context`). The org-side compose path is
admin/teacher gated, parses the `send_at_iso` (ISO 8601) schedule,
and fans out push to everyone matching the audience string via
`_resolve_audience` (which we lift with the router since this is its
only call site).

Push fan-out is intentionally best-effort: if FCM/APNs keys aren't
configured the create still returns 201, the row is persisted, and
the push_summary shows what would have been delivered. The wrapping
try/except never blocks notification creation on push failure.

Late-imports `web` for the shared globals — same pattern as scim.py,
branding.py, orgs_exams.py, parents.py.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


def _resolve_audience(org_id: str, audience: str) -> list[str]:
    """Map a notification audience string to concrete user_ids for
    push fan-out. Audience formats:
      'all'           → every org member who has a user_id
      'class:<cid>'   → members whose class_id matches
      'role:<role>'   → members with that role
      'user:<uid>'    → exactly that one user
    """
    from .. import web as _web
    if audience.startswith("user:"):
        return [audience.split(":", 1)[1]] if audience[5:] else []
    role_filter: str | None = None
    class_filter: str | None = None
    if audience == "all":
        pass
    elif audience.startswith("class:"):
        class_filter = audience.split(":", 1)[1]
    elif audience.startswith("role:"):
        role_filter = audience.split(":", 1)[1]
    else:
        return []
    members = _web._orgs.list_members(org_id, role=role_filter)
    return [
        m.user_id for m in members
        if m.user_id
        and (class_filter is None or m.class_id == class_filter)
    ]


@router.get("/api/notifications/me")
def my_notifications_route(
    unread_only: bool = False,
    limit: int = 50,
    user: AuthUser | None = Depends(current_user),
):
    """The current user's notification feed across all their orgs."""
    from .. import web as _web
    user = _web._require_user(user)
    org_ids, role, class_id = _web._resolve_user_org_context(user)
    feed = _web._notifs.feed_for_user(
        user_id=user.id, user_role=role,
        user_class_id=class_id, org_ids=org_ids,
        unread_only=unread_only, limit=limit,
    )
    unread = _web._notifs.unread_count(
        user_id=user.id, user_role=role,
        user_class_id=class_id, org_ids=org_ids,
    )
    return {"notifications": feed, "unread_count": unread}


@router.post("/api/notifications/{nid}/read")
def mark_notification_read_route(
    nid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Mark one notification as read for this user."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._notifs.mark_read(notification_id=nid, user_id=user.id)
    return {"ok": True}


@router.post("/api/notifications/read-all")
def mark_all_read_route(user: AuthUser | None = Depends(current_user)):
    """Mark every notification across all the user's orgs as read."""
    from .. import web as _web
    user = _web._require_user(user)
    org_ids, _, _ = _web._resolve_user_org_context(user)
    n = _web._notifs.mark_all_read(user_id=user.id, org_ids=org_ids)
    return {"marked": n}


@router.post("/api/orgs/{org_id}/notifications", status_code=201)
def create_org_notification_route(
    org_id: str,
    audience: str = Form(
        ..., description="all | class:<id> | role:teacher | user:<id>",
    ),
    kind: str = Form("announcement"),
    title: str = Form(..., min_length=2, max_length=120),
    body: str | None = Form(None),
    link_url: str | None = Form(None),
    send_at_iso: str | None = Form(
        None, description="ISO 8601; default = send now",
    ),
    channels: str = Form("in_app"),
    user: AuthUser | None = Depends(current_user),
):
    """Compose + queue a notification. Admin or teacher only;
    teachers are scoped to audience that mentions their own class (the
    full enforcement lands in v0.12 with proper class-ownership
    tracking; for v0.11 we allow any class/role audience from
    teachers)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})

    send_at: float | None = None
    if send_at_iso:
        try:
            send_at = datetime.fromisoformat(send_at_iso).timestamp()
        except ValueError:
            raise HTTPException(
                400,
                f"send_at_iso must be ISO 8601, got {send_at_iso!r}",
            ) from None
    try:
        n = _web._notifs.create(
            org_id=org_id, audience=audience, kind=kind,
            title=title, body=body, link_url=link_url,
            sent_by=user.id, send_at=send_at, channels=channels,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # I3 — fan out push for everyone whose tokens we hold + who is
    # opted in to this notification's category. Best-effort: if push
    # isn't configured (FCM/APNs keys missing) the per-send log row
    # still gets written with failed_reason='no_provider' so admin
    # telemetry shows what would have been delivered.
    push_summary = {"delivered": 0, "failed": 0, "recipients": 0}
    try:
        recipients = _resolve_audience(org_id, n.audience)
        push_summary["recipients"] = len(recipients)
        for r in _web._push.fan_out_for_notification(n, recipients):
            push_summary["delivered"] += r.delivered
            push_summary["failed"] += r.failed
    except Exception as e:
        # Never let push failures block notification creation.
        _web._log.warning(
            "[push] fan_out failed for notification %s: %s", n.id, e,
        )
    return {
        "id": n.id, "title": n.title, "audience": n.audience,
        "kind": n.kind, "send_at": n.send_at, "push": push_summary,
    }


@router.get("/api/orgs/{org_id}/notifications")
def list_org_notifications_route(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """Admin view — all notifications in this org (sent + scheduled)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    items = _web._notifs.list_for_admin(org_id=org_id, limit=limit)
    return {
        "notifications": [
            {
                "id": n.id, "audience": n.audience, "kind": n.kind,
                "title": n.title, "body": n.body, "link_url": n.link_url,
                "sent_by": n.sent_by, "send_at": n.send_at,
                "channels": n.channels, "created_at": n.created_at,
            }
            for n in items
        ],
    }
