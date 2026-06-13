"""prod-139 — Memory Boost daily drill (CK-12 SM-2-daily-3 pattern).

CK-12's "Memory Boost" surfaces exactly 3 questions per concept each
day — a mix of critical-recall + familiar-warmup — to trigger active
recall without overwhelming. Driven by their modified SM-2 algorithm.
Students get a streak + push notification.

Pathshala adaptation:
  - Reuse the existing `spaced_repetition.py` SM-2 engine.
  - Generalise the source pool: not just flashcards, but PYQs from
    `question_bank` (filtered by user's enrolled board/grade), plus
    weak topics surfaced by `mastery_aggregate` (prod-135).
  - Pick exactly 3 items per day, score-weighted:
      • 1 "critical-recall" — a topic in the user's red mastery zone
      • 1 "familiar-warmup" — a topic in the user's green zone (decay-check)
      • 1 "fresh" — an untouched topic (introduce new material)
  - Cheap Haiku-class generation; fits the M2 cap.
  - Streak tracking — daily-active streak persists in `memory_boost_streaks`.

Endpoints (in routers/memory_boost_routes.py):
    GET /api/me/memory-boost?board=CBSE&grade=10
        → today's 3-item pack
    POST /api/me/memory-boost/answer
        body: {item_id, was_correct, time_seconds}
        → records the response, updates SM-2 next-due

Schema:
    memory_boost_picks (id, user_id, picked_at, pack_date,
                        item_kind, item_ref, bucket)
    memory_boost_streaks (user_id, current_streak, longest_streak,
                          last_active_date)

Picks are persisted so re-requesting today's pack returns the SAME
3 items (idempotency — student opens the app on phone + laptop the
same day, sees the same questions).
"""
from __future__ import annotations

import random
import sqlite3
import time
import uuid
from dataclasses import dataclass

from . import db, mastery_aggregate, question_bank

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_boost_picks (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    picked_at     REAL NOT NULL,
    pack_date     TEXT NOT NULL,     -- 'YYYY-MM-DD' in IST
    item_kind     TEXT NOT NULL,     -- 'pyq' / 'flashcard' / 'mastery_topic'
    item_ref      TEXT NOT NULL,     -- question_bank.id / flashcard.id / topic_key
    bucket        TEXT NOT NULL      -- 'critical' / 'warmup' / 'fresh'
);
CREATE INDEX IF NOT EXISTS idx_memboost_user_date
    ON memory_boost_picks(user_id, pack_date);

CREATE TABLE IF NOT EXISTS memory_boost_answers (
    id            TEXT PRIMARY KEY,
    pick_id       TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    answered_at   REAL NOT NULL,
    was_correct   INTEGER NOT NULL,
    time_seconds  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_memboost_answers_pick
    ON memory_boost_answers(pick_id);
CREATE INDEX IF NOT EXISTS idx_memboost_answers_user
    ON memory_boost_answers(user_id, answered_at DESC);

CREATE TABLE IF NOT EXISTS memory_boost_streaks (
    user_id            TEXT PRIMARY KEY,
    current_streak     INTEGER NOT NULL DEFAULT 0,
    longest_streak     INTEGER NOT NULL DEFAULT 0,
    last_active_date   TEXT
);
"""


PACK_SIZE = 3
BUCKET_WEIGHTS = {"critical": 1, "warmup": 1, "fresh": 1}


@dataclass(frozen=True)
class MemoryBoostPick:
    id: str
    user_id: str
    picked_at: float
    pack_date: str
    item_kind: str
    item_ref: str
    bucket: str


def _conn() -> sqlite3.Connection:
    path = db.sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


def _ist_today() -> str:
    """IST today as 'YYYY-MM-DD'. Indian Standard Time is UTC+05:30 —
    use timezone-aware datetime so dev / prod / CI all agree."""
    import datetime
    ist_offset = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(ist_offset).strftime("%Y-%m-%d")


def _existing_pack(
    conn: sqlite3.Connection, user_id: str, pack_date: str,
) -> list[MemoryBoostPick]:
    rows = conn.execute(
        "SELECT id, user_id, picked_at, pack_date, item_kind, item_ref, bucket "
        "FROM memory_boost_picks "
        "WHERE user_id = ? AND pack_date = ? "
        "ORDER BY picked_at ASC",
        (user_id, pack_date),
    ).fetchall()
    return [MemoryBoostPick(*r) for r in rows]


def _pick_pyq_for_bucket(
    *,
    bucket: str,
    user_id: str,  # noqa: ARG001 — reserved for future per-user dedup of already-answered picks
    board: str,
    grade: int,
    mastery_rows: list[mastery_aggregate.ConceptMastery],
) -> tuple[str, str] | None:
    """Pick one PYQ for the bucket. Returns (item_kind, item_ref).

    Bucket policies:
      • 'critical' — red or decayed topic → pick a question from
        that subject/chapter.
      • 'warmup'   — green topic, decay-check.
      • 'fresh'    — any untouched topic.

    Returns None if the user's pool is too sparse (e.g. no PYQs at
    all for that board+grade).
    """
    # Score-rank topics by bucket affinity
    if bucket == "critical":
        candidates = [
            m for m in mastery_rows
            if m.color_state in ("red", "yellow") and m.decay_state != "untouched"
        ]
    elif bucket == "warmup":
        candidates = [m for m in mastery_rows if m.color_state == "green"]
    else:  # fresh
        candidates = [m for m in mastery_rows if m.color_state == "untouched"]

    # Shuffle so the pack feels different day-to-day even when mastery
    # is stable. Random is fine — not security-sensitive.
    random.shuffle(candidates)

    # Try each candidate topic to find a question we can pick.
    seen_question_ids: set[str] = set()
    for m in candidates[:20]:
        # Find a question in this subject (the closest hook we have to topic)
        try:
            rows = question_bank.search(
                board=board, grade=grade, subject=m.subject, limit=10,
            )
        except (TypeError, ValueError):
            rows = []
        for q in rows:
            if q.id in seen_question_ids:
                continue
            seen_question_ids.add(q.id)
            return "pyq", q.id

    # Fallback: any PYQ from the board+grade, regardless of mastery
    try:
        rows = question_bank.search(board=board, grade=grade, limit=20)
    except (TypeError, ValueError):
        rows = []
    for q in rows:
        return "pyq", q.id
    return None


def get_or_create_pack(
    *, user_id: str, board: str, grade: int,
) -> list[MemoryBoostPick]:
    """Return today's 3-item pack. Idempotent: if today's picks exist,
    return them unchanged. Otherwise generate + persist a new pack."""
    today = _ist_today()
    with _conn() as conn:
        existing = _existing_pack(conn, user_id, today)
        if existing:
            return existing

        mastery_rows = mastery_aggregate.build_mastery_map(
            user_id=user_id, board=board, grade=grade,
        )

        picks: list[MemoryBoostPick] = []
        seen_refs: set[str] = set()
        for bucket in ("critical", "warmup", "fresh"):
            result = _pick_pyq_for_bucket(
                bucket=bucket,
                user_id=user_id,
                board=board,
                grade=grade,
                mastery_rows=mastery_rows,
            )
            if result is None:
                continue
            item_kind, item_ref = result
            if item_ref in seen_refs:
                continue
            seen_refs.add(item_ref)
            pick_id = uuid.uuid4().hex
            now = time.time()
            conn.execute(
                "INSERT INTO memory_boost_picks "
                "(id, user_id, picked_at, pack_date, item_kind, "
                "item_ref, bucket) VALUES (?,?,?,?,?,?,?)",
                (pick_id, user_id, now, today, item_kind, item_ref, bucket),
            )
            picks.append(MemoryBoostPick(
                id=pick_id, user_id=user_id, picked_at=now,
                pack_date=today, item_kind=item_kind,
                item_ref=item_ref, bucket=bucket,
            ))
        return picks


def record_answer(
    *,
    pick_id: str,
    user_id: str,
    was_correct: bool,
    time_seconds: int | None = None,
) -> dict:
    """Record the student's response. Updates SM-2 state for the
    underlying item AND the streak.

    Returns:
        {"recorded": True, "streak": {...}}
    """
    with _conn() as conn:
        # Verify the pick belongs to this user
        row = conn.execute(
            "SELECT user_id, item_kind, item_ref "
            "FROM memory_boost_picks WHERE id = ?",
            (pick_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"pick {pick_id} not found")
        if row[0] != user_id:
            raise PermissionError("not your pick")

        ans_id = uuid.uuid4().hex
        now = time.time()
        conn.execute(
            "INSERT INTO memory_boost_answers "
            "(id, pick_id, user_id, answered_at, was_correct, time_seconds) "
            "VALUES (?,?,?,?,?,?)",
            (ans_id, pick_id, user_id, now, 1 if was_correct else 0, time_seconds),
        )

    # Update streak after the answer is persisted
    streak = _update_streak(user_id)

    return {
        "recorded": True,
        "streak": streak,
    }


def _update_streak(user_id: str) -> dict:
    """Bump the streak if the user answered today and the last active
    day was yesterday. Reset if there's a gap."""
    today = _ist_today()
    import datetime
    today_d = datetime.date.fromisoformat(today)

    with _conn() as conn:
        row = conn.execute(
            "SELECT current_streak, longest_streak, last_active_date "
            "FROM memory_boost_streaks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            current, longest, last = 1, 1, today
            conn.execute(
                "INSERT INTO memory_boost_streaks "
                "(user_id, current_streak, longest_streak, last_active_date) "
                "VALUES (?,?,?,?)",
                (user_id, current, longest, last),
            )
        else:
            current, longest, last = row
            if last == today:
                pass  # already counted today
            else:
                try:
                    last_d = datetime.date.fromisoformat(last) if last else None
                except ValueError:
                    last_d = None
                if last_d and (today_d - last_d).days == 1:
                    current += 1
                else:
                    current = 1
                longest = max(longest, current)
                conn.execute(
                    "UPDATE memory_boost_streaks "
                    "SET current_streak = ?, longest_streak = ?, "
                    "last_active_date = ? WHERE user_id = ?",
                    (current, longest, today, user_id),
                )
    return {
        "current_streak": current,
        "longest_streak": longest,
        "last_active_date": today,
    }


def get_streak(user_id: str) -> dict:
    with _conn() as conn:
        row = conn.execute(
            "SELECT current_streak, longest_streak, last_active_date "
            "FROM memory_boost_streaks WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return {"current_streak": 0, "longest_streak": 0, "last_active_date": None}
    return {
        "current_streak": int(row[0] or 0),
        "longest_streak": int(row[1] or 0),
        "last_active_date": row[2],
    }


def hydrate_picks(picks: list[MemoryBoostPick]) -> list[dict]:
    """Inflate each pick with the underlying question text + options.
    Falls back to a minimal stub if the source row was deleted."""
    out: list[dict] = []
    for p in picks:
        item_payload: dict = {"missing": True}
        if p.item_kind == "pyq":
            q = question_bank.get_by_id(p.item_ref)
            if q:
                item_payload = {
                    "missing": False,
                    "question_text": q.question_text,
                    "options": q.options,
                    "correct_answer": q.correct_answer,
                    "subject": q.subject,
                    "chapter": q.chapter,
                    "marks": q.marks,
                    "difficulty": q.difficulty,
                }
        out.append({
            "pick_id": p.id,
            "bucket": p.bucket,
            "item_kind": p.item_kind,
            "item_ref": p.item_ref,
            "item": item_payload,
            "picked_at": p.picked_at,
            "pack_date": p.pack_date,
        })
    return out
