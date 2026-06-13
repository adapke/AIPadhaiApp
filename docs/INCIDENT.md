# Incident response playbook

When something is on fire in production. Read this in the calm
moments so you don't have to read it in the panic moments.

**Companion docs**: [`MONITORING.md`](MONITORING.md) (what to watch),
[`DEPLOY.md`](DEPLOY.md) (rollback procedure).

---

## 0. The five-minute triage

Before doing anything else:

1. **Is the site down?** `curl https://app.your-domain.com/healthz`
   - HTTP 200 + `db_status: ok` → not a full outage; jump to §1
   - HTTP 5xx / timeout → full outage; jump to §2
2. **What changed?** `git log --oneline -10` and check Render's
   deploy timeline. If a deploy went out in the last 30 minutes,
   that's your prime suspect.
3. **Is it just you?** Test from a different network (phone hotspot).
   If only your office sees it, it's local / Cloudflare cache.

If the triage isolates the issue → match to a playbook below.
Otherwise: roll back the last deploy (DEPLOY.md §9), then debug
in calm.

---

## 1. 5xx burst (Sentry going off)

**Symptom**: Sentry → Issues shows > 50 new events/hour, or "new
issue burst" alert fired.

**Triage**:
1. Open the top issue in Sentry. Look at:
   - The exception type (e.g. `psycopg.OperationalError`)
   - The route (e.g. `/api/lessons`)
   - The `release` tag — is it the latest deploy?
2. Click "Affected users" — is it 1 user (a runaway script) or
   broad (real bug)?
3. Check `git log` for the release SHA — what changed?

**Common causes + first action**:

| Pattern | Likely cause | First action |
|---|---|---|
| `OperationalError` on every route | Postgres unreachable | Check Render PG dashboard; restart connection pool |
| `BudgetExceeded` (one user, repeated) | User automation hit the cap | Talk to user; consider bumping their tier |
| `RateLimitError` from Anthropic | Hit per-minute quota | Reduce concurrent jobs; contact Anthropic if persistent |
| `UnicodeDecodeError` on a specific endpoint | Bad input from a client | Add input validation; consider returning 400 instead of 500 |
| New exception type, first seen at deploy | Regression in latest code | Roll back (DEPLOY.md §9) |

**Recovery**:
- If rolled back: tag the broken commit `bad/<sha>` in git so
  fix-forward commits know what NOT to revert.
- Write a Sentry alert filter to silence the existing events
  while you fix — otherwise the dashboard stays noisy.

---

## 2. Full outage (`/healthz` not responding)

**Symptom**: curl from outside times out, or returns 5xx for /
healthz specifically.

**Triage in order**:
1. Render dashboard → Service → status. "Failed" / "Deploy failing"?
   → Last deploy crashed; check build logs.
2. Render dashboard → Logs → last 100 lines. Look for:
   - `uvicorn` startup messages — did it boot?
   - Database connection errors
   - OOM (out-of-memory) — Render auto-restarts but flaps if memory grows unbounded
3. If logs look normal but /healthz still doesn't respond:
   - Cloudflare status page (if you front it through CF)
   - DNS — `dig app.your-domain.com` from a different network

**Recovery**:
- If a deploy is mid-rolling: cancel it via Render dashboard.
- If the worker is wedged: Render → Manual Deploy → re-deploy the
  last known-good SHA.
- If Postgres is unreachable: Render PG dashboard → check
  connection count + storage usage. Free tier sleeps after idle.

---

## 3. Payment fraud (Razorpay)

**Symptom**: Razorpay dashboard shows unusual spike in:
- Failed verifications
- Refunded transactions
- Disputes (chargebacks)

**Triage**:
1. Razorpay dashboard → Risk → check flagged transactions.
2. Audit log: `audit_log` table → events of type
   `subscription.upgraded` in the last 24h. Group by user_id
   and IP.
3. Look for patterns:
   - One IP signing up many users → tier-gaming
   - High-velocity card use (same card, many emails) → card testing
   - Refunds requested within minutes → chargeback fraud

**Immediate actions**:

| Pattern | Action |
|---|---|
| Card testing | Razorpay → Settings → Risk Engine → enable stricter rules |
| Tier gaming | Cap signups per IP at 3/day (server-side rate limit) |
| Single fraudulent transaction | Razorpay dashboard → refund + suspend the user account |
| Wave of disputes | Pause new payments; contact Razorpay support |

**Suspending a user** (admin action):

```bash
PYTHONPATH=. python -c "
from padhai import auth
auth.suspend_user(user_id='<uid>', reason='fraud investigation')
"
```

This sets `account_locked = 1` immediately; user can't log in
until you unlock.

---

## 4. DPDP §11 data request

**Symptom**: user emails support requesting data export, deletion,
or correction.

**SLA**: 30 days from request receipt (DPDP Act 2023 §11).

**Procedure**:
1. Verify the requester's identity. Email signature alone isn't
   enough — require:
   - Login from the registered email (timestamped)
   - Match between the requester's claimed phone/address and
     account record
2. **Export request** (`GET /api/me/data/export`):
   - The endpoint exists and returns a complete JSON dump
   - Email the dump to the user (encrypted ZIP) within 7 days
3. **Deletion request** (`DELETE /api/me/account`):
   - Endpoint anonymises immediately
   - Schedule the 30-day full purge via cron (this is built —
     verify it actually ran)
   - Confirm purge by emailing user from a non-account address
4. **Correction request**:
   - Manual SQL update — log who changed what in `audit_log`
5. Record everything in the compliance log:
   ```
   /var/lib/padhai/compliance/2026-06-13-export-<user_id>.json
   ```

**Penalty for missing SLA**: up to ₹250 crore under DPDP §33.
This is not a soft deadline.

---

## 5. AI cost overrun (Anthropic bill spiking)

**Symptom**: weekly Claude invoice is 2x+ the previous week, or
`/admin/llm-costs` chart shows a sustained climb.

**Triage**:
1. `/admin/llm-costs` → group by user → identify outliers.
2. `llm_calls` table → recent rows where `cost_paise > 1000`
   (₹10+ per call is unusually high).
3. Check for:
   - One user making thousands of tutor requests (automation?)
   - A new feature shipped that didn't use the prompt cache
   - Anthropic price change (rare but check changelog)

**Cost-cap tightening**:

```bash
# Edit padhai/llm_obs.py:DAILY_COST_CAPS_BY_TIER
# Lower the cap for the problem tier
# Restart the server
```

The caps take effect on next request. Existing in-flight calls
finish; no aborts.

**Refund / credit policy**:
- If a user hit the cap because we shipped a bug (regression),
  refund or extend their tier. They didn't choose to burn their
  budget.
- If a user is automating against the API (against TOS), suspend
  them per §3.

---

## 6. Concept-video curator queue meltdown

**Symptom**: `/admin/curator-stats` shows queue > 200 OR
`freshest_verified_iso` > 14 days ago.

**Triage**:
1. `make iframe-check` (or `scripts/check_verified_iframes.py`)
   — is the nightly cron demoting verified rows faster than
   curators can re-verify?
2. Check `STRICT_IFRAME` env on the host. If accidentally `1`,
   broken iframes propagate exit codes that cause cron to fail
   the whole job.
3. PostHog → `concept_video.played` event count: are students
   actually using these? If no plays, the urgency is low.

**Actions**:
- Pause auto-demote: `AUTO_DEMOTE=0` in the cron env. Manual
  curation only.
- Bulk re-verify by source channel: if Peekaboo Kidz suddenly
  blocks all embeds, demote that channel's rows in one query
  and replace with Khan Academy.
- Hire a curator (the real fix at scale).

---

## 7. Mobile shell broken

**Symptom**: Capacitor app users report blank screens or
`net::ERR_CONNECTION_REFUSED`.

**Common causes**:
1. Mobile shells were built with `localhost:8000` — Cypress smoke
   `cypress/e2e/15-mobile-shell.cy.js` catches this in CI.
2. Production URL not configured: run
   `cd mobile && CAPACITOR_SERVER_URL=https://app.your-domain.com
   node scripts/configure-server.cjs` and rebuild.
3. CORS misconfiguration: shells request from
   `capacitor://localhost`. Verify `padhai/web.py` allows that
   origin.

**Recovery**:
- Push a new build via the mobile app stores. There's no hot-fix
  for shipped Capacitor builds short of a fresh build.

---

## 8. Communication during an incident

During a real outage, decide quickly:

| Severity | Audience | Channel | Frequency |
|---|---|---|---|
| Minor (one feature down) | Internal team | Slack #engineering | When status changes |
| Major (full outage) | All users | status.your-domain.com + Twitter | Every 30 min until resolved |
| Critical (data exposure) | DPO + legal + affected users | Direct email + DPDP §10 notification to DPB | Within 72 hours |

DPDP §10 (breach notification): "Significant" breaches require
notifying the Data Protection Board within 72 hours. "Significant"
is defined by impact + number of affected users. When in doubt,
notify.

---

## 9. Post-incident review

Every Sev1 / Sev2 incident gets a write-up within 48 hours.
Template:

```markdown
# Incident YYYY-MM-DD — <one-line summary>

**Severity**: Sev1 / Sev2
**Duration**: HH:MM start → HH:MM end (UTC)
**Affected**: <user count / feature scope>

## Timeline
- HH:MM — first symptom
- HH:MM — alert fired
- HH:MM — engineer paged
- HH:MM — root cause identified
- HH:MM — fix deployed
- HH:MM — resolved

## Root cause
<technical explanation>

## What went well
- <e.g. Sentry alert fired within 2 minutes>

## What went badly
- <e.g. rollback took 15 min because no tagged release>

## Action items
- [ ] <ticket #> — fix root cause
- [ ] <ticket #> — add test to prevent regression
- [ ] <ticket #> — improve detection (faster alert, better
       monitoring)
```

Files under `docs/incidents/`. The pattern is the value, not the
prose.

---

## 10. Contacts (FILL IN BEFORE LAUNCH)

| Role | Person | Contact | Backup |
|---|---|---|---|
| Engineering on-call | <TBD> | <phone/email> | <TBD> |
| DPO (DPDP compliance) | <TBD> | <email> | <TBD> |
| Razorpay account manager | <TBD> | <email> | dashboard support |
| Sentry billing / quota | <TBD> | account email | self-serve |
| Anthropic account | <TBD> | account email | self-serve |
| AWS / Render billing | <TBD> | <email> | dashboard |

**Update this section the day you sign your first paying customer.**
Outage with no on-call contact is a self-inflicted Sev1.
