# AI Pathshala (padhai)

Multilingual AI teacher for K-12, coaching, and SAARC markets.
Web + mobile + admin console. Started as a scan-to-video CLI in v0.1;
now a school-ERP-grade platform at v2.0.4.

- 🇮🇳 10 Indian languages + Bengali / Nepali / Sinhala for SAARC
- 📱 PWA + iOS + Android (Capacitor) + parent + teacher apps
- 🏫 School ERP — orgs, classes, attendance, exams, fees, timetable
- 🔐 Enterprise SSO (SAML 2.0 + SCIM 2.0 + Google + Microsoft OIDC)
- 🌏 Data residency (India / EU / Global) + multi-region failover ready
- 📊 Curriculum alignment scorer (CBSE/ICSE) + UPSC/JEE/NEET coaching engine
- 🎯 Adaptive difficulty + streaks + leaderboards
- 🛡️ SOC 2 Type 1 evidence dashboard + audit log CSV export

**Current release**: v2.0.4 (see `CHANGELOG.md` for the full history).
**ROADMAP status**: v1 (28 items) + v2 (29 items) both complete.
v3 (30 items: AI tutor, live learning, marketplace, govt depth)
scoped at `ROADMAP_V3.md`.

## Quickstart (dev)

```bash
pip install -r requirements.txt
# Optional features per-deployment (Redis queue, SAML, Sarvam TTS, …):
# pip install -r requirements-optional.txt

# Run the API + SPA
PYTHONPATH=. uvicorn padhai.web:app --reload

# Open the SPA at:
#   http://localhost:8000/ui
# OpenAPI docs at:
#   http://localhost:8000/docs
```

The dev path needs no external services — SQLite, in-process worker,
no Postgres / Redis / R2 / FCM / etc. Each subsystem activates when
its env vars are set; everything degrades gracefully when they're not.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│   FastAPI app (padhai/web.py + padhai/routers/*)             │
│   ~10.9k LOC, 160 routes, 6 router modules                   │
├──────────────────────────────────────────────────────────────┤
│   Subsystems (one module each, additive migrations)          │
│   auth · orgs · branding · audit · push · saml · scim ·      │
│   streaks · mastery · coaching · question_bank · preschool · │
│   procurement · custom_domains · residency · math_render ·   │
│   diagram_generator · indic_polish · voice_sarvam · ···      │
├──────────────────────────────────────────────────────────────┤
│   Job queue (`padhai/queue_backend.py`)                      │
│     in-process JobRunner by default                          │
│     Redis + RQ when REDIS_URL is set                         │
├──────────────────────────────────────────────────────────────┤
│   Storage (`padhai/storage.py`)                              │
│     LocalDiskStorage by default                              │
│     Cloudflare R2 / S3 when S3_BUCKET is set                 │
├──────────────────────────────────────────────────────────────┤
│   GPU worker (Modal, separate deploy)                        │
│     padhai/gpu_worker.py + modal_deploy.py                   │
│     Wav2Lip photoreal avatar rendering for M3+ tier          │
└──────────────────────────────────────────────────────────────┘
```

## Major surfaces

| Surface | Entry point | Notes |
|---|---|---|
| **API** | `/api/v2/video-requests`, `/api/orgs/*`, `/api/me/*` | 160 routes; see `/docs` for the OpenAPI spec |
| **SPA** | `/ui` | Single-page app served from `padhai/web.py` |
| **PWA** | `/manifest.json` + `/sw.js` | Branding-aware install |
| **iOS / Android** | `mobile/capacitor.config.json` | Capacitor 6 wrapper around the PWA |
| **Parent / Teacher apps** | `mobile/{parent,teacher}/capacitor.config.json` | Separate App Store / Play Store listings |
| **Admin** | `admin/` (separate FastAPI app) | Queue, moderation, KPIs |
| **GPU worker** | `python -m padhai.gpu_worker` | Modal-deployed; Wav2Lip jobs |
| **CPU worker** | `python -m padhai.worker_entrypoint` | In-process or RQ-backed |

## Production deployment

Each subsystem has a corresponding env var that activates it. The
default config runs everything locally with SQLite + in-process
worker + local files; production flips them one at a time:

| Env var | Activates |
|---|---|
| `DATABASE_URL=postgres://…` | Postgres backend (see `G1_POSTGRES_MIGRATION.md`) |
| `REDIS_URL=redis://…` | Redis + RQ distributed queue (`G2_QUEUE_MIGRATION.md`) |
| `S3_BUCKET=padhai-media` + `S3_ENDPOINT_URL=…` | R2 / S3 object storage |
| `PADHAI_CDN_BASE_URL=…` + `PADHAI_CDN_SIGNING_KEY=…` | Signed CDN URLs |
| `ANTHROPIC_API_KEY=…` | Claude Opus 4.7 for lesson generation |
| `SARVAM_API_KEY=…` | Sarvam.ai bulbul TTS (M2+ tier Hindi voice) |
| `BHASHINI_API_KEY=…` | Bhashini neural TTS (free tier) |
| `FCM_SERVER_KEY=…`, `APNS_KEY_ID=…`, `VAPID_PRIVATE_KEY=…` | Push notifications |
| `PADHAI_REGION=mumbai|singapore|eu` | Multi-region awareness (G4) |
| `PADHAI_TALKING_HEAD_PROVIDER=wav2lip|heygen|synthesia|tavus|d-id|cartoon` | Force avatar provider |

When any of these are unset, the relevant module degrades cleanly —
audit logs still record `failed_reason='no_provider'`, queue falls
back to in-process, storage falls back to local disk, etc.

## Testing

```bash
# Per-release smoke (each is self-contained, ~20s)
PYTHONPATH=. python scripts/test_v2_0_3.py

# Full regression matrix (~3 min, all 13 releases)
for s in scripts/test_v1.py scripts/test_v1_*.py scripts/test_v2_*.py; do
  PYTHONPATH=. python "$s" || break
done
```

CI runs the full matrix on every PR + push to main via
`.github/workflows/smoke.yml`.

Load testing (Locust):

```bash
pip install locust
locust -f scripts/loadtest_locustfile.py --host http://localhost:8000 \
       --headless --users 200 --spawn-rate 20 --run-time 60s
```

See `LOAD_TESTING.md` for SLO targets + capacity-planning matrix.

## Avatar providers

The talking-head layer picks a provider based on user tier + env keys.
Defaults: Wav2Lip → DeepBrain → Synthesia → Tavus → HeyGen → D-ID →
Cartoon (cheapest photoreal first).

| Tier | Provider | Cost/lesson |
|---|---|---|
| M1 Free | Cartoon (CPU) | ₹0 |
| M2 Student Basic | Cartoon + Sarvam Hindi voice | ₹0.50 |
| M3 Student Pro | Wav2Lip on Modal A10G | ₹3-4 |
| M4 Enterprise | HeyGen / Synthesia / Tavus / D-ID | ₹15-30 |

Force a provider regardless: `export PADHAI_TALKING_HEAD_PROVIDER=…`

## Layout

```
padhai/
├── web.py              FastAPI app + lifespan + middleware (~10.9k LOC)
├── routers/            v2.0.2-3 router split (6 modules)
│   ├── public_preview.py    math + diagram + scorer (rate-limited)
│   ├── catalog.py           preschool, procurement, region, etc.
│   ├── coaching.py          tracks list + daily digest
│   ├── question_bank.py     search + stats + get
│   ├── me.py                streak, mastery, push tokens (auth-gated)
│   └── orgs_admin.py        branding, audit, SAML, SCIM, residency
├── api_deps.py         shared auth helpers (v2.0.3)
├── orgs.py             schools / classes / members
├── auth.py             user repo + JWT
├── audit.py            audit log (H3)
├── push.py             FCM + APNs + Web Push (I3)
├── saml.py             SAML 2.0 SP (H1)
├── scim.py             SCIM 2.0 (H2)
├── branding.py         white-label themes (E9)
├── streaks.py          XP + leaderboards (I4)
├── mastery.py          adaptive difficulty (J5)
├── coaching.py         UPSC/JEE/NEET engine (K3)
├── question_bank.py    past papers (J6)
├── preschool.py        K-2 catalog (K4)
├── countries.py        SAARC profiles (K2)
├── procurement.py      GeM SKU catalog (H7)
├── custom_domains.py   per-org top-level domains (H5)
├── residency.py        data residency flag (H4)
├── soc2.py             evidence dashboard (H6)
├── region.py           multi-region awareness (G4)
├── math_render.py      LaTeX → SVG (J1)
├── diagram_generator.py procedural SVG (J2)
├── indic_polish.py     per-language rendering profiles (K1)
├── voice_sarvam.py     Sarvam bulbul TTS (J4)
├── rate_limit.py       in-process token bucket (v2.0.1)
├── queue_backend.py    in-process / Redis-RQ selector (G2)
├── db_backend.py       SQLite / Postgres selector (G1)
├── cdn.py              HMAC-signed URLs (G3)
├── pedagogy.py         page image → Lesson via Claude
├── render.py           Lesson → MP4
├── cache.py            content-addressed cache
└── …
mobile/
├── capacitor.config.json    main app (I1 + I2)
├── parent/                  parent app (I5)
└── teacher/                 teacher app (I6)
admin/
└── …                   separate FastAPI admin console (E5-v0.13)
scripts/
├── test_v1.py … test_v2_0_4.py    release smoke suite
├── loadtest_locustfile.py          G6 load tests
└── …
```

## Documentation

| File | Purpose |
|---|---|
| `ROADMAP.md` | v0.10 → v1.0 plan (28 items, all shipped) |
| `ROADMAP_V2.md` | v1.1 → v2.0 plan (29 items, all shipped) |
| `ROADMAP_V3.md` | v2.1 → v3.0 scoping (30 items, in flight) |
| `CHANGELOG.md` | Curated release history |
| `IMPLEMENTATION_STATUS.md` | PRD §-by-§ shipped/deferred tracker |
| `G1_POSTGRES_MIGRATION.md` | Postgres cutover runbook |
| `G2_QUEUE_MIGRATION.md` | Redis + RQ cutover runbook |
| `G4_MULTI_REGION.md` | Mumbai + Singapore failover runbook |
| `G5_DISASTER_RECOVERY.md` | Backup + restore + drill protocol |
| `A1_PHOTOREAL_DEPLOY.md` | Wav2Lip GPU host setup |
| `H7_GEM_PROCUREMENT.md` | Govt e-Marketplace listing paperwork |
| `MOBILE_BUILD.md` | iOS + Android build runbook |
| `LOAD_TESTING.md` | Locust harness + SLO targets |

## Status

`autoDeploy: false` on `render.yaml` — code lives on `main`, ops
flips the deploy switch. Trigger deploys via the Render dashboard
or by setting `autoDeploy: true` when ready to ship.

## License

Proprietary. AI Pathshala / padhai project.
