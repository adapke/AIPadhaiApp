"""L6 — LLM observability.

Per-prompt tracking: tokens in/out, latency, cost, model used, prompt
template version. Cumulative dashboard. Per-user cost caps.
Hallucination flagging (when a student or teacher reports a wrong
answer, the source prompt + response goes into a review queue).

Without this, the Anthropic bill scales linearly with usage + we
can't tune token use. Cost-saving target: 30% via prompt-caching +
batch.

Module-level singleton — every other module that calls Claude
imports `llm_obs.record_call(...)` to log its usage. Lazy, idempotent,
swallows errors so an observability bug never breaks a real call.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    org_id          TEXT,
    module          TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    model           TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL,
    tokens_out      INTEGER NOT NULL,
    cost_inr_paise  INTEGER NOT NULL,
    latency_ms      INTEGER NOT NULL,
    cached          INTEGER NOT NULL DEFAULT 0,
    request_id      TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_user_time ON llm_calls(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_org_time  ON llm_calls(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_module    ON llm_calls(module, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_model     ON llm_calls(model, created_at DESC);

-- One row per user per day per crossed threshold. Prevents alert spam
-- (record_call would otherwise emit on every call once the user is
-- past the threshold). Bucket is the percentage band crossed: '80'
-- when first past 80% of cap, '100' on hitting the cap. New buckets
-- can be added without schema migration — they're just strings.
CREATE TABLE IF NOT EXISTS llm_alerts (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    day             TEXT NOT NULL,           -- 'YYYY-MM-DD' (UTC)
    bucket          TEXT NOT NULL,           -- '80' | '100'
    cap_paise       INTEGER NOT NULL,
    spent_paise_at_crossing INTEGER NOT NULL,
    subscription_tier TEXT,
    created_at      REAL NOT NULL,
    UNIQUE (user_id, day, bucket)
);
CREATE INDEX IF NOT EXISTS idx_llm_alerts_day
    ON llm_alerts(day, created_at DESC);

CREATE TABLE IF NOT EXISTS llm_flags (
    id              TEXT PRIMARY KEY,
    llm_call_id     TEXT NOT NULL,
    reporter_user_id TEXT,
    reason          TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'medium', -- low | medium | high
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | reviewed | confirmed | dismissed
    note            TEXT,
    created_at      REAL NOT NULL,
    reviewed_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_llm_flags_status ON llm_flags(status, created_at DESC);
"""


# Model-specific cost rates (₹ per million tokens, May 2026).
# Update when Anthropic changes prices. Tuned for India billing
# (₹/USD at ~85).
MODEL_COSTS_INR_PER_MTOK = {
    # Claude family (Anthropic)
    "claude-opus-4-7":        {"in": 1275, "out": 6375},   # $15/$75 per Mtok × 85
    "claude-sonnet-4-6":      {"in": 255,  "out": 1275},   # $3/$15 × 85
    "claude-haiku-4-5":       {"in": 68,   "out": 340},    # $0.80/$4 × 85
    "claude-haiku-4-5-20251001": {"in": 68, "out": 340},
    # Cached input: 90% discount
    "claude-opus-4-7-cached":    {"in": 128, "out": 6375},
    "claude-sonnet-4-6-cached":  {"in": 26,  "out": 1275},
    "claude-haiku-4-5-cached":   {"in": 7,   "out": 340},
}


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


def estimate_cost_paise(
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cached: bool = False,
) -> int:
    """Cost in paise (₹/100). Returns int so audit trails are exact —
    no floating-point creep across rollups."""
    key = (model + "-cached") if cached else model
    rates = MODEL_COSTS_INR_PER_MTOK.get(key) or MODEL_COSTS_INR_PER_MTOK.get(model)
    if not rates:
        # Unknown model — log at zero rather than guess
        return 0
    cost_inr = (
        (tokens_in / 1_000_000.0) * rates["in"]
        + (tokens_out / 1_000_000.0) * rates["out"]
    )
    return round(cost_inr * 100)  # paise


@dataclass(frozen=True)
class LLMCall:
    id: str
    user_id: str | None
    org_id: str | None
    module: str
    prompt_version: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_inr_paise: int
    latency_ms: int
    cached: bool
    request_id: str | None
    created_at: float


def record_call(
    *,
    module: str,
    prompt_version: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    user_id: str | None = None,
    org_id: str | None = None,
    cached: bool = False,
    request_id: str | None = None,
    cost_inr_paise: int | None = None,
    subscription_tier: str | None = None,
) -> str:
    """Log one LLM call. Swallows every exception — instrumentation
    bugs never block real Claude calls. Returns the row id (for
    later flag-by-id lookup).

    If cost_inr_paise is None, it's computed from model + tokens via
    `estimate_cost_paise()`.

    `subscription_tier` (optional) lets `_maybe_emit_alert` evaluate
    the user's daily cap when this call lands. Without it, alerts are
    not emitted — but the call still records. Caller modules that
    already know the tier (tutor / essay / lesson) should pass it so
    the admin dashboard sees soft-threshold crossings before the user
    hits the hard cap."""
    if cost_inr_paise is None:
        cost_inr_paise = estimate_cost_paise(
            model=model, tokens_in=tokens_in,
            tokens_out=tokens_out, cached=cached,
        )
    call_id = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO llm_calls "
                "(id, user_id, org_id, module, prompt_version, model, "
                " tokens_in, tokens_out, cost_inr_paise, latency_ms, "
                " cached, request_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (call_id, user_id, org_id, module, prompt_version,
                 model, tokens_in, tokens_out, cost_inr_paise,
                 latency_ms, 1 if cached else 0, request_id,
                 time.time()),
            )
    except Exception as e:
        print(f"[llm_obs] record failed (non-fatal): {e}")
    # Soft-threshold alert — best-effort, never blocks the caller.
    if user_id and subscription_tier:
        try:
            _maybe_emit_alert(
                user_id=user_id,
                subscription_tier=subscription_tier,
            )
        except Exception as e:
            print(f"[llm_obs] alert emit failed (non-fatal): {e}")
    return call_id


# Soft-threshold percentage. Crossing this triggers an alert row so
# the admin dashboard can see "users approaching budget" before they
# hit the hard cap. Override via PADHAI_LLM_ALERT_PCT (0 disables).
LLM_ALERT_THRESHOLD_PCT = 0.80


def _today_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _maybe_emit_alert(
    *,
    user_id: str,
    subscription_tier: str,
) -> None:
    """Emit an alert row when the user crosses the soft threshold of
    their daily cap. Idempotent per (user_id, day, bucket) via the
    UNIQUE constraint — repeat calls after the crossing are no-ops.

    Buckets: '80' (80% of cap), '100' (hard cap reached). 100% means
    the user is now blocked; the row is still useful for the dashboard
    to show "X users blocked today".
    """
    try:
        pct_env = os.environ.get("PADHAI_LLM_ALERT_PCT", "")
        if pct_env:
            try:
                pct = float(pct_env)
                if pct <= 0:
                    return  # disabled
            except ValueError:
                pct = LLM_ALERT_THRESHOLD_PCT
        else:
            pct = LLM_ALERT_THRESHOLD_PCT
    except Exception:
        pct = LLM_ALERT_THRESHOLD_PCT

    cap = daily_cap_paise(subscription_tier)
    if cap is None or cap <= 0:
        return  # uncapped or no-AI tier
    spent = user_cost_today_paise(user_id)
    if spent <= 0:
        return
    day = _today_str()
    soft = round(cap * pct)
    buckets: list[tuple[str, int]] = []
    if spent >= soft:
        buckets.append((f"{int(pct * 100)}", soft))
    if spent >= cap:
        buckets.append(("100", cap))
    if not buckets:
        return
    with _conn() as conn:
        for bucket, _threshold in buckets:
            try:
                conn.execute(
                    "INSERT INTO llm_alerts "
                    "(id, user_id, day, bucket, cap_paise, "
                    " spent_paise_at_crossing, subscription_tier, "
                    " created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, user_id, day, bucket, cap,
                     spent, subscription_tier, time.time()),
                )
                print(
                    f"[llm_obs] ALERT user={user_id[:8]} tier={subscription_tier} "
                    f"bucket={bucket}% spent={spent}p cap={cap}p"
                )
            except Exception:
                # UNIQUE constraint hit — already alerted today
                pass


def recent_alerts(*, hours: float = 24.0, limit: int = 100) -> list[dict]:
    """Pull recent llm_alerts rows for the admin dashboard."""
    since = time.time() - hours * 3600
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, day, bucket, cap_paise, "
            "       spent_paise_at_crossing, subscription_tier, created_at "
            "FROM llm_alerts WHERE created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    return [
        {
            "id": r[0], "user_id": r[1], "day": r[2],
            "bucket": r[3], "cap_paise": r[4],
            "spent_paise_at_crossing": r[5],
            "subscription_tier": r[6], "created_at": r[7],
        }
        for r in rows
    ]


def stats_for_period(*, hours: float = 24.0) -> dict:
    """Aggregate LLM usage. Drives the admin dashboard. Public-ish
    (counts only, no PII or prompts in the response)."""
    since = time.time() - hours * 3600
    with _conn() as conn:
        total_calls = conn.execute(
            "SELECT COUNT(*) FROM llm_calls WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
        tokens_in = conn.execute(
            "SELECT COALESCE(SUM(tokens_in), 0) FROM llm_calls "
            "WHERE created_at >= ?", (since,),
        ).fetchone()[0]
        tokens_out = conn.execute(
            "SELECT COALESCE(SUM(tokens_out), 0) FROM llm_calls "
            "WHERE created_at >= ?", (since,),
        ).fetchone()[0]
        cost_paise = conn.execute(
            "SELECT COALESCE(SUM(cost_inr_paise), 0) FROM llm_calls "
            "WHERE created_at >= ?", (since,),
        ).fetchone()[0]
        cached_calls = conn.execute(
            "SELECT COUNT(*) FROM llm_calls "
            "WHERE created_at >= ? AND cached = 1", (since,),
        ).fetchone()[0]
        avg_latency = conn.execute(
            "SELECT COALESCE(AVG(latency_ms), 0) FROM llm_calls "
            "WHERE created_at >= ?", (since,),
        ).fetchone()[0]
        by_module = conn.execute(
            "SELECT module, COUNT(*), SUM(cost_inr_paise) "
            "FROM llm_calls WHERE created_at >= ? GROUP BY module",
            (since,),
        ).fetchall()
        by_model = conn.execute(
            "SELECT model, COUNT(*), SUM(cost_inr_paise) "
            "FROM llm_calls WHERE created_at >= ? GROUP BY model",
            (since,),
        ).fetchall()
    return {
        "hours": hours,
        "total_calls": int(total_calls),
        "tokens_in": int(tokens_in),
        "tokens_out": int(tokens_out),
        "total_cost_inr": round(cost_paise / 100, 2),
        "cached_calls": int(cached_calls),
        "cache_hit_pct": (cached_calls / total_calls) if total_calls else 0,
        "avg_latency_ms": int(avg_latency),
        "by_module": {
            r[0]: {"calls": r[1], "cost_inr": round((r[2] or 0) / 100, 2)}
            for r in by_module
        },
        "by_model": {
            r[0]: {"calls": r[1], "cost_inr": round((r[2] or 0) / 100, 2)}
            for r in by_model
        },
    }


def user_cost_today(user_id: str) -> float:
    """₹ spent on Claude for this user since midnight UTC. Used by
    the per-tier daily cap enforcement."""
    from datetime import datetime, timezone
    midnight = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()
    with _conn() as conn:
        r = conn.execute(
            "SELECT COALESCE(SUM(cost_inr_paise), 0) FROM llm_calls "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, midnight),
        ).fetchone()
    return round((r[0] or 0) / 100, 2)


def user_cost_today_paise(user_id: str) -> int:
    """Same as user_cost_today() but returns paise as int — no
    floating-point creep when summed against a cap."""
    from datetime import datetime, timezone
    midnight = datetime.now(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()
    with _conn() as conn:
        r = conn.execute(
            "SELECT COALESCE(SUM(cost_inr_paise), 0) FROM llm_calls "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, midnight),
        ).fetchone()
    return int(r[0] or 0)


# Daily cost caps by subscription tier (paise). One cap per tier across
# all AI surfaces — tutor + lesson + essay + doubt + practice all
# subtract from the same daily budget. Sentinel `0` means "no AI access
# at this tier"; `None` (from daily_cap_paise) means uncapped.
# Caps were tuned at v0.7 when a Claude lesson cost ~₹5 and we wanted
# free tier to be zero. After v0.14 the SPA does many more Claude calls
# per session (essay grading + tutor follow-ups + interview turns) and
# a ₹20/day cap on M2 burns out in ~6 essay grades. prod-33 bumped:
#   • M1 stays at 0 (premium-feature gate stays clean)
#   • M2 to ₹100/day so a real student can use the premium features
#   • M3 to ₹400/day for the lip-sync tier
# M4* enterprise tiers continue to be uncapped (handled below).
DAILY_COST_CAPS_BY_TIER: dict[str, int] = {
    "M1": 0,         # free tier — premium AI gated, heuristic fallback
    "M2": 10000,     # ₹100 / day  (premium-voice tier; ~30 essay grades)
    "M3": 40000,     # ₹400 / day  (lip-sync video tier)
    # M4* (enterprise tiers) inherit uncapped via daily_cap_paise() below.
}


class BudgetExceeded(Exception):
    """Raised by check_daily_cap when a user has already spent past
    their daily AI budget. Carries the numbers so the caller can
    show a useful message + record provenance with `fallback_reason`."""

    def __init__(
        self, reason: str, *,
        spent_today_paise: int, cap_paise: int,
    ):
        super().__init__(reason)
        self.reason = reason
        self.spent_today_paise = spent_today_paise
        self.cap_paise = cap_paise


def daily_cap_paise(tier: str | None) -> int | None:
    """Resolve a subscription tier to today's spend cap in paise.

    Returns:
      • 0    — tier has no AI access at all (caller should refuse with
               "upgrade your plan" copy)
      • int  — daily cap in paise; refuse new calls when
               user_cost_today_paise >= this value
      • None — tier is uncapped (M4 enterprise sub-tiers)
    """
    if not tier:
        tier = "M1"
    if tier in DAILY_COST_CAPS_BY_TIER:
        return DAILY_COST_CAPS_BY_TIER[tier]
    # M4a / M4b / M4c / M4d / M4e — enterprise, uncapped
    if tier.startswith("M4"):
        return None
    # Unknown tier — treat as free tier (safe default)
    return DAILY_COST_CAPS_BY_TIER["M1"]


def check_daily_cap(
    *,
    user_id: str | None,
    subscription_tier: str | None,
) -> None:
    """Pre-flight check before a Claude call. Raises BudgetExceeded
    when the user has already spent past their daily cap.

    Anonymous users (user_id is None) are NOT tracked — the runaway-
    abuse vector here is signed-in users with loop bugs or scraping.
    Anonymous calls are rate-limited via padhai.rate_limit instead.
    """
    if not user_id:
        return
    cap = daily_cap_paise(subscription_tier)
    if cap is None:
        return  # uncapped tier
    if cap == 0:
        # Premium feature for a free-tier user
        raise BudgetExceeded(
            "premium_feature",
            spent_today_paise=0,
            cap_paise=0,
        )
    spent = user_cost_today_paise(user_id)
    if spent >= cap:
        raise BudgetExceeded(
            "over_budget",
            spent_today_paise=spent,
            cap_paise=cap,
        )


# ---------- hallucination flagging ----------

VALID_SEVERITIES = {"low", "medium", "high"}
VALID_FLAG_STATUSES = {"pending", "reviewed", "confirmed", "dismissed"}


def flag_call(
    *,
    llm_call_id: str,
    reporter_user_id: str | None,
    reason: str,
    severity: str = "medium",
    note: str | None = None,
) -> str:
    """User or teacher reports a wrong / hallucinated response. The
    call goes into the review queue. Severity 'high' alerts on-call;
    medium/low queue up for batch review."""
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"severity must be in {sorted(VALID_SEVERITIES)}")
    fid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO llm_flags "
            "(id, llm_call_id, reporter_user_id, reason, severity, "
            " status, note, created_at) "
            "VALUES (?,?,?,?,?, 'pending', ?, ?)",
            (fid, llm_call_id, reporter_user_id, reason[:500],
             severity, note, time.time()),
        )
    return fid


def review_flag(*, flag_id: str, status: str, note: str | None = None) -> bool:
    if status not in VALID_FLAG_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_FLAG_STATUSES)}")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE llm_flags SET status = ?, note = COALESCE(?, note), "
            "reviewed_at = ? WHERE id = ?",
            (status, note, time.time(), flag_id),
        )
        return cur.rowcount > 0


def pending_flags(*, limit: int = 50) -> list[dict]:
    """Reviewer queue. Highest severity first, then oldest."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, llm_call_id, reporter_user_id, reason, severity, "
            "       status, note, created_at "
            "FROM llm_flags WHERE status = 'pending' "
            "ORDER BY CASE severity WHEN 'high' THEN 0 "
            "  WHEN 'medium' THEN 1 ELSE 2 END, created_at "
            "LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {"id": r[0], "llm_call_id": r[1], "reporter_user_id": r[2],
         "reason": r[3], "severity": r[4], "status": r[5],
         "note": r[6], "created_at": r[7]}
        for r in rows
    ]
