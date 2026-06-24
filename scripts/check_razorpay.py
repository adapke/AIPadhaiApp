"""prod-174 — Razorpay verifier.

Three modes:

  --check        Pure config check (env vars set, prefix sanity, sdk
                 importable). Always safe; no API call.

  --create-order Creates a real ₹1 test-mode order via the Razorpay
                 API and prints the order_id. Verifies live API access.
                 ONLY safe in test mode (RAZORPAY_KEY_ID starts with
                 'rzp_test_'). Refuses to run with live keys.

  --verify-sig   Roundtrip-tests the webhook signature path. Synthesises
                 a payload + signs with RAZORPAY_WEBHOOK_SECRET, then
                 calls verify_webhook_signature to confirm the secret is
                 wired correctly. No network call.

Required env vars:
    RAZORPAY_KEY_ID
    RAZORPAY_KEY_SECRET
    RAZORPAY_WEBHOOK_SECRET  (for --verify-sig)

Exit codes:
    0 — checks passed
    1 — config gap / API error / signature mismatch
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _check_config() -> tuple[list[str], bool]:
    """Return (gaps, is_test_mode). is_test_mode is True iff key_id
    starts with 'rzp_test_'. Live keys start with 'rzp_live_'."""
    gaps = []
    kid = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
    ksec = (os.environ.get("RAZORPAY_KEY_SECRET") or "").strip()
    if not kid:
        gaps.append("RAZORPAY_KEY_ID is unset")
    elif not (kid.startswith("rzp_test_") or kid.startswith("rzp_live_")):
        gaps.append(
            f"RAZORPAY_KEY_ID does not start with rzp_test_ or rzp_live_ "
            f"(got: {kid[:12]!r}…)"
        )
    if not ksec:
        gaps.append("RAZORPAY_KEY_SECRET is unset")
    elif len(ksec) < 20:
        gaps.append(
            f"RAZORPAY_KEY_SECRET too short ({len(ksec)} chars; "
            "real secrets are 24+ chars)"
        )
    is_test = kid.startswith("rzp_test_")
    return gaps, is_test


def _verify_signature_roundtrip(verbose: bool) -> tuple[bool, str]:
    """Roundtrip test the webhook signature path. Doesn't call Razorpay."""
    secret = (os.environ.get("RAZORPAY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return False, "RAZORPAY_WEBHOOK_SECRET is unset"
    if len(secret) < 16:
        return False, f"RAZORPAY_WEBHOOK_SECRET too short ({len(secret)} chars)"

    # Synthesise a fake payment.captured webhook payload + sign it
    # ourselves; then verify it via the module's verifier.
    payload = (
        b'{"event":"payment.captured","payload":{"payment":{"entity":'
        b'{"id":"pay_smoke_test","status":"captured","amount":100,'
        b'"currency":"INR"}}}}'
    )
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if verbose:
        print(f"  synthesised payload: {len(payload)} bytes")
        print(f"  HMAC-SHA256 signature: {signature[:16]}…")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from padhai import razorpay_client as rpc

    ok = rpc.verify_webhook_signature(body=payload, signature=signature)
    if not ok:
        return False, "verify_webhook_signature rejected our own correctly-signed payload"

    # Also confirm the verifier rejects a tampered payload (real safety
    # check — if both pass + fail roundtrips work, the secret is wired).
    bad_payload = payload.replace(b"100", b"999")
    if rpc.verify_webhook_signature(body=bad_payload, signature=signature):
        return False, (
            "verify_webhook_signature accepted a payload we tampered with. "
            "Webhook secret comparison broken — DO NOT DEPLOY."
        )
    return True, "signature roundtrip OK + tampering correctly rejected"


def _create_test_order(verbose: bool) -> tuple[bool, str]:
    """Create a ₹1 test order. Refuses to run with live keys."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from padhai import razorpay_client as rpc

    kid = (os.environ.get("RAZORPAY_KEY_ID") or "").strip()
    if not kid.startswith("rzp_test_"):
        return False, (
            f"--create-order refuses to run with non-test keys "
            f"(RAZORPAY_KEY_ID={kid[:12]!r}…). Use test-mode keys."
        )
    try:
        order_id = rpc.create_order(
            amount_paise=100,  # ₹1
            currency="INR",
            receipt="smoke-test-prod174",
            notes={"source": "scripts/check_razorpay.py", "purpose": "smoke"},
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if verbose:
        print(f"  order_id: {order_id}")
    # Mock orders start with order_mock_; real ones with order_
    if order_id.startswith("order_mock_"):
        return False, (
            "razorpay SDK created a MOCK order (order_mock_*) — "
            "it didn't actually call the Razorpay API. Check that "
            "the `razorpay` package is installed: pip install razorpay"
        )
    if not order_id.startswith("order_"):
        return False, f"unexpected order_id format: {order_id!r}"
    return True, f"created real test order {order_id}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="Config-only check (default).")
    p.add_argument("--verify-sig", action="store_true",
                   help="Webhook signature roundtrip (no network).")
    p.add_argument("--create-order", action="store_true",
                   help="Create a ₹1 test-mode order (test keys ONLY).")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if not (args.check or args.verify_sig or args.create_order):
        args.check = True

    print("[razorpay-check] configuration:")
    gaps, is_test = _check_config()
    for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        v = (os.environ.get(k) or "").strip()
        if k.endswith("_SECRET"):
            shown = "***" if v else "(unset)"
        else:
            shown = (v[:16] + "…") if len(v) > 18 else (v or "(unset)")
        print(f"  {k:30s} = {shown}")
    mode = "TEST" if is_test else ("LIVE" if (os.environ.get('RAZORPAY_KEY_ID') or '').startswith('rzp_live_') else "(no key)")
    print(f"  mode = {mode}")

    if gaps:
        print("\n[razorpay-check] FAIL — config gaps:")
        for g in gaps:
            print(f"  - {g}")
        return 1
    print("\n[razorpay-check] config OK.")

    if args.verify_sig:
        print("\n[razorpay-check] webhook signature roundtrip:")
        ok, msg = _verify_signature_roundtrip(verbose=args.verbose)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            return 1

    if args.create_order:
        print("\n[razorpay-check] create test order:")
        ok, msg = _create_test_order(verbose=args.verbose)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
