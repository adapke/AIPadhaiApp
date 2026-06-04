"""Pricing + checkout router.

Surfaces the subscription tier ladder + a Razorpay-backed checkout flow
that was previously orphaned (`padhai/razorpay_client.py` had no public
HTTP surface):

  GET  /api/pricing/plans         — public list of plans + INR pricing
  POST /api/pricing/checkout      — create Razorpay order for a tier
  POST /api/pricing/verify        — verify payment signature + upgrade user
  GET  /pricing                   — HTML page with plan cards

The plan catalog is defined inline (single source of truth in this
file) so it's easy to tweak without touching the auth tier ladder.
Server-side tier enforcement still lives in auth.py — this router
just updates `users.subscription_tier` on a successful payment.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()
_log = logging.getLogger("padhai.pricing")


# ============================================================================
# Plan catalog
# ============================================================================

# INR prices. Annual gets ~25% discount over monthly × 12.
PLANS = [
    {
        "tier": "M1",
        "name": "Free",
        "price_inr_monthly": 0,
        "price_inr_annual": 0,
        "tagline": "Start learning — no card needed",
        "features": [
            "1 video lesson per day",
            "Cartoon avatar tutor",
            "Basic voice tutor (Hindi + English)",
            "Practice tests (heuristic mode)",
            "Streak tracking",
        ],
        "badge": None,
    },
    {
        "tier": "M2",
        "name": "Starter",
        "price_inr_monthly": 499,
        "price_inr_annual": 4499,
        "tagline": "For school / Class 9-12 daily use",
        "features": [
            "Unlimited video lessons",
            "All 10 Indic languages with premium voice",
            "Essay grader (Claude-powered)",
            "Mock interview (Claude-powered)",
            "Adaptive practice packs",
            "Flashcards from any PDF",
        ],
        "badge": "Popular",
    },
    {
        "tier": "M3",
        "name": "Pro",
        "price_inr_monthly": 999,
        "price_inr_annual": 8999,
        "tagline": "For JEE / NEET / UPSC serious prep",
        "features": [
            "Everything in Starter",
            "Lip-sync avatar (Wav2Lip)",
            "Math Vision — handwriting recognition",
            "Live group classes",
            "Chat-over-PDF for any textbook",
            "Parent + teacher dashboards",
            "Priority Claude (Sonnet) for tutor",
        ],
        "badge": None,
    },
    {
        "tier": "M4b",
        "name": "Premium",
        "price_inr_monthly": 1499,
        "price_inr_annual": 13499,
        "tagline": "Photo-real avatar + 1-on-1 mock interviews",
        "features": [
            "Everything in Pro",
            "Photo-real avatar (HeyGen / D-ID)",
            "Unlimited mock interviews",
            "1-on-1 live tutor sessions (4/month)",
            "Custom rubrics for essay grading",
            "Earliest access to new features",
        ],
        "badge": "Best for exams",
    },
]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/api/pricing/plans")
def pricing_plans():
    """Public — no auth needed. UI calls this to render the pricing page."""
    from .. import razorpay_client as rp
    return {
        "currency": "INR",
        "razorpay_configured": rp.is_configured(),
        "plans": PLANS,
    }


@router.post("/api/pricing/checkout")
def pricing_checkout(
    tier: str = Form(..., description="M2 / M3 / M4b"),
    cycle: str = Form("monthly", description="monthly | annual"),
    user=Depends(current_user),
):
    """Create a Razorpay order for the chosen plan + cycle. Returns
    the order_id + amount that the frontend Checkout widget needs."""
    from .. import razorpay_client as rp
    user = require_user(user)
    plan = _find_plan(tier)
    if plan["tier"] == "M1":
        raise HTTPException(400, "M1 is free — no checkout needed")
    if cycle not in ("monthly", "annual"):
        raise HTTPException(400, "cycle must be 'monthly' or 'annual'")
    amount_inr = plan[f"price_inr_{cycle}"]
    if amount_inr <= 0:
        raise HTTPException(400, "plan price is zero")
    amount_paise = amount_inr * 100
    try:
        order = rp.create_order(
            amount_paise=amount_paise,
            receipt=f"padhai_{user.id[:8]}_{tier}_{cycle}",
            notes={
                "user_id": user.id,
                "tier": tier,
                "cycle": cycle,
                "email": user.email,
            },
        )
    except Exception as e:
        _log.error("[checkout] razorpay order create failed: %s", e)
        raise HTTPException(500, "could not create order") from e

    return {
        "order_id": order["id"],
        "amount": order["amount"],
        "currency": order["currency"],
        "razorpay_key_id": _safe_key_id(),
        "user_email": user.email,
        "tier": tier,
        "cycle": cycle,
        "plan_name": plan["name"],
        "mock": order.get("mock", False),
    }


@router.post("/api/pricing/verify")
def pricing_verify(
    order_id: str = Form(...),
    payment_id: str = Form(...),
    signature: str = Form(...),
    tier: str = Form(...),
    cycle: str = Form(...),
    user=Depends(current_user),
):
    """Verify the Razorpay payment signature; on success, upgrade the
    user's subscription_tier."""
    from .. import razorpay_client as rp
    user = require_user(user)
    plan = _find_plan(tier)
    if plan["tier"] == "M1":
        raise HTTPException(400, "M1 is free")
    ok = rp.verify_payment_signature(
        order_id=order_id, payment_id=payment_id, signature=signature,
    )
    if not ok:
        raise HTTPException(400, "payment signature verification failed")

    _persist_subscription(user.id, tier=tier, cycle=cycle,
                          order_id=order_id, payment_id=payment_id)

    return {
        "ok": True,
        "new_tier": tier,
        "cycle": cycle,
        "order_id": order_id,
        "payment_id": payment_id,
        "plan_name": plan["name"],
    }


# ============================================================================
# HTML page
# ============================================================================

@router.get("/pricing", response_class=HTMLResponse, include_in_schema=False)
def pricing_page():
    """Static-ish HTML page with the four plan cards. The JS fetches
    /api/pricing/plans for current INR prices + posts to /checkout.

    Intentionally lightweight — no SPA framework, no build step.
    """
    return HTMLResponse(_PRICING_HTML)


def _find_plan(tier: str) -> dict:
    for p in PLANS:
        if p["tier"] == tier:
            return p
    raise HTTPException(404, f"unknown tier {tier!r}")


def _safe_key_id() -> str:
    """Return the Razorpay key_id for the frontend Checkout widget.
    Mock orders return an empty string — the JS knows to skip Checkout
    in that case."""
    import os
    return (os.environ.get("RAZORPAY_KEY_ID") or "").strip()


def _persist_subscription(
    user_id: str, *, tier: str, cycle: str,
    order_id: str, payment_id: str,
) -> None:
    """Update users.subscription_tier + optionally insert a
    subscription_payments audit row. Best-effort — non-fatal on error
    so payment verification still succeeds."""
    from ..db import get_db_url
    db_url = get_db_url()
    if not db_url:
        _log.warning("[pricing] no DATABASE_URL — skipping tier upgrade persistence")
        return
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True) as conn:
            conn.execute(
                "UPDATE users SET subscription_tier = %s WHERE id = %s",
                (tier, user_id),
            )
            # Best-effort audit row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscription_payments (
                    id              TEXT PRIMARY KEY,
                    user_id         TEXT NOT NULL,
                    tier            TEXT NOT NULL,
                    cycle           TEXT NOT NULL,
                    order_id        TEXT NOT NULL,
                    payment_id      TEXT NOT NULL,
                    amount_inr      INTEGER,
                    paid_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """,
            )
            plan = _find_plan(tier)
            amount = plan[f"price_inr_{cycle}"]
            conn.execute(
                "INSERT INTO subscription_payments "
                "(id, user_id, tier, cycle, order_id, payment_id, amount_inr) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4().hex, user_id, tier, cycle,
                 order_id, payment_id, amount),
            )
    except Exception as e:
        _log.error("[pricing] _persist_subscription failed: %s", e)


# ============================================================================
# HTML page body
# ============================================================================

_PRICING_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pricing · AI Pathshala</title>
  <style>
    :root {
      --bg: #0f172a;
      --card: #1e293b;
      --border: #334155;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --accent: #f59e0b;
      --accent-2: #10b981;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    header { padding: 32px 24px; text-align: center; }
    h1 { margin: 0 0 8px 0; font-size: 36px; }
    .tagline { color: var(--muted); margin: 0 0 24px 0; }
    .cycle-toggle { display: inline-flex; background: var(--card);
      border-radius: 999px; padding: 4px; gap: 4px; }
    .cycle-toggle button { background: transparent; color: var(--muted);
      border: 0; padding: 8px 20px; border-radius: 999px; cursor: pointer; font-size: 14px; }
    .cycle-toggle button.active { background: var(--accent); color: #0f172a; font-weight: 600; }
    .save-pill { color: var(--accent-2); font-size: 12px; margin-left: 8px; }
    main { max-width: 1180px; margin: 0 auto; padding: 24px;
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; }
    @media (max-width: 1000px) { main { grid-template-columns: repeat(2, 1fr); } }
    @media (max-width: 600px)  { main { grid-template-columns: 1fr; } }
    .plan { background: var(--card); border: 1px solid var(--border);
      border-radius: 14px; padding: 24px; position: relative; display: flex; flex-direction: column; }
    .plan.popular { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
    .badge { position: absolute; top: -10px; right: 18px;
      background: var(--accent); color: #0f172a; font-size: 11px;
      padding: 4px 10px; border-radius: 999px; font-weight: 700; }
    .plan h3 { margin: 0; font-size: 22px; }
    .plan .tagline { font-size: 13px; color: var(--muted); margin: 4px 0 16px 0; }
    .price { font-size: 36px; font-weight: 800; margin: 0; }
    .price small { font-size: 14px; font-weight: 400; color: var(--muted); }
    ul { list-style: none; padding: 0; margin: 16px 0; flex: 1; }
    ul li { padding: 6px 0; font-size: 14px; color: var(--text); position: relative; padding-left: 22px; }
    ul li::before { content: "✓"; position: absolute; left: 0; color: var(--accent-2); font-weight: 700; }
    .cta { display: block; width: 100%; padding: 12px; border-radius: 10px;
      background: var(--accent); color: #0f172a; border: 0; font-weight: 700;
      font-size: 15px; cursor: pointer; }
    .cta.free { background: var(--border); color: var(--text); }
    .cta:disabled { opacity: 0.5; cursor: not-allowed; }
    footer { text-align: center; padding: 32px; color: var(--muted); font-size: 13px; }
    footer a { color: var(--accent); }
  </style>
</head>
<body>
  <header>
    <h1>Choose your plan</h1>
    <p class="tagline">Cancel anytime. Pay in INR. Built for Indian students.</p>
    <div class="cycle-toggle" role="tablist" aria-label="Billing cycle">
      <button id="cycMonthly" role="tab" aria-selected="true" aria-controls="plans" class="active" onclick="setCycle('monthly')">Monthly</button>
      <button id="cycAnnual" role="tab" aria-selected="false" aria-controls="plans" onclick="setCycle('annual')">Annual <span class="save-pill">save 25%</span></button>
    </div>
  </header>

  <main id="plans">Loading plans…</main>

  <footer>
    <p>Need a school / institutional plan? <a href="mailto:sales@aipadhai.app">Contact sales</a>.</p>
    <p>By subscribing you agree to our <a href="/terms">Terms</a> and <a href="/privacy">Privacy Policy</a>.</p>
  </footer>

  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <script>
    let CURRENT_CYCLE = 'monthly';
    let PLANS = [];
    let RZP_KEY = '';

    function setCycle(c) {
      CURRENT_CYCLE = c;
      document.getElementById('cycMonthly').classList.toggle('active', c === 'monthly');
      document.getElementById('cycAnnual').classList.toggle('active', c === 'annual');
      renderPlans();
    }

    async function loadPlans() {
      const r = await fetch('/api/pricing/plans');
      const j = await r.json();
      PLANS = j.plans || [];
      renderPlans();
    }

    function renderPlans() {
      const root = document.getElementById('plans');
      root.innerHTML = '';
      PLANS.forEach(p => {
        const card = document.createElement('div');
        card.className = 'plan' + (p.badge === 'Popular' ? ' popular' : '');
        const price = p['price_inr_' + CURRENT_CYCLE];
        const monthly = CURRENT_CYCLE === 'annual' ? Math.round(price / 12) : price;
        const priceLine = price === 0
          ? '<p class="price">Free</p>'
          : '<p class="price">₹' + monthly + '<small>/mo' + (CURRENT_CYCLE === 'annual' ? ' billed annually' : '') + '</small></p>';
        card.innerHTML = (p.badge ? '<div class="badge">' + p.badge + '</div>' : '')
          + '<h3>' + p.name + '</h3>'
          + '<div class="tagline">' + p.tagline + '</div>'
          + priceLine
          + '<ul>' + p.features.map(f => '<li>' + f + '</li>').join('') + '</ul>'
          + (price === 0
              ? '<button class="cta free" disabled>Current free tier</button>'
              : '<button class="cta" data-tier="' + p.tier + '">Get ' + p.name + '</button>');
        root.appendChild(card);
      });
      root.querySelectorAll('button[data-tier]').forEach(btn => {
        btn.addEventListener('click', () => startCheckout(btn.dataset.tier));
      });
    }

    async function startCheckout(tier) {
      const token = localStorage.getItem('pathshala_token');
      if (!token) {
        alert('Please sign in first to subscribe.');
        window.location.href = '/login?next=/pricing';
        return;
      }
      const fd = new FormData();
      fd.append('tier', tier);
      fd.append('cycle', CURRENT_CYCLE);
      const r = await fetch('/api/pricing/checkout', {
        method: 'POST',
        body: fd,
        headers: { 'Authorization': 'Bearer ' + token },
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('Checkout failed: ' + (err.detail || r.statusText));
        return;
      }
      const j = await r.json();
      if (j.mock) {
        alert('Razorpay not configured on this server — mock payment accepted. Your tier will be upgraded to ' + tier + '.');
        await verifyPayment({
          order_id: j.order_id,
          payment_id: 'mock_pay_' + Date.now(),
          signature: 'mock',
          tier, cycle: CURRENT_CYCLE,
        });
        return;
      }
      const options = {
        key: j.razorpay_key_id,
        amount: j.amount,
        currency: j.currency,
        order_id: j.order_id,
        name: 'AI Pathshala',
        description: j.plan_name + ' — ' + CURRENT_CYCLE,
        prefill: { email: j.user_email },
        theme: { color: '#f59e0b' },
        handler: async function (resp) {
          await verifyPayment({
            order_id: resp.razorpay_order_id,
            payment_id: resp.razorpay_payment_id,
            signature: resp.razorpay_signature,
            tier, cycle: CURRENT_CYCLE,
          });
        },
      };
      const rzp = new Razorpay(options);
      rzp.open();
    }

    async function verifyPayment(payload) {
      const token = localStorage.getItem('pathshala_token');
      const fd = new FormData();
      Object.entries(payload).forEach(([k, v]) => fd.append(k, v));
      const r = await fetch('/api/pricing/verify', {
        method: 'POST',
        body: fd,
        headers: { 'Authorization': 'Bearer ' + token },
      });
      const j = await r.json();
      if (j.ok) {
        alert('Welcome to ' + j.plan_name + '! Your tier is now ' + j.new_tier + '.');
        window.location.href = '/home';
      } else {
        alert('Verification failed: ' + (j.detail || 'unknown'));
      }
    }

    loadPlans();
  </script>
</body>
</html>
"""
