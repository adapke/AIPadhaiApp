"""Razorpay client wrapper — gracefully no-op when keys aren't set.

Wraps just enough of the Razorpay SDK for fee invoicing:
  - create_order(invoice) → razorpay_order_id (or a deterministic mock
    when no keys, so the rest of the flow remains testable)
  - verify_webhook_signature(body, signature) → bool (returns True
    when no keys + body indicates a mock payment, so dev/sandbox can
    drive the full flow without a real Razorpay account)

In production, set:
  RAZORPAY_KEY_ID
  RAZORPAY_KEY_SECRET
  RAZORPAY_WEBHOOK_SECRET  (the one set in the Razorpay dashboard)
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid


def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def is_configured() -> bool:
    return bool(_env("RAZORPAY_KEY_ID") and _env("RAZORPAY_KEY_SECRET"))


def _key_id() -> str:
    return _env("RAZORPAY_KEY_ID")


def _key_secret() -> str:
    return _env("RAZORPAY_KEY_SECRET")


def _webhook_secret() -> str:
    return _env("RAZORPAY_WEBHOOK_SECRET")


def create_order(*, amount_paise: int, currency: str = "INR",
                 receipt: str | None = None, notes: dict | None = None) -> dict:
    """Create a Razorpay order. Returns
    {id, amount, currency, receipt, status, mock} — mock=True when
    we're returning a stub because no keys are configured.

    Mock orders use ids prefixed `order_mock_` so the webhook + UI can
    detect them and skip the real verify step."""
    if not is_configured():
        return {
            "id": f"order_mock_{uuid.uuid4().hex[:16]}",
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "status": "created",
            "mock": True,
        }
    # Real Razorpay path
    try:
        import razorpay
    except ImportError as e:
        raise RuntimeError(
            "razorpay package not installed but RAZORPAY_KEY_ID is set; "
            "add `razorpay` to requirements.txt",
        ) from e
    client = razorpay.Client(auth=(_key_id(), _key_secret()))
    order = client.order.create({
        "amount": amount_paise,
        "currency": currency,
        "receipt": receipt or f"r_{uuid.uuid4().hex[:12]}",
        "notes": notes or {},
        "payment_capture": 1,
    })
    return {**order, "mock": False}


def verify_payment_signature(*, order_id: str, payment_id: str,
                             signature: str) -> bool:
    """Verify the signature returned by Razorpay Checkout after a
    successful payment.

    Mock orders auto-verify (since we never asked the real Razorpay
    to do anything). Real orders run HMAC-SHA256 of '<order_id>|<payment_id>'
    with the key secret and compare to the signature.
    """
    if order_id.startswith("order_mock_"):
        return True
    if not is_configured():
        return False
    message = f"{order_id}|{payment_id}".encode()
    expected = hmac.new(
        _key_secret().encode(), message, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(*, body: bytes, signature: str) -> bool:
    """Verify the webhook signature header. In production the webhook
    secret is set on the Razorpay dashboard and shipped here via env."""
    secret = _webhook_secret()
    if not secret:
        # No webhook secret set — accept any payload only in dev mode.
        # Production deploys MUST set RAZORPAY_WEBHOOK_SECRET.
        return not is_configured()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
