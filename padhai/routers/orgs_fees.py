"""Org fees router — tenth web.py slice.

Seven endpoints covering the school-fees subsystem:
  POST /api/orgs/{org_id}/fees/structures            (create fee structure)
  GET  /api/orgs/{org_id}/fees/structures            (list)
  POST /api/orgs/{org_id}/fees/structures/{sid}/generate  (bulk-invoice)
  GET  /api/orgs/{org_id}/fees/invoices              (list invoices, scoped)
  GET  /api/orgs/{org_id}/fees/summary               (admin top-line)
  POST /api/orgs/{org_id}/fees/invoices/{iid}/pay    (start Razorpay order)
  POST /api/orgs/{org_id}/fees/invoices/{iid}/confirm (verify + mark paid)

The companion `/api/webhooks/razorpay` endpoint stays in web.py — it
handles both fees AND subscription-tier events, so it's not purely a
fees route.

Role gates:
- admin only: structure CRUD, invoice generation, fee summary
- admin + teacher: list structures
- admin + invoice owner: pay / confirm an invoice
- admin + teacher: list invoices (all); student: list invoices (own)

`_fee_struct_to_dict` and `_invoice_to_dict` helpers lifted out of
web.py since this is their only call site.

Late-imports `web` for the shared globals — same pattern as
orgs_assignments.py, orgs_attendance.py, parents.py, multipage.py.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


def _fee_struct_to_dict(s) -> dict:
    return {
        "id": s.id, "org_id": s.org_id, "name": s.name,
        "amount_paise": s.amount_paise, "amount_rupees": s.amount_paise / 100,
        "currency": s.currency, "applies_to": s.applies_to,
        "due_date": s.due_date, "notes": s.notes,
        "created_at": s.created_at,
    }


def _invoice_to_dict(inv) -> dict:
    return {
        "id": inv.id, "org_id": inv.org_id, "user_id": inv.user_id,
        "structure_id": inv.structure_id,
        "amount_paise": inv.amount_paise,
        "amount_rupees": inv.amount_paise / 100,
        "currency": inv.currency,
        "status": inv.status, "due_date": inv.due_date,
        "paid_at": inv.paid_at,
        "razorpay_order_id": inv.razorpay_order_id,
        "razorpay_payment_id": inv.razorpay_payment_id,
        "receipt_url": inv.receipt_url,
        "created_at": inv.created_at,
    }


@router.post("/api/orgs/{org_id}/fees/structures", status_code=201)
def create_org_fee_structure_route(
    org_id: str,
    name: str = Form(..., min_length=2, max_length=120),
    amount_paise: int = Form(
        ..., ge=100,
        description="Amount in paise — ₹100 = 10000 paise",
    ),
    applies_to: str = Form(
        ..., description="'all' or 'class:<class_id>'",
    ),
    due_date: str | None = Form(None, description="YYYY-MM-DD"),
    notes: str | None = Form(None),
    user: AuthUser | None = Depends(current_user),
):
    """Create a fee structure (template). Admin only."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})
    try:
        s = _web._orgs.create_fee_structure(
            org_id=org_id, name=name, amount_paise=amount_paise,
            applies_to=applies_to, due_date=due_date, notes=notes,
            created_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _fee_struct_to_dict(s)


@router.get("/api/orgs/{org_id}/fees/structures")
def list_org_fee_structures_route(
    org_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """List fee structures. Admin + teacher."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher"})
    return {
        "structures": [
            _fee_struct_to_dict(s)
            for s in _web._orgs.list_fee_structures(org_id, limit=limit)
        ],
    }


@router.post(
    "/api/orgs/{org_id}/fees/structures/{sid}/generate",
    status_code=201,
)
def generate_fee_invoices_route(
    org_id: str, sid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Bulk-create pending invoices for every student the structure
    applies to. Idempotent — UNIQUE(structure_id, user_id) skips
    already-invoiced students."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})
    try:
        return _web._orgs.generate_invoices_for_structure(structure_id=sid)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/api/orgs/{org_id}/fees/invoices")
def list_org_fee_invoices_route(
    org_id: str,
    status: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    user: AuthUser | None = Depends(current_user),
):
    """Admin/teacher see all invoices in the org. Students see only
    their own (used by the "Pay my fees" surface)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
    if my_role is None:
        raise HTTPException(403, "not a member of this org")
    # Students get auto-filtered to their own user_id
    user_filter = user.id if my_role == "student" else None
    invoices = _web._orgs.list_invoices(
        org_id, status=status, user_id=user_filter, limit=limit,
    )
    return {"invoices": [_invoice_to_dict(i) for i in invoices]}


@router.get("/api/orgs/{org_id}/fees/summary")
def get_fee_summary_route(
    org_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Admin dashboard top-line numbers."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin"})
    return _web._orgs.fee_summary(org_id)


@router.post("/api/orgs/{org_id}/fees/invoices/{iid}/pay")
def init_invoice_payment_route(
    org_id: str, iid: str,
    user: AuthUser | None = Depends(current_user),
):
    """Student starts payment — we create a Razorpay order (or a mock
    when RAZORPAY_KEY_ID is unset) and return the checkout details
    the client needs to launch Razorpay's hosted page or web SDK."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    inv = _web._orgs.get_invoice(iid)
    if not inv or inv.org_id != org_id:
        raise HTTPException(404, "invoice not found")
    if inv.user_id != user.id:
        my_role = _web._orgs.user_role_in_org(org_id=org_id, user_id=user.id)
        if my_role != "admin":
            raise HTTPException(
                403, "students may only pay their own invoices",
            )
    if inv.status == "paid":
        return {"already_paid": True, "invoice": _invoice_to_dict(inv)}
    if inv.status in ("cancelled", "refunded"):
        raise HTTPException(409, f"cannot pay {inv.status} invoice")

    order = _web._rzp.create_order(
        amount_paise=inv.amount_paise,
        currency=inv.currency,
        receipt=f"inv_{inv.id[:12]}",
        notes={"invoice_id": inv.id, "user_id": inv.user_id},
    )
    _web._orgs.attach_razorpay_order(invoice_id=inv.id, order_id=order["id"])
    return {
        "invoice": _invoice_to_dict(_web._orgs.get_invoice(iid)),
        "razorpay_order": order,
        "razorpay_key_id": (
            os.environ.get("RAZORPAY_KEY_ID")
            if _web._rzp.is_configured() else None
        ),
        "mock": order.get("mock", False),
    }


@router.post("/api/orgs/{org_id}/fees/invoices/{iid}/confirm")
def confirm_invoice_payment_route(
    org_id: str, iid: str,
    razorpay_payment_id: str = Form(...),
    razorpay_order_id: str = Form(...),
    razorpay_signature: str = Form(...),
    user: AuthUser | None = Depends(current_user),
):
    """Client-side Razorpay Checkout callback — after the user pays,
    Razorpay returns the three handshake values. We verify the
    signature server-side, then mark the invoice paid.

    Mock orders auto-verify (always true) so dev/sandbox can drive
    the full flow without real keys."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    inv = _web._orgs.get_invoice(iid)
    if not inv or inv.org_id != org_id:
        raise HTTPException(404, "invoice not found")
    if not _web._rzp.verify_payment_signature(
        order_id=razorpay_order_id,
        payment_id=razorpay_payment_id,
        signature=razorpay_signature,
    ):
        raise HTTPException(400, "signature verification failed")
    paid = _web._orgs.mark_invoice_paid(
        invoice_id=iid, razorpay_payment_id=razorpay_payment_id,
    )
    return _invoice_to_dict(paid)
