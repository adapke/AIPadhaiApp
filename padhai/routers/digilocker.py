"""DigiLocker integration router — exposes padhai/digilocker.py over HTTP.

DigiLocker (digilocker.gov.in) is the Government of India's national
credential vault. Issuing certificates here lets students store their
AI Pathshala progress / completion certificates in their official
locker, which boards / employers can verify with one tap.

  GET  /api/digilocker/doc-types          — list issuable doc types
  GET  /api/digilocker/consent            — current user's consent state
  POST /api/digilocker/consent            — grant consent (Aadhaar + purposes)
  DELETE /api/digilocker/consent          — revoke consent (DPDP §13)
  POST /api/digilocker/issue              — enqueue a credential issuance
  GET  /api/digilocker/issuances          — list current user's issuances
  GET  /api/digilocker/issuances/{id}     — status of one issuance

Org-issuer admin (school plugs their DigiLocker issuer id):
  POST /api/digilocker/orgs/{org_id}/issuer  — register the org as issuer
  POST /api/digilocker/orgs/{org_id}/activate — flip to live

NOTE: actual delivery to DigiLocker servers happens out-of-band via the
DigiLocker Push API (tools/digilocker_push.py background worker). This
router only enqueues / reads — never blocks the request on the upstream
RPC.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException

from ..api_deps import require_org_role, require_user
from ..web import current_user

router = APIRouter()


# ============================================================================
# Doc types
# ============================================================================

@router.get("/api/digilocker/doc-types")
def doc_types(user=Depends(current_user)):
    """List all enabled credential types this platform can issue
    into DigiLocker."""
    user = require_user(user)
    from .. import digilocker as dl
    types = dl.list_doc_types(enabled_only=True)
    return {
        "doc_types": [
            {
                "code": t.code,
                "title": t.title,
                "description": getattr(t, "description", None),
                "template_url": getattr(t, "template_url", None),
            }
            for t in types
        ],
        "count": len(types),
    }


# ============================================================================
# Consent
# ============================================================================

@router.get("/api/digilocker/consent")
def my_consent(user=Depends(current_user)):
    user = require_user(user)
    from .. import digilocker as dl
    c = dl.get_consent(user.id)
    if not c:
        return {"consented": False}
    return {
        "consented": c.revoked_at is None,
        "consented_at": c.consented_at,
        "revoked_at": c.revoked_at,
        "purposes": c.consent_purposes,
        "consent_text": c.consent_text,
    }


@router.post("/api/digilocker/consent", status_code=201)
def grant_consent(
    aadhaar: str = Form(..., description="12-digit Aadhaar (will be hashed immediately)"),
    consent_text: str = Form(..., description="Plain-language consent text shown to user"),
    purposes: str = Form(..., description="Comma-separated doc_type codes"),
    user=Depends(current_user),
):
    """Grant DigiLocker push consent. Aadhaar is hashed at the boundary
    — the raw value never persists. Idempotent: re-granting updates
    purposes / consent_text."""
    user = require_user(user)
    from .. import digilocker as dl
    purpose_list = [p.strip() for p in purposes.split(",") if p.strip()]
    if not purpose_list:
        raise HTTPException(400, "at least one purpose required")
    try:
        c = dl.record_consent(
            user_id=user.id,
            aadhaar_raw=aadhaar,
            consent_purposes=purpose_list,
            consent_text=consent_text,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "ok": True,
        "consent_id": c.id,
        "purposes": c.consent_purposes,
        "consented_at": c.consented_at,
    }


@router.delete("/api/digilocker/consent")
def revoke_consent(user=Depends(current_user)):
    """DPDP §13 — withdraw consent. Existing issuances remain valid
    in DigiLocker (we can't reach in and delete) but no new ones
    will be queued."""
    user = require_user(user)
    from .. import digilocker as dl
    ok = dl.revoke_consent(user.id)
    return {"ok": ok}


# ============================================================================
# Issuance
# ============================================================================

@router.post("/api/digilocker/issue", status_code=201)
def issue_credential(
    org_id: str = Form(..., description="Org that's authoring the issuance"),
    doc_type_code: str = Form(...),
    payload: str = Form(
        ...,
        description="JSON object of credential fields (e.g. {course, marks, completed_on})",
    ),
    user=Depends(current_user),
):
    """Enqueue a credential issuance to DigiLocker for the current
    user. The background worker (tools/digilocker_push.py) picks up
    queued issuances and actually pushes them to DigiLocker servers."""
    import json as _json
    user = require_user(user)
    from .. import digilocker as dl
    if not dl.has_active_consent(user_id=user.id, doc_type_code=doc_type_code):
        raise HTTPException(
            422,
            f"user has not granted DigiLocker consent for "
            f"doc_type {doc_type_code!r}",
        )
    try:
        parsed = _json.loads(payload)
    except _json.JSONDecodeError:
        raise HTTPException(400, "payload must be valid JSON")
    try:
        iss = dl.enqueue_issuance(
            org_id=org_id,
            user_id=user.id,
            doc_type_code=doc_type_code,
            payload=parsed,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "issuance_id": iss.id,
        "status": iss.status,
        "org_id": iss.org_id,
        "doc_type_code": iss.doc_type_code,
        "queued_at": iss.queued_at,
    }


@router.get("/api/digilocker/issuances")
def my_issuances(user=Depends(current_user)):
    user = require_user(user)
    from .. import digilocker as dl
    items = dl.list_user_issuances(user.id)
    return {
        "issuances": [_iss_to_dict(i) for i in items],
        "count": len(items),
    }


@router.get("/api/digilocker/issuances/{iid}")
def issuance_status(iid: str, user=Depends(current_user)):
    user = require_user(user)
    from .. import digilocker as dl
    i = dl.get_issuance(iid)
    if not i:
        raise HTTPException(404, "issuance not found")
    if i.user_id != user.id:
        raise HTTPException(403, "not your issuance")
    return _iss_to_dict(i)


def _iss_to_dict(i) -> dict:
    return {
        "id": i.id,
        "user_id": i.user_id,
        "org_id": i.org_id,
        "doc_type_code": i.doc_type_code,
        "status": i.status,
        "provider_doc_uri": getattr(i, "provider_doc_uri", None),
        "queued_at": i.queued_at,
        "issued_at": getattr(i, "issued_at", None),
        "error": getattr(i, "error", None),
    }


# ============================================================================
# Org issuer admin
# ============================================================================

@router.post("/api/digilocker/orgs/{org_id}/issuer", status_code=201)
def register_org_issuer(
    org_id: str,
    issuer_id: str = Form(..., description="DigiLocker-assigned issuer id"),
    issuer_name: str = Form(...),
    contact_email: str = Form(...),
    user=Depends(current_user),
):
    """Register the org as a DigiLocker issuer. Admin-only."""
    user = require_user(user)
    require_org_role(org_id=org_id, user_id=user.id, allowed={"admin"})
    from .. import digilocker as dl
    try:
        org = dl.register_org_issuer(
            org_id=org_id,
            issuer_id=issuer_id,
            issuer_name=issuer_name,
            contact_email=contact_email,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "org_id": org.org_id,
        "issuer_id": org.issuer_id,
        "status": org.status,
    }


@router.post("/api/digilocker/orgs/{org_id}/activate")
def activate_org_issuer(org_id: str, user=Depends(current_user)):
    user = require_user(user)
    require_org_role(org_id=org_id, user_id=user.id, allowed={"admin"})
    from .. import digilocker as dl
    ok = dl.activate_org_issuer(org_id=org_id)
    if not ok:
        raise HTTPException(404, "org issuer not found")
    return {"ok": True}
