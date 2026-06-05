"""v3.2 — Exam Readiness Score.

Per gap-review §18 — current analytics are usage-tracking; the
gap is **learning outcome tracking**. The headline outcome is
"how ready is this student for their target exam?" — a single
0-100 number that compresses:

  • Topic mastery  (from mastery.py — per-topic correctness avg)
  • Mock performance (from mock_engine.py — recent attempt
    percentile + accuracy)
  • Topic coverage  (% of exam_taxonomy chapters touched, weighted
    by weightage_pct)
  • Consistency    (from streaks.py — study habit signal)
  • Trust          (from citations.grounding_rate — answer-quality
    signal for the answers they've been getting)

Single table:
  exam_pack_readiness  one row per (user, pack) — current score +
                        breakdown, refreshed on a schedule

The score is intentionally interpretable: each component scores
0-100 independently, then the headline is a weighted blend. UI
shows each component so the student knows where to push.

`compute_readiness(user_id, pack_code)` is called:
  • On mock submission (re-score immediately)
  • Daily for active enrollments (nightly batch)
  • On dashboard load if stale (>1h old)
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
CREATE TABLE IF NOT EXISTS exam_pack_readiness (
    id              TEXT PRIMARY KEY,
    pack_code       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    score           REAL NOT NULL,                   -- 0..100 headline
    mastery_score   REAL NOT NULL DEFAULT 0,
    mock_score      REAL NOT NULL DEFAULT 0,
    coverage_score  REAL NOT NULL DEFAULT 0,
    consistency_score REAL NOT NULL DEFAULT 0,
    trust_score     REAL NOT NULL DEFAULT 0,
    weak_topics_json TEXT,                            -- top-5 weak topic codes
    components_json TEXT,                             -- {full breakdown for transparency}
    computed_at     REAL NOT NULL,
    UNIQUE (user_id, pack_code)
);
CREATE INDEX IF NOT EXISTS idx_epr_user
    ON exam_pack_readiness(user_id, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_epr_pack
    ON exam_pack_readiness(pack_code, score DESC);
"""


# Weights applied to the five components when computing the headline.
# Sum to 1.0. Tuned for govt/competitive exams; school/college packs
# could lean coverage-heavier — that's a per-pack override the
# admin API can set later.
DEFAULT_WEIGHTS = {
    "mastery":     0.35,
    "mock":        0.30,
    "coverage":    0.15,
    "consistency": 0.10,
    "trust":       0.10,
}

# Stale cutoff — readiness rows older than this auto-refresh on
# dashboard reads.
STALE_AFTER_SEC = 3600


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


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Readiness:
    id: str
    pack_code: str
    user_id: str
    score: float
    mastery_score: float
    mock_score: float
    coverage_score: float
    consistency_score: float
    trust_score: float
    weak_topics: list[str]
    components: dict
    computed_at: float


# ---------- component scorers ----------

def _mastery_component(*, user_id: str, exam_code: str) -> dict:
    """Aggregate topic mastery against the exam's topic tree. A
    student who's mastered 60% of topics scores higher than one
    who's mastered 30% — weighted by topic weightage_pct."""
    try:
        from . import exam_taxonomy as _et
        from . import mastery as _m
    except ImportError:
        return {"score": 0.0, "reason": "modules unavailable",
                "weak": []}
    topics = _et.list_topics(exam_code, depth=0)
    if not topics:
        return {"score": 0.0,
                "reason": "no topic tree for exam",
                "weak": []}
    # Pull user mastery; we expect topic_key to align with
    # exam_taxonomy.topic.code prefixed by exam_code (convention)
    rows = _m.list_for_user(user_id, limit=500)
    by_key = {r.topic_key: r for r in rows}
    weighted_sum = 0.0
    weight_total = 0.0
    contributions: list[dict] = []
    weak: list[str] = []
    for t in topics:
        w = t.weightage_pct or 0.0
        weight_total += w
        # Match on either '<exam>::<topic_code>' or plain topic_code
        key_full = f"{exam_code}::{t.code}"
        m = by_key.get(key_full) or by_key.get(t.code)
        if m and m.attempts > 0:
            comp = m.mastery * 100
            weighted_sum += comp * w
            contributions.append({
                "topic": t.code, "weightage": w,
                "mastery": round(m.mastery * 100, 1),
                "attempts": m.attempts,
            })
            if m.mastery < 0.5:
                weak.append(t.code)
        else:
            # Untouched topics drag the score down proportionally
            contributions.append({
                "topic": t.code, "weightage": w,
                "mastery": 0.0, "attempts": 0,
            })
            if w > 5.0:
                weak.append(t.code)
    score = (
        weighted_sum / weight_total if weight_total else 0.0
    )
    return {
        "score": round(score, 1),
        "weighted_sum": round(weighted_sum, 1),
        "weight_total": round(weight_total, 1),
        "contributions": contributions[:20],
        "weak": weak[:5],
    }


def _mock_component(*, user_id: str, pack_code: str) -> dict:
    """Mock performance — recent attempts on papers within this
    pack, scored by mean percentile across last 5."""
    try:
        from . import mock_engine as _me
    except ImportError:
        return {"score": 0.0, "reason": "module unavailable"}
    # Fetch papers in pack, then attempts by user
    with _conn() as conn:
        rows = conn.execute(
            "SELECT a.raw_score, a.max_score, a.percentile, "
            "       a.submitted_at "
            "FROM mock_attempts a "
            "JOIN mock_papers p ON p.id = a.paper_id "
            "WHERE a.user_id = ? AND p.pack_code = ? "
            "  AND a.status = 'submitted' "
            "ORDER BY a.submitted_at DESC LIMIT 5",
            (user_id, pack_code),
        ).fetchall()
    if not rows:
        return {
            "score": 0.0, "attempts": 0,
            "reason": "no submitted mocks in pack",
        }
    # Headline = mean of (percentile if available else accuracy*100)
    parts: list[float] = []
    for raw, mx, pct, _ in rows:
        if pct is not None:
            parts.append(float(pct))
        elif mx and mx > 0:
            parts.append(float(raw or 0) / float(mx) * 100)
    if not parts:
        return {"score": 0.0, "attempts": len(rows),
                "reason": "no scorable attempts"}
    score = sum(parts) / len(parts)
    return {
        "score": round(max(0, min(score, 100)), 1),
        "attempts": len(rows),
        "recent_percentiles": [round(p, 1) for p in parts],
    }


def _coverage_component(*, user_id: str, exam_code: str) -> dict:
    """What fraction of the exam's chapter tree has the student
    touched (any attempt counts)? Weighted by weightage_pct."""
    try:
        from . import exam_taxonomy as _et
        from . import mastery as _m
    except ImportError:
        return {"score": 0.0, "reason": "modules unavailable"}
    topics = _et.list_topics(exam_code, depth=0)
    if not topics:
        return {"score": 0.0, "reason": "no topic tree"}
    rows = _m.list_for_user(user_id, limit=500)
    touched_keys = {r.topic_key for r in rows if r.attempts > 0}
    covered_weight = 0.0
    total_weight = 0.0
    covered_count = 0
    for t in topics:
        w = t.weightage_pct or 0.0
        total_weight += w
        if (
            f"{exam_code}::{t.code}" in touched_keys
            or t.code in touched_keys
        ):
            covered_weight += w
            covered_count += 1
    score = (
        covered_weight / total_weight * 100 if total_weight else 0
    )
    return {
        "score": round(score, 1),
        "covered_topics": covered_count,
        "total_topics": len(topics),
    }


def _consistency_component(*, user_id: str) -> dict:
    """Study consistency — streak length normalised to 100.
    Cap at 30-day streak = 100%; everything below scales linearly."""
    try:
        from . import streaks as _s
    except ImportError:
        return {"score": 0.0, "reason": "module unavailable"}
    try:
        snap = _s.snapshot(user_id=user_id)
    except Exception:
        return {"score": 0.0, "reason": "streaks unavailable"}
    cur = snap.get("current_streak", 0) if isinstance(snap, dict) else 0
    # 30-day cap → 100. Tunable.
    score = min(100.0, cur / 30 * 100)
    return {
        "score": round(score, 1),
        "current_streak": int(cur),
    }


def _trust_component(*, user_id: str) -> dict:
    """Grounding-rate signal — what % of THIS user's recent AI
    answers had citations? High-trust answers = the student is
    interrogating sources, not just accepting the LLM."""
    try:
        from . import citations as _c
    except ImportError:
        return {"score": 0.0, "reason": "module unavailable"}
    # Pull user's recent answers + count grounded
    answers = _c.list_user_answers(user_id=user_id, limit=50)
    if not answers:
        return {
            "score": 50.0,
            "reason": "no AI answers yet — neutral default",
        }
    grounded = sum(1 for a in answers if a.grounded)
    rate = grounded / len(answers) * 100
    return {
        "score": round(rate, 1),
        "answers_sample": len(answers),
        "grounded_answers": grounded,
    }


# ---------- compose ----------

def compute_readiness(
    *,
    user_id: str,
    pack_code: str,
    weights: dict | None = None,
) -> Readiness:
    """Recompute + persist the readiness row for (user, pack).
    Returns the freshly computed Readiness."""
    try:
        from . import exam_taxonomy as _et
    except ImportError:
        raise RuntimeError("exam_taxonomy module required") from None
    pack = _et.get_pack(pack_code)
    if not pack:
        raise ValueError(f"pack {pack_code!r} not found")
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    # Sanity — weights sum ≈ 1.0
    total_w = sum(w.values())
    if abs(total_w - 1.0) > 0.01:
        raise ValueError(
            f"weights must sum to 1.0; got {total_w:.3f}",
        )

    mast = _mastery_component(
        user_id=user_id, exam_code=pack.exam_code,
    )
    mock = _mock_component(user_id=user_id, pack_code=pack_code)
    cov = _coverage_component(
        user_id=user_id, exam_code=pack.exam_code,
    )
    cons = _consistency_component(user_id=user_id)
    trust = _trust_component(user_id=user_id)

    score = (
        mast["score"] * w["mastery"]
        + mock["score"] * w["mock"]
        + cov["score"] * w["coverage"]
        + cons["score"] * w["consistency"]
        + trust["score"] * w["trust"]
    )
    weak = mast.get("weak", [])
    components = {
        "mastery": mast, "mock": mock, "coverage": cov,
        "consistency": cons, "trust": trust,
        "weights": w,
    }
    now = time.time()
    rid = uuid.uuid4().hex
    with _conn() as conn:
        # Upsert (user, pack) — refresh existing row if there.
        existing = conn.execute(
            "SELECT id FROM exam_pack_readiness "
            "WHERE user_id = ? AND pack_code = ?",
            (user_id, pack_code),
        ).fetchone()
        if existing:
            rid = existing[0]
            conn.execute(
                "UPDATE exam_pack_readiness SET "
                " score = ?, mastery_score = ?, mock_score = ?, "
                " coverage_score = ?, consistency_score = ?, "
                " trust_score = ?, "
                " weak_topics_json = ?, components_json = ?, "
                " computed_at = ? "
                "WHERE id = ?",
                (round(score, 1), mast["score"], mock["score"],
                 cov["score"], cons["score"], trust["score"],
                 json.dumps(weak), json.dumps(components),
                 now, rid),
            )
        else:
            conn.execute(
                "INSERT INTO exam_pack_readiness "
                "(id, pack_code, user_id, score, "
                " mastery_score, mock_score, coverage_score, "
                " consistency_score, trust_score, "
                " weak_topics_json, components_json, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, pack_code, user_id, round(score, 1),
                 mast["score"], mock["score"], cov["score"],
                 cons["score"], trust["score"],
                 json.dumps(weak), json.dumps(components), now),
            )
    return Readiness(
        id=rid, pack_code=pack_code, user_id=user_id,
        score=round(score, 1),
        mastery_score=mast["score"], mock_score=mock["score"],
        coverage_score=cov["score"],
        consistency_score=cons["score"],
        trust_score=trust["score"],
        weak_topics=weak,
        components=components, computed_at=now,
    )


def get_readiness(
    *,
    user_id: str,
    pack_code: str,
    refresh_if_stale: bool = True,
) -> Readiness | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, pack_code, user_id, score, "
            "       mastery_score, mock_score, coverage_score, "
            "       consistency_score, trust_score, "
            "       weak_topics_json, components_json, computed_at "
            "FROM exam_pack_readiness "
            "WHERE user_id = ? AND pack_code = ?",
            (user_id, pack_code),
        ).fetchone()
    if not r:
        if refresh_if_stale:
            try:
                return compute_readiness(
                    user_id=user_id, pack_code=pack_code,
                )
            except ValueError:
                return None
        return None
    row = Readiness(
        id=r[0], pack_code=r[1], user_id=r[2], score=r[3],
        mastery_score=r[4], mock_score=r[5],
        coverage_score=r[6], consistency_score=r[7],
        trust_score=r[8],
        weak_topics=json.loads(r[9]) if r[9] else [],
        components=json.loads(r[10]) if r[10] else {},
        computed_at=r[11],
    )
    if refresh_if_stale and (time.time() - row.computed_at) > STALE_AFTER_SEC:
        return compute_readiness(
            user_id=user_id, pack_code=pack_code,
        )
    return row


def list_user_readiness(user_id: str) -> list[Readiness]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, pack_code, user_id, score, "
            "       mastery_score, mock_score, coverage_score, "
            "       consistency_score, trust_score, "
            "       weak_topics_json, components_json, computed_at "
            "FROM exam_pack_readiness "
            "WHERE user_id = ? ORDER BY score DESC",
            (user_id,),
        ).fetchall()
    return [
        Readiness(
            id=r[0], pack_code=r[1], user_id=r[2], score=r[3],
            mastery_score=r[4], mock_score=r[5],
            coverage_score=r[6], consistency_score=r[7],
            trust_score=r[8],
            weak_topics=json.loads(r[9]) if r[9] else [],
            components=json.loads(r[10]) if r[10] else {},
            computed_at=r[11],
        )
        for r in rows
    ]


def pack_leaderboard(
    pack_code: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Top scorers on a pack — drives the §6 community-room
    leaderboard. Returns anonymised entries (user_id + score)."""
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, score, mastery_score, mock_score "
            "FROM exam_pack_readiness "
            "WHERE pack_code = ? "
            "ORDER BY score DESC LIMIT ?",
            (pack_code, limit),
        ).fetchall()
    return [
        {"user_id": r[0], "score": r[1],
         "mastery": r[2], "mock": r[3]}
        for r in rows
    ]
