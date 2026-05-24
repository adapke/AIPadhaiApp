"""Org-admin endpoints — anything under `/api/orgs/{org_id}/*` that
needs both `current_user` AND an org-role gate.

Extracted from web.py in v2.0.3. Covers:
  E9 branding read/update
  H3 audit log query + CSV export
  H1 SAML config get/upsert
  H2 SCIM token issue/list/revoke
  H4 data residency get/set
  H5 custom domain get/set/remove
  K2 country get/set
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..api_deps import org_or_404, require_org_role, require_user
from ..web import current_user


router = APIRouter()


def _branding_to_dict(b) -> dict:
    return {
        "org_id": b.org_id, "brand_name": b.brand_name,
        "brand_color": b.brand_color, "brand_accent": b.brand_accent,
        "brand_logo_url": b.brand_logo_url,
        "brand_subdomain": b.brand_subdomain,
    }


# ---------- E9: branding ----------

@router.get("/api/orgs/{org_id}/branding")
def get_org_branding(
    org_id: str,
    user=Depends(current_user),
):
    from .. import branding
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    b = branding.get_branding(org_id)
    if b is None:
        raise HTTPException(404, "org not found")
    return _branding_to_dict(b)


@router.post("/api/orgs/{org_id}/branding")
def update_org_branding(
    org_id: str,
    request: Request,
    brand_name: str | None = Form(None, max_length=120),
    brand_color: str | None = Form(None, description="#RRGGBB hex"),
    brand_accent: str | None = Form(None, description="#RRGGBB hex"),
    brand_subdomain: str | None = Form(None,
        description="url-safe slug; 1-32 chars [a-z0-9-]"),
    user=Depends(current_user),
):
    from .. import audit, branding
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    before = branding.get_branding(org_id)
    try:
        b = branding.update_branding(
            org_id=org_id, brand_name=brand_name,
            brand_color=brand_color, brand_accent=brand_accent,
            brand_subdomain=brand_subdomain,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(
        action="org.branding.update",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        before=_branding_to_dict(before) if before else None,
        after=_branding_to_dict(b),
        **audit.actor_from_request(request),
    )
    return _branding_to_dict(b)


# ---------- H3: audit log query + export ----------

def _audit_row_to_dict(r) -> dict:
    import json as _json
    def _safe(s):
        if s is None: return None
        try: return _json.loads(s)
        except (ValueError, TypeError): return s
    return {
        "id": r.id, "created_at": r.created_at, "action": r.action,
        "actor_user_id": r.actor_user_id, "actor_ip": r.actor_ip,
        "actor_ua": r.actor_ua, "org_id": r.org_id,
        "target_type": r.target_type, "target_id": r.target_id,
        "before": _safe(r.before_json), "after": _safe(r.after_json),
        "request_id": r.request_id, "note": r.note,
    }


@router.get("/api/orgs/{org_id}/audit")
def list_org_audit(
    org_id: str,
    action: str | None = None,
    action_prefix: str | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = 100,
    offset: int = 0,
    user=Depends(current_user),
):
    from .. import audit
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    rows = audit.query(
        org_id=org_id, action=action, action_prefix=action_prefix,
        from_ts=from_ts, to_ts=to_ts, limit=limit, offset=offset,
    )
    return {
        "rows": [_audit_row_to_dict(r) for r in rows],
        "total": audit.count(org_id=org_id, from_ts=from_ts, to_ts=to_ts),
        "limit": limit, "offset": offset,
    }


@router.get("/api/orgs/{org_id}/audit/export.csv")
def export_org_audit_csv(
    org_id: str,
    from_ts: float | None = None,
    to_ts: float | None = None,
    user=Depends(current_user),
):
    from .. import audit
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    return StreamingResponse(
        audit.export_csv_iter(
            org_id=org_id, from_ts=from_ts, to_ts=to_ts,
        ),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="audit_{org_id}.csv"'
            ),
        },
    )


# ---------- H1: SAML config ----------

@router.get("/api/orgs/{org_id}/saml")
def get_org_saml(
    org_id: str,
    user=Depends(current_user),
):
    from .. import saml
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    cfg = saml.get_config(org_id)
    if cfg is None:
        return {"configured": False}
    return {
        "configured": True, "enabled": cfg.enabled,
        "idp_entity_id": cfg.idp_entity_id,
        "idp_sso_url": cfg.idp_sso_url,
        "sp_entity_id": cfg.sp_entity_id,
        "updated_at": cfg.updated_at,
    }


@router.post("/api/orgs/{org_id}/saml")
def upsert_org_saml(
    org_id: str,
    request: Request,
    idp_entity_id: str = Form(..., max_length=500),
    idp_sso_url: str = Form(..., max_length=1000),
    idp_certificate: str = Form(..., max_length=10000),
    sp_entity_id: str = Form(..., max_length=500),
    idp_slo_url: str | None = Form(None, max_length=1000),
    user=Depends(current_user),
):
    from .. import audit, saml
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    cfg = saml.upsert(
        org_id=org_id, idp_entity_id=idp_entity_id,
        idp_sso_url=idp_sso_url, idp_certificate=idp_certificate,
        sp_entity_id=sp_entity_id, idp_slo_url=idp_slo_url,
    )
    audit.record(
        action="org.saml.update",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        after={"idp_entity_id": cfg.idp_entity_id, "enabled": cfg.enabled},
        **audit.actor_from_request(request),
    )
    return {
        "org_id": cfg.org_id, "idp_entity_id": cfg.idp_entity_id,
        "sp_entity_id": cfg.sp_entity_id, "enabled": cfg.enabled,
    }


# ---------- H2: SCIM tokens (per-org) ----------

@router.post("/api/orgs/{org_id}/scim/tokens", status_code=201)
def create_scim_token(
    org_id: str,
    request: Request,
    name: str = Form(..., min_length=2, max_length=120),
    user=Depends(current_user),
):
    from .. import audit, scim
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    tok, raw = scim.issue_token(org_id=org_id, name=name)
    audit.record(
        action="org.scim.token.issue",
        org_id=org_id, actor_user_id=user.id,
        target_type="scim_token", target_id=tok.id,
        after={"name": tok.name, "prefix": tok.token_prefix},
        **audit.actor_from_request(request),
    )
    return {
        "id": tok.id, "name": tok.name,
        "token_prefix": tok.token_prefix,
        "bearer_token": raw,                # SHOWN ONCE
        "created_at": tok.created_at,
    }


@router.get("/api/orgs/{org_id}/scim/tokens")
def list_scim_tokens(
    org_id: str,
    user=Depends(current_user),
):
    from .. import scim
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    return {
        "tokens": [
            {"id": t.id, "name": t.name, "token_prefix": t.token_prefix,
             "created_at": t.created_at, "last_used": t.last_used,
             "revoked_at": t.revoked_at}
            for t in scim.list_tokens(org_id)
        ],
    }


@router.delete("/api/orgs/{org_id}/scim/tokens/{token_id}")
def revoke_scim_token(
    org_id: str, token_id: str,
    request: Request,
    user=Depends(current_user),
):
    from .. import audit, scim
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    if not scim.revoke_token(token_id=token_id, org_id=org_id):
        raise HTTPException(404, "token not found or already revoked")
    audit.record(
        action="org.scim.token.revoke",
        org_id=org_id, actor_user_id=user.id,
        target_type="scim_token", target_id=token_id,
        **audit.actor_from_request(request),
    )
    return {"ok": True}


# ---------- H4: data residency ----------

@router.get("/api/orgs/{org_id}/data-residency")
def get_org_residency(
    org_id: str,
    user=Depends(current_user),
):
    from .. import residency
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    return {
        "org_id": org_id,
        "data_residency": residency.get_residency(org_id),
        "storage": {
            "region": residency.storage_for_org(org_id).region,
            "configured":
                residency.storage_for_org(org_id).endpoint_url is not None,
        },
    }


@router.post("/api/orgs/{org_id}/data-residency")
def set_org_residency(
    org_id: str,
    request: Request,
    region: str = Form(..., description="'global' | 'india' | 'eu'"),
    user=Depends(current_user),
):
    from .. import audit, residency
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    before = residency.get_residency(org_id)
    try:
        after = residency.set_residency(org_id=org_id, region=region)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(
        action="org.residency.update",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        before={"data_residency": before},
        after={"data_residency": after},
        **audit.actor_from_request(request),
    )
    return {"org_id": org_id, "data_residency": after}


# ---------- H5: custom domain ----------

@router.get("/api/orgs/{org_id}/custom-domain")
def get_custom_domain(
    org_id: str,
    user=Depends(current_user),
):
    from .. import custom_domains
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    s = custom_domains.get_status(org_id)
    return {
        "custom_domain": s.custom_domain, "verified": s.verified,
        "verified_at": s.verified_at,
        "verification_record": s.verification_record,
        "cname_target": s.cname_target,
    }


@router.post("/api/orgs/{org_id}/custom-domain")
def set_custom_domain(
    org_id: str,
    request: Request,
    domain: str = Form(..., min_length=3, max_length=253),
    user=Depends(current_user),
):
    from .. import audit, custom_domains
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    try:
        s = custom_domains.set_domain(org_id=org_id, domain=domain)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(
        action="org.custom_domain.set",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        after={"domain": domain},
        **audit.actor_from_request(request),
    )
    return {
        "custom_domain": s.custom_domain, "verified": s.verified,
        "verification_record": s.verification_record,
        "cname_target": s.cname_target,
    }


@router.delete("/api/orgs/{org_id}/custom-domain")
def remove_custom_domain(
    org_id: str,
    request: Request,
    user=Depends(current_user),
):
    from .. import audit, custom_domains
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    custom_domains.remove_domain(org_id=org_id)
    audit.record(
        action="org.custom_domain.remove",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        **audit.actor_from_request(request),
    )
    return {"ok": True}


# ---------- K2: country ----------

@router.get("/api/orgs/{org_id}/country")
def get_org_country(
    org_id: str,
    user=Depends(current_user),
):
    from .. import countries
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    code = countries.get_country(org_id)
    p = countries.profile_for(code)
    return {
        "country": code, "name": p.name, "currency": p.currency,
        "payment_provider": p.payment_provider,
    }


@router.post("/api/orgs/{org_id}/country")
def set_org_country(
    org_id: str,
    request: Request,
    code: str = Form(..., min_length=2, max_length=2),
    user=Depends(current_user),
):
    from .. import audit, countries
    user = require_user(user)
    org_or_404(org_id)
    require_org_role(org_id, user.id, {"admin"})
    try:
        applied = countries.set_country(org_id=org_id, code=code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record(
        action="org.country.update",
        org_id=org_id, actor_user_id=user.id,
        target_type="org", target_id=org_id,
        after={"country": applied},
        **audit.actor_from_request(request),
    )
    return {"country": applied}
