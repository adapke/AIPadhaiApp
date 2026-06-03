"""J5 — Adaptive difficulty engine.

Per-student topic mastery model. Tracks how well each user knows
each topic (e.g. 'photosynthesis', 'quadratic_equations') based on
quiz / practice outcomes. `build_profile()` uses these scores to:
- Auto-skew next lesson 'easier' / 'standard' / 'advanced'
- Inject prerequisite-recap sections when mastery on a dependency
  is low
- Surface 'weak topics' on the student dashboard

Algorithm: lightweight EWMA (exponential weighted moving average)
rather than full Bayesian Knowledge Tracing — easier to debug, fast
enough at 100k students. Each correct/incorrect signal updates the
mastery score with α=0.3 (so the last ~3-4 attempts dominate).

Schema:
  user_topic_mastery   one row per (user_id, topic_key)
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS user_topic_mastery (
    user_id     TEXT NOT NULL,
    topic_key   TEXT NOT NULL,
    mastery     REAL NOT NULL,        -- 0.0 .. 1.0
    attempts    INTEGER NOT NULL,
    correct     INTEGER NOT NULL,
    last_seen   REAL NOT NULL,
    PRIMARY KEY (user_id, topic_key)
);
CREATE INDEX IF NOT EXISTS idx_mastery_user ON user_topic_mastery(user_id, mastery);
"""

# EWMA smoothing factor. α=0.3 means each new signal moves the
# mastery score by ~30% — last few attempts dominate, but old
# attempts still leave a trace. Tunable per-cohort during pilot.
ALPHA = 0.3

# Difficulty tier from mastery score.
LOW_THRESHOLD = 0.4
HIGH_THRESHOLD = 0.75


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


@dataclass(frozen=True)
class TopicMastery:
    user_id: str
    topic_key: str
    mastery: float
    attempts: int
    correct: int
    last_seen: float


def update(*, user_id: str, topic_key: str, correct: bool) -> TopicMastery:
    """Record one quiz/practice outcome + update EWMA. Returns the
    new mastery state."""
    signal = 1.0 if correct else 0.0
    with _conn() as conn:
        r = conn.execute(
            "SELECT mastery, attempts, correct FROM user_topic_mastery "
            "WHERE user_id = ? AND topic_key = ?",
            (user_id, topic_key),
        ).fetchone()
        if r:
            old_mastery, attempts, correct_count = r
            new_mastery = (1 - ALPHA) * old_mastery + ALPHA * signal
        else:
            attempts = 0
            correct_count = 0
            new_mastery = signal  # first signal IS the score
        attempts += 1
        if correct:
            correct_count += 1
        now = time.time()
        conn.execute(
            "INSERT INTO user_topic_mastery "
            "(user_id, topic_key, mastery, attempts, correct, last_seen) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(user_id, topic_key) DO UPDATE SET "
            " mastery = excluded.mastery, "
            " attempts = excluded.attempts, "
            " correct = excluded.correct, "
            " last_seen = excluded.last_seen",
            (user_id, topic_key, new_mastery, attempts, correct_count, now),
        )
    return TopicMastery(
        user_id=user_id, topic_key=topic_key, mastery=new_mastery,
        attempts=attempts, correct=correct_count, last_seen=now,
    )


def get(*, user_id: str, topic_key: str) -> TopicMastery | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_id, topic_key, mastery, attempts, correct, last_seen "
            "FROM user_topic_mastery WHERE user_id = ? AND topic_key = ?",
            (user_id, topic_key),
        ).fetchone()
    if not r:
        return None
    return TopicMastery(
        user_id=r[0], topic_key=r[1], mastery=r[2],
        attempts=r[3], correct=r[4], last_seen=r[5],
    )


def list_for_user(user_id: str, *, limit: int = 100) -> list[TopicMastery]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, topic_key, mastery, attempts, correct, last_seen "
            "FROM user_topic_mastery WHERE user_id = ? "
            "ORDER BY last_seen DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [
        TopicMastery(
            user_id=r[0], topic_key=r[1], mastery=r[2],
            attempts=r[3], correct=r[4], last_seen=r[5],
        )
        for r in rows
    ]


def recommend_difficulty(*, user_id: str, topic_key: str) -> str:
    """Returns 'easier' | 'standard' | 'advanced' based on the user's
    mastery on this topic. Falls back to 'standard' when no prior
    attempts."""
    m = get(user_id=user_id, topic_key=topic_key)
    if m is None or m.attempts < 2:
        return "standard"
    if m.mastery < LOW_THRESHOLD:
        return "easier"
    if m.mastery > HIGH_THRESHOLD:
        return "advanced"
    return "standard"


def weak_topics(*, user_id: str, threshold: float = LOW_THRESHOLD,
                min_attempts: int = 2) -> list[TopicMastery]:
    """Topics where mastery is below threshold + at least N attempts.
    Drives the 'review these' card on the student dashboard."""
    return [
        m for m in list_for_user(user_id, limit=500)
        if m.attempts >= min_attempts and m.mastery < threshold
    ]


def strong_topics(*, user_id: str, threshold: float = HIGH_THRESHOLD,
                  min_attempts: int = 2) -> list[TopicMastery]:
    return [
        m for m in list_for_user(user_id, limit=500)
        if m.attempts >= min_attempts and m.mastery > threshold
    ]
