"""SCIM 2.0 provisioning router — thirteenth web.py slice.

Four endpoints implementing the subset of SCIM 2.0 that IdPs (Okta,
Azure AD, Google Workspace) actually call:

  GET   /scim/v2/ServiceProviderConfig   (IdP discovery)
  GET   /scim/v2/Users                   (list — paginated, with filter)
  POST  /scim/v2/Users                   (provision a new user)
  PATCH /scim/v2/Users/{member_id}       (deactivate — `active=false`)

Each org issues its own bearer token via the org-admin UI; that token
is the auth, not a platform-level JWT. `_scim_authenticate` resolves
the token to `org_id` and 401s on invalid/revoked.

The list/create/patch responses use the SCIM 2.0 envelope (schemas,
ListResponse, error_response) via `padhai/scim.py`. The provisioning
path goes through `_orgs.add_member` / `_orgs.deactivate_member` so
SCIM-managed users land in the same place as console-invited users.

Late-imports `web` for the shared globals — same pattern as
branding.py, orgs_exams.py, orgs_fees.py, parents.py.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _scim_authenticate(request: Request) -> str:
    """Returns the org_id when the bearer token is valid; else raises
    401 with the SCIM 2.0 error shape."""
    from .. import web as _web
    auth_h = request.headers.get("authorization", "")
    if not auth_h.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    raw = auth_h.split(" ", 1)[1].strip()
    org_id = _web._scim.authenticate(raw)
    if not org_id:
        raise HTTPException(401, "invalid or revoked token")
    return org_id


@router.get("/scim/v2/ServiceProviderConfig")
def scim_spc_route():
    """SCIM 2.0 ServiceProviderConfig — IdPs read this at integration
    time to discover what we support."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": "https://aipathshala.in/docs/scim",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [
            {"name": "OAuth Bearer Token", "type": "oauthbearertoken",
             "description": "Per-org bearer issued by the org admin."},
        ],
    }


@router.get("/scim/v2/Users")
def scim_list_users_route(
    request: Request,
    startIndex: int = 1,
    count: int = 100,
    filter: str | None = None,
):
    """SCIM ListResponse of org members. Filter param accepts the
    SCIM filter syntax for `userName eq "..."` (the only operator
    IdPs actually use in practice). Other filters ignored."""
    from .. import web as _web
    org_id = _scim_authenticate(request)
    # Resolve users + members for the org. Real impl would page off
    # users + org_members JOIN; for v1.3 we list org_members (which
    # is what IdPs care about — the SCIM-managed cohort).
    members = _web._orgs.list_members(org_id)
    resources: list[dict] = []
    for m in members:
        # Synth a minimal user record from the org_member row when
        # we don't have a separate users-table backing.
        u_email = m.invited_email or ""
        if filter and "userName eq" in filter:
            wanted = filter.split('"')[1] if '"' in filter else ""
            if wanted and wanted.lower() != u_email.lower():
                continue
        # Synthesised "user" object for SCIM rendering.
        class _U:
            pass
        u = _U()
        u.id = m.id
        u.email = u_email
        u.scim_external_id = None
        u.deactivated_at = None
        u.created_at = m.joined_at or time.time()
        resources.append(_web._scim.user_to_scim(u, member=m))
    paged = resources[
        max(0, startIndex - 1):max(0, startIndex - 1) + count
    ]
    return _web._scim.list_response(
        resources=paged, total=len(resources),
        start=startIndex, per_page=count,
    )


@router.post("/scim/v2/Users", status_code=201)
def scim_create_user_route(request: Request, payload: dict):
    """SCIM provisioning create. IdP POSTs a User resource; we
    add them to the org. Returns the persisted resource."""
    from .. import web as _web
    org_id = _scim_authenticate(request)
    try:
        info = _web._scim.parse_scim_user(payload)
    except ValueError as e:
        return JSONResponse(
            _web._scim.error_response(
                status=400, detail=str(e), scim_type="invalidValue",
            ),
            status_code=400,
        )
    role = info["roles"][0] if info["roles"] else "student"
    m = _web._orgs.add_member(
        org_id=org_id, role=role,
        invited_email=info["email"],
        display_name=info["display_name"],
    )
    _web._audit.record(
        action="scim.user.create",
        org_id=org_id,
        target_type="org_member", target_id=m.id,
        after={"email": info["email"], "role": role,
               "external_id": info["external_id"]},
        **_web._audit.actor_from_request(request),
    )
    # Synth user for response
    class _U:
        pass
    u = _U()
    u.id = m.id
    u.email = info["email"]
    u.scim_external_id = info["external_id"]
    u.deactivated_at = None
    u.created_at = m.joined_at or time.time()
    return JSONResponse(
        _web._scim.user_to_scim(u, member=m), status_code=201,
    )


@router.patch("/scim/v2/Users/{member_id}")
def scim_patch_user_route(member_id: str, request: Request, payload: dict):
    """SCIM PATCH — usually used for deactivation
    (Operations: [{op:'replace', path:'active', value:false}])."""
    from .. import web as _web
    org_id = _scim_authenticate(request)
    ops = (payload or {}).get("Operations") or []
    for op in ops:
        if op.get("op", "").lower() == "replace" and op.get("path") == "active":  # noqa: SIM102
            if not op.get("value", True):
                # Soft-delete via the dedicated helper (v2.0.1).
                _web._orgs.deactivate_member(
                    org_id=org_id, member_id=member_id,
                )
                _web._audit.record(
                    action="scim.user.deactivate",
                    org_id=org_id,
                    target_type="org_member", target_id=member_id,
                    **_web._audit.actor_from_request(request),
                )
                return {
                    "schemas": [
                        "urn:ietf:params:scim:schemas:core:2.0:User",
                    ],
                    "id": member_id, "active": False,
                }
    return JSONResponse(
        _web._scim.error_response(status=400, detail="no supported ops"),
        status_code=400,
    )
