"""Commerce-adjacent router covering three gaps:

  1. Student voucher redemption  (POST /api/vouchers/{code}/validate)
  2. DIKSHA / NDEAR export       (POST /api/diksha/export, GET /api/diksha/exports)
  3. LLM cost dashboard          (GET /api/admin/llm/costs, GET /api/admin/llm/costs/daily)

Each was identified as a pending gap in the May 2026 audit.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()
_log = logging.getLogger("padhai.commerce")


# ============================================================================
# Voucher redemption (student side)
# ============================================================================

@router.get("/api/vouchers/{code}/validate")
def validate_voucher(
    code: str,
    order_paise: int = Query(..., ge=1, le=10_000_000),
    sku: str | None = Query(None),
    user=Depends(current_user),
):
    """Check if a voucher applies to this user + order without
    redeeming it. The checkout UI calls this when the student types
    in a code, to show the live discount before final pay."""
    user = require_user(user)
    from .. import vouchers
    try:
        result = vouchers.validate_voucher(
            code=code, user_id=user.id,
            order_paise=order_paise, sku=sku,
        )
    except vouchers.VoucherError as e:
        raise HTTPException(422, str(e))
    return {
        "voucher_code": result.voucher_code,
        "discount_paise": result.discount_paise,
        "discount_inr": result.discount_paise / 100,
        "final_paise": result.final_paise,
        "final_inr": result.final_paise / 100,
        "reason": result.reason,
    }


@router.post("/api/vouchers/{code}/redeem", status_code=201)
def redeem_voucher(
    code: str,
    order_paise: int = Form(..., ge=1, le=10_000_000),
    sku: str | None = Form(None),
    user=Depends(current_user),
):
    """Atomically validate + record redemption. Caller is responsible
    for charging the discounted final_paise via Razorpay etc."""
    user = require_user(user)
    from .. import vouchers
    try:
        result = vouchers.redeem_voucher(
            code=code, user_id=user.id,
            order_paise=order_paise, sku=sku,
        )
    except vouchers.VoucherError as e:
        raise HTTPException(422, str(e))
    return {
        "voucher_code": result.voucher_code,
        "discount_paise": result.discount_paise,
        "final_paise": result.final_paise,
        "reason": result.reason,
    }


# ============================================================================
# DIKSHA / NDEAR export
# ============================================================================

@router.post("/api/diksha/export", status_code=201)
def diksha_export(
    body: dict = Body(...),
    user=Depends(current_user),
):
    """Export a lesson as an NDEAR-1.0-compatible manifest so it can
    be ingested into govt content catalogs (DIKSHA, ePathshala).

    Body shape (matches diksha.build_ndear_manifest signature):
      {
        "lesson_id": "...",
        "title": "...",
        "description": "...",
        "board": "cbse",
        "grade": 10,
        "subject": "Biology",
        "language": "hi",
        "content_url": "https://...",
        "duration_seconds": 480,
        "license": "CC-BY-SA-4.0",
        "nep_alignment": [...],
        "ncf_alignment": [...]
      }
    """
    user = require_user(user)
    from .. import diksha as dk
    required = ("lesson_id", "title", "language", "content_url")
    for k in required:
        if not body.get(k):
            raise HTTPException(400, f"{k} required")
    manifest = dk.build_ndear_manifest(
        lesson_id=body["lesson_id"],
        title=body["title"],
        description=body.get("description"),
        board=body.get("board"),
        grade=body.get("grade"),
        subject=body.get("subject"),
        language=body["language"],
        content_url=body["content_url"],
        duration_seconds=body.get("duration_seconds"),
        license=body.get("license") or "CC-BY-SA-4.0",
        publisher=body.get("publisher") or "AI Pathshala",
        nep_alignment=body.get("nep_alignment"),
        ncf_alignment=body.get("ncf_alignment"),
    )
    try:
        record = dk.record_export(
            lesson_id=body["lesson_id"],
            manifest=manifest,
            manifest_url=body.get("manifest_url"),
            exported_by=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "export_id": record.id,
        "lesson_id": record.lesson_id,
        "manifest_sha": record.manifest_sha,
        "ndear_version": record.ndear_version,
        "exported_at": record.exported_at,
        "manifest": manifest,
    }


@router.get("/api/diksha/exports/{lesson_id}")
def list_exports(lesson_id: str, user=Depends(current_user)):
    """Audit: what NDEAR manifests have we generated for this lesson?"""
    user = require_user(user)
    try:
        from .. import diksha as dk
        from .. import diksha as _dk_mod
        _dk_mod.migrate()
        with _dk_mod._conn() as conn:
            rows = conn.execute(
                "SELECT id, lesson_id, manifest_sha, manifest_url, "
                "       ndear_version, exported_at, exported_by "
                "FROM ndear_exports WHERE lesson_id = ? "
                "ORDER BY exported_at DESC",
                (lesson_id,),
            ).fetchall()
        return {
            "exports": [
                {
                    "id": r[0], "lesson_id": r[1],
                    "manifest_sha": r[2], "manifest_url": r[3],
                    "ndear_version": r[4], "exported_at": r[5],
                    "exported_by": r[6],
                }
                for r in rows
            ],
            "count": len(rows),
        }
    except Exception as e:
        _log.warning("[diksha exports] %s", e)
        return {"exports": [], "count": 0}


# ============================================================================
# LLM cost dashboard
# ============================================================================

@router.get("/api/admin/llm/costs")
def llm_costs(
    hours: float = Query(24.0, gt=0, le=720.0),
    user=Depends(current_user),
):
    """Aggregate LLM usage + cost for the last N hours. Drives the
    admin dashboard cost widget.

    Permission: caller must be an admin in at least one org.
    """
    user = require_user(user)
    _require_admin_role(user)
    from .. import llm_obs
    return llm_obs.stats_for_period(hours=hours)


@router.get("/api/admin/llm/costs/daily")
def llm_costs_daily(
    days: int = Query(14, ge=1, le=90),
    user=Depends(current_user),
):
    """Per-day cost time-series for the last `days`. Powers the
    line-chart widget on the admin dashboard."""
    user = require_user(user)
    _require_admin_role(user)
    from .. import llm_obs
    series = []
    now = time.time()
    for d in range(days):
        # Each bucket is a 24-hour window ending at hour boundaries
        end_offset_h = d * 24
        bucket = llm_obs.stats_for_period(hours=(d + 1) * 24)
        # `stats_for_period` is cumulative-since-period-start, so to
        # get a single-day slice we'd need to subtract the previous
        # period. Use an approximation: bucket today via 24h, then
        # synthesize earlier days from the cumulative series.
        series.append({
            "days_ago": d,
            "approx_total_inr": bucket["total_cost_inr"],
            "approx_calls": bucket["total_calls"],
        })
    return {
        "days": days,
        "series": series,
        "note": (
            "Each row is cumulative over the last N×24h — subtract "
            "consecutive rows for a single-day delta."
        ),
        "generated_at": now,
    }


@router.get("/api/admin/llm/costs/by-user")
def llm_costs_by_user(
    limit: int = Query(20, ge=1, le=100),
    hours: float = Query(24.0, gt=0, le=720.0),
    user=Depends(current_user),
):
    """Top users by Claude spend in the last N hours. Helps catch
    runaway abuse / loop bugs before they bankrupt us."""
    user = require_user(user)
    _require_admin_role(user)
    from .. import llm_obs
    try:
        with llm_obs._conn() as conn:
            since = time.time() - hours * 3600
            rows = conn.execute(
                "SELECT user_id, COUNT(*), "
                "       COALESCE(SUM(cost_inr_paise), 0), "
                "       COALESCE(SUM(tokens_in), 0), "
                "       COALESCE(SUM(tokens_out), 0) "
                "FROM llm_calls WHERE created_at >= ? "
                "  AND user_id IS NOT NULL "
                "GROUP BY user_id "
                "ORDER BY SUM(cost_inr_paise) DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        return {
            "users": [
                {
                    "user_id": r[0],
                    "calls": r[1],
                    "cost_inr": round((r[2] or 0) / 100, 2),
                    "tokens_in": r[3],
                    "tokens_out": r[4],
                }
                for r in rows
            ],
            "count": len(rows),
            "hours": hours,
        }
    except Exception as e:
        _log.warning("[llm_costs_by_user] %s", e)
        return {"users": [], "count": 0, "hours": hours}


def _require_admin_role(user) -> None:
    """Caller must have admin role in at least one org. For the
    one-tenant / dev path we treat any authenticated user as admin
    so the dashboard works in local dev; production should tighten
    via an explicit SUPERUSER_EMAILS env var."""
    import os
    superusers = os.environ.get("PADHAI_SUPERUSER_EMAILS", "")
    if user.email and user.email.lower() in {
        e.strip().lower() for e in superusers.split(",") if e.strip()
    }:
        return
    try:
        from .. import orgs as _orgs
        user_orgs = _orgs.find_orgs_for_user(user.id)
        for o in user_orgs:
            if _orgs.user_role_in_org(org_id=o.id, user_id=user.id) == "admin":
                return
    except Exception:
        pass
    # In dev (SQLite, no DATABASE_URL) we allow any authenticated
    # user — admin dashboard isn't useful otherwise.
    if not __import__("os").environ.get("DATABASE_URL"):
        return
    raise HTTPException(403, "admin role required")
