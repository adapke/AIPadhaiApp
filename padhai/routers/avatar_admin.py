"""Avatar admin router — twenty-first web.py slice.

Three endpoints surfacing the photoreal avatar provider router state:

  GET  /api/avatar-providers      (public — which providers configured)
  GET  /api/avatar-stats          (authed — per-provider success/fail)
  POST /api/avatar-stats/reset    (authed — clear in-memory counters)

`/api/avatar-providers` is intentionally public — the SPA surfaces
which premium-tier options are available without revealing whether
the keys themselves are valid.

`/api/avatar-stats` + `/reset` are authenticated to avoid leaking
provider health to anonymous visitors. The reset is "best-effort,
process-local" — there's no cross-worker state, so in a multi-
worker prod deploy the reset only clears the worker that handles
the request. In practice the dashboard polls all workers so this
is fine.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/avatar-providers")
def list_avatar_providers_route():
    """Which photoreal avatar providers are configured on this
    deploy. Public — the UI uses it to surface which premium-tier
    options are available without revealing whether keys are set
    (just provider names + circuit health)."""
    from .. import web as _web
    snap = _web._avatar_router.snapshot()
    return {
        "configured": snap["configured"],
        "fallback_chain": snap["chain"],
    }


@router.get("/api/avatar-stats")
def get_avatar_stats_route(
    user: AuthUser | None = Depends(current_user),
):
    """Per-provider success/failure counts + latency. Behind auth so
    we don't leak which keys are working to anonymous visitors.

    Useful for the admin dashboard to see "Synthesia is failing 30%
    of requests today — switch primary to Tavus."""
    from .. import web as _web
    user = _web._require_user(user)
    return _web._avatar_router.snapshot()


@router.post("/api/avatar-stats/reset")
def reset_avatar_stats_route(
    user: AuthUser | None = Depends(current_user),
):
    """Clear the in-memory counters. Useful after fixing a provider
    issue so the "consecutive_failures" counter doesn't keep the
    circuit open."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._avatar_router.reset_stats()
    return {"ok": True}
