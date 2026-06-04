"""I4 — Daily streaks + XP + leaderboards.

Engagement layer. Studies show daily-streak nudges + visible XP move
DAU/MAU from 0.15 (avg India edtech) to 0.35+ when calibrated right.

Three tables (additive):
  user_streaks   one row per user — current_streak, longest, level, XP
  xp_events      ledger of every XP grant — auditable + replay-able

Optional opt-out per user — under-13 default opt-IN (still motivating)
but the org admin can disable globally for schools that disapprove.

XP rule catalog (tunable in code, not yet a DB table — v1.5.x):
  lesson_done        +10 XP
  quiz_perfect       +25 XP (100% score)
  quiz_passed        +15 XP (>= 70%)
  streak_7           +50 XP bonus
  streak_30          +200 XP bonus
  attendance_marked  +5 XP per day present
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_streaks (
    user_id           TEXT PRIMARY KEY,
    current_streak    INTEGER NOT NULL DEFAULT 0,
    longest_streak    INTEGER NOT NULL DEFAULT 0,
    last_active_date  TEXT,                    -- YYYY-MM-DD UTC
    xp_total          INTEGER NOT NULL DEFAULT 0,
    level             INTEGER NOT NULL DEFAULT 1,
    opted_in          INTEGER NOT NULL DEFAULT 1,
    updated_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_events (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,
    xp_amount       INTEGER NOT NULL,
    context_json    TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xp_user_time ON xp_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xp_kind_time ON xp_events(kind, created_at DESC);
"""

XP_RULES = {
    "lesson_done": 10,
    "quiz_perfect": 25,
    "quiz_passed": 15,
    "streak_7": 50,
    "streak_30": 200,
    "attendance_marked": 5,
}

# Level thresholds — leveling is intentionally non-linear so early
# levels are quick wins, later ones feel earned.
LEVEL_THRESHOLDS = [0, 100, 300, 600, 1000, 1600, 2500, 3800, 5500, 8000]


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


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_utc() -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _level_for_xp(xp: int) -> int:
    for i in range(len(LEVEL_THRESHOLDS) - 1, -1, -1):
        if xp >= LEVEL_THRESHOLDS[i]:
            return i + 1
    return 1


@dataclass(frozen=True)
class UserStreak:
    user_id: str
    current_streak: int
    longest_streak: int
    last_active_date: str | None
    xp_total: int
    level: int
    opted_in: bool


def get_streak(user_id: str) -> UserStreak:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_id, current_streak, longest_streak, "
            "       last_active_date, xp_total, level, opted_in "
            "FROM user_streaks WHERE user_id = ?", (user_id,),
        ).fetchone()
    if not r:
        return UserStreak(
            user_id=user_id, current_streak=0, longest_streak=0,
            last_active_date=None, xp_total=0, level=1, opted_in=True,
        )
    return UserStreak(
        user_id=r[0], current_streak=r[1], longest_streak=r[2],
        last_active_date=r[3], xp_total=r[4], level=r[5],
        opted_in=bool(r[6]),
    )


def award(*, user_id: str, kind: str, xp_amount: int | None = None,
          context: dict | None = None) -> UserStreak:
    """Append an XP event + update the user's totals + maybe extend
    the streak. Idempotency is the caller's problem (we don't dedupe
    on `kind` because the same kind can fire many times per day —
    lesson_done for different lessons, etc.)."""
    import json as _json
    amount = xp_amount if xp_amount is not None else XP_RULES.get(kind, 0)
    if amount < 0:
        raise ValueError("xp_amount must be non-negative")
    now = time.time()
    today = _today_utc()
    with _conn() as conn:
        # Ledger insert (always)
        conn.execute(
            "INSERT INTO xp_events (id, user_id, kind, xp_amount, "
            "context_json, created_at) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, user_id, kind, amount,
             _json.dumps(context) if context else None, now),
        )
        # Existing streak row?
        r = conn.execute(
            "SELECT current_streak, longest_streak, last_active_date, "
            "       xp_total, opted_in "
            "FROM user_streaks WHERE user_id = ?", (user_id,),
        ).fetchone()
        if r:
            current, longest, last_date, xp_total, opted_in = r
        else:
            current, longest, last_date, xp_total, opted_in = 0, 0, None, 0, 1
        # Streak math — track whether THIS call moved the streak forward
        # so we only fire milestone bonuses on the transition, not on
        # every subsequent same-day signal.
        streak_advanced = False
        if last_date == today:
            pass  # already active today; streak unchanged
        elif last_date == _yesterday_utc():
            current += 1
            streak_advanced = True
        else:
            current = 1
            streak_advanced = True  # day-0 of a new streak
        if current > longest:
            longest = current
        xp_total += amount
        level = _level_for_xp(xp_total)
        # Upsert
        conn.execute(
            "INSERT INTO user_streaks "
            "(user_id, current_streak, longest_streak, last_active_date, "
            " xp_total, level, opted_in, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            " current_streak = excluded.current_streak, "
            " longest_streak = excluded.longest_streak, "
            " last_active_date = excluded.last_active_date, "
            " xp_total = excluded.xp_total, "
            " level = excluded.level, "
            " updated_at = excluded.updated_at",
            (user_id, current, longest, today, xp_total, level,
             opted_in, now),
        )
    # Bonus only on the transition into the milestone — second
    # check against xp_events guarantees idempotency even across
    # process restarts (rare race: two writes complete the same day).
    if streak_advanced and current == 7 and kind != "streak_7":
        if not _already_awarded_today(user_id, "streak_7"):
            return award(user_id=user_id, kind="streak_7")
    if streak_advanced and current == 30 and kind != "streak_30":
        if not _already_awarded_today(user_id, "streak_30"):
            return award(user_id=user_id, kind="streak_30")
    return get_streak(user_id)


def _already_awarded_today(user_id: str, kind: str) -> bool:
    """Defence-in-depth: even if streak_advanced flag is wrong, never
    grant the same milestone twice on the same UTC day."""
    since = time.time() - 86400
    with _conn() as conn:
        r = conn.execute(
            "SELECT 1 FROM xp_events WHERE user_id = ? AND kind = ? "
            "AND created_at >= ? LIMIT 1",
            (user_id, kind, since),
        ).fetchone()
    return r is not None


def set_opted_in(*, user_id: str, opted_in: bool) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO user_streaks (user_id, opted_in, updated_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET opted_in = excluded.opted_in, "
            " updated_at = excluded.updated_at",
            (user_id, 1 if opted_in else 0, time.time()),
        )


def leaderboard(*, scope_user_ids: list[str], period: str = "alltime",
                limit: int = 50) -> list[dict]:
    """Top N users by XP within the given user set (typically a class
    or grade). Period: 'week' | 'month' | 'alltime'."""
    if not scope_user_ids:
        return []
    limit = max(1, min(limit, 200))
    if period == "alltime":
        # Direct from user_streaks
        placeholders = ",".join("?" * len(scope_user_ids))
        with _conn() as conn:
            rows = conn.execute(
                f"SELECT user_id, xp_total, level, current_streak "
                f"FROM user_streaks WHERE user_id IN ({placeholders}) "
                f"ORDER BY xp_total DESC LIMIT ?",
                (*scope_user_ids, limit),
            ).fetchall()
        return [
            {"user_id": r[0], "xp": r[1], "level": r[2],
             "current_streak": r[3]}
            for r in rows
        ]
    # Period-bounded: aggregate from xp_events
    from datetime import timedelta
    if period == "week":
        since = time.time() - 7 * 86400
    elif period == "month":
        since = time.time() - 30 * 86400
    else:
        raise ValueError("period must be week | month | alltime")
    placeholders = ",".join("?" * len(scope_user_ids))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT user_id, SUM(xp_amount) AS xp "
            f"FROM xp_events WHERE created_at >= ? "
            f"AND user_id IN ({placeholders}) "
            f"GROUP BY user_id ORDER BY xp DESC LIMIT ?",
            (since, *scope_user_ids, limit),
        ).fetchall()
    return [{"user_id": r[0], "xp": int(r[1] or 0)} for r in rows]


def calendar_heatmap(*, user_id: str, days: int = 90) -> dict[str, int]:
    """GitHub-style heatmap: {YYYY-MM-DD: xp_earned}. Used by the
    profile-page calendar widget."""
    days = max(1, min(days, 365))
    since = time.time() - days * 86400
    with _conn() as conn:
        rows = conn.execute(
            "SELECT created_at, xp_amount FROM xp_events "
            "WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ).fetchall()
    heatmap: dict[str, int] = {}
    for ts, amt in rows:
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        heatmap[d] = heatmap.get(d, 0) + (amt or 0)
    return heatmap
