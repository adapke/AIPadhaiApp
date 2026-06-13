# Mobile shell home screen — design rationale

**Status:** prod-133. Affects the **student** Capacitor shell only —
parent + teacher shells unchanged.

---

## What changed

The student Capacitor shell now launches at `/?home=math` instead of
`/`. The home page (`HOME_HTML` in `padhai/home_ui.py`) detects the
`?home=math` query string and synchronously redirects the user to
`/math` — the photo-OCR math-vision page (from prod-28).

End result: a student who opens the AI Pathshala app on Android/iOS
sees the **scan-a-problem** entry as the first thing on launch.

---

## Why

CK-12 (the international K-12 EdTech we benchmarked in the prod-130
analysis) opens its mobile app directly to scan-and-solve. That's
the highest-engagement loop for mobile users — pulling a phone out
of a pocket, photographing a textbook problem, getting a worked
solution back in seconds.

For web users, the dashboard-first layout still makes sense — you're
on a laptop, you want to plan a study session, browse lessons. For
mobile users, the dashboard adds a tap between intent and answer.
prod-133 routes around that.

This is also CK-12's *only* mobile play they execute better than us
(we have wider features overall — full lesson library, mock interview,
UPSC essay grading — but their single-purpose entry is sharper). This
sprint copies their best idea while keeping our breadth on `/` for
returning users.

---

## How to override

The behaviour is configurable per shell via environment variables
read by `mobile/scripts/configure-server.cjs`:

| Env var | What it sets | Default |
|---|---|---|
| `CAPACITOR_HOME_PATH_STUDENT` | Student shell home | `/?home=math` |
| `CAPACITOR_HOME_PATH_PARENT` | Parent shell home | `/ui?mode=parent` |
| `CAPACITOR_HOME_PATH_TEACHER` | Teacher shell home | `/ui?mode=teacher` |

To restore the dashboard-first landing for the student shell:

```bash
CAPACITOR_HOME_PATH_STUDENT=/ npm run build
```

To point students at a different page entirely (e.g. `/dashboard`,
`/tutor`):

```bash
CAPACITOR_HOME_PATH_STUDENT=/tutor npm run build
```

The script rewrites `mobile/capacitor.config.json`'s `server.url`
field in place. Re-run with empty overrides to restore defaults.

---

## Why the JS-redirect approach (and not a server-side route)

We considered three implementations:

1. **Server-side redirect**: change `/` to return a 302 to `/math`
   for shells. Rejected — affects all callers, not just shells.
2. **New `/m/home` route**: serve a math-vision-first page. Rejected —
   doubles the HTML maintenance surface, and the math-vision page
   from prod-28 is already perfect for this.
3. **JS-redirect on `?home=math`** ← chosen. The shell URL carries
   the intent; the home page reads it and bounces. Zero new server
   code, zero new HTML. Easy to override per-build.

The redirect script runs **synchronously inline in `<body>`**, before
any of the page DOM paints. This means:
- The user never sees a flash of HOME_HTML before redirecting.
- Slow networks don't bury the redirect under lazy-loaded scripts.
- The redirect is robust to JavaScript exceptions — the `try/catch`
  ensures we fall back to rendering the home page if anything fails.

---

## What still lives at `/`

- Direct deep links: a parent or teacher who taps a `/` link from
  WhatsApp or email lands on the dashboard.
- PWA installs initiated from a desktop browser.
- Anonymous visitors hitting the landing page.
- Logged-in returning users who saved a `/` bookmark.

The `?home=math` redirect ONLY fires when the literal query string
is present. All other entry paths to `/` behave exactly as before.

---

## How to test

Local emulator:

```bash
cd mobile
CAPACITOR_SERVER_URL=http://10.0.2.2:8000 node scripts/configure-server.cjs
# Inspect mobile/capacitor.config.json - server.url should end with /?home=math
npm run android:run
# On launch, the WebView should bounce directly to /math.
```

Production smoke (without rebuilding the app):

```bash
curl -s "http://localhost:8000/home?home=math" -i | head -1
# Expect 200 — the redirect is client-side, not server-side.
# Open the URL in a browser; you should land on /math within 50ms.
```

Cypress + pytest coverage:

- `tests/test_mobile_home.py` — 7 tests pin the contract:
  HOME_HTML carries the script, redirect script lives in `<body>`,
  `/home` returns the script in HTML, `/math` exists, the configure
  script honours the env override, parent/teacher shells unchanged.
- `cypress/e2e/15-mobile-shell.cy.js` covers the SPA-side entry URLs
  (continues to work — the redirect is invisible to Cypress because
  `cy.visit('/?home=math')` follows the 302-equivalent automatically).

---

## Honest gaps

1. **No iOS testing yet.** The shim relies on `window.location.replace`
   which Safari WebView supports back to iOS 9. But the actual `app
   loads → WebView opens /?home=math → redirect fires → /math paints`
   sequence hasn't been measured on a real iOS device. Should be a
   ~50ms additional load vs landing directly on `/math`.
2. **The math-vision page assumes camera permission** which Capacitor
   does not auto-grant on first launch. The page handles the
   denied-permission case (shows an upload-button fallback), but
   first-touch UX could be smoother — show a "Allow camera to scan
   problems" pre-prompt before the OS dialog. Out of scope for prod-133.
3. **Deep-linking back to `/` after a math-vision session.** If the
   user wants to browse the lesson library, they need to tap the
   home icon in the math-vision page header — there's no back button
   from the OS that returns to `/`. The page header link works, but
   the affordance could be more prominent. Out of scope.
4. **No A/B test of impact yet.** Pure design hypothesis — math-vision
   as the mobile entry should increase D1 retention and session
   length on mobile. We'd want PostHog feature-flag-driven A/B with
   the dashboard-first alternative once we have ~500 daily mobile
   users to power a real test.
