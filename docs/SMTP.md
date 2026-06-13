# SMTP — provider walkthrough

Why this matters: until SMTP is wired, every email AI Pathshala
*should* send (password reset, DPDP §9 parent-consent token, payment
receipts) sits in the `parent_consent_outbox` table. Dev fine,
production blocking.

This doc covers the four providers Indian startups typically pick,
side-by-side. Pick one, generate API key, paste env vars, restart.

---

## 0. Quick decision matrix

| Provider | Free tier | India price | Setup time | Notes |
|---|---|---|---|---|
| **SendGrid** | 100/day forever | $19.95 / 50k mo | ~10 min | Twilio-owned; easy SMTP gateway |
| **Amazon SES** | 62k/mo from EC2 free | $0.10 / 1000 | ~30 min (DKIM + verify) | Best price, ops heavier |
| **Postmark** | 100/mo trial | $15 / 10k mo | ~10 min | Best deliverability for transactional |
| **Mailgun** | None (was 5k/mo, removed 2024) | $35 / 50k mo | ~10 min | Strong EU presence; not India-first |

**For first launch**: SendGrid (fastest setup, generous free tier).
**For scale > 100k/mo**: SES (10× cheaper than SendGrid at scale).
**For "every email must land"**: Postmark (best inbox rates).

---

## 1. SendGrid (recommended for first launch)

1. Sign up at [sendgrid.com](https://sendgrid.com).
2. Settings → Sender Authentication → verify a Single Sender (your
   `from` email). Production: do Domain Authentication instead
   (adds DKIM/SPF/DMARC records to your DNS).
3. Settings → API Keys → Create API Key → "Restricted Access" →
   only enable "Mail Send". Copy the key (shown once).
4. `.env`:

   ```bash
   SMTP_HOST=smtp.sendgrid.net
   SMTP_PORT=587
   SMTP_USER=apikey
   SMTP_PASSWORD=SG.your-api-key-here
   SMTP_FROM=noreply@yourdomain.com
   SMTP_FROM_NAME=AI Pathshala
   ```

5. Test:

   ```bash
   curl -X POST http://127.0.0.1:8000/auth/forgot-password \
     -d "email=you@yourdomain.com"
   ```

   Check inbox (and spam). If nothing arrives in 60s, check
   SendGrid dashboard → Activity Feed.

---

## 2. Amazon SES

1. AWS console → SES → Verified identities → verify your sending
   domain (adds 3 DNS CNAME records — DKIM).
2. SES is "sandbox" by default — can only send to verified
   addresses. Move out of sandbox via "Request production access"
   (24-48 hr review).
3. IAM → Create User → attach `AmazonSESFullAccess` (or scope
   tighter to `ses:SendEmail`).
4. SES → SMTP Settings → "Create SMTP Credentials" — generates a
   user + password specifically for SMTP (different from the IAM
   user's API keys).
5. `.env`:

   ```bash
   SMTP_HOST=email-smtp.ap-south-1.amazonaws.com   # use your region
   SMTP_PORT=587
   SMTP_USER=AKIA...                                # SES SMTP user
   SMTP_PASSWORD=<SES SMTP password>
   SMTP_FROM=noreply@yourdomain.com
   ```

6. Common SES gotcha: bounce + complaint rates. Stay above 5% and
   AWS pauses your sending. Use SNS to feed bounces back to your
   suppression list. Out of scope for v1 — start with SendGrid if
   you don't want to deal with this.

---

## 3. Postmark

1. Sign up at [postmarkapp.com](https://postmarkapp.com).
2. Create a Server (call it "AI Pathshala — transactional").
3. Sender Signatures → add your `from` email; verify via DKIM/SPF.
4. Server Settings → API Tokens → copy the Server Token.
5. `.env`:

   ```bash
   SMTP_HOST=smtp.postmarkapp.com
   SMTP_PORT=587
   SMTP_USER=<server token>      # Postmark uses the token as both user + pass
   SMTP_PASSWORD=<server token>
   SMTP_FROM=noreply@yourdomain.com
   ```

6. Postmark is opinionated about types of mail — "transactional"
   server tokens reject anything that smells like marketing. For
   AI Pathshala this is what you want (password reset, parent
   consent, receipts).

---

## 4. Mailgun

1. Sign up at [mailgun.com](https://mailgun.com).
2. Sending → Domains → add your domain; add the DNS records they
   show.
3. Sending → Domain settings → SMTP credentials → create user.
4. `.env`:

   ```bash
   SMTP_HOST=smtp.mailgun.org              # or smtp.eu.mailgun.org
   SMTP_PORT=587
   SMTP_USER=postmaster@your-mailgun-subdomain.mailgun.org
   SMTP_PASSWORD=<from Mailgun dashboard>
   SMTP_FROM=noreply@yourdomain.com
   ```

5. Mailgun's pricing model changed in 2024 — no free tier; the $35
   flag plan is the entry. Only pick this if you have an existing
   Mailgun relationship.

---

## 5. Verify it works

```bash
# 1. Restart the server so it picks up the new .env
bash scripts/stop_local.sh && bash scripts/run_local.sh

# 2. Trigger a real email path
TOK=$(curl -s -X POST http://127.0.0.1:8000/auth/login -d "email=you@yourdomain.com&password=YourPass1" | python -c "import json,sys;print(json.load(sys.stdin)['token'])")

# 3. The DPDP parent-consent flow is the cleanest test
curl -X POST http://127.0.0.1:8000/auth/parent-consent/send \
  -H "Authorization: Bearer $TOK" \
  -d "parent_email=you+test@yourdomain.com"
```

If the email arrives — done.

If nothing arrives:

```bash
# Check the outbox table — if SMTP isn't wired, the email got
# stashed here instead of sent
PYTHONPATH=. python -c "
import sqlite3
from pathlib import Path
conn = sqlite3.connect(str(Path.home() / '.padhai' / 'jobs.db'))
for r in conn.execute('SELECT * FROM parent_consent_outbox ORDER BY created_at DESC LIMIT 5'):
    print(r)
"
```

Rows in outbox = SMTP env vars weren't picked up. Re-check spelling,
restart server, retry.

---

## 6. Common gotchas across providers

- **`Authentication failed`**: usually you pasted the API token
  into `SMTP_PASSWORD` but `SMTP_USER` is still empty or wrong.
  SendGrid wants `apikey` literally as the user; Postmark wants
  the server token as BOTH user and password.
- **`Connection refused`**: port 587 (STARTTLS) is what most
  providers expect; some require 465 (TLS-from-start). Try both.
  Corporate VPNs sometimes block 587 — test from a clean network.
- **Mail goes to spam**: you skipped DKIM/SPF/DMARC. Without
  domain authentication, Gmail/Outlook filter aggressively. Add
  the DNS records the provider asks for; allow 24-48hr to
  propagate.
- **`From` address rejected**: providers refuse to send from
  domains you haven't verified ownership of. Use a `from` on a
  domain you control.
- **High bounce rate from sign-up emails**: real users mistype
  emails. Confirm `From` and `Reply-To` are valid addresses you
  monitor — otherwise bounces accumulate and the provider
  throttles you.

---

## 7. Code paths that send mail

| Flow | Code | Triggered by |
|---|---|---|
| Password reset | `padhai/auth.py:send_password_reset()` | `POST /auth/forgot-password` |
| DPDP parent consent | `padhai/dpdp.py:send_consent_token()` | `POST /auth/parent-consent/send` |
| Payment receipt | `padhai/web.py` razorpay webhook | `POST /api/webhooks/razorpay` (success) |
| Notification email (org) | `padhai/messaging.py:_send_email_smtp()` | `POST /api/orgs/{id}/notifications` |

All four use the same `smtplib`-based helper. If SMTP is unset,
they no-op and stash the message in `parent_consent_outbox`
(the table is misnamed — it's actually a generic outbox).

---

## 8. Going to production

- DKIM, SPF, DMARC: pass all three for inbox placement. Use
  `mxtoolbox.com` to verify after DNS propagates.
- Soft-launch volume: start at <1000 emails/day for 1-2 weeks
  so the provider can build a reputation for your `from` domain.
  Don't blast 50k on day 1 — Gmail will route to spam.
- Bounce handling: providers expose webhooks for bounces.
  Wire them to an `email_bounces` table (TODO — not built
  yet; engineering deferred).
- Reply-To: set to a real address you read. Receipt emails
  generate replies; ignoring them looks bad.
