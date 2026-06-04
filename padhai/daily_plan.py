"""v3.4 — Daily plan generator.

Per gap-review §3 + §6 — students don't pick the app because it
has 400 routes; they pick it because it tells them **what to study
today**. This module is the scheduler: given a student's
Exam-Pack enrollment + their readiness breakdown + the topic tree,
emit a per-day plan that fills their `daily_minutes` budget.

Composition over the v3.1-v3.3 pieces:
  • exam_taxonomy.list_topics(exam_code)   → what to cover
  • readiness.get_readiness(user, pack)    → which components weak
  • mastery.list_for_user(user)            → per-topic mastery
  • mock_engine.list_papers(pack_code)     → which mocks to slot
  • mock_engine.list_attempts_for_user     → which mocks already taken
  • citations / retrieval                   → "study source" links

Block kinds the plan emits:
  read       — read NCERT / source notes on a topic (10-20 min)
  practice   — practice MCQs on a topic (5-15 min)
  mock       — full / sectional mock paper (paper.total_time_min)
  revise     — revisit a weak topic from the last 3-7 days
  current_affairs — for govt segments only

Tables:
  daily_plans         one row per (user, pack, date)
  daily_plan_blocks   ordered blocks within a plan

The plan is **regenerated** when:
  • The student lands on the home screen and today's plan is missing
  • Readiness materially changes (>5 points)
  • The student submits a mock (re-plan with fresh weak-topic data)

`generate_plan(user_id, pack_code, date)` is the entry point. UI
just calls `get_or_generate(user_id, pack_code)` and accepts
whatever lands.
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
CREATE TABLE IF NOT EXISTS daily_plans (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    pack_code       TEXT NOT NULL,
    plan_date       TEXT NOT NULL,                  -- 'YYYY-MM-DD' (user-local intent)
    total_minutes   INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'active' | 'completed' | 'skipped'
    completion_pct  REAL NOT NULL DEFAULT 0,
    generation_reason TEXT,                          -- 'fresh' / 'readiness_changed' / 'post_mock'
    created_at      REAL NOT NULL,
    completed_at    REAL,
    UNIQUE (user_id, pack_code, plan_date)
);
CREATE INDEX IF NOT EXISTS idx_dp_user
    ON daily_plans(user_id, plan_date DESC);

CREATE TABLE IF NOT EXISTS daily_plan_blocks (
    id              TEXT PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    position        INTEGER NOT NULL,
    kind            TEXT NOT NULL,                  -- 'read' / 'practice' / 'mock' / 'revise' / 'current_affairs'
    title           TEXT NOT NULL,
    topic_code      TEXT,
    estimated_min   INTEGER NOT NULL,
    ref_kind        TEXT,                            -- 'paper' / 'topic' / 'upload' / 'free'
    ref_id          TEXT,                            -- FK to ref_kind target
    payload_json    TEXT,                            -- extra structured data (e.g. paper section codes)
    completed       INTEGER NOT NULL DEFAULT 0,
    completed_at    REAL,
    UNIQUE (plan_id, position)
);
CREATE INDEX IF NOT EXISTS idx_dpb_plan
    ON daily_plan_blocks(plan_id, position);
"""


VALID_PLAN_STATUSES = frozenset({"active", "completed", "skipped"})
VALID_BLOCK_KINDS = frozenset({
    "read", "practice", "mock", "revise", "current_affairs",
})


# How fresh is "today's plan" — if older than this, regen.
PLAN_STALE_AFTER_HOURS = 18

# Re-plan triggers
READINESS_DRIFT_THRESHOLD = 5.0  # |new - old| > 5 → re-plan


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


def _today_iso(tz_offset_min: int = 330) -> str:
    """Default IST (+05:30 = 330 min) — India-first per review."""
    now = datetime.now(UTC) + timedelta(minutes=tz_offset_min)
    return now.date().isoformat()


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Block:
    id: str
    plan_id: str
    position: int
    kind: str
    title: str
    topic_code: str | None
    estimated_min: int
    ref_kind: str | None
    ref_id: str | None
    payload: dict
    completed: bool
    completed_at: float | None


@dataclass(frozen=True)
class Plan:
    id: str
    user_id: str
    pack_code: str
    plan_date: str
    total_minutes: int
    status: str
    completion_pct: float
    generation_reason: str | None
    created_at: float
    completed_at: float | None
    blocks: list[Block]


# ---------- helpers ----------

def _pick_weak_topics(
    *, user_id: str, exam_code: str, k: int = 3,
) -> list[dict]:
    """Bottom-mastery + high-weightage topics. Used to populate
    'practice' + 'revise' blocks. Returns dicts with topic_code,
    title, weightage, mastery."""
    try:
        from . import exam_taxonomy as _et
        from . import mastery as _m
    except ImportError:
        return []
    topics = _et.list_topics(exam_code, depth=0)
    if not topics:
        return []
    rows = _m.list_for_user(user_id, limit=500)
    by_key = {r.topic_key: r for r in rows}
    scored: list[dict] = []
    for t in topics:
        full = f"{exam_code}::{t.code}"
        m = by_key.get(full) or by_key.get(t.code)
        mastery = m.mastery if m else 0.0
        attempts = m.attempts if m else 0
        # Weakness signal — high weightage × low mastery
        gap = (1.0 - mastery) * (t.weightage_pct or 1.0)
        scored.append({
            "topic_code": t.code,
            "title": t.title,
            "weightage": t.weightage_pct or 0.0,
            "mastery": round(mastery, 3),
            "attempts": attempts,
            "gap_score": round(gap, 2),
        })
    # Sort by gap_score desc — biggest opportunity first
    scored.sort(key=lambda r: r["gap_score"], reverse=True)
    return scored[:k]


def _pick_strong_topics(
    *, user_id: str, exam_code: str, k: int = 2,
) -> list[dict]:
    """Pull recently-touched topics with decent mastery — for
    'revise' blocks (don't lose what you've built)."""
    try:
        from . import mastery as _m
    except ImportError:
        return []
    rows = _m.list_for_user(user_id, limit=500)
    # Filter for the exam prefix; fall back to plain
    candidates = []
    for r in rows:
        if not r.attempts:
            continue
        if r.mastery < 0.6:
            continue
        if not (
            r.topic_key.startswith(f"{exam_code}::")
            or "::" not in r.topic_key
        ):
            continue
        candidates.append(r)
    candidates.sort(key=lambda r: r.last_seen, reverse=True)
    out = []
    for r in candidates[:k]:
        code = (
            r.topic_key.split("::", 1)[1]
            if "::" in r.topic_key else r.topic_key
        )
        out.append({
            "topic_code": code,
            "mastery": round(r.mastery, 3),
            "last_seen": r.last_seen,
        })
    return out


def _next_mock(
    *, user_id: str, pack_code: str,
) -> dict | None:
    """Find the next mock the student should take — a published
    paper they haven't submitted yet, smallest paper first."""
    try:
        from . import mock_engine as _me
    except ImportError:
        return None
    papers = _me.list_papers(pack_code=pack_code, status="published")
    if not papers:
        return None
    # User's already-submitted attempts
    attempts = _me.list_attempts_for_user(user_id, limit=500)
    done_ids = {
        a.paper_id for a in attempts if a.status == "submitted"
    }
    candidates = [p for p in papers if p.id not in done_ids]
    if not candidates:
        return None
    # Prefer 'sectional' shorter papers; sort by time required
    candidates.sort(key=lambda p: (p.mode != "sectional", p.total_time_min))
    p = candidates[0]
    return {
        "paper_id": p.id, "title": p.title,
        "time_min": p.total_time_min, "mode": p.mode,
        "total_questions": p.total_questions,
    }


def _is_govt_segment(exam_code: str) -> bool:
    """Govt exams need a daily current-affairs block."""
    try:
        from . import exam_taxonomy as _et
        e = _et.get_exam(exam_code)
        return e is not None and e.segment_code == "govt"
    except Exception:  # noqa: BLE001
        return False


# ---------- block builders ----------

def _build_blocks(
    *,
    user_id: str,
    pack_code: str,
    exam_code: str,
    total_min: int,
) -> list[dict]:
    """Compose a list of block-dicts to fill total_min minutes.
    Allocation rules (% of daily budget):
       40% practice on weak topics
       25% read on weak topic source material
       15% mock attempt (if one is available + budget allows)
       10% revise strong topic
       10% current affairs (govt segment only)

    For non-govt segments the current-affairs allocation rolls
    into practice instead.
    """
    is_govt = _is_govt_segment(exam_code)
    weak = _pick_weak_topics(
        user_id=user_id, exam_code=exam_code, k=3,
    )
    strong = _pick_strong_topics(
        user_id=user_id, exam_code=exam_code, k=1,
    )
    next_mock = _next_mock(user_id=user_id, pack_code=pack_code)
    blocks: list[dict] = []
    pos = 1
    # Allocate minutes — keep arithmetic explicit so a future
    # tweak doesn't drift the rounding.
    practice_min = int(total_min * 0.40)
    read_min = int(total_min * 0.25)
    mock_min = int(total_min * 0.15)
    revise_min = int(total_min * 0.10)
    ca_min = total_min - practice_min - read_min - mock_min - revise_min
    if not is_govt:
        practice_min += ca_min
        ca_min = 0

    # Practice on weak topics (split across top 2)
    practice_topics = weak[:2] if weak else []
    if practice_topics:
        per_topic = practice_min // len(practice_topics)
        for t in practice_topics:
            blocks.append({
                "position": pos, "kind": "practice",
                "title": f"Practice MCQs — {t['title']}",
                "topic_code": t["topic_code"],
                "estimated_min": per_topic,
                "ref_kind": "topic",
                "ref_id": f"{exam_code}::{t['topic_code']}",
                "payload": {
                    "weightage": t["weightage"],
                    "current_mastery": t["mastery"],
                },
            })
            pos += 1
    elif practice_min:
        blocks.append({
            "position": pos, "kind": "practice",
            "title": "Practice — any topic",
            "topic_code": None,
            "estimated_min": practice_min,
            "ref_kind": "free", "ref_id": None,
            "payload": {},
        })
        pos += 1

    # Read source notes on the top weak topic
    if weak and read_min:
        blocks.append({
            "position": pos, "kind": "read",
            "title": f"Read source notes — {weak[0]['title']}",
            "topic_code": weak[0]["topic_code"],
            "estimated_min": read_min,
            "ref_kind": "topic",
            "ref_id": f"{exam_code}::{weak[0]['topic_code']}",
            "payload": {"weightage": weak[0]["weightage"]},
        })
        pos += 1
    elif read_min:
        blocks.append({
            "position": pos, "kind": "read",
            "title": "Read — choose a chapter",
            "topic_code": None,
            "estimated_min": read_min,
            "ref_kind": "free", "ref_id": None,
            "payload": {},
        })
        pos += 1

    # Mock — only if the available mock fits the budget
    if next_mock and next_mock["time_min"] <= max(mock_min + 10, 30):
        blocks.append({
            "position": pos, "kind": "mock",
            "title": f"Mock — {next_mock['title']}",
            "topic_code": None,
            "estimated_min": next_mock["time_min"],
            "ref_kind": "paper",
            "ref_id": next_mock["paper_id"],
            "payload": {
                "mode": next_mock["mode"],
                "total_questions": next_mock["total_questions"],
            },
        })
        pos += 1
    elif mock_min:
        # No mock available — fold the budget into practice
        if blocks and blocks[0]["kind"] == "practice":
            blocks[0]["estimated_min"] += mock_min

    # Revise strong topic
    if strong and revise_min:
        blocks.append({
            "position": pos, "kind": "revise",
            "title": (
                "Revise — "
                f"{strong[0]['topic_code'].replace('_', ' ').title()}"
            ),
            "topic_code": strong[0]["topic_code"],
            "estimated_min": revise_min,
            "ref_kind": "topic",
            "ref_id": f"{exam_code}::{strong[0]['topic_code']}",
            "payload": {
                "current_mastery": strong[0]["mastery"],
            },
        })
        pos += 1

    # Current affairs — govt only
    if is_govt and ca_min:
        blocks.append({
            "position": pos, "kind": "current_affairs",
            "title": "Current affairs — last 7 days",
            "topic_code": None,
            "estimated_min": ca_min,
            "ref_kind": "free", "ref_id": None,
            "payload": {"segment": "govt"},
        })
        pos += 1

    return blocks


# ---------- public API ----------

def generate_plan(
    *,
    user_id: str,
    pack_code: str,
    plan_date: str | None = None,
    total_minutes: int | None = None,
    reason: str = "fresh",
) -> Plan:
    """Generate (and persist) a daily plan. If a plan already
    exists for (user, pack, date), it's deleted + replaced — caller
    is expected to gate this with `get_or_generate` for the common
    case."""
    try:
        from . import exam_taxonomy as _et
    except ImportError:
        raise RuntimeError("exam_taxonomy required") from None
    pack = _et.get_pack(pack_code)
    if not pack:
        raise ValueError(f"pack {pack_code!r} not found")
    # Resolve target minutes — prefer enrollment.daily_minutes
    if total_minutes is None:
        enrollments = _et.list_user_enrollments(user_id)
        target = next(
            (e for e in enrollments if e.pack_code == pack_code),
            None,
        )
        total_minutes = target.daily_minutes if target else 60
    if total_minutes < 10 or total_minutes > 720:
        raise ValueError("total_minutes must be in [10, 720]")
    plan_date = plan_date or _today_iso()
    if not _valid_date(plan_date):
        raise ValueError("plan_date must be YYYY-MM-DD")
    blocks = _build_blocks(
        user_id=user_id, pack_code=pack_code,
        exam_code=pack.exam_code, total_min=total_minutes,
    )
    if not blocks:
        # Pathological — no topics seeded, no fallback. Emit a
        # single 'read' block so the UI has something.
        blocks = [{
            "position": 1, "kind": "read",
            "title": "Read — choose a chapter to start",
            "topic_code": None,
            "estimated_min": total_minutes,
            "ref_kind": "free", "ref_id": None,
            "payload": {},
        }]
    plan_id = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        # Replace any existing plan for (user, pack, date)
        existing = conn.execute(
            "SELECT id FROM daily_plans "
            "WHERE user_id = ? AND pack_code = ? AND plan_date = ?",
            (user_id, pack_code, plan_date),
        ).fetchone()
        if existing:
            old_id = existing[0]
            conn.execute(
                "DELETE FROM daily_plan_blocks WHERE plan_id = ?",
                (old_id,),
            )
            conn.execute(
                "DELETE FROM daily_plans WHERE id = ?", (old_id,),
            )
        conn.execute(
            "INSERT INTO daily_plans "
            "(id, user_id, pack_code, plan_date, total_minutes, "
            " status, generation_reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (plan_id, user_id, pack_code, plan_date,
             total_minutes, reason, now),
        )
        for b in blocks:
            conn.execute(
                "INSERT INTO daily_plan_blocks "
                "(id, plan_id, position, kind, title, topic_code, "
                " estimated_min, ref_kind, ref_id, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, plan_id, b["position"],
                 b["kind"], b["title"], b.get("topic_code"),
                 b["estimated_min"], b.get("ref_kind"),
                 b.get("ref_id"),
                 json.dumps(b.get("payload") or {})),
            )
    return get_plan(plan_id)  # type: ignore[return-value]


def _valid_date(s: str) -> bool:
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except (ValueError, TypeError):
        return False


def get_plan(plan_id: str) -> Plan | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, pack_code, plan_date, "
            "       total_minutes, status, completion_pct, "
            "       generation_reason, created_at, completed_at "
            "FROM daily_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if not r:
            return None
        block_rows = conn.execute(
            "SELECT id, plan_id, position, kind, title, topic_code, "
            "       estimated_min, ref_kind, ref_id, payload_json, "
            "       completed, completed_at "
            "FROM daily_plan_blocks WHERE plan_id = ? "
            "ORDER BY position",
            (plan_id,),
        ).fetchall()
    blocks = [
        Block(
            id=b[0], plan_id=b[1], position=b[2], kind=b[3],
            title=b[4], topic_code=b[5], estimated_min=b[6],
            ref_kind=b[7], ref_id=b[8],
            payload=json.loads(b[9]) if b[9] else {},
            completed=bool(b[10]), completed_at=b[11],
        )
        for b in block_rows
    ]
    return Plan(
        id=r[0], user_id=r[1], pack_code=r[2], plan_date=r[3],
        total_minutes=r[4], status=r[5], completion_pct=r[6],
        generation_reason=r[7], created_at=r[8],
        completed_at=r[9], blocks=blocks,
    )


def get_today_plan(
    *,
    user_id: str,
    pack_code: str,
    plan_date: str | None = None,
) -> Plan | None:
    plan_date = plan_date or _today_iso()
    with _conn() as conn:
        r = conn.execute(
            "SELECT id FROM daily_plans "
            "WHERE user_id = ? AND pack_code = ? AND plan_date = ?",
            (user_id, pack_code, plan_date),
        ).fetchone()
    return get_plan(r[0]) if r else None


def get_or_generate(
    *,
    user_id: str,
    pack_code: str,
    plan_date: str | None = None,
) -> Plan:
    """The common entry point. Returns today's plan if it exists,
    otherwise generates one. Stale check is left to the caller —
    use `should_regenerate` to decide."""
    existing = get_today_plan(
        user_id=user_id, pack_code=pack_code, plan_date=plan_date,
    )
    if existing:
        return existing
    return generate_plan(
        user_id=user_id, pack_code=pack_code, plan_date=plan_date,
    )


def should_regenerate(
    *,
    user_id: str,
    pack_code: str,
    last_readiness_score: float | None = None,
) -> tuple[bool, str]:
    """Decide whether today's plan needs a regen. Returns (bool,
    reason). UI calls this on dashboard load; backend cron uses
    it after mock submissions."""
    plan = get_today_plan(user_id=user_id, pack_code=pack_code)
    if not plan:
        return True, "no_plan_today"
    age_h = (time.time() - plan.created_at) / 3600
    if age_h >= PLAN_STALE_AFTER_HOURS:
        return True, "stale"
    if last_readiness_score is not None:
        try:
            from . import readiness as _rd
            current = _rd.get_readiness(
                user_id=user_id, pack_code=pack_code,
                refresh_if_stale=False,
            )
            if current and abs(current.score - last_readiness_score) > READINESS_DRIFT_THRESHOLD:
                return True, "readiness_drift"
        except Exception:  # noqa: BLE001
            pass
    return False, "fresh"


def mark_block_done(
    *,
    block_id: str,
    user_id: str,
) -> bool:
    """Student ticks off a block. Updates parent plan's
    completion_pct + flips to 'completed' when all blocks done."""
    with _conn() as conn:
        r = conn.execute(
            "SELECT plan_id FROM daily_plan_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        if not r:
            return False
        plan_id = r[0]
        # Verify ownership
        owner = conn.execute(
            "SELECT user_id FROM daily_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if not owner or owner[0] != user_id:
            return False
        now = time.time()
        conn.execute(
            "UPDATE daily_plan_blocks SET "
            " completed = 1, completed_at = ? "
            "WHERE id = ? AND completed = 0",
            (now, block_id),
        )
        # Recompute plan completion
        counts = conn.execute(
            "SELECT SUM(completed), COUNT(*) "
            "FROM daily_plan_blocks WHERE plan_id = ?",
            (plan_id,),
        ).fetchone()
        done = int(counts[0] or 0)
        total = int(counts[1] or 1)
        pct = round(done / total * 100, 1)
        if done >= total:
            conn.execute(
                "UPDATE daily_plans SET "
                " completion_pct = ?, status = 'completed', "
                " completed_at = ? WHERE id = ?",
                (pct, now, plan_id),
            )
        else:
            conn.execute(
                "UPDATE daily_plans SET completion_pct = ? "
                "WHERE id = ?", (pct, plan_id),
            )
    return True


def skip_plan(*, plan_id: str, user_id: str) -> bool:
    """Mark today's plan skipped. Counts negatively against
    streaks/consistency — but the user remains free to enroll
    tomorrow."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE daily_plans SET status = 'skipped' "
            "WHERE id = ? AND user_id = ? AND status = 'active'",
            (plan_id, user_id),
        )
        return cur.rowcount > 0


def list_recent_plans(
    *,
    user_id: str,
    pack_code: str | None = None,
    limit: int = 14,
) -> list[Plan]:
    limit = max(1, min(limit, 90))
    sql = (
        "SELECT id FROM daily_plans WHERE user_id = ?"
    )
    params: list = [user_id]
    if pack_code:
        sql += " AND pack_code = ?"; params.append(pack_code)
    sql += " ORDER BY plan_date DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        ids = [r[0] for r in conn.execute(sql, params).fetchall()]
    return [p for p in (get_plan(i) for i in ids) if p]


def pack_completion_stats(
    *,
    user_id: str, pack_code: str,
    last_n_days: int = 14,
) -> dict:
    """Rolling 14-day completion rate — drives the consistency
    component of readiness + the dashboard's 'study habit' card."""
    last_n_days = max(1, min(last_n_days, 90))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, completion_pct "
            "FROM daily_plans "
            "WHERE user_id = ? AND pack_code = ? "
            "ORDER BY plan_date DESC LIMIT ?",
            (user_id, pack_code, last_n_days),
        ).fetchall()
    if not rows:
        return {
            "plans": 0, "avg_completion_pct": 0.0,
            "completed_days": 0, "skipped_days": 0,
        }
    completed = sum(1 for r in rows if r[0] == "completed")
    skipped = sum(1 for r in rows if r[0] == "skipped")
    avg = sum(r[1] for r in rows) / len(rows)
    return {
        "plans": len(rows),
        "avg_completion_pct": round(avg, 1),
        "completed_days": completed,
        "skipped_days": skipped,
    }
