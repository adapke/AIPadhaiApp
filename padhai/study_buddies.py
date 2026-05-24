"""N3 — Study-buddy matching.

Algorithm pairs students with **similar mastery + complementary weak
areas** (so they can teach each other) + overlapping study schedule.
Pairs unlock joint quiz challenges + a simple chat channel.

Three tables:
  buddy_profiles   per-user opt-in profile (exam, grade, language,
                   available_windows, weak_topics)
  buddy_pairs      matched pairs with status (proposed | active | dissolved)
  buddy_messages   simple chat between paired users

Matching scoring (higher = better):
  +3  same exam track
  +2  same grade
  +1  shared language
  +2  complementary weak topics (each user's weak ∈ other's strong)
  +1  overlapping available_windows
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS buddy_profiles (
    user_id           TEXT PRIMARY KEY,
    exam              TEXT,                    -- 'upsc' | 'jee_main' | 'neet' | 'class10' | ...
    grade             INTEGER,
    language          TEXT NOT NULL DEFAULT 'en',
    study_hours_week  INTEGER NOT NULL DEFAULT 10,
    available_windows TEXT,                     -- JSON: ['evening', 'weekend']
    bio               TEXT,
    opted_in          INTEGER NOT NULL DEFAULT 1,
    updated_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bp_exam_grade
    ON buddy_profiles(exam, grade, opted_in);

CREATE TABLE IF NOT EXISTS buddy_pairs (
    id              TEXT PRIMARY KEY,
    user_a_id       TEXT NOT NULL,
    user_b_id       TEXT NOT NULL,
    score           REAL NOT NULL,
    matched_at      REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'proposed',
    -- proposed | active | dissolved | declined
    accepted_a_at   REAL,
    accepted_b_at   REAL,
    dissolved_at    REAL,
    last_interaction_at REAL,
    UNIQUE (user_a_id, user_b_id)
);
CREATE INDEX IF NOT EXISTS idx_pair_a ON buddy_pairs(user_a_id, status);
CREATE INDEX IF NOT EXISTS idx_pair_b ON buddy_pairs(user_b_id, status);

CREATE TABLE IF NOT EXISTS buddy_messages (
    id              TEXT PRIMARY KEY,
    pair_id         TEXT NOT NULL,
    sender_user_id  TEXT NOT NULL,
    body            TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bmsg_pair
    ON buddy_messages(pair_id, created_at DESC);
"""


VALID_WINDOWS = frozenset({
    "morning", "afternoon", "evening", "night", "weekend",
})

VALID_STATUSES = frozenset({
    "proposed", "active", "dissolved", "declined",
})


def _db_path() -> Path:
    custom = os.environ.get("PADHAI_DB_PATH")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "jobs.db"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


# ---------- profiles ----------

@dataclass(frozen=True)
class Profile:
    user_id: str
    exam: str | None
    grade: int | None
    language: str
    study_hours_week: int
    available_windows: list[str]
    bio: str | None
    opted_in: bool
    updated_at: float


def upsert_profile(
    *,
    user_id: str,
    exam: str | None = None,
    grade: int | None = None,
    language: str = "en",
    study_hours_week: int = 10,
    available_windows: list[str] | None = None,
    bio: str | None = None,
    opted_in: bool = True,
) -> Profile:
    if available_windows:
        bad = [w for w in available_windows if w not in VALID_WINDOWS]
        if bad:
            raise ValueError(
                f"available_windows entries must be in "
                f"{sorted(VALID_WINDOWS)}, got bad: {bad}"
            )
    if grade is not None and (grade < 1 or grade > 16):
        raise ValueError("grade must be in [1, 16]")
    if study_hours_week < 0 or study_hours_week > 168:
        raise ValueError("study_hours_week must be in [0, 168]")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO buddy_profiles "
            "(user_id, exam, grade, language, study_hours_week, "
            " available_windows, bio, opted_in, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            " exam = excluded.exam, grade = excluded.grade, "
            " language = excluded.language, "
            " study_hours_week = excluded.study_hours_week, "
            " available_windows = excluded.available_windows, "
            " bio = excluded.bio, opted_in = excluded.opted_in, "
            " updated_at = excluded.updated_at",
            (user_id, exam, grade, language, study_hours_week,
             json.dumps(available_windows) if available_windows else None,
             bio, 1 if opted_in else 0, time.time()),
        )
    return get_profile(user_id)  # type: ignore[return-value]


def get_profile(user_id: str) -> Profile | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_id, exam, grade, language, study_hours_week, "
            "       available_windows, bio, opted_in, updated_at "
            "FROM buddy_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not r:
        return None
    return Profile(
        user_id=r[0], exam=r[1], grade=r[2], language=r[3],
        study_hours_week=r[4],
        available_windows=json.loads(r[5]) if r[5] else [],
        bio=r[6], opted_in=bool(r[7]), updated_at=r[8],
    )


def opt_out(user_id: str) -> bool:
    """Toggle opted_in=0 + dissolve any active pairs the user has."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE buddy_profiles SET opted_in = 0, updated_at = ? "
            "WHERE user_id = ?",
            (time.time(), user_id),
        )
        now = time.time()
        conn.execute(
            "UPDATE buddy_pairs SET status = 'dissolved', "
            " dissolved_at = ? "
            "WHERE (user_a_id = ? OR user_b_id = ?) "
            "AND status IN ('proposed', 'active')",
            (now, user_id, user_id),
        )
        return cur.rowcount > 0


# ---------- matching ----------

@dataclass(frozen=True)
class Match:
    candidate_user_id: str
    score: float
    reasons: list[str]


def find_matches(
    *,
    user_id: str,
    limit: int = 5,
) -> list[Match]:
    """Find top-N candidates for `user_id`. Excludes users already
    in a non-dissolved pair with the requester + anyone opted out."""
    me = get_profile(user_id)
    if not me or not me.opted_in:
        return []
    # Topic mastery info (J5) for the complementary-weak-topics score
    try:
        from . import mastery
        my_weak = {m.topic_key for m in mastery.weak_topics(user_id=user_id)}
        my_strong = {m.topic_key for m in mastery.strong_topics(user_id=user_id)}
    except Exception:  # noqa: BLE001
        my_weak = set()
        my_strong = set()

    # Build candidate pool: same exam + grade + opted in, exclude
    # anyone already paired
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, exam, grade, language, study_hours_week, "
            "       available_windows, bio "
            "FROM buddy_profiles "
            "WHERE user_id != ? AND opted_in = 1 "
            "AND (exam = ? OR exam IS NULL OR ? IS NULL) "
            "LIMIT 500",
            (user_id, me.exam, me.exam),
        ).fetchall()
        # Already-paired user_ids (any status except dissolved/declined)
        paired_rows = conn.execute(
            "SELECT user_a_id, user_b_id FROM buddy_pairs "
            "WHERE (user_a_id = ? OR user_b_id = ?) "
            "AND status IN ('proposed', 'active')",
            (user_id, user_id),
        ).fetchall()
    excluded = set()
    for a, b in paired_rows:
        excluded.add(a); excluded.add(b)

    matches: list[Match] = []
    for r in rows:
        cand_uid = r[0]
        if cand_uid in excluded:
            continue
        cand_exam, cand_grade, cand_lang = r[1], r[2], r[3]
        cand_windows = json.loads(r[5]) if r[5] else []

        score = 0.0
        reasons = []
        if me.exam and cand_exam and me.exam == cand_exam:
            score += 3; reasons.append("same exam")
        if me.grade is not None and cand_grade == me.grade:
            score += 2; reasons.append("same grade")
        if me.language and cand_lang and me.language == cand_lang:
            score += 1; reasons.append("same language")
        # Complementary topics — fetch candidate's strengths
        try:
            from . import mastery
            cand_strong = {
                m.topic_key
                for m in mastery.strong_topics(user_id=cand_uid)
            }
            cand_weak = {
                m.topic_key
                for m in mastery.weak_topics(user_id=cand_uid)
            }
            # Each user's weak overlaps the other's strong → teach each other
            overlap = (my_weak & cand_strong) | (cand_weak & my_strong)
            if overlap:
                score += 2
                reasons.append(f"complementary topics ({len(overlap)})")
        except Exception:  # noqa: BLE001
            pass
        # Window overlap
        if me.available_windows and cand_windows:
            common = set(me.available_windows) & set(cand_windows)
            if common:
                score += 1
                reasons.append(f"overlap windows: {','.join(sorted(common))}")
        if score <= 0:
            continue
        matches.append(Match(
            candidate_user_id=cand_uid,
            score=score, reasons=reasons,
        ))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:max(1, min(limit, 50))]


# ---------- pairs ----------

@dataclass(frozen=True)
class Pair:
    id: str
    user_a_id: str
    user_b_id: str
    score: float
    matched_at: float
    status: str
    accepted_a_at: float | None
    accepted_b_at: float | None
    last_interaction_at: float | None


def _canonical(a: str, b: str) -> tuple[str, str]:
    return tuple(sorted((a, b)))  # type: ignore[return-value]


def propose_pair(
    *,
    user_a_id: str,
    user_b_id: str,
    score: float = 0.0,
) -> Pair:
    """Create a proposed pair. `user_a_id` is canonically the lexically
    smaller id so duplicate proposals collapse via UNIQUE."""
    if user_a_id == user_b_id:
        raise ValueError("cannot pair user with self")
    a, b = _canonical(user_a_id, user_b_id)
    now = time.time()
    pid = uuid.uuid4().hex
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM buddy_pairs "
            "WHERE user_a_id = ? AND user_b_id = ?",
            (a, b),
        ).fetchone()
        if existing:
            # If a dissolved pair exists, re-propose a fresh one.
            if existing[1] in ("dissolved", "declined"):
                conn.execute(
                    "UPDATE buddy_pairs SET "
                    " status = 'proposed', score = ?, matched_at = ?, "
                    " accepted_a_at = NULL, accepted_b_at = NULL, "
                    " dissolved_at = NULL "
                    "WHERE id = ?",
                    (score, now, existing[0]),
                )
                return _get_pair_by_id(existing[0])
            # Otherwise return the existing live one
            return _get_pair_by_id(existing[0])
        conn.execute(
            "INSERT INTO buddy_pairs "
            "(id, user_a_id, user_b_id, score, matched_at, status) "
            "VALUES (?, ?, ?, ?, ?, 'proposed')",
            (pid, a, b, score, now),
        )
    return _get_pair_by_id(pid)


def _get_pair_by_id(pair_id: str) -> Pair:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_a_id, user_b_id, score, matched_at, "
            "       status, accepted_a_at, accepted_b_at, "
            "       last_interaction_at "
            "FROM buddy_pairs WHERE id = ?", (pair_id,),
        ).fetchone()
    if not r:
        raise ValueError(f"pair {pair_id!r} not found")
    return Pair(
        id=r[0], user_a_id=r[1], user_b_id=r[2], score=r[3],
        matched_at=r[4], status=r[5],
        accepted_a_at=r[6], accepted_b_at=r[7],
        last_interaction_at=r[8],
    )


def accept_pair(*, pair_id: str, user_id: str) -> Pair:
    """A proposed pair becomes 'active' only after BOTH sides accept.
    Idempotent — re-accepting from the same side is a no-op."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_a_id, user_b_id, status, "
            "       accepted_a_at, accepted_b_at "
            "FROM buddy_pairs WHERE id = ?", (pair_id,),
        ).fetchone()
        if not r:
            raise ValueError(f"pair {pair_id!r} not found")
        _, ua, ub, status, acc_a, acc_b = r
        if user_id not in (ua, ub):
            raise PermissionError("not your pair")
        if status not in ("proposed",):
            raise ValueError(f"pair is {status!r}, can't accept")
        now = time.time()
        if user_id == ua and acc_a is None:
            conn.execute(
                "UPDATE buddy_pairs SET accepted_a_at = ? WHERE id = ?",
                (now, pair_id),
            )
            acc_a = now
        elif user_id == ub and acc_b is None:
            conn.execute(
                "UPDATE buddy_pairs SET accepted_b_at = ? WHERE id = ?",
                (now, pair_id),
            )
            acc_b = now
        if acc_a is not None and acc_b is not None:
            conn.execute(
                "UPDATE buddy_pairs SET status = 'active' WHERE id = ?",
                (pair_id,),
            )
    return _get_pair_by_id(pair_id)


def decline_pair(*, pair_id: str, user_id: str) -> bool:
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_a_id, user_b_id, status "
            "FROM buddy_pairs WHERE id = ?", (pair_id,),
        ).fetchone()
        if not r:
            return False
        ua, ub, status = r
        if user_id not in (ua, ub):
            return False
        if status not in ("proposed",):
            return False
        conn.execute(
            "UPDATE buddy_pairs SET status = 'declined', "
            " dissolved_at = ? WHERE id = ?",
            (time.time(), pair_id),
        )
    return True


def dissolve_pair(*, pair_id: str, user_id: str) -> bool:
    """Either party can dissolve an active pair at any time."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT user_a_id, user_b_id, status "
            "FROM buddy_pairs WHERE id = ?", (pair_id,),
        ).fetchone()
        if not r:
            return False
        ua, ub, status = r
        if user_id not in (ua, ub):
            return False
        if status not in ("proposed", "active"):
            return False
        conn.execute(
            "UPDATE buddy_pairs SET status = 'dissolved', "
            " dissolved_at = ? WHERE id = ?",
            (time.time(), pair_id),
        )
    return True


def my_pairs(user_id: str, *, statuses: list[str] | None = None) -> list[Pair]:
    statuses = statuses or ["proposed", "active"]
    bad = [s for s in statuses if s not in VALID_STATUSES]
    if bad:
        raise ValueError(f"unknown statuses: {bad}")
    placeholders = ",".join("?" * len(statuses))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT id, user_a_id, user_b_id, score, matched_at, "
            f"       status, accepted_a_at, accepted_b_at, "
            f"       last_interaction_at "
            f"FROM buddy_pairs "
            f"WHERE (user_a_id = ? OR user_b_id = ?) "
            f"AND status IN ({placeholders}) "
            f"ORDER BY matched_at DESC",
            (user_id, user_id, *statuses),
        ).fetchall()
    return [
        Pair(
            id=r[0], user_a_id=r[1], user_b_id=r[2], score=r[3],
            matched_at=r[4], status=r[5],
            accepted_a_at=r[6], accepted_b_at=r[7],
            last_interaction_at=r[8],
        )
        for r in rows
    ]


# ---------- messages ----------

@dataclass(frozen=True)
class Message:
    id: str
    pair_id: str
    sender_user_id: str
    body: str
    created_at: float


def send_message(
    *,
    pair_id: str,
    sender_user_id: str,
    body: str,
) -> Message:
    body = (body or "").strip()
    if len(body) < 1 or len(body) > 4000:
        raise ValueError("body must be [1, 4000] chars")
    pair = _get_pair_by_id(pair_id)
    if sender_user_id not in (pair.user_a_id, pair.user_b_id):
        raise PermissionError("not your pair")
    if pair.status != "active":
        raise ValueError(f"pair is {pair.status!r}, can't send")
    mid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO buddy_messages "
            "(id, pair_id, sender_user_id, body, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, pair_id, sender_user_id, body, now),
        )
        conn.execute(
            "UPDATE buddy_pairs SET last_interaction_at = ? "
            "WHERE id = ?", (now, pair_id),
        )
    return Message(
        id=mid, pair_id=pair_id, sender_user_id=sender_user_id,
        body=body, created_at=now,
    )


def list_messages(*, pair_id: str, limit: int = 100) -> list[Message]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, pair_id, sender_user_id, body, created_at "
            "FROM buddy_messages WHERE pair_id = ? "
            "ORDER BY created_at LIMIT ?",
            (pair_id, limit),
        ).fetchall()
    return [
        Message(
            id=r[0], pair_id=r[1], sender_user_id=r[2],
            body=r[3], created_at=r[4],
        )
        for r in rows
    ]
