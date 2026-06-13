# PostHog — product analytics walkthrough

Sentry catches errors; PostHog catches what users *do*. Funnels,
retention curves, feature flags, A/B tests. Wiring is already in
place (`padhai/observability.py:init_posthog` + `track()`); this
doc walks the curator through provider setup + the event taxonomy
already plumbed.

---

## 1. Sign up + get project key

1. Sign up at [posthog.com](https://posthog.com) (Cloud) or self-host.
2. Create new project → choose "Backend (Python)" or
   "Custom integration" — both yield the same project key.
3. Settings → Project → Project API Key. Copy it.
   - Cloud format: `phc_<32-char-string>`
   - Self-hosted: same format, different host.
4. Note the host URL:
   - US Cloud: `https://us.i.posthog.com`
   - EU Cloud: `https://eu.i.posthog.com`
   - Self-hosted: your domain

---

## 2. `.env` setup

```bash
# Required
POSTHOG_API_KEY=phc_your-project-key-here

# Optional — default https://app.posthog.com (legacy, works for both
# regions). For new projects, use the region-specific host:
POSTHOG_HOST=https://us.i.posthog.com    # or eu.i.posthog.com

# Optional — for self-hosted only:
# POSTHOG_HOST=https://posthog.yourdomain.com
```

Restart the server. Boot logs should show:

```
observability.posthog.init status=ok
```

If the line says `status=failed`, the `posthog` Python package isn't
installed. Install with:

```bash
pip install 'posthog>=3.0'
```

It's already in `requirements-optional.txt`.

---

## 3. What events are already wired

`padhai/observability.py:track()` is a thin wrapper. Search for
`track(` to find every call site. Current event taxonomy (as of
prod-111):

| Event name | Fired when | Properties |
|---|---|---|
| `user.signup` | New user signs up via `/auth/signup` | `tier`, `via` (email/sso) |
| `user.signin` | User logs in | `via`, `is_admin` |
| `lesson.requested` | `POST /lessons` accepted | `mode`, `pages`, `subject` |
| `lesson.completed` | Render finished successfully | `mode`, `duration_sec` |
| `essay.graded` | Essay grader returns a score | `tier`, `score`, `model` |
| `mock_interview.completed` | Mock interview submitted | `topic`, `duration_min` |
| `subscription.upgraded` | Tier transition via Razorpay | `from_tier`, `to_tier`, `amount_paise` |
| `concept_video.played` | Student clicks Play on a concept video | `concept`, `quality_tier` |
| `practice_test.completed` | Practice test submitted | `subject`, `score_pct` |
| `daily_cap.hit` | User hit the M2/M3 daily Claude cost cap | `tier`, `cost_paise` |

If you need a new event, just call `track("event.name", user_id=...,
prop1=..., prop2=...)`. The function is a no-op when PostHog isn't
configured, so it's safe to sprinkle freely.

---

## 4. PostHog feature flags (optional)

PostHog also gates features behind cohort rules. The minimal wiring:

```python
from padhai import observability

if observability.is_feature_enabled("new_recap_ui", user_id=user.id):
    return render_new_recap()
else:
    return render_legacy_recap()
```

This is NOT currently wired — `is_feature_enabled()` doesn't exist
in observability.py yet. Adding it is a small follow-up:

```python
# Inside observability.py
def is_feature_enabled(flag_key: str, user_id: str) -> bool:
    if not _posthog_initialised:
        return False
    try:
        import posthog
        return bool(posthog.feature_enabled(flag_key, user_id))
    except Exception:
        return False
```

Use cases: gradual rollouts (50% of new signups get new dashboard),
A/B testing copy, kill-switch for buggy features.

---

## 5. Verify events flow

After restart with a valid `POSTHOG_API_KEY`:

```bash
# 1. Trigger a real signup
curl -X POST http://127.0.0.1:8000/auth/signup \
  -d "email=posthog-test@example.com&password=Pass@12345&terms_accepted=true"

# 2. Open PostHog → Events → Live → should see user.signup event
#    within ~10 seconds. PostHog batches by default.
```

If nothing arrives in 30 seconds:

- Check `padhai_server.log` for `observability.posthog.init` (must say `status=ok`).
- Check PostHog dashboard → Project → Activity → check the timestamp
  of the most recent event landing. Network egress from your VPC?
- Manually flush in dev:
  ```python
  import posthog; posthog.flush()
  ```

---

## 6. Common gotchas

- **Events don't appear:** PostHog batches by default (every 30s
  or 100 events, whichever first). For dev testing, call
  `posthog.flush()` explicitly or wait ~60s.
- **Wrong host:** Cloud users on the wrong region (`us` vs `eu`)
  see events go nowhere. Match the region of your project.
- **PII leaks:** `track()` accepts arbitrary properties — be
  deliberate about not passing email addresses, full names,
  payment details. PostHog has a person-properties cache that
  retains everything you send.
- **Free-tier limits:** 1M events / mo. The free tier is enough
  for ~30k MAU at modest engagement. Beyond that, paid plans
  scale up; budget at $0.000031 per event over the cap.
- **GDPR / DPDP**: under DPDP §6, behavioural tracking of users
  under 18 requires parental consent. The existing
  `padhai/dpdp.py` flow doesn't gate PostHog — TODO: add a check
  in `track()` that no-ops when `user.account_locked` is true
  (minor not yet parent-consented). Engineering deferred.

---

## 7. Useful dashboards / queries

Once events are flowing, build these in PostHog:

- **Signup funnel**: `user.signup` → `lesson.requested` →
  `lesson.completed`. Drop-offs tell you where students bounce.
- **Subscription conversion**: anonymous user → `user.signup` →
  `subscription.upgraded`. The retention curve here is the
  business.
- **AI cost vs revenue**: filter `daily_cap.hit` by tier; correlate
  with `subscription.upgraded` to see if cost caps are converting
  free users to M2.
- **Concept-video engagement**: `concept_video.played` grouped by
  concept. Identifies the curator's high-impact verifications.

---

## 8. Code paths

| Surface | File |
|---|---|
| Init | `padhai/observability.py:init_posthog()` |
| Event capture | `padhai/observability.py:track()` |
| Feature flags (not yet wired) | TODO: `is_feature_enabled()` |
| Boot-time call | `padhai/web.py:install_observability()` |
| Optional dep | `requirements-optional.txt` (`posthog>=3.0`) |

---

## 9. PostHog vs Sentry vs server logs

| Tool | Purpose | When to grep |
|---|---|---|
| Sentry | Server-side errors (5xx, exceptions) | Something broke |
| PostHog | User behaviour, funnels, retention | Something is/isn't being used |
| `padhai_server.log` | Tactical / per-request | Recreating a specific bug |
| `llm_calls` table | Claude cost per user per day | Cost overruns |
| `audit_log` table | Org admin actions | Compliance / who-did-what |

Don't try to make PostHog do Sentry's job (or vice versa). The
event-stream model is wrong for error grouping, and the issue-
tracker model is wrong for funnel analysis.
