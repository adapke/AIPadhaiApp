"""prod-135 — Concept Mastery Map aggregator (CK-12 BrainFlex pattern).

Joins existing per-topic / per-attempt rows into a single read-side
"concept mastery map" view: for the student's enrolled board+grade,
returns each curriculum topic with:

  - **mastery** (0.0-1.0): EWMA from `user_topic_mastery` if present,
    else weighted average from cross-module signals (essay /
    practice / flashcard), else 0.
  - **last_practised** (epoch seconds, or None if untouched)
  - **decay_state**: 'fresh' (<7d) / 'stale' (7-30d) / 'decayed' (>30d) /
    'untouched' (no signal at all)
  - **color_state**: 'green' (mastery ≥0.7 AND fresh) / 'yellow' (0.4-0.7
    OR stale) / 'red' (<0.4 OR decayed) / 'untouched'
  - **source_attempts**: dict of {module: count} so the SPA can show
    "5 practice attempts, 2 essays, 12 flashcards" provenance

Design constraints:
  - **No new tables.** Pure read-side aggregation over what we already
    have. Caller-side caching is fine (~5min window).
  - **No new Claude calls.** This is a derivation, not a generation.
  - **Honest about gaps.** If `user_topic_mastery` is empty and there
    are no cross-module signals, we return all topics as `untouched`.

Topic-key resolution: `mastery.py` keys by free-form `topic_key` (e.g.
'photosynthesis'). `curriculum_objectives` has structured `chapter`.
We normalise both via `_normalise_topic_key()` (lowercase + strip +
collapse whitespace + remove punctuation) to make joins forgiving.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass

from . import db

# Tunables -----------------------------------------------------

# Green if mastery ≥ this AND not decayed.
GREEN_THRESHOLD = 0.70
# Yellow if mastery ≥ this; red otherwise.
YELLOW_THRESHOLD = 0.40

# "Fresh": last practised within 7 days.
FRESH_WINDOW_SEC = 7 * 24 * 3600
# "Stale": 7-30 days.
STALE_WINDOW_SEC = 30 * 24 * 3600

# Time-decay multiplier when last_practised > 14d ago.
# After 14d, mastery score is multiplied by (0.5 ** (days/14)) so a
# topic at 0.85 mastery untouched for 14 days drops to ~0.42 — the
# CK-12 "memory decay" insight.
DECAY_HALF_LIFE_SEC = 14 * 24 * 3600
DECAY_START_SEC = 14 * 24 * 3600

# Cross-module score conversion: an essay AI-score of 75/100 → 0.75
# mastery contribution; a mock interview overall_score of 0.8 →
# 0.8 mastery; flashcard SM-2 grade 4-5 → 0.75-1.0 contribution.
ESSAY_SCORE_MAX = 100.0
FLASHCARD_GRADE_MAX = 5

# How many cross-module attempts to consider per topic.
RECENT_ATTEMPTS_PER_MODULE = 5


# ----------------------------- types -----------------------------


@dataclass(frozen=True)
class ConceptMastery:
    """One row in the mastery map."""

    topic_key: str
    title: str
    chapter: str | None
    subject: str
    board: str
    grade: int
    mastery: float                  # 0.0 .. 1.0 (post-decay)
    raw_mastery: float              # pre-decay
    last_practised: float | None    # epoch sec
    decay_state: str                # 'fresh' / 'stale' / 'decayed' / 'untouched'
    color_state: str                # 'green' / 'yellow' / 'red' / 'untouched'
    source_attempts: dict[str, int] # {'flashcard': N, 'essay': N, ...}


# ----------------------------- helpers -----------------------------


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def _normalise_topic_key(s: str | None) -> str:
    """Aggressive lowercase + strip + collapse for join-tolerance."""
    if not s:
        return ""
    s = _PUNCT_RE.sub(" ", s.lower())
    s = _WS_RE.sub(" ", s).strip()
    return s


def _decay_state(last_practised: float | None, now: float) -> str:
    if last_practised is None:
        return "untouched"
    age = now - last_practised
    if age <= FRESH_WINDOW_SEC:
        return "fresh"
    if age <= STALE_WINDOW_SEC:
        return "stale"
    return "decayed"


def _apply_decay(mastery: float, last_practised: float | None, now: float) -> float:
    """Half-life decay after DECAY_START_SEC. Returns the time-adjusted
    mastery score, clamped to [0, 1]."""
    if last_practised is None:
        return mastery
    age = now - last_practised
    if age <= DECAY_START_SEC:
        return mastery
    half_lives = (age - DECAY_START_SEC) / DECAY_HALF_LIFE_SEC
    return max(0.0, min(1.0, mastery * (0.5 ** half_lives)))


def _color_state(mastery: float, decay_state: str) -> str:
    if decay_state == "untouched":
        return "untouched"
    if mastery >= GREEN_THRESHOLD and decay_state != "decayed":
        return "green"
    if mastery >= YELLOW_THRESHOLD:
        return "yellow"
    return "red"


# ----------------------------- data sources -----------------------------


def _conn() -> sqlite3.Connection:
    path = db.sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(path), timeout=10.0)


def _topics_for_board_grade(
    conn: sqlite3.Connection, *, board: str, grade: int, subject: str | None,
) -> list[tuple[str, str, str]]:
    """Return [(topic_key, title, subject)] from curriculum_objectives.

    `topic_key` is `chapter` lowercased + normalised.
    """
    q = (
        "SELECT chapter, subject FROM curriculum_objectives "
        "WHERE board = ? AND grade = ? "
    )
    args: list = [board, grade]
    if subject:
        q += "AND subject = ? "
        args.append(subject)
    q += "GROUP BY chapter, subject ORDER BY subject, chapter"
    try:
        rows = conn.execute(q, args).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for chapter, subj in rows:
        key = _normalise_topic_key(chapter)
        if not key:
            continue
        out.append((key, chapter, subj or "general"))
    return out


def _user_topic_mastery_index(
    conn: sqlite3.Connection, user_id: str,
) -> dict[str, dict]:
    """Return {normalised_topic_key: {mastery, attempts, correct, last_seen}}
    pulled from user_topic_mastery."""
    out: dict[str, dict] = {}
    try:
        rows = conn.execute(
            "SELECT topic_key, mastery, attempts, correct, last_seen "
            "FROM user_topic_mastery WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for topic_key, m, attempts, correct, last_seen in rows:
        norm = _normalise_topic_key(topic_key)
        if not norm:
            continue
        out[norm] = {
            "mastery": float(m or 0.0),
            "attempts": int(attempts or 0),
            "correct": int(correct or 0),
            "last_seen": float(last_seen or 0.0) or None,
        }
    return out


def _essay_signals(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Recent essay submissions with AI scores. Topic comes from the
    rubric — `essay_rubrics.exam` is the closest proxy for subject."""
    try:
        rows = conn.execute(
            "SELECT s.ai_score, s.human_score, s.submitted_at, r.exam "
            "FROM essay_submissions s LEFT JOIN essay_rubrics r ON s.rubric_id = r.id "
            "WHERE s.user_id = ? AND s.submitted_at IS NOT NULL "
            "ORDER BY s.submitted_at DESC LIMIT 50",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # essay_rubrics may not have `exam` column on some envs; gracefully degrade
        return []
    out = []
    for ai_score, human_score, submitted_at, exam in rows:
        score = human_score if human_score is not None else ai_score
        if score is None:
            continue
        out.append({
            "score": float(score) / ESSAY_SCORE_MAX,
            "submitted_at": float(submitted_at or 0.0),
            "exam": exam or "",
        })
    return out


def _flashcard_signals(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Recent flashcard reviews. Topic via flashcard_decks.topic_code."""
    try:
        rows = conn.execute(
            "SELECT r.grade, r.reviewed_at, d.topic_code, d.title "
            "FROM flashcard_reviews r "
            "INNER JOIN flashcards c ON r.card_id = c.id "
            "INNER JOIN flashcard_decks d ON c.deck_id = d.id "
            "WHERE r.user_id = ? ORDER BY r.reviewed_at DESC LIMIT 200",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for grade, reviewed_at, topic_code, title in rows:
        if grade is None:
            continue
        out.append({
            "score": min(1.0, float(grade) / FLASHCARD_GRADE_MAX),
            "reviewed_at": float(reviewed_at or 0.0),
            "topic_key": _normalise_topic_key(topic_code or title or ""),
        })
    return out


def _practice_signals(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Recent practice tests with subject + score."""
    try:
        rows = conn.execute(
            "SELECT subject, score_json, submitted_at "
            "FROM practice_tests "
            "WHERE user_id = ? AND submitted_at IS NOT NULL "
            "ORDER BY submitted_at DESC LIMIT 30",
            (user_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for subject, score_json, submitted_at in rows:
        if not score_json:
            continue
        try:
            import json as _json
            data = _json.loads(score_json)
            # score_json shape varies; common keys: 'percent', 'pct', 'score'
            pct = (
                data.get("percent")
                or data.get("pct")
                or data.get("score_pct")
                or 0
            )
            if isinstance(pct, (int, float)) and pct > 1:
                pct = pct / 100.0
            out.append({
                "score": float(pct),
                "subject": subject or "",
                "submitted_at": float(submitted_at or 0.0),
            })
        except (ValueError, KeyError, TypeError):
            continue
    return out


# ----------------------------- main entry -----------------------------


def build_mastery_map(
    *, user_id: str, board: str, grade: int, subject: str | None = None,
) -> list[ConceptMastery]:
    """Return one ConceptMastery row per curriculum topic for the
    user's board+grade. Untouched topics show up as `color_state =
    'untouched'` so the SPA can render them as "not started" tiles.

    Args:
      user_id: required.
      board: 'CBSE' / 'ICSE' / state-board key.
      grade: 1..12.
      subject: optional filter (e.g. 'Math' / 'Science').

    Returns: list[ConceptMastery] sorted by (subject, title).
    """
    if not user_id or not board or not isinstance(grade, int):
        return []
    now = time.time()
    conn = _conn()
    try:
        topics = _topics_for_board_grade(
            conn, board=board, grade=grade, subject=subject,
        )
        utm_index = _user_topic_mastery_index(conn, user_id)
        essays = _essay_signals(conn, user_id)
        flashcards = _flashcard_signals(conn, user_id)
        practices = _practice_signals(conn, user_id)
    finally:
        conn.close()

    # Flashcard signals keyed by topic_key
    fc_by_topic: dict[str, list[dict]] = {}
    for sig in flashcards:
        fc_by_topic.setdefault(sig["topic_key"], []).append(sig)

    out: list[ConceptMastery] = []
    for topic_key, title, subj in topics:
        # Direct mastery row, if any
        direct = utm_index.get(topic_key)
        signals: list[float] = []
        attempts_by_module: dict[str, int] = {}
        last_practised: float | None = None

        if direct:
            signals.append(direct["mastery"])
            attempts_by_module["topic_mastery"] = direct["attempts"]
            if direct["last_seen"]:
                last_practised = max(last_practised or 0.0, direct["last_seen"])

        # Flashcard signals matching this topic_key
        fc_signals = fc_by_topic.get(topic_key, [])[:RECENT_ATTEMPTS_PER_MODULE]
        for sig in fc_signals:
            signals.append(sig["score"])
            last_practised = max(last_practised or 0.0, sig["reviewed_at"])
        if fc_signals:
            attempts_by_module["flashcard"] = len(fc_signals)

        # Practice signals matching this subject (subject-level, not topic-level)
        prac_sigs = [p for p in practices if p["subject"].lower() == subj.lower()]
        prac_sigs = prac_sigs[:RECENT_ATTEMPTS_PER_MODULE]
        for sig in prac_sigs:
            # Weight: subject-level matches are softer than direct-topic
            signals.append(sig["score"] * 0.7)
            last_practised = max(last_practised or 0.0, sig["submitted_at"])
        if prac_sigs:
            attempts_by_module["practice"] = len(prac_sigs)

        # Essay signals — soft contribution, can't be confidently keyed
        # to a specific topic; only contribute when the rubric's exam
        # name overlaps the subject.
        ess_sigs = [
            e for e in essays
            if e["exam"] and (subj.lower() in e["exam"].lower()
                              or e["exam"].lower() in subj.lower())
        ]
        ess_sigs = ess_sigs[:RECENT_ATTEMPTS_PER_MODULE]
        for sig in ess_sigs:
            signals.append(sig["score"] * 0.5)  # heavier discount
            last_practised = max(last_practised or 0.0, sig["submitted_at"])
        if ess_sigs:
            attempts_by_module["essay"] = len(ess_sigs)

        # Compute raw + decayed mastery
        raw_mastery = sum(signals) / len(signals) if signals else 0.0
        last_practised = last_practised or None
        decayed = _apply_decay(raw_mastery, last_practised, now)
        decay = _decay_state(last_practised, now)
        color = _color_state(decayed, decay)

        out.append(ConceptMastery(
            topic_key=topic_key,
            title=title,
            chapter=title,
            subject=subj,
            board=board,
            grade=grade,
            mastery=round(decayed, 3),
            raw_mastery=round(raw_mastery, 3),
            last_practised=last_practised,
            decay_state=decay,
            color_state=color,
            source_attempts=attempts_by_module,
        ))

    # Sort: untouched topics last, then by subject + title alphabetically
    out.sort(key=lambda c: (
        0 if c.color_state != "untouched" else 1,
        c.subject,
        c.title,
    ))
    return out


def summarise(rows: list[ConceptMastery]) -> dict[str, int]:
    """Roll up color-state counts for dashboard widgets."""
    summary = {"green": 0, "yellow": 0, "red": 0, "untouched": 0, "total": len(rows)}
    for r in rows:
        summary[r.color_state] = summary.get(r.color_state, 0) + 1
    return summary
