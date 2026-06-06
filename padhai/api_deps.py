"""Shared API dependencies + helpers — extracted from web.py in v2.0.3
so router modules can import them without depending on web.py's
late-loading internals.

Three helpers live here:
  require_user(user)        — 401 if user is None
  org_or_404(org_id)        — returns Org or 404
  require_org_role(...)     — RBAC check, raises 403 on mismatch

`current_user` (the FastAPI Depends-able) stays in web.py because it
closes over `_user_repo` which itself depends on `DATABASE_URL` env
detection at module-load time. Router files import it directly via
`from padhai.web import current_user` — that works because web.py
defines current_user at line ~134, well before it triggers router
loading at line ~478.
"""

from __future__ import annotations

from fastapi import HTTPException

from . import orgs as _orgs


def require_user(user):
    """Org endpoints all require auth — bail with 401 if the
    `current_user` dependency returned None (anonymous mode)."""
    if user is None:
        raise HTTPException(401, "sign in to manage an organisation")
    return user


def org_or_404(org_id: str):
    """Resolve org by id; 404 if missing. Returns the Org dataclass."""
    org = _orgs.get_org(org_id)
    if not org:
        raise HTTPException(404, "organisation not found")
    return org


def require_org_role(org_id: str, user_id: str, allowed: set[str]) -> None:
    """RBAC — raises 403 if the user isn't a member with one of the
    allowed roles in this org."""
    try:
        _orgs.require_role(org_id=org_id, user_id=user_id, allowed=allowed)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


# v3.1 — system-wide ownership-check helper. Centralizes the
# `caller owns this resource` check that's been ad-hoc across job /
# upload / video / chat / note endpoints. Resource resolvers are
# registered by the owning module on import; this keeps api_deps.py
# from importing every data module.
_OWNER_RESOLVERS: dict[str, callable] = {}


def register_owner_resolver(resource_type: str, resolver) -> None:
    """`resolver(resource_id) -> owner_user_id | None`. Called once
    per module at import time. Resource type is a short slug like
    'job' / 'upload' / 'chat'."""
    _OWNER_RESOLVERS[resource_type] = resolver


def require_owner(*, resource_type: str, resource_id: str, user) -> None:
    """Auth + ownership combo. Raises 401 if user is None, 404 if
    resource doesn't exist, 403 if it exists but the caller isn't
    the owner. Owners-only — for shared resources use
    require_org_role instead."""
    if user is None:
        raise HTTPException(401, "sign in to access this resource")
    resolver = _OWNER_RESOLVERS.get(resource_type)
    if resolver is None:
        raise HTTPException(
            500, f"no owner resolver for {resource_type!r}",
        )
    owner_id = resolver(resource_id)
    if owner_id is None:
        raise HTTPException(404, f"{resource_type} not found")
    if owner_id != getattr(user, "id", None):
        raise HTTPException(403, "not your resource")


def require_admin_role(user) -> None:
    """Caller must be an org admin OR a superuser. Used by /api/admin/*
    routes that aren't mounted under the standalone admin Flask app.

    Resolution order:
      1. `PADHAI_SUPERUSER_EMAILS` env var (comma-separated) — matched
         case-insensitively against the caller's email.
      2. Membership with `admin` role in at least one org.
      3. Dev fallback: when `DATABASE_URL` is unset (local SQLite mode),
         any authenticated user is treated as admin so dashboards work.

    Production deployments MUST set either `DATABASE_URL` or
    `PADHAI_SUPERUSER_EMAILS` to prevent unintended elevation.
    """
    import os
    if user is None:
        raise HTTPException(401, "sign in to access admin endpoints")
    superusers = os.environ.get("PADHAI_SUPERUSER_EMAILS", "")
    email = (getattr(user, "email", "") or "").lower()
    if email and email in {
        e.strip().lower() for e in superusers.split(",") if e.strip()
    }:
        return
    try:
        user_orgs = _orgs.find_orgs_for_user(user.id)
        for o in user_orgs:
            if _orgs.user_role_in_org(org_id=o.id, user_id=user.id) == "admin":
                return
    except Exception:
        pass
    if not os.environ.get("DATABASE_URL"):
        return
    raise HTTPException(403, "admin only")


def make_admin_dep():
    """Build a FastAPI-Depends-able admin gate. Returns a function
    `f(user = Depends(current_user)) -> AuthUser` that raises 401
    if unauthenticated, 403 if not admin.

    Late-built (not module-level) so importers don't pull web.py at
    api_deps load time — that order matters because web.py imports
    api_deps during its own startup.
    """
    from fastapi import Depends

    from .web import current_user as _current_user

    def admin_user_dep(user=Depends(_current_user)):
        require_admin_role(user)
        return user

    return admin_user_dep
