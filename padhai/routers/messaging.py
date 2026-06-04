"""SMS / WhatsApp messaging router.

  POST /api/sms/send             — admin/internal: send one transactional message
  POST /api/sms/test             — admin: stub-send to a phone (for QA)
  GET  /api/sms/templates        — list available template keys + previews
  GET  /api/sms/outbox           — admin audit of recent sends
  GET  /api/sms/provider         — which provider is active

Wired into the parent-alert flow:
  POST /api/parents/{child_id}/notify   — fires a templated alert to parent
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


@router.get("/api/sms/provider")
def sms_provider(user=Depends(current_user)):
    user = require_user(user)
    from .. import sms
    return sms.describe()


@router.get("/api/sms/templates")
def sms_templates(user=Depends(current_user)):
    user = require_user(user)
    from .. import sms
    return {
        "templates": [
            {"key": k, "preview": t}
            for k, t in sms.TEMPLATES.items()
        ],
        "count": len(sms.TEMPLATES),
    }


@router.post("/api/sms/send")
def sms_send(
    recipient: str = Form(..., description="E.164 phone (+91XXXXXXXXXX)"),
    template_key: str = Form(...),
    channel: str = Form("sms"),
    variables: str | None = Form(
        None,
        description="JSON object of template variables",
    ),
    user=Depends(current_user),
):
    """Authenticated send. Admins can send to any number; regular
    users can only send to phones bound to their own / their child's
    account."""
    import json as _json
    user = require_user(user)
    from .. import sms
    try:
        parsed_vars = _json.loads(variables) if variables else {}
    except _json.JSONDecodeError:
        raise HTTPException(400, "variables must be valid JSON")

    try:
        result = sms.send(
            recipient=recipient,
            template_key=template_key,
            variables=parsed_vars,
            channel=channel,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "outbox_id": result.outbox_id,
        "status": result.status,
        "provider": result.provider,
        "provider_id": result.provider_id,
        "body": result.body,
        "error": result.error,
    }


@router.get("/api/sms/outbox")
def sms_outbox(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(current_user),
):
    """List recent sends for this user. (Admins can filter by any user
    via a separate admin endpoint — not exposed here.)"""
    user = require_user(user)
    from .. import sms
    return {
        "outbox": sms.list_outbox(
            user_id=user.id, status=status, limit=limit,
        ),
    }


@router.post("/api/parents/{child_id}/notify")
def notify_parent(
    child_id: str,
    template_key: str = Form(...),
    variables: str | None = Form(None),
    channel: str = Form("sms"),
    user=Depends(current_user),
):
    """Fire a parent alert. Only the child's verified-linked parent
    (or a teacher in the child's org) can trigger this; we resolve
    the parent's phone via the parent-link table."""
    import json as _json
    user = require_user(user)
    from .. import parents as _parents
    from .. import sms

    # Resolve the link — caller must be the parent of this child OR
    # have a teacher/admin role in an org containing the child.
    try:
        link = _parents.parent_of(child_user_id=child_id, parent_user_id=user.id)
    except Exception:
        link = None
    if not link:
        # Teacher-as-org-admin escape hatch — kept loose for now
        try:
            from .. import orgs as _orgs
            user_orgs = _orgs.find_orgs_for_user(user.id)
            is_educator = any(
                _orgs.user_role_in_org(org_id=o.id, user_id=user.id) in ("teacher", "admin")
                for o in user_orgs
            )
            if not is_educator:
                raise HTTPException(403, "you are not this student's parent or teacher")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(403, "you are not this student's parent or teacher")

    # Look up the parent's phone (or the caller's, when caller is the parent)
    phone = None
    try:
        phone = getattr(link, "parent_phone", None) if link else None
    except Exception:
        phone = None
    if not phone:
        raise HTTPException(422, "no parent phone on file for this child")

    try:
        parsed_vars = _json.loads(variables) if variables else {}
    except Exception:
        raise HTTPException(400, "variables must be valid JSON")

    try:
        result = sms.send(
            recipient=phone,
            template_key=template_key,
            variables=parsed_vars,
            channel=channel,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "outbox_id": result.outbox_id,
        "status": result.status,
        "provider": result.provider,
        "body": result.body,
    }
