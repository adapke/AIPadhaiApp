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
)


def all_routers():
    """Lazy-import each module and yield its `router`. Avoids importing
    every router at package import time; lets web.py's lifecycle stay
    in control."""
    for name in _ROUTER_NAMES:
        mod = __import__(f"padhai.routers.{name}", fromlist=["router"])
        yield mod.router
