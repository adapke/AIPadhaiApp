# Monitoring — day-2 ops watchlist

What to watch after launch, where to watch it, and what
"healthy" looks like. Designed to read in under 10 minutes.

If you can only watch four things, watch these:

1. **Sentry — new issue rate** (regression detector)
2. **`/admin/health`** (curator queue + verified-video freshness)
3. **`/admin/llm-costs`** (cost-cap breaches per tier)
4. **PostHog — signup → first-lesson funnel** (does the product
   actually work for new users?)

Everything below is the longer version.

---

## 1. Daily 10-minute checklist

These five things take 10 minutes if green and infinite minutes
if red. Do them every morning for the first month.

| What | Where | Healthy looks like |
|---|---|---|
| Sentry new issues (last 24h) | sentry.io → Issues → "First seen: last 24h" | 0–2 new issues, all explainable |
| Server uptime | `curl /healthz` from outside your VPC | 200 + `db_status: ok` |
| Curator queue size | `/admin/health` chip "Pending" | ≤ 30 (anything higher is a backlog) |
| Daily Claude cost | `/admin/llm-costs` chart | Trending flat or down per active user |
| Razorpay test failures | dashboard → Transactions → Failed | 0 in test mode; investigate any spike |

If any row is red, jump to the corresponding section below.

---

## 2. Sentry alerts to set up

Open Sentry → Settings → Alerts → New Alert.

### 2.1 New issue burst
- **Condition**: number of events seen in 1 hour > 50
- **Action**: Slack #padhai-alerts
- **Why**: a single new bug rarely throws 50 events. A traffic
  spike + a logic regression does.

### 2.2 Spike in 5xx error rate
- **Condition**: percentage of events with status 5xx > 5% over
  10 minutes
- **Action**: PagerDuty (if you have it) or email
- **Why**: 1-2% is normal background noise. 5% is real.

### 2.3 Specific exception type
- **Condition**: First seen of `BudgetExceeded` exception
- **Action**: email (low urgency)
- **Why**: someone hit the daily cost cap. Check if it's a real
  power user (upgrade them to M3) or a runaway loop (fix the bug).

### 2.4 The /healthz dropout
- Not in Sentry — use a third-party uptime monitor (Uptime Robot,
  Better Uptime, Pingdom). Free tier covers one URL.
- **URL**: `https://app.your-domain.com/healthz`
- **Interval**: 1 minute
- **Threshold**: alert after 2 consecutive failures

---

## 3. PostHog dashboards to build

Walk through these from the PostHog dashboard. None take more than
10 minutes to set up.

### 3.1 Onboarding funnel
- Steps:
  1. `$pageview` on `/landing`
  2. `user.signup`
  3. `lesson.requested` (first lesson)
  4. `lesson.completed`
- **Healthy**: 5–15% step 1→2, 60–80% step 2→3, 90%+ step 3→4
- **Red flag**: step 3→4 < 50% means the lesson pipeline is
  failing for new users.

### 3.2 Tier upgrade conversion
- Step 1: `user.signup` (M1 free)
- Step 2: `daily_cap.hit` (hit the M1 cap)
- Step 3: `subscription.upgraded`
- **Healthy**: 1-3% step 1→3 in the first month is industry
  baseline for freemium EdTech. Higher = pricing might be too
  cheap; lower = M1 cap is too generous.

### 3.3 Concept-video engagement
- Top 20 events on `concept_video.played` grouped by `concept`
- **Use**: tells the curator where to spend the next hour. Verify
  the top-20 channel_seed videos first.

### 3.4 Daily active users
- Insight → DAU + WAU + MAU
- **Healthy**: DAU/MAU ratio of 0.2+ means people come back.

---

## 4. Cost watch (`/admin/llm-costs`)

The page already shows per-user daily Claude spend. Watch for:

| Pattern | What it means | Action |
|---|---|---|
| One user > ₹500/day on M2 | Power user OR runaway loop | Talk to them; check `llm_calls` for an automation pattern |
| Day-over-day total cost flat as DAU grows | Cache hits are working | Nothing — celebrate |
| Day-over-day total cost growing faster than DAU | Prompt cache miss rate climbing OR new feature shipped | Diff llm_calls for the surface that grew |
| 0 cost for a model tier | Outage in that tier's wrapper | Check `padhai/llm_call.py` + Anthropic status |

The `make stats` CLI emits these as JSON for shell scripting:

```bash
PADHAI_DB_PATH=/var/lib/padhai/jobs.db make stats STATS_DAYS=7
```

---

## 5. Curator queue staleness

The `/admin/curator-stats` page shows verification freshness. Watch:

- **Newest verification > 7 days old**: curator has stopped working.
  Either nudge them or hire one.
- **Queue size growing > 30**: backlog. The nightly iframe-check
  may be demoting verified rows faster than humans can re-verify.
  Tune `STRICT_IFRAME=0` (the default) to stop auto-demotion if
  needed.
- **0 verified rows**: launch hasn't happened yet OR a script
  truncated the table. Run a backup-restore drill.

```bash
# How many channel_seed are pending?
PYTHONPATH=. python -c "
from padhai import concept_videos as cv
print(cv.stats())
"
```

---

## 6. Nightly cron — is it running?

The `scripts/nightly_ops.sh` cron should run at 03:23 UTC. To
verify it actually ran:

```bash
# Look for last successful run
tail -100 /var/log/padhai-nightly.log | grep "all ok"

# Or check backup directory
ls -lh ~/.padhai/backups/jobs_*.db.gz | tail -3
# Should see a file from the last 24h
```

If no recent backup file exists, the cron didn't run (or ran but
failed silently — check the log).

---

## 7. Sentry release tagging

Every deploy should bump the Sentry release. On Render this is
automatic via `RENDER_GIT_COMMIT`. On other hosts, push manually:

```bash
sentry-cli releases new $(git rev-parse HEAD)
sentry-cli releases set-commits --auto $(git rev-parse HEAD)
sentry-cli releases finalize $(git rev-parse HEAD)
```

Without release tagging, Sentry can't tell you "this bug appeared
in v3.27" — it just shows "first seen 3 hours ago", which is much
less useful when triaging.

---

## 8. The DPDP §9 audit (legal monitoring)

Per DPDP Act 2023, you owe Indian users:

1. **Parental consent for minors**: every account with `dob` indicating
   age < 18 should have `account_locked = 1` until consent token
   is redeemed.
   ```sql
   SELECT COUNT(*) FROM users
   WHERE age < 18 AND account_locked = 0;
   ```
   This should always be 0. If > 0, the consent gate is bypassed
   somewhere.

2. **Data export request response time**: SLA 30 days.
   `/api/me/data/export` is the single source of truth — check
   audit log for any export requests pending.

3. **Account deletion within 30 days**: `/api/me/account` (DELETE)
   anonymises immediately and schedules a hard purge. Verify the
   purge actually ran.

The compliance officer (your CEO until you hire one) should review
this monthly.

---

## 9. Backup restore drill (monthly)

Once a month, restore the latest backup to a scratch DB and verify
integrity:

```bash
# Stop nothing — this is read-only
LATEST=$(ls -t ~/.padhai/backups/jobs_*.db.gz | head -1)
gunzip -c "$LATEST" > /tmp/restore_test.db
sqlite3 /tmp/restore_test.db "SELECT COUNT(*) FROM users;"
sqlite3 /tmp/restore_test.db "PRAGMA integrity_check;"
# → integrity_check should print 'ok'
```

If integrity_check is anything other than `ok`, your backup is
corrupted — investigate before the actual disk fails.

---

## 10. What you don't need to monitor

These metrics are tempting but mostly noise:

- **Per-endpoint latency p95**: useful at 10k+ DAU, distracting before.
- **Server memory usage**: Render alerts on OOM automatically.
- **Database query slowness**: Postgres `pg_stat_statements` is
  more useful than dashboards until you have real slow queries.
- **CDN cache hit rates**: not relevant until you have a CDN.
- **Per-route 4xx breakdown**: the Sentry status filter already
  drops these; surfacing them adds noise.

The framing test: if a metric being red wouldn't change what you'd
do tomorrow, don't watch it.

---

## 11. Escalation matrix

| Symptom | Likely cause | First action |
|---|---|---|
| `/healthz` returning 500 | Postgres down or app crashed | Render dashboard → Logs; check `db_status` field |
| Sentry burst > 200 events/hr | Real bug just shipped | Roll back to the last green deploy (see DEPLOY.md §9) |
| User reports "didn't get email" | SMTP misconfigured or in spam | Check `parent_consent_outbox` table; if rows piling up, SMTP env vars are wrong |
| Razorpay payment "stuck" | Webhook didn't fire / signature failed | Check webhook log in Razorpay dashboard + server log for `verify_webhook_signature` errors |
| Cost cap blocking real students | Cap too aggressive or pricing off | Bump `DAILY_COST_CAPS_BY_TIER` for the affected tier, or upgrade the student |
| 5xx on `/lessons` | Anthropic key invalid / rate limit / model deprecated | Check `padhai/llm_obs.py` for the error code; sometimes Anthropic deprecates a model |

---

## 12. The "what we'd actually do" answer to common questions

**Q: How do I know if the launch is going well?**
A: Three numbers: DAU is climbing, signup→lesson_completed funnel
   is > 50%, and Sentry has < 5 new issues/day.

**Q: How do I know when to hire a curator?**
A: When `/admin/curator-stats` shows the queue growing faster
   than your verification rate. Typically once `pending > 100`
   and your verifications-per-week is < 50.

**Q: How do I know when to switch from Render Starter to a real plan?**
A: When `/healthz` shows latency > 500ms p95, or when Render's
   own dashboard shows CPU pegged at 80%+ during business hours.

**Q: What's the first sign of fraud?**
A: One user spawning many child accounts to game the M1 free
   tier. `audit_log` table → `event_type = 'user.signup'` grouped
   by IP. If you see > 5 signups/day from one IP, investigate.
