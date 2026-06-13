# Razorpay — test-mode walkthrough

How to wire Razorpay into AI Pathshala from scratch, end-to-end,
without burning real money. Production-mode lift is documented at the
bottom.

---

## 1. Razorpay account + test-mode keys

1. Sign up at [https://razorpay.com](https://razorpay.com).
2. **Stay in test mode.** The dashboard has a toggle top-right; keep
   it on "Test Mode" until you're ready to go live.
3. Settings → API Keys → Generate Test Key. You'll get:
   - `key_id` (looks like `rzp_test_AB12CD34EF56GH`)
   - `key_secret` (32-char string — shown once; copy now)
4. Webhooks → Add New Webhook:
   - URL: `https://your-domain/api/webhooks/razorpay`
   - Active events: `payment.captured`, `payment.failed`,
     `subscription.activated`, `subscription.charged`,
     `subscription.cancelled`
   - Set a webhook secret (also 32-char; copy now)

---

## 2. `.env` shape

Add these to `.env` (the local one — `cp .env.example .env` first):

```bash
# Razorpay — test mode
RAZORPAY_KEY_ID=rzp_test_AB12CD34EF56GH
RAZORPAY_KEY_SECRET=<your 32-char secret>
RAZORPAY_WEBHOOK_SECRET=<your 32-char webhook secret>

# Optional: override the public key the SPA uses
RAZORPAY_KEY_ID_PUBLIC=rzp_test_AB12CD34EF56GH
```

Restart the server. Visit `/api/fees/config` — should return
`{"key_id": "rzp_test_..."}` (no secret). That confirms env-loading
worked.

---

## 3. Test cards (free; no real money)

Razorpay provides these for test mode — use any of them on the
Checkout widget:

| Card number | CVV | Expiry | Behaviour |
|---|---|---|---|
| 4111 1111 1111 1111 | any 3 digits | any future | success (Visa) |
| 5267 3181 8797 5449 | any | any future | success (Mastercard) |
| 4000 0000 0000 0002 | any | any future | declined |
| 4000 0000 0000 0119 | any | any future | processing failed |

For UPI: enter `success@razorpay` (success) or `failure@razorpay`
(failure). For Netbanking: pick any bank from the dropdown and click
"Success" / "Failure" in the simulator.

---

## 4. End-to-end test flow

### 4.1 Create an order

```bash
TOK=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
    -d "email=you@example.com&password=YourPass1" \
    | python -c "import json,sys;print(json.load(sys.stdin)['token'])")

curl -X POST http://127.0.0.1:8000/api/payments \
    -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" \
    -d '{"plan": "M2", "amount_paise": 49900, "currency": "INR"}'
```

Response shape:

```json
{
  "order_id": "order_NSGVMa3xV...",
  "key_id": "rzp_test_AB12CD34EF56GH",
  "amount": 49900,
  "currency": "INR"
}
```

### 4.2 Frontend completes payment

The SPA loads Razorpay's Checkout widget with the returned `order_id`
+ `key_id`. Use a test card from §3. Checkout returns these to the
SPA via the success handler:

- `razorpay_payment_id`
- `razorpay_order_id`
- `razorpay_signature`

### 4.3 SPA POSTs signature back for verification

```bash
curl -X POST http://127.0.0.1:8000/api/payments/verify \
    -H "Authorization: Bearer $TOK" \
    -H "Content-Type: application/json" \
    -d '{
      "razorpay_payment_id": "pay_NSGVMa3xV...",
      "razorpay_order_id": "order_NSGVMa3xV...",
      "razorpay_signature": "..."
    }'
```

If the signature matches (HMAC SHA256 of `order_id|payment_id` with
`key_secret`), the user's tier is upgraded server-side. Response:

```json
{"ok": true, "tier_upgraded_to": "M2"}
```

### 4.4 Razorpay also fires the webhook

A few seconds later Razorpay calls `POST /api/webhooks/razorpay`
with the same payment event. The server verifies the signature
(separate `RAZORPAY_WEBHOOK_SECRET`) and idempotently records the
event. This is the path that handles payments that succeeded but
where the user's browser closed before §4.3 ran.

---

## 5. Verify it worked

After §4.3 or §4.4:

```bash
# User's tier should be upgraded
curl -s -H "Authorization: Bearer $TOK" http://127.0.0.1:8000/auth/me \
    | python -c "import json,sys;d=json.load(sys.stdin);print(d['subscription_tier'])"
# → M2
```

Razorpay dashboard → Transactions → you'll see the test payment with
status `captured` (real money never moved).

---

## 6. Common test-mode gotchas

- **Signature verification fails:** double-check `RAZORPAY_KEY_SECRET`
  is the secret from the dashboard (NOT the webhook secret). They're
  two different 32-char strings.
- **Webhook fires but server returns 401:** check
  `RAZORPAY_WEBHOOK_SECRET`. The header is `X-Razorpay-Signature` —
  the server's webhook route does HMAC verification before calling
  any handlers.
- **Public key not in /api/fees/config:** server only exposes
  `RAZORPAY_KEY_ID` from env. Confirm the env var was set BEFORE
  the server started (no hot reload of secrets).
- **Test card declined unexpectedly:** check the dashboard event
  log — Razorpay sometimes simulates random failures in test mode
  for resilience testing.

---

## 7. Going live (production checklist)

When you've successfully completed §4 end-to-end at least 3 times in
test mode:

1. Razorpay dashboard → toggle to **Live Mode**.
2. Settings → API Keys → Generate Live Key.
3. KYC verification — Razorpay requires business documents
   (PAN, GSTIN, bank account). Allow 2-3 working days.
4. Activate the account from the dashboard banner.
5. Replace `.env` values:
   ```bash
   RAZORPAY_KEY_ID=rzp_live_AB12CD34EF56GH   # rzp_live_ prefix
   RAZORPAY_KEY_SECRET=<live secret>
   RAZORPAY_WEBHOOK_SECRET=<live webhook secret>
   ```
6. Update the webhook URL to your production domain.
7. Make a real ₹1 transaction from your own card to validate
   the live flow. Refund yourself from the dashboard.
8. Confirm `padhai/web.py:_PROVIDER_KEY_SPECS` accepts `rzp_live_*`
   (it does — the prefix check accepts both).

---

## 8. SECURITY (do not skip)

- Both secrets MUST be in `.env`, never committed.
  `.gitignore` covers `.env*` already.
- `RAZORPAY_KEY_SECRET` never travels to the SPA. Only `key_id`
  does (via `/api/fees/config`).
- Webhook handler is the only path that can promote a user to a
  paid tier WITHOUT the user being authenticated to AIPadhaiApp.
  Signature verification is therefore required, not optional. See
  `padhai/razorpay_client.py:verify_webhook_signature()`.
- In production, ensure the webhook URL is HTTPS. Razorpay refuses
  to deliver to HTTP endpoints in live mode.

---

## 9. Where the code lives

| Surface | File |
|---|---|
| Order creation, signature verification | `padhai/razorpay_client.py` |
| `POST /api/payments` | `padhai/web.py:13008` |
| `POST /api/webhooks/razorpay` | `padhai/web.py:13049` |
| `GET /api/fees/config` (public key only) | `padhai/routers/misc_status.py` |
| Tier upgrade after successful payment | `padhai/auth.py:upgrade_subscription_tier()` |
| Provider key validation at boot | `padhai/web.py:_validate_provider_keys()` |
