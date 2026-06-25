"""Router package — extracted from `padhai/web.py` to keep that file
under control.

Each router is a self-contained FastAPI `APIRouter` covering one
subsystem. `web.py` imports + includes them at app bootstrap.

Convention:
- Module-level `router = APIRouter()` named exactly that
- Cross-module deps (rate_limit, math_render, etc.) imported inside
  endpoint functions (lazy) so a router can be imported standalone
  for unit testing without bootstrapping the whole app
- Auth-needed endpoints stay in web.py until v2.0.3 when we wire a
  shared `api_deps.py` for the user/org dependencies

To add a new router: write `padhai/routers/<name>.py` exposing
`router`, then add `"name"` to the `all_routers` list below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Single source of truth for which routers are wired. Order doesn't
# matter for correctness (FastAPI dedupes) but matters for readability
# in the auto-generated /docs page.
_ROUTER_NAMES = (
    "public_preview",
    "catalog",
    "coaching",
    "question_bank",
    "me",            # v2.0.3 — /api/me/*, /api/users/me/*, /api/coaching/practice/*
    "orgs_admin",    # v2.0.3 — /api/orgs/{org_id}/* admin-gated
    "v3",            # v2.1.0 — L1 tutor + L6 LLM obs + Q1 flags
    "learning",      # v3.x — wires essay / math / mock / adaptive / practice / live
    "uploads_ai",    # v3.x — PDF chat, flashcards, quiz, summary from uploads
    "onboarding",    # v3.x — class/board/exam/lang/goal onboarding funnel
    "dashboard",     # v3.x — student / parent / teacher dashboards
    "pricing",       # v3.x — pricing page + Razorpay checkout
    "tutor_stream",  # v3.x — SSE streaming companion to /api/tutor/.../message
    "offline",       # v3.x — offline pack manifest + download for PWA / mobile
    "messaging",     # v3.x — SMS / WhatsApp parent alerts (MSG91 / Gupshup / Twilio)
    "digilocker",    # v3.x — DigiLocker credential issuance + consent
    "commerce",      # v3.x — voucher redemption + DIKSHA export + LLM cost dashboard
    "doubt_ai",      # v3.x — instant Claude Vision answer for /api/doubts/{did}/ai-answer
    "ux_signals",    # v3.x — RUM CWV beacon + i18n + festivals + A/B experiments
    "multipage",     # v3.x — multi-page video stitching (/jobs/{id}/combined*)
    "explainer",     # v3.x — /explain + /explain/video (Haiku-only + image-grounded)
    "v2_video",      # v3.x — GET /api/v2/video-requests/{id}/status + /result
    "parents",       # v3.x — /api/parents/* (link, revoke, list, stats)
    "orgs_api",      # v3.x — /api/orgs core CRUD (6 of 37 — others TBD)
    "orgs_classes",  # v3.x — /api/orgs/{id}/classes list+create
    "orgs_leaderboard",  # v3.x — /api/orgs/{id}/classes/{cid}/leaderboard (XP / streaks)
    "orgs_attendance",   # v3.x — /api/orgs/{id}/classes/{cid}/attendance* (4 routes)
    "orgs_assignments",  # v3.x — /api/orgs/{id}/assignments* (list, create, completion, stats)
    "orgs_fees",         # v3.x — /api/orgs/{id}/fees* (structures + invoices + Razorpay)
    "orgs_exams",        # v3.x — /api/orgs/{id}/exams* (school exam create/take/grade)
    "branding",          # v3.x — branding resolve + logo upload + serve (3 routes)
    "scim",              # v3.x — SCIM 2.0 /scim/v2/* (IdP provisioning — 4 routes)
    "notifications",     # v3.x — /api/notifications/* + /api/orgs/{id}/notifications (5 routes)
    "orgs_schedule",     # v3.x — timetable + today + student-history (4 routes)
    "lesson_detail",     # v3.x — /lessons/{id}/{flashcards,quiz,notes,rate} (5 cache-only)
    "lesson_chat_recap", # v3.x — /chat/{id} + /lessons/{id}/recap* (3 Claude/TTS routes)
    "curriculum",        # v3.x — /lessons/{id}/curriculum + /curriculum/index (2 routes)
    "uploads",           # v3.x — POST/GET /api/uploads* (3 routes — upload/analyze/get)
    "sso",               # v3.x — /auth/sso/* (3 routes — OAuth/OIDC flow)
    "avatar_admin",      # v3.x — /api/avatar-providers + /api/avatar-stats* (3 routes)
    "misc_status",       # v3.x — /api/exam-mode/active + /api/fees/config (2 routes)
    "personalisation",   # v3.x — /me/stats + /learning-path (per-user dashboard)
    "push_admin",        # v3.x — /api/push/* (3 routes — opened beacon, log, stats)
    "dpdp_rights",       # v3.x — /api/me/data/export + DELETE /api/me/account (DPDP §11/§12)
    "concept_videos",    # prod-14 — curated YouTube/Khan/etc concept-video catalog
    "new_ui_pages",      # prod-28 — dedicated /essay /interview /practice /adaptive /math /voice /live /recap /notes /curriculum /path /library /school pages
    "teacher_tools",     # prod-131/132 — CK-12-inspired teacher AI tools (assignment gen + reading-level adjuster)
    "concept_seo",       # prod-134 — public /concept/{slug} SEO page (Open Graph, Schema.org, hreflang)
    "mastery_map",       # prod-135 — CK-12-inspired Concept Mastery Map (color-coded per-topic state)
    "concept_examples_routes",  # prod-137 — Real-World Examples catalog (curator queue + public read)
    "questions_by_standard",    # prod-138 — NCERT standards filter + tagger
    "memory_boost_routes",      # prod-139 — Memory Boost daily 3-item drill + streak
    "class_heat_map",           # prod-140 — Class Heat Map for teacher dashboards (students × topics)
    "mastery_page",             # prod-141 — server-rendered /mastery page (color-coded grid)
    "ck12_ui_pages",            # prod-142..145 — /tutor-modes, /memory-boost, /teacher/.../heat-map, /admin/examples-queue
    "search",                   # prod-181 — unified search (/api/search + /search) over videos + PYQs + examples
)


def _inject_admin_dep(routers):
    """prod-9 — close the /api/admin/* anonymous-access gap.

    v3.py + a couple of strays declare `/api/admin/*` routes
    without per-handler auth. Rather than touch 112 handlers, we
    inject the admin-gate dependency into each `/api/admin/*` route's
    `dependencies` list BEFORE `app.include_router` reads it.

    Why dependencies and not dependant: `app.include_router` doesn't
    reuse the router's route objects — it rebuilds them from
    `route.dependencies` (the declarative list) plus its own. Mutating
    `route.dependant.dependencies` (the computed tree) is invisible to
    the app's copy. Mutating `route.dependencies` propagates.

    Idempotent: skipped if the admin dep is already in the list.
    """
    from fastapi import Depends

    from .. import api_deps
    admin_dep_fn = api_deps.make_admin_dep()
    dep = Depends(admin_dep_fn)

    for router in routers:
        for route in router.routes:
            path = getattr(route, "path", "") or ""
            if not path.startswith("/api/admin/"):
                continue
            existing = getattr(route, "dependencies", None)
            if existing is None:
                continue
            # Idempotency check — don't double-inject on a reload.
            already = any(
                getattr(d, "dependency", None) is admin_dep_fn
                for d in existing
            )
            if already:
                continue
            existing.insert(0, dep)


def all_routers():
    """Lazy-import each module and yield its `router`. Avoids importing
    every router at package import time; lets web.py's lifecycle stay
    in control.

    Also injects the admin auth dep into `/api/admin/*` routes (see
    `_inject_admin_dep` — closes the prod-8 finding).
    """
    routers = []
    for name in _ROUTER_NAMES:
        mod = __import__(f"padhai.routers.{name}", fromlist=["router"])
        routers.append(mod.router)
    _inject_admin_dep(routers)
    yield from routers
