"""v3.15 — Adaptive / personalised Exam Packs.

Per review §11 + §22 — the v3.1 Exam Packs ship a static
catalog. Real students differ wildly: a CBSE-10 student who's
strong in algebra but weak in geometry needs a different daily
plan than one who's the inverse. This module produces a
**personalised pack** — a per-user variant of an underlying
official pack with adjusted topic weightages.

The base pack (`exam_packs`) stays canonical. We layer a
personalised overlay on top:

  personalised_packs       one row per (user, base_pack) overlay
  personalised_weightages  per-topic overrides on the base topic
                            tree (higher = more focus)
  pack_adaptation_signals  audit trail — what events drove the
                            re-weighting

The adaptation engine reads:
  • `mastery` per topic
  • Recent `mock_engine` attempts (per-topic accuracy)
  • `readiness.weak_topics` list
  • `daily_plan` completion patterns (skipped topics → boost)

It writes adjusted `weightage_pct` overrides. The base
`weightage_pct` from `exam_taxonomy.exam_topics` is preserved;
this module's overrides are additive on the **personalised**
view. UI queries `personalised_topic_view()` instead of the raw
exam_taxonomy when a personalised pack exists.

Adaptation rules (each adds to base weightage):
  weak_topic_boost      mastery < 0.4 → +50% of base weightage
  skipped_topic_boost   skipped 2+ daily plans on this topic → +30%
  recent_mock_low       last mock < 30% on topic → +40%
  strong_topic_relief   mastery > 0.85 + 5+ attempts → -30%

A topic's final weightage is bounded [base × 0.3, base × 3.0]
to avoid extreme outliers that wreck the schedule.

`re_adapt(user, base_pack)` is the entry point — runs all rules,
recomputes overrides, persists. Cron calls it weekly per active
user. Dashboard hits it on-demand when a major signal changes.
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
CREATE TABLE IF NOT EXISTS personalised_packs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    base_pack_code  TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    -- The user's adapted version of the base pack — readable in
    -- their dashboard alongside the base.
    last_adapted_at REAL,
    adaptation_count INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    UNIQUE (user_id, base_pack_code)
);
CREATE INDEX IF NOT EXISTS idx_pp_user
    ON personalised_packs(user_id, last_adapted_at DESC);

CREATE TABLE IF NOT EXISTS personalised_weightages (
    personalised_pack_id TEXT NOT NULL,
    topic_code      TEXT NOT NULL,
    base_weightage  REAL NOT NULL,                   -- snapshot of exam_taxonomy.weightage_pct
    adjusted_weightage REAL NOT NULL,                -- our override
    reasons         TEXT,                             -- JSON list of rule codes that contributed
    updated_at      REAL NOT NULL,
    PRIMARY KEY (personalised_pack_id, topic_code)
);

CREATE TABLE IF NOT EXISTS pack_adaptation_signals (
    id              TEXT PRIMARY KEY,
    personalised_pack_id TEXT NOT NULL,
    rule_code       TEXT NOT NULL,                   -- 'weak_topic_boost' / 'skipped_topic_boost' / ...
    topic_code      TEXT,                             -- NULL for pack-wide signals
    signal_value    REAL,                             -- e.g. mastery=0.32, last_mock_pct=22.5
    weightage_delta REAL,                             -- how much we shifted (+ or -)
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pas_pack
    ON pack_adaptation_signals(personalised_pack_id, created_at DESC);
"""


# Rule weights — additive percentage of base weightage applied
# when the rule fires. Tunable.
RULE_DELTAS = {
    "weak_topic_boost":    0.50,    # +50%
    "skipped_topic_boost": 0.30,    # +30%
    "recent_mock_low":     0.40,    # +40%
    "strong_topic_relief": -0.30,   # -30%
}

# Thresholds — see module docstring
WEAK_MASTERY_THRESHOLD = 0.40
STRONG_MASTERY_THRESHOLD = 0.85
STRONG_ATTEMPTS_MIN = 5
SKIPPED_TOPIC_THRESHOLD = 2
RECENT_MOCK_LOW_PCT = 30.0

# Bounded final weightage — [base × this_min, base × this_max]
WEIGHTAGE_MIN_RATIO = 0.30
WEIGHTAGE_MAX_RATIO = 3.0

# Stale cutoff — re-adapt if older than this
ADAPT_STALE_AFTER_DAYS = 7


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
class PersonalisedPack:
    id: str
    user_id: str
    base_pack_code: str
    title: str
    description: str | None
    last_adapted_at: float | None
    adaptation_count: int
    created_at: float


@dataclass(frozen=True)
class TopicOverride:
    topic_code: str
    base_weightage: float
    adjusted_weightage: float
    reasons: list[str]
    updated_at: float


@dataclass(frozen=True)
class Signal:
    id: str
    rule_code: str
    topic_code: str | None
    signal_value: float | None
    weightage_delta: float
    created_at: float


# ---------- pack lifecycle ----------

def create_personalised_pack(
    *,
    user_id: str,
    base_pack_code: str,
    title: str | None = None,
    description: str | None = None,
) -> PersonalisedPack:
    """Create a per-user overlay of `base_pack_code`. Idempotent —
    re-creating returns the existing row. Caller should follow up
    with `re_adapt()` to populate weightages."""
    try:
        from . import exam_taxonomy as et
    except ImportError:
        raise RuntimeError("exam_taxonomy required") from None
    base = et.get_pack(base_pack_code)
    if not base:
        raise ValueError(f"base pack {base_pack_code!r} not found")
    if not title:
        title = f"Personalised — {base.title}"
    pid = uuid.uuid4().hex
    now = time.time()
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO personalised_packs "
                "(id, user_id, base_pack_code, title, "
                " description, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (pid, user_id, base_pack_code, title,
                 description, now),
            )
    except sqlite3.IntegrityError:
        # Already exists — return existing
        existing = get_personalised_pack(
            user_id=user_id, base_pack_code=base_pack_code,
        )
        if existing:
            return existing
        raise
    return _get_personalised(pid)  # type: ignore[return-value]


def _get_personalised(pid: str) -> PersonalisedPack | None:
    with _conn() as conn:
        r = conn.execute(
            _P_SEL + " WHERE id = ?", (pid,),
        ).fetchone()
    return _row_to_pack(r) if r else None


_P_SEL = (
    "SELECT id, user_id, base_pack_code, title, description, "
    "       last_adapted_at, adaptation_count, created_at "
    "FROM personalised_packs"
)


def _row_to_pack(r) -> PersonalisedPack:
    return PersonalisedPack(
        id=r[0], user_id=r[1], base_pack_code=r[2],
        title=r[3], description=r[4],
        last_adapted_at=r[5], adaptation_count=r[6],
        created_at=r[7],
    )


def get_personalised_pack(
    *,
    user_id: str,
    base_pack_code: str,
) -> PersonalisedPack | None:
    with _conn() as conn:
        r = conn.execute(
            _P_SEL + " WHERE user_id = ? AND base_pack_code = ?",
            (user_id, base_pack_code),
        ).fetchone()
    return _row_to_pack(r) if r else None


def list_user_packs(user_id: str) -> list[PersonalisedPack]:
    with _conn() as conn:
        rows = conn.execute(
            _P_SEL + " WHERE user_id = ? "
            "ORDER BY last_adapted_at DESC NULLS LAST, created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_pack(r) for r in rows]


def delete_personalised_pack(
    *, pack_id: str, user_id: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM personalised_packs "
            "WHERE id = ? AND user_id = ?",
            (pack_id, user_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                "DELETE FROM personalised_weightages "
                "WHERE personalised_pack_id = ?", (pack_id,),
            )
            conn.execute(
                "DELETE FROM pack_adaptation_signals "
                "WHERE personalised_pack_id = ?", (pack_id,),
            )
        return cur.rowcount > 0


# ---------- adaptation rules ----------

def _gather_signals(
    *,
    user_id: str,
    exam_code: str,
) -> dict:
    """Pull every signal the adaptation engine reads. Returns a
    dict keyed by `topic_code` with what we know about each.

    Each value: {
        mastery, attempts, recent_mock_pct, skipped_count,
    }
    """
    out: dict[str, dict] = {}
    # Mastery + attempts
    try:
        from . import mastery as _m
        for r in _m.list_for_user(user_id, limit=500):
            if not r.topic_key.startswith(f"{exam_code}::"):
                # Also accept plain topic_code
                code = r.topic_key
            else:
                code = r.topic_key.split("::", 1)[1]
            d = out.setdefault(code, {})
            d["mastery"] = r.mastery
            d["attempts"] = r.attempts
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            "adaptive signal collection failed (non-fatal)", exc_info=_e
        )
    # Recent mock performance — last 5 mocks, per-topic accuracy
    try:
        from . import mock_engine as _me
        attempts = _me.list_attempts_for_user(user_id, limit=5)
        for a in attempts:
            if a.status != "submitted":
                continue
            for topic, breakdown in (a.topic_breakdown or {}).items():
                d = out.setdefault(topic, {})
                attempted = (
                    breakdown.get("correct", 0)
                    + breakdown.get("wrong", 0)
                )
                if attempted == 0:
                    continue
                accuracy = breakdown.get("correct", 0) / attempted * 100
                # Keep the WORST recent — we want to address the
                # slipping topics
                prev = d.get("recent_mock_pct")
                if prev is None or accuracy < prev:
                    d["recent_mock_pct"] = accuracy
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            "adaptive signal collection failed (non-fatal)", exc_info=_e
        )
    # Skipped plans — count topics that appeared in skipped plans
    try:
        from . import daily_plan as _dp
        recent_plans = _dp.list_recent_plans(
            user_id=user_id, limit=14,
        )
        for p in recent_plans:
            if p.status != "skipped":
                continue
            for b in p.blocks:
                if b.topic_code:
                    d = out.setdefault(b.topic_code, {})
                    d["skipped_count"] = d.get("skipped_count", 0) + 1
    except Exception as _e:
        import logging
        logging.getLogger(__name__).warning(
            "adaptive signal collection failed (non-fatal)", exc_info=_e
        )
    return out


def _evaluate_rules_for_topic(
    *,
    topic_code: str,  # noqa: ARG001
    topic_signals: dict,
) -> tuple[float, list[str]]:
    """For a topic given its signal bundle, apply all rules and
    return (delta_pct_of_base, [rule_codes_fired])."""
    delta = 0.0
    fired: list[str] = []
    mastery = topic_signals.get("mastery", 0.5)
    attempts = topic_signals.get("attempts", 0)
    recent_mock = topic_signals.get("recent_mock_pct")
    skipped = topic_signals.get("skipped_count", 0)
    if attempts >= 2 and mastery < WEAK_MASTERY_THRESHOLD:
        delta += RULE_DELTAS["weak_topic_boost"]
        fired.append("weak_topic_boost")
    if recent_mock is not None and recent_mock < RECENT_MOCK_LOW_PCT:
        delta += RULE_DELTAS["recent_mock_low"]
        fired.append("recent_mock_low")
    if skipped >= SKIPPED_TOPIC_THRESHOLD:
        delta += RULE_DELTAS["skipped_topic_boost"]
        fired.append("skipped_topic_boost")
    if (attempts >= STRONG_ATTEMPTS_MIN
            and mastery >= STRONG_MASTERY_THRESHOLD):
        delta += RULE_DELTAS["strong_topic_relief"]
        fired.append("strong_topic_relief")
    return delta, fired


# ---------- adaptation engine ----------

def re_adapt(
    *,
    user_id: str,
    base_pack_code: str,
) -> dict:
    """Recompute personalised weightages for (user, base_pack).
    Persists new weightages + signal audit rows. Returns a
    summary dict."""
    try:
        from . import exam_taxonomy as et
    except ImportError:
        raise RuntimeError("exam_taxonomy required") from None
    pack = et.get_pack(base_pack_code)
    if not pack:
        raise ValueError(f"base pack {base_pack_code!r} not found")
    # Find or create the personalised wrapper
    personalised = get_personalised_pack(
        user_id=user_id, base_pack_code=base_pack_code,
    )
    if not personalised:
        personalised = create_personalised_pack(
            user_id=user_id, base_pack_code=base_pack_code,
        )
    topics = et.list_topics(pack.exam_code, depth=0)
    if not topics:
        return {
            "personalised_pack_id": personalised.id,
            "topics_updated": 0,
            "rules_fired": {},
            "reason": "no topic tree for exam",
        }
    signals = _gather_signals(
        user_id=user_id, exam_code=pack.exam_code,
    )
    now = time.time()
    topics_updated = 0
    rules_fired: dict[str, int] = {}
    with _conn() as conn:
        # Clear prior signals — re_adapt is a full recompute
        conn.execute(
            "DELETE FROM pack_adaptation_signals "
            "WHERE personalised_pack_id = ?",
            (personalised.id,),
        )
        for t in topics:
            base_w = t.weightage_pct or 0.0
            if base_w <= 0:
                continue
            ts = signals.get(t.code, {})
            delta, fired = _evaluate_rules_for_topic(
                topic_code=t.code, topic_signals=ts,
            )
            # Apply delta + clamp
            adjusted = base_w * (1.0 + delta)
            adjusted = max(
                base_w * WEIGHTAGE_MIN_RATIO,
                min(base_w * WEIGHTAGE_MAX_RATIO, adjusted),
            )
            # Persist override
            conn.execute(
                "INSERT INTO personalised_weightages "
                "(personalised_pack_id, topic_code, "
                " base_weightage, adjusted_weightage, "
                " reasons, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(personalised_pack_id, topic_code) "
                "DO UPDATE SET "
                " adjusted_weightage = excluded.adjusted_weightage, "
                " reasons = excluded.reasons, "
                " updated_at = excluded.updated_at",
                (personalised.id, t.code, round(base_w, 2),
                 round(adjusted, 2),
                 json.dumps(fired), now),
            )
            topics_updated += 1
            # Audit signal rows — one per fired rule
            for rule in fired:
                conn.execute(
                    "INSERT INTO pack_adaptation_signals "
                    "(id, personalised_pack_id, rule_code, "
                    " topic_code, signal_value, "
                    " weightage_delta, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, personalised.id, rule,
                     t.code, _signal_value_for_rule(rule, ts),
                     RULE_DELTAS[rule], now),
                )
                rules_fired[rule] = rules_fired.get(rule, 0) + 1
        conn.execute(
            "UPDATE personalised_packs SET "
            " last_adapted_at = ?, "
            " adaptation_count = adaptation_count + 1 "
            "WHERE id = ?",
            (now, personalised.id),
        )
    return {
        "personalised_pack_id": personalised.id,
        "topics_updated": topics_updated,
        "rules_fired": rules_fired,
        "adapted_at": now,
    }


def _signal_value_for_rule(rule: str, topic_signals: dict) -> float | None:
    """Pull the relevant signal value for the audit row."""
    if rule == "weak_topic_boost":
        return topic_signals.get("mastery")
    if rule == "strong_topic_relief":
        return topic_signals.get("mastery")
    if rule == "recent_mock_low":
        return topic_signals.get("recent_mock_pct")
    if rule == "skipped_topic_boost":
        return topic_signals.get("skipped_count")
    return None


# ---------- view ----------

def personalised_topic_view(
    *,
    user_id: str,
    base_pack_code: str,
) -> list[dict]:
    """Return the personalised topic list for (user, pack). Each
    entry has base_weightage + adjusted_weightage + reasons.
    Falls back to base weightages when no personalised pack exists
    or no override for a particular topic."""
    try:
        from . import exam_taxonomy as et
    except ImportError:
        raise RuntimeError("exam_taxonomy required") from None
    pack = et.get_pack(base_pack_code)
    if not pack:
        raise ValueError(f"base pack {base_pack_code!r} not found")
    topics = et.list_topics(pack.exam_code, depth=0)
    personalised = get_personalised_pack(
        user_id=user_id, base_pack_code=base_pack_code,
    )
    overrides: dict[str, dict] = {}
    if personalised:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT topic_code, base_weightage, "
                "       adjusted_weightage, reasons, updated_at "
                "FROM personalised_weightages "
                "WHERE personalised_pack_id = ?",
                (personalised.id,),
            ).fetchall()
        overrides = {
            r[0]: {
                "base_weightage": r[1],
                "adjusted_weightage": r[2],
                "reasons": json.loads(r[3]) if r[3] else [],
                "updated_at": r[4],
            }
            for r in rows
        }
    out: list[dict] = []
    for t in topics:
        base_w = t.weightage_pct or 0.0
        o = overrides.get(t.code)
        out.append({
            "topic_code": t.code,
            "title": t.title,
            "base_weightage": base_w,
            "adjusted_weightage": (
                o["adjusted_weightage"] if o else base_w
            ),
            "reasons": o["reasons"] if o else [],
            "is_personalised": o is not None,
        })
    return out


def get_overrides(personalised_pack_id: str) -> list[TopicOverride]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT topic_code, base_weightage, "
            "       adjusted_weightage, reasons, updated_at "
            "FROM personalised_weightages "
            "WHERE personalised_pack_id = ? "
            "ORDER BY adjusted_weightage DESC",
            (personalised_pack_id,),
        ).fetchall()
    return [
        TopicOverride(
            topic_code=r[0], base_weightage=r[1],
            adjusted_weightage=r[2],
            reasons=json.loads(r[3]) if r[3] else [],
            updated_at=r[4],
        )
        for r in rows
    ]


def list_signals(
    personalised_pack_id: str,
    *,
    limit: int = 100,
) -> list[Signal]:
    """Audit trail — what events drove the most recent
    adaptation."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, rule_code, topic_code, signal_value, "
            "       weightage_delta, created_at "
            "FROM pack_adaptation_signals "
            "WHERE personalised_pack_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (personalised_pack_id, limit),
        ).fetchall()
    return [
        Signal(
            id=r[0], rule_code=r[1], topic_code=r[2],
            signal_value=r[3], weightage_delta=r[4],
            created_at=r[5],
        )
        for r in rows
    ]


def should_re_adapt(
    *, user_id: str, base_pack_code: str,
) -> tuple[bool, str]:
    """Cheap decision — should we re-adapt? Used by the dashboard
    to decide whether to trigger a refresh. Returns (bool, reason)."""
    p = get_personalised_pack(
        user_id=user_id, base_pack_code=base_pack_code,
    )
    if not p:
        return True, "no_personalised_pack"
    if not p.last_adapted_at:
        return True, "never_adapted"
    age_days = (time.time() - p.last_adapted_at) / 86400
    if age_days >= ADAPT_STALE_AFTER_DAYS:
        return True, "stale"
    return False, "fresh"
