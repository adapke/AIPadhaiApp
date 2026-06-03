"""Real-User Monitoring (RUM) for Core Web Vitals.

Client-side beacon: the SPA imports Google's `web-vitals` library,
captures LCP / INP / CLS / TTFB / FCP for the actual user session,
and POSTs a single JSON envelope to /api/cwv/sample. We persist into
a small SQLite table with date + path + page + locale + tier so we
can slice CWV by city tier (proxy via timezone-region), exam pack,
and authenticated vs anonymous.

Why first-party RUM:
  • No PostHog/Sentry/Datadog needed for the first slice.
  • CWV is the single highest-leverage performance signal in India
    per the original report (Nykaa LCP -40% → +28% T2/T3 organic).
  • All data stays in our DB; no PII collected beyond IP-hashed user.

Sampling:
  • 100% in dev (low volume)
  • 10% sample in prod (CWV_SAMPLE_RATE env var) to cap insert pressure

Two tables:
  cwv_samples       one row per beacon (date, path, metric, value, ...)
  cwv_aggregates    daily p75 rollup per (path × locale × device class)

Stats endpoints:
  GET /api/cwv/stats              admin dashboard summary
  GET /api/cwv/stats/{path}       per-page detail (sliced)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SCHEMA = """
CREATE TABLE IF NOT EXISTS cwv_samples (
    id              TEXT PRIMARY KEY,
    sampled_at      REAL NOT NULL,
    path            TEXT NOT NULL,        -- e.g. /home, /home/hi, /pricing
    metric          TEXT NOT NULL,        -- LCP | INP | CLS | TTFB | FCP
    value_ms        REAL NOT NULL,        -- ms; for CLS we multiply 1000
    rating          TEXT,                 -- good | needs-improvement | poor
    navigation_type TEXT,                 -- navigate | reload | back-forward
    locale          TEXT,                 -- html lang attribute
    device_class    TEXT,                 -- mobile | tablet | desktop
    ip_hash         TEXT,                 -- sha256(IP+salt) — privacy
    user_hash       TEXT,                 -- sha256(user_id+salt) — privacy
    user_tier       TEXT,                 -- M1 | M2 | ... | anonymous
    ua_class        TEXT,                 -- chrome | safari | firefox | other
    -- Anti-abuse: cap inserts per IP per minute
    minute_bucket   INTEGER GENERATED ALWAYS AS (CAST(sampled_at / 60 AS INTEGER)) VIRTUAL
);
CREATE INDEX IF NOT EXISTS idx_cwv_path_time ON cwv_samples(path, sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_cwv_metric_time ON cwv_samples(metric, sampled_at DESC);
CREATE INDEX IF NOT EXISTS idx_cwv_ip_minute ON cwv_samples(ip_hash, minute_bucket);
"""


# Per Google Core Web Vitals thresholds (Mar 2024):
THRESHOLDS = {
    "LCP":  {"good": 2500, "poor": 4000},   # ms
    "INP":  {"good": 200,  "poor": 500},    # ms
    "CLS":  {"good": 100,  "poor": 250},    # *1000 from unitless
    "TTFB": {"good": 800,  "poor": 1800},   # ms
    "FCP":  {"good": 1800, "poor": 3000},   # ms
}

VALID_METRICS = frozenset(THRESHOLDS.keys())


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    # `GENERATED ALWAYS AS` requires SQLite 3.31+; fall back gracefully.
    try:
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError:
        # Older SQLite — drop the generated column.
        fallback = SCHEMA.replace(
            "minute_bucket   INTEGER GENERATED ALWAYS AS (CAST(sampled_at / 60 AS INTEGER)) VIRTUAL",
            "minute_bucket   INTEGER",
        )
        conn.executescript(fallback)
    return conn


def migrate() -> None:
    with _conn():
        pass


# ============================================================================
# Recording
# ============================================================================

def _salt() -> str:
    """Hash salt — env-pinned so identical IPs hash the same across
    process restarts but no two deployments share hashes."""
    return (os.environ.get("PADHAI_RUM_SALT") or "padhai-rum-v1").encode().hex()


def _hash(value: str | None) -> str | None:
    if not value:
        return None
    h = hashlib.sha256()
    h.update((value + _salt()).encode("utf-8"))
    return h.hexdigest()[:32]


def _rate_capped(ip_hash: str | None) -> bool:
    """At most 200 beacons per IP per minute. Anti-abuse + cost-cap."""
    if not ip_hash:
        return True
    now_minute = int(time.time() / 60)
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM cwv_samples "
            "WHERE ip_hash = ? AND minute_bucket = ?",
            (ip_hash, now_minute),
        ).fetchone()
    return (r[0] if r else 0) < 200


def record_sample(
    *,
    path: str,
    metric: str,
    value_ms: float,
    rating: str | None = None,
    navigation_type: str | None = None,
    locale: str | None = None,
    device_class: str | None = None,
    user_id: str | None = None,
    user_tier: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str | None:
    """Insert one CWV sample. Returns row id, or None on rate-cap /
    invalid input. Never raises — this is a hot-path metric."""
    metric = (metric or "").upper()
    if metric not in VALID_METRICS:
        return None
    if value_ms is None or value_ms < 0 or value_ms > 600_000:
        return None  # bogus reading
    if not path or len(path) > 200:
        return None

    sample_id = uuid.uuid4().hex
    ip_h = _hash(ip)
    if not _rate_capped(ip_h):
        return None  # silently drop

    if not rating:
        t = THRESHOLDS.get(metric)
        if t:
            if value_ms <= t["good"]:
                rating = "good"
            elif value_ms <= t["poor"]:
                rating = "needs-improvement"
            else:
                rating = "poor"

    ua_class = _classify_ua(user_agent or "")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO cwv_samples "
                "(id, sampled_at, path, metric, value_ms, rating, "
                " navigation_type, locale, device_class, "
                " ip_hash, user_hash, user_tier, ua_class) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sample_id, time.time(), path[:200], metric,
                 float(value_ms), rating, navigation_type, locale,
                 device_class, ip_h, _hash(user_id),
                 user_tier or "anonymous", ua_class),
            )
    except Exception:
        return None
    return sample_id


def _classify_ua(ua: str) -> str:
    ua = ua.lower()
    if "chrome" in ua and "edg" not in ua:
        return "chrome"
    if "safari" in ua and "chrome" not in ua:
        return "safari"
    if "firefox" in ua:
        return "firefox"
    if "edg" in ua:
        return "edge"
    return "other"


# ============================================================================
# Stats
# ============================================================================

def percentile(values: list[float], p: float) -> float:
    """Simple percentile without numpy. p in [0, 100]."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def stats_for_period(
    *,
    hours: float = 24.0,
    path: str | None = None,
) -> dict:
    """Aggregate CWV for the last N hours. Returns p75 per metric,
    distribution of rating, and per-path breakdown."""
    since = time.time() - hours * 3600
    where = "sampled_at >= ?"
    params: list = [since]
    if path:
        where += " AND path = ?"
        params.append(path)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT metric, value_ms, rating FROM cwv_samples WHERE {where}",
            params,
        ).fetchall()
    by_metric: dict[str, list[float]] = {}
    by_rating: dict[str, dict[str, int]] = {}
    for metric, val, rating in rows:
        by_metric.setdefault(metric, []).append(val)
        by_rating.setdefault(metric, {"good": 0, "needs-improvement": 0, "poor": 0})
        by_rating[metric][rating or "needs-improvement"] = (
            by_rating[metric].get(rating or "needs-improvement", 0) + 1
        )
    metrics_out: dict[str, dict] = {}
    for m, vals in by_metric.items():
        metrics_out[m] = {
            "samples": len(vals),
            "p50": round(percentile(vals, 50), 1),
            "p75": round(percentile(vals, 75), 1),
            "p95": round(percentile(vals, 95), 1),
            "rating_dist": by_rating.get(m, {}),
            "good_pct": (
                by_rating.get(m, {}).get("good", 0) / max(1, len(vals))
            ),
        }
    return {
        "hours": hours,
        "path": path,
        "total_samples": len(rows),
        "by_metric": metrics_out,
    }


def stats_by_path(*, hours: float = 24.0, limit: int = 20) -> list[dict]:
    """Top paths by sample count, with p75 LCP each."""
    since = time.time() - hours * 3600
    with _conn() as conn:
        rows = conn.execute(
            "SELECT path, COUNT(*) FROM cwv_samples "
            "WHERE sampled_at >= ? GROUP BY path "
            "ORDER BY COUNT(*) DESC LIMIT ?",
            (since, limit),
        ).fetchall()
    out = []
    for path, count in rows:
        out.append({"path": path, "samples": count,
                    "lcp_p75": _path_metric_p75(path, "LCP", since)})
    return out


def _path_metric_p75(path: str, metric: str, since: float) -> float | None:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT value_ms FROM cwv_samples "
            "WHERE path = ? AND metric = ? AND sampled_at >= ?",
            (path, metric, since),
        ).fetchall()
    if not rows:
        return None
    return round(percentile([r[0] for r in rows], 75), 1)
