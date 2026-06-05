"""UX signals router — three pillars added in the P3 batch:

  RUM (Real-User Monitoring) for Core Web Vitals
    POST /api/cwv/sample           — client beacon entry
    GET  /api/cwv/stats             — admin aggregate (auth + admin)
    GET  /api/cwv/stats/{path}      — per-page detail

  Server-side i18n (locale JSON)
    GET  /api/i18n/{lang}.json      — full string dict for client cache
    GET  /api/i18n/coverage         — translation coverage % per locale

  Indian festival / promo feed (driven by padhai/festivals.json)
    GET  /api/festivals/upcoming    — next 30-day window
    GET  /api/festivals/next        — single most-relevant entry

  A/B test exposure (wraps feature_flags.py)
    GET  /api/experiments/me        — variant assignment for the caller

All endpoints are designed for high-frequency use:
  - cwv/sample is rate-capped per IP per minute (defend against bots)
  - i18n responses set long cache headers (locale strings rarely change)
  - festivals feed reads a static JSON file (cheap)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()
_log = logging.getLogger("padhai.ux_signals")


# ============================================================================
# CWV / RUM beacon
# ============================================================================

@router.post("/api/cwv/sample", status_code=204)
async def cwv_sample(
    request: Request,
    body: dict = Body(..., description="web-vitals JSON envelope"),
):
    """Receive a single Core Web Vital sample from the client.

    Anonymous + authenticated callers both accepted — CWV is a privacy-
    preserving signal that should NEVER 401. We pull the bearer token
    out of the header manually (rather than via Depends(current_user))
    so anonymous traffic isn't rejected when PADHAI_REQUIRE_AUTH=1.

    Expected body (matches web-vitals.js onCLS/onLCP/onINP/... callback):
      {
        "name": "LCP", "value": 2317.4, "rating": "good",
        "navigationType": "navigate", "path": "/home",
        "locale": "hi-IN", "device": "mobile"
      }
    Returns 204 always. Bad requests silently 204 too (RUM shouldn't
    surface errors to users).
    """
    from .. import cwv
    # Best-effort user resolution from bearer header. Failure → anonymous.
    user_id = None
    user_tier = "anonymous"
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        try:
            from .. import auth as _auth_mod
            uid = _auth_mod.decode_token(auth[7:].strip())
            if uid:
                user_id = uid
                # Tier lookup is cheap — best-effort
                try:
                    from ..web import _get_user_repo
                    repo = _get_user_repo()
                    if repo:
                        u = repo.find_by_id(uid)
                        if u:
                            user_tier = u.subscription_tier or "M1"
                except Exception:
                    pass
        except Exception:
            pass

    try:
        ip = (
            request.headers.get("cf-connecting-ip")
            or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        )
        ua = request.headers.get("user-agent", "")
        cwv.record_sample(
            path=str(body.get("path") or request.headers.get("referer") or "/")[:200],
            metric=str(body.get("name") or "").upper(),
            value_ms=float(body.get("value") or 0),
            rating=body.get("rating"),
            navigation_type=body.get("navigationType"),
            locale=body.get("locale"),
            device_class=body.get("device"),
            user_id=user_id,
            user_tier=user_tier,
            ip=ip,
            user_agent=ua,
        )
    except Exception as e:
        _log.debug("[cwv] sample drop: %s", e)
    return JSONResponse(content=None, status_code=204)


@router.get("/api/cwv/stats")
def cwv_stats(
    hours: float = Query(24.0, gt=0, le=720),
    user=Depends(current_user),
):
    """Aggregate CWV for the last N hours. Admin-gated (loose check —
    matches the existing /api/admin/llm/* model)."""
    from .. import cwv
    _require_admin_or_dev(user)
    return cwv.stats_for_period(hours=hours)


@router.get("/api/cwv/stats/by-path")
def cwv_stats_by_path(
    hours: float = Query(24.0, gt=0, le=720),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(current_user),
):
    from .. import cwv
    _require_admin_or_dev(user)
    return {"paths": cwv.stats_by_path(hours=hours, limit=limit)}


@router.get("/api/cwv/stats/path")
def cwv_stats_one_path(
    path: str = Query(...),
    hours: float = Query(24.0, gt=0, le=720),
    user=Depends(current_user),
):
    from .. import cwv
    _require_admin_or_dev(user)
    return cwv.stats_for_period(hours=hours, path=path)


# ============================================================================
# Server-side i18n
# ============================================================================

@router.get("/api/i18n/{lang}.json")
def i18n_locale(lang: str):
    """Return the full locale dictionary for the given language code.
    Cached aggressively because locale strings rarely change in-flight."""
    from .. import i18n
    if lang not in i18n.SUPPORTED_LOCALES:
        raise HTTPException(404, f"unknown locale {lang!r}")
    data = i18n.merged(lang)
    resp = JSONResponse(data)
    resp.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    resp.headers["X-Locale"] = lang
    return resp


@router.get("/api/i18n/coverage")
def i18n_coverage():
    """Per-locale translation coverage. Public — useful for the marketing
    page that says '10 languages, 100% translated.'"""
    from .. import i18n
    return i18n.coverage()


@router.get("/api/i18n/locales")
def i18n_supported():
    """List of supported locale codes + their native names."""
    from .. import i18n
    out = []
    for code in i18n.SUPPORTED_LOCALES:
        d = i18n.load(code)
        out.append({
            "code": code,
            "name": d.get("_meta_name", code),
            "native": d.get("_meta_native", code),
        })
    return {"locales": out}


# ============================================================================
# Festival / promo feed
# ============================================================================

def _festivals_data() -> dict:
    p = Path(__file__).resolve().parent.parent / "festivals.json"
    if not p.exists():
        return {"festivals_2026": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"festivals_2026": []}


@router.get("/api/festivals/upcoming")
def festivals_upcoming(
    window_days: int = Query(30, ge=1, le=180),
    region: str | None = Query(None, description="State code: TN, MH, KL, ..."),
):
    """Festivals + promo windows in the next N days. Optionally filtered
    to a region (e.g. ?region=TN returns Pongal but not Onam)."""
    data = _festivals_data()
    fests = data.get("festivals_2026") or data.get("festivals") or []
    now = time.time()
    cutoff = now + window_days * 86400
    out = []
    for f in fests:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(f["date"]).timestamp()
        except Exception:
            continue
        if dt < now or dt > cutoff:
            continue
        if region:
            regions = f.get("regions") or ["ALL"]
            if region.upper() not in regions and "ALL" not in regions:
                continue
        days_to = max(0, int((dt - now) / 86400))
        out.append({**f, "days_to": days_to})
    out.sort(key=lambda x: x["days_to"])
    return {"festivals": out, "count": len(out)}


@router.get("/api/festivals/next")
def festivals_next(region: str | None = Query(None)):
    """The single most-relevant upcoming festival (within 14d)."""
    res = festivals_upcoming(window_days=14, region=region)
    fests = res.get("festivals", [])
    return {"festival": fests[0] if fests else None}


# ============================================================================
# A/B test exposure
# ============================================================================

@router.get("/api/experiments/me")
def experiments_for_me(user=Depends(current_user)):
    """Return variant assignment for every active experiment for the
    calling user. Drives the SPA's hero/CTA variant rendering."""
    user = require_user(user)
    from .. import feature_flags as ff
    # Hero copy A/B
    hero_variant = "control"
    cta_variant = "control"
    trust_variant = "control"
    try:
        flag = ff.get("home.hero_copy")
        if flag and ff.is_enabled("home.hero_copy", user_id=user.id):
            hero_variant = ff.variant_for("home.hero_copy", user_id=user.id) or "control"
            ff.log_exposure(flag_key="home.hero_copy", user_id=user.id,
                            variant=hero_variant)
    except Exception:
        pass
    try:
        flag = ff.get("home.cta_position")
        if flag and ff.is_enabled("home.cta_position", user_id=user.id):
            cta_variant = ff.variant_for("home.cta_position", user_id=user.id) or "control"
            ff.log_exposure(flag_key="home.cta_position", user_id=user.id,
                            variant=cta_variant)
    except Exception:
        pass
    try:
        flag = ff.get("home.trust_strip_placement")
        if flag and ff.is_enabled("home.trust_strip_placement", user_id=user.id):
            trust_variant = ff.variant_for("home.trust_strip_placement",
                                            user_id=user.id) or "control"
    except Exception:
        pass
    return {
        "experiments": {
            "home.hero_copy": hero_variant,
            "home.cta_position": cta_variant,
            "home.trust_strip_placement": trust_variant,
        },
        "user_id_hash": (user.id or "")[:8],
    }


# ============================================================================
# Helpers
# ============================================================================

def _require_admin_or_dev(user) -> None:
    """Same loose admin gate as routers/commerce.py — accept any
    authenticated user in dev (no DATABASE_URL), require admin in prod."""
    import os
    if user is None and os.environ.get("DATABASE_URL"):
        raise HTTPException(401, "authentication required")
    if not os.environ.get("DATABASE_URL"):
        return  # dev: open
    superusers = os.environ.get("PADHAI_SUPERUSER_EMAILS", "")
    if user and user.email and user.email.lower() in {
        e.strip().lower() for e in superusers.split(",") if e.strip()
    }:
        return
    try:
        from .. import orgs as _orgs
        for o in _orgs.find_orgs_for_user(user.id):
            if _orgs.user_role_in_org(org_id=o.id, user_id=user.id) == "admin":
                return
    except Exception:
        pass
    raise HTTPException(403, "admin role required")
