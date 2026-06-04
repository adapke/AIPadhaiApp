"""Q3 — Event stream + lightweight data warehouse.

Every user action → `events` table. Daily rollup job aggregates into
`daily_metrics` (DAU, MAU, retention, funnel, etc.). The eventual
production path routes through Kafka → BigQuery / Snowflake; we
ship the in-DB shape so the v2.3.x cutover is just a destination
change.

Two tables:
  events            — raw event log, indexed for cohort queries
  daily_metrics     — pre-aggregated rollups by (date, metric, dimensions)

Plus a tiny worker:
  rollup_yesterday()   — DAU + new-user + by-event-kind aggregations

Sampling: optional `PADHAI_EVENTS_SAMPLE_PCT` env var (default 100)
caps logged events. At 100M events/day we'd drop to 10%; for now
log everything to build a baseline.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    user_id       TEXT,
    org_id        TEXT,
    session_id    TEXT,
    kind          TEXT NOT NULL,     -- 'lesson.start', 'quiz.submit', 'tutor.message', ...
    props_json    TEXT,               -- arbitrary key/value
    source        TEXT,               -- 'web' | 'ios' | 'android' | 'system'
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user_time
    ON events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind_time
    ON events(kind, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_org_time
    ON events(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_date
    ON events(created_at);

CREATE TABLE IF NOT EXISTS daily_metrics (
    date          TEXT NOT NULL,        -- 'YYYY-MM-DD' UTC
    metric        TEXT NOT NULL,         -- 'dau' | 'new_users' | 'events.tutor.message' | ...
    dimensions    TEXT NOT NULL DEFAULT '',  -- JSON of grouping dims
    value         REAL NOT NULL,
    computed_at   REAL NOT NULL,
    PRIMARY KEY (date, metric, dimensions)
);
CREATE INDEX IF NOT EXISTS idx_dm_metric_date
    ON daily_metrics(metric, date DESC);
"""

# Valid event kinds — extensible. New kinds get added here when the
# feature ships so we have a canonical list for the dashboard.
KNOWN_KINDS = frozenset({
    # Auth
    "auth.signup", "auth.login.success", "auth.login.fail",
    "auth.logout",
    # Lesson lifecycle
    "lesson.requested", "lesson.start", "lesson.complete",
    "lesson.video.watch_25", "lesson.video.watch_50",
    "lesson.video.watch_75", "lesson.video.watch_100",
    # Quiz / practice
    "quiz.submit", "practice_test.start", "practice_test.submit",
    "essay.submit", "essay.graded",
    # Tutor (L1)
    "tutor.session.start", "tutor.message", "tutor.session.end",
    # Doubt clearing (M2)
    "doubt.submit", "doubt.claim", "doubt.answer",
    # Live (M1)
    "live_class.join", "live_class.leave",
    # Monetization
    "subscription.start", "subscription.renew", "subscription.cancel",
})


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


def _sample_pct() -> float:
    try:
        return max(0.0, min(100.0, float(
            os.environ.get("PADHAI_EVENTS_SAMPLE_PCT", "100"),
        )))
    except ValueError:
        return 100.0


# ---------- log path ----------

def log(
    *,
    kind: str,
    user_id: str | None = None,
    org_id: str | None = None,
    session_id: str | None = None,
    props: dict | None = None,
    source: str = "system",
) -> str | None:
    """Log one event. Swallows exceptions — analytics never blocks
    real work. Sampling: drops a fraction based on
    PADHAI_EVENTS_SAMPLE_PCT env var (default 100)."""
    pct = _sample_pct()
    if pct < 100.0 and random.random() * 100.0 >= pct:
        return None
    if not kind or len(kind) > 80:
        return None
    eid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO events "
                "(id, user_id, org_id, session_id, kind, props_json, "
                " source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, user_id, org_id, session_id, kind,
                 json.dumps(props) if props else None,
                 source, time.time()),
            )
    except Exception as e:  # noqa: BLE001
        print(f"[analytics] log failed (non-fatal): {e}")
        return None
    return eid


def log_batch(events_iter) -> int:
    """Bulk log path — useful when a client sends a batched beacon."""
    rows = []
    now = time.time()
    for e in events_iter:
        kind = (e.get("kind") or "")[:80]
        if not kind:
            continue
        rows.append((
            uuid.uuid4().hex,
            e.get("user_id"), e.get("org_id"), e.get("session_id"),
            kind,
            json.dumps(e.get("props")) if e.get("props") else None,
            (e.get("source") or "system")[:32],
            now,
        ))
    if not rows:
        return 0
    try:
        with _conn() as conn:
            conn.executemany(
                "INSERT INTO events "
                "(id, user_id, org_id, session_id, kind, props_json, "
                " source, created_at) VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
    except Exception as e:  # noqa: BLE001
        print(f"[analytics] log_batch failed (non-fatal): {e}")
        return 0
    return len(rows)


# ---------- read / aggregation ----------

def dau(*, date: str | None = None) -> int:
    """Distinct user_ids with at least one event on that UTC date."""
    if date is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
    start, end = _date_bounds(date)
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events "
            "WHERE user_id IS NOT NULL "
            "AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()
    return int(r[0] or 0)


def mau(*, date: str | None = None) -> int:
    """Distinct user_ids over the trailing 30 UTC days ending on
    `date`."""
    if date is None:
        date = datetime.now(UTC).strftime("%Y-%m-%d")
    end_start, end_end = _date_bounds(date)
    start = end_end - 30 * 86400
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events "
            "WHERE user_id IS NOT NULL "
            "AND created_at >= ? AND created_at < ?",
            (start, end_end),
        ).fetchone()
    return int(r[0] or 0)


def event_count_by_kind(*, hours: float = 24.0) -> dict[str, int]:
    since = time.time() - hours * 3600
    with _conn() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) FROM events "
            "WHERE created_at >= ? GROUP BY kind ORDER BY 2 DESC",
            (since,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def funnel(steps: list[str], *, hours: float = 24.0) -> dict:
    """Sequential funnel: distinct users who hit step[0], then step[1],
    etc. (in that order, by time). Steps are event kinds.

    Returns {step: count, ...} plus pct-of-previous.
    """
    if not steps:
        return {"steps": [], "counts": [], "pct": []}
    since = time.time() - hours * 3600
    counts = []
    prev_users: set[str] = set()
    with _conn() as conn:
        for i, step in enumerate(steps):
            sql = (
                "SELECT DISTINCT user_id FROM events "
                "WHERE kind = ? AND user_id IS NOT NULL "
                "AND created_at >= ?"
            )
            params: list = [step, since]
            if i > 0:
                placeholders = ",".join("?" * len(prev_users))
                sql += f" AND user_id IN ({placeholders})"
                params.extend(prev_users)
            users = {r[0] for r in conn.execute(sql, params).fetchall()}
            counts.append(len(users))
            prev_users = users
            if not prev_users:
                # Pad remaining steps with zero
                counts.extend([0] * (len(steps) - i - 1))
                break
    pct = []
    for i, c in enumerate(counts):
        if i == 0:
            pct.append(1.0 if c else 0.0)
        elif counts[i - 1]:
            pct.append(c / counts[i - 1])
        else:
            pct.append(0.0)
    return {"steps": steps, "counts": counts, "pct": pct, "hours": hours}


def retention_d1_d7_d30(*, cohort_date: str) -> dict:
    """Pct of users who signed up on `cohort_date` and returned on
    day +1 / +7 / +30 (any event with their user_id)."""
    start, end = _date_bounds(cohort_date)
    with _conn() as conn:
        signups = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT user_id FROM events "
                "WHERE kind = 'auth.signup' "
                "AND user_id IS NOT NULL "
                "AND created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchall()
        }
        if not signups:
            return {"cohort_date": cohort_date, "size": 0,
                    "d1_pct": 0, "d7_pct": 0, "d30_pct": 0}
        placeholders = ",".join("?" * len(signups))

        def returned_pct(day_offset: int) -> float:
            d_start = start + day_offset * 86400
            d_end = d_start + 86400
            users = {
                r[0] for r in conn.execute(
                    f"SELECT DISTINCT user_id FROM events "
                    f"WHERE user_id IN ({placeholders}) "
                    f"AND created_at >= ? AND created_at < ?",
                    (*signups, d_start, d_end),
                ).fetchall()
            }
            return len(users) / len(signups)
    return {
        "cohort_date": cohort_date,
        "size": len(signups),
        "d1_pct": round(returned_pct(1), 4),
        "d7_pct": round(returned_pct(7), 4),
        "d30_pct": round(returned_pct(30), 4),
    }


# ---------- daily rollup worker ----------

def rollup_for_date(date: str) -> dict:
    """Compute + persist the standard set of daily metrics for one
    UTC date. Idempotent on (date, metric, dimensions). Cron runs
    this for yesterday."""
    start, end = _date_bounds(date)
    persisted = {}
    now = time.time()
    with _conn() as conn:
        # DAU
        dau_v = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events "
            "WHERE user_id IS NOT NULL "
            "AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()[0]
        _upsert_metric(conn, date, "dau", "", float(dau_v), now)
        persisted["dau"] = dau_v

        # New users (auth.signup events)
        nu_v = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events "
            "WHERE kind = 'auth.signup' AND user_id IS NOT NULL "
            "AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()[0]
        _upsert_metric(conn, date, "new_users", "", float(nu_v), now)
        persisted["new_users"] = nu_v

        # Event counts by kind
        by_kind = conn.execute(
            "SELECT kind, COUNT(*) FROM events "
            "WHERE created_at >= ? AND created_at < ? GROUP BY kind",
            (start, end),
        ).fetchall()
        for kind, count in by_kind:
            _upsert_metric(
                conn, date, f"events.{kind}", "", float(count), now,
            )
        persisted["events_by_kind"] = {k: c for k, c in by_kind}

    return {"date": date, "metrics_count": 2 + len(persisted.get("events_by_kind", {})),
            "persisted": persisted}


def rollup_yesterday() -> dict:
    """Convenience for the cron: yesterday in UTC."""
    yest = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    return rollup_for_date(yest)


def _upsert_metric(
    conn: sqlite3.Connection,
    date: str, metric: str, dimensions: str,
    value: float, now: float,
) -> None:
    conn.execute(
        "INSERT INTO daily_metrics (date, metric, dimensions, value, computed_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(date, metric, dimensions) DO UPDATE SET "
        " value = excluded.value, computed_at = excluded.computed_at",
        (date, metric, dimensions, value, now),
    )


def get_metric_series(*, metric: str, days: int = 30) -> list[dict]:
    """Time series for one metric, last N days. Drives the dashboard
    line charts."""
    days = max(1, min(days, 365))
    today = datetime.now(UTC).date()
    dates = [
        (today - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        for i in range(days)
    ]
    with _conn() as conn:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT date, value FROM daily_metrics "
            f"WHERE metric = ? AND date IN ({placeholders}) "
            f"ORDER BY date",
            (metric, *dates),
        ).fetchall()
    series = {r[0]: r[1] for r in rows}
    return [
        {"date": d, "value": series.get(d, 0.0)} for d in dates
    ]


# ---------- helpers ----------

def _date_bounds(date: str) -> tuple[float, float]:
    """Returns (start_ts, end_ts) for the UTC day in `date`
    ('YYYY-MM-DD')."""
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
    start = d.timestamp()
    end = (d + timedelta(days=1)).timestamp()
    return start, end


@dataclass(frozen=True)
class Event:
    id: str
    user_id: str | None
    org_id: str | None
    session_id: str | None
    kind: str
    props: dict | None
    source: str
    created_at: float


def recent_events(*, user_id: str | None = None, limit: int = 100) -> list[Event]:
    """Diagnostic / debug path. Most-recent events for one user (or
    everyone). Not for hot-path use."""
    limit = max(1, min(limit, 1000))
    with _conn() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT id, user_id, org_id, session_id, kind, "
                "       props_json, source, created_at "
                "FROM events WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, user_id, org_id, session_id, kind, "
                "       props_json, source, created_at "
                "FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        Event(
            id=r[0], user_id=r[1], org_id=r[2], session_id=r[3],
            kind=r[4],
            props=json.loads(r[5]) if r[5] else None,
            source=r[6], created_at=r[7],
        )
        for r in rows
    ]
