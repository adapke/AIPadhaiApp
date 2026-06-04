"""v3.8 — Spaced repetition + active recall.

Per review §7 (Quizlet / Knowt gap) — students need polished
active recall + spaced repetition. The existing `mastery.py`
tracks topic-level mastery; this module tracks **card-level**
review schedules using a Leitner/SM-2 hybrid.

Cards can be generated from any source:
  • From an upload: title + body extracted by retrieval module
  • From a question_bank question: front=question, back=answer
  • Manual: student creates their own cards
  • From a tutor reply: question + AI answer with citations

Three tables:
  flashcard_decks       collections of cards (per pack / per topic / personal)
  flashcards            individual cards (front, back, optional cloze)
  flashcard_reviews     review history — per (card, user, attempt)

We track per-user, per-card SRS state on `flashcards_user_state`:
  ease_factor       SM-2 EF (default 2.5; bounded 1.3..3.0)
  interval_days     current interval; doubles on success, resets on fail
  repetitions       count of consecutive successful reviews
  due_at            next-review timestamp

Daily review queue is what the student sees: all `due_at <= now`
cards across all decks, ordered by due_at ASC. The card stays in
the queue until reviewed.
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
CREATE TABLE IF NOT EXISTS flashcard_decks (
    id              TEXT PRIMARY KEY,
    owner_user_id   TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    pack_code       TEXT,                            -- optional link to Exam Pack
    topic_code      TEXT,                            -- optional link to exam_taxonomy topic
    language        TEXT NOT NULL DEFAULT 'en',
    visibility      TEXT NOT NULL DEFAULT 'private',
    -- 'private' | 'shared' | 'public'
    card_count      INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decks_owner
    ON flashcard_decks(owner_user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_decks_pack
    ON flashcard_decks(pack_code, visibility);

CREATE TABLE IF NOT EXISTS flashcards (
    id              TEXT PRIMARY KEY,
    deck_id         TEXT NOT NULL,
    position        INTEGER NOT NULL,
    front           TEXT NOT NULL,
    back            TEXT NOT NULL,
    hint            TEXT,
    source_ref      TEXT,                            -- 'upload:abc' / 'qb:qid' / 'manual'
    citation_json   TEXT,                            -- optional citation dict from retrieval
    created_at      REAL NOT NULL,
    UNIQUE (deck_id, position)
);
CREATE INDEX IF NOT EXISTS idx_cards_deck
    ON flashcards(deck_id, position);

CREATE TABLE IF NOT EXISTS flashcards_user_state (
    card_id         TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    ease_factor     REAL NOT NULL DEFAULT 2.5,
    interval_days   REAL NOT NULL DEFAULT 1.0,
    repetitions     INTEGER NOT NULL DEFAULT 0,
    last_reviewed_at REAL,
    due_at          REAL NOT NULL,
    lapses          INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (card_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_state_due
    ON flashcards_user_state(user_id, due_at);

CREATE TABLE IF NOT EXISTS flashcard_reviews (
    id              TEXT PRIMARY KEY,
    card_id         TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    grade           INTEGER NOT NULL,                -- 0=again, 3=hard, 4=good, 5=easy
    time_seconds    INTEGER,
    new_interval    REAL NOT NULL,
    new_ease        REAL NOT NULL,
    reviewed_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_user
    ON flashcard_reviews(user_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS idx_reviews_card
    ON flashcard_reviews(card_id, reviewed_at DESC);
"""


VALID_VISIBILITIES = frozenset({"private", "shared", "public"})

# SM-2 algorithm parameters
EASE_MIN = 1.3
EASE_MAX = 3.0
EASE_DEFAULT = 2.5

# Grade thresholds (0 = total fail, 5 = perfect recall)
GRADE_AGAIN = 0     # forgot, reset
GRADE_HARD = 3      # struggled but recalled
GRADE_GOOD = 4      # standard recall
GRADE_EASY = 5      # instant recall
VALID_GRADES = frozenset({0, 1, 2, 3, 4, 5})

# Bounds on the interval (days)
INTERVAL_MIN_DAYS = 1.0
INTERVAL_MAX_DAYS = 365.0 * 2     # 2 years


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _use_postgres() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith("postgres")


_schema_applied = False


class _DB:
    """Thin wrapper around sqlite3 / psycopg that normalises ? → %s for
    Postgres and handles schema bootstrap once per process."""

    def __init__(self) -> None:
        global _schema_applied
        self._pg = _use_postgres()
        if self._pg:
            import psycopg  # type: ignore[import]
            self._conn = psycopg.connect(
                os.environ["DATABASE_URL"],
                options="-c search_path=public",
            )
            if not _schema_applied:
                for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
                    self._conn.execute(stmt)
                self._conn.commit()
                _schema_applied = True
        else:
            path = _db_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), timeout=10.0)
            if not _schema_applied:
                self._conn.executescript(SCHEMA)
                _schema_applied = True

    def execute(self, sql: str, params=()):
        if self._pg:
            sql = sql.replace("?", "%s")
        return self._conn.execute(sql, params)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()
        self._conn.close()


def _conn() -> _DB:
    return _DB()


def migrate() -> None:
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Deck:
    id: str
    owner_user_id: str
    title: str
    description: str | None
    pack_code: str | None
    topic_code: str | None
    language: str
    visibility: str
    card_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Card:
    id: str
    deck_id: str
    position: int
    front: str
    back: str
    hint: str | None
    source_ref: str | None
    citation: dict | None
    created_at: float


@dataclass(frozen=True)
class CardState:
    card_id: str
    user_id: str
    ease_factor: float
    interval_days: float
    repetitions: int
    last_reviewed_at: float | None
    due_at: float
    lapses: int


@dataclass(frozen=True)
class ReviewOutcome:
    card_id: str
    new_interval: float
    new_ease: float
    new_due_at: float
    repetitions: int
    lapses: int


# ---------- decks ----------

def create_deck(
    *,
    owner_user_id: str,
    title: str,
    description: str | None = None,
    pack_code: str | None = None,
    topic_code: str | None = None,
    language: str = "en",
    visibility: str = "private",
) -> Deck:
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 200:
        raise ValueError("title must be [3, 200] chars")
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(
            f"visibility must be in {sorted(VALID_VISIBILITIES)}",
        )
    did = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO flashcard_decks "
            "(id, owner_user_id, title, description, pack_code, "
            " topic_code, language, visibility, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (did, owner_user_id, title, description, pack_code,
             topic_code, language, visibility, now, now),
        )
    return get_deck(did)  # type: ignore[return-value]


def get_deck(deck_id: str) -> Deck | None:
    with _conn() as conn:
        r = conn.execute(
            _DECK_SEL + " WHERE id = ?", (deck_id,),
        ).fetchone()
    return _row_to_deck(r) if r else None


_DECK_SEL = (
    "SELECT id, owner_user_id, title, description, pack_code, "
    "       topic_code, language, visibility, card_count, "
    "       created_at, updated_at "
    "FROM flashcard_decks"
)


def _row_to_deck(r) -> Deck:
    return Deck(
        id=r[0], owner_user_id=r[1], title=r[2],
        description=r[3], pack_code=r[4], topic_code=r[5],
        language=r[6], visibility=r[7],
        card_count=r[8], created_at=r[9], updated_at=r[10],
    )


def list_my_decks(user_id: str, *, limit: int = 200) -> list[Deck]:
    with _conn() as conn:
        rows = conn.execute(
            _DECK_SEL + " WHERE owner_user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_deck(r) for r in rows]


def list_public_decks(
    *,
    pack_code: str | None = None,
    topic_code: str | None = None,
    limit: int = 50,
) -> list[Deck]:
    limit = max(1, min(limit, 500))
    sql = _DECK_SEL + " WHERE visibility = 'public'"
    params: list = []
    if pack_code:
        sql += " AND pack_code = ?"; params.append(pack_code)
    if topic_code:
        sql += " AND topic_code = ?"; params.append(topic_code)
    sql += " ORDER BY card_count DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_deck(r) for r in rows]


def delete_deck(*, deck_id: str, owner_user_id: str) -> bool:
    """Cascade: cards + states + review history all dropped."""
    with _conn() as conn:
        deck = conn.execute(
            "SELECT owner_user_id FROM flashcard_decks WHERE id = ?",
            (deck_id,),
        ).fetchone()
        if not deck or deck[0] != owner_user_id:
            return False
        # Get card ids
        cards = conn.execute(
            "SELECT id FROM flashcards WHERE deck_id = ?",
            (deck_id,),
        ).fetchall()
        card_ids = [c[0] for c in cards]
        if card_ids:
            ph = ",".join("?" * len(card_ids))
            conn.execute(
                f"DELETE FROM flashcards_user_state "
                f"WHERE card_id IN ({ph})",
                card_ids,
            )
            conn.execute(
                f"DELETE FROM flashcard_reviews "
                f"WHERE card_id IN ({ph})",
                card_ids,
            )
        conn.execute(
            "DELETE FROM flashcards WHERE deck_id = ?",
            (deck_id,),
        )
        conn.execute(
            "DELETE FROM flashcard_decks WHERE id = ?",
            (deck_id,),
        )
    return True


# ---------- cards ----------

def add_card(
    *,
    deck_id: str,
    owner_user_id: str,
    front: str,
    back: str,
    hint: str | None = None,
    source_ref: str | None = None,
    citation: dict | None = None,
) -> Card:
    deck = get_deck(deck_id)
    if not deck:
        raise ValueError("deck not found")
    if deck.owner_user_id != owner_user_id:
        raise PermissionError("not your deck")
    front = (front or "").strip()
    back = (back or "").strip()
    if len(front) < 1 or len(front) > 4000:
        raise ValueError("front must be [1, 4000] chars")
    if len(back) < 1 or len(back) > 8000:
        raise ValueError("back must be [1, 8000] chars")
    cid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 "
            "FROM flashcards WHERE deck_id = ?", (deck_id,),
        ).fetchone()
        position = int(pos_row[0])
        conn.execute(
            "INSERT INTO flashcards "
            "(id, deck_id, position, front, back, hint, "
            " source_ref, citation_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, deck_id, position, front, back, hint,
             source_ref,
             json.dumps(citation) if citation else None, now),
        )
        conn.execute(
            "UPDATE flashcard_decks SET "
            " card_count = card_count + 1, updated_at = ? "
            "WHERE id = ?",
            (now, deck_id),
        )
    return _get_card(cid)  # type: ignore[return-value]


def _get_card(card_id: str) -> Card | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, deck_id, position, front, back, hint, "
            "       source_ref, citation_json, created_at "
            "FROM flashcards WHERE id = ?",
            (card_id,),
        ).fetchone()
    if not r:
        return None
    return Card(
        id=r[0], deck_id=r[1], position=r[2],
        front=r[3], back=r[4], hint=r[5],
        source_ref=r[6],
        citation=json.loads(r[7]) if r[7] else None,
        created_at=r[8],
    )


def get_card(card_id: str) -> Card | None:
    return _get_card(card_id)


def list_cards_for_deck(deck_id: str) -> list[Card]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, deck_id, position, front, back, hint, "
            "       source_ref, citation_json, created_at "
            "FROM flashcards WHERE deck_id = ? "
            "ORDER BY position",
            (deck_id,),
        ).fetchall()
    return [
        Card(
            id=r[0], deck_id=r[1], position=r[2],
            front=r[3], back=r[4], hint=r[5],
            source_ref=r[6],
            citation=json.loads(r[7]) if r[7] else None,
            created_at=r[8],
        )
        for r in rows
    ]


# ---------- generate from retrieval / question_bank ----------

def generate_from_chunks(
    *,
    owner_user_id: str,
    deck_title: str,
    chunks: list[dict],
    pack_code: str | None = None,
    topic_code: str | None = None,
) -> Deck:
    """Take a list of retrieval-hit dicts (from
    `retrieval.hits_to_citations`) and create a deck where each
    chunk becomes a card: front = page-section context,
    back = chunk text."""
    if not chunks:
        raise ValueError("at least one chunk required")
    deck = create_deck(
        owner_user_id=owner_user_id, title=deck_title,
        pack_code=pack_code, topic_code=topic_code,
    )
    for c in chunks:
        page = c.get("page_number")
        section = c.get("section")
        front_bits = []
        if section:
            front_bits.append(section)
        if page:
            front_bits.append(f"page {page}")
        front = " · ".join(front_bits) or "Recall this passage"
        add_card(
            deck_id=deck.id, owner_user_id=owner_user_id,
            front=front, back=c.get("citation_text") or "",
            source_ref=(
                f"upload:{c.get('source_id')}"
                if c.get("source_kind") == "upload"
                else f"{c.get('source_kind')}:{c.get('source_id')}"
            ),
            citation=c,
        )
    return get_deck(deck.id)  # type: ignore[return-value]


def generate_from_questions(
    *,
    owner_user_id: str,
    deck_title: str,
    question_ids: list[str],
    pack_code: str | None = None,
) -> Deck:
    """Generate cards from `question_bank` ids — front = question,
    back = correct_answer."""
    if not question_ids:
        raise ValueError("at least one question_id required")
    try:
        from . import question_bank as qb
    except ImportError:
        raise RuntimeError("question_bank module unavailable") from None
    deck = create_deck(
        owner_user_id=owner_user_id, title=deck_title,
        pack_code=pack_code,
    )
    for qid in question_ids:
        q = qb.get_by_id(qid)
        if not q or not q.correct_answer:
            continue
        add_card(
            deck_id=deck.id, owner_user_id=owner_user_id,
            front=q.question_text[:4000],
            back=q.correct_answer[:8000],
            source_ref=f"qb:{qid}",
        )
    return get_deck(deck.id)  # type: ignore[return-value]


# ---------- SM-2 review algorithm ----------

def _next_interval(
    *,
    ease: float,
    interval: float,
    repetitions: int,
    grade: int,
) -> tuple[float, float, int]:
    """Standard SM-2 with floor + ceiling. Returns
    (new_interval_days, new_ease, new_repetitions)."""
    if grade < 3:
        # Failed — reset repetitions, drop to a 1-day interval
        new_rep = 0
        new_interval = 1.0
    else:
        new_rep = repetitions + 1
        if new_rep == 1:
            new_interval = 1.0
        elif new_rep == 2:
            new_interval = 6.0
        else:
            new_interval = interval * ease
    # Adjust ease factor
    new_ease = ease + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    new_ease = max(EASE_MIN, min(EASE_MAX, new_ease))
    new_interval = max(
        INTERVAL_MIN_DAYS, min(INTERVAL_MAX_DAYS, new_interval),
    )
    return new_interval, new_ease, new_rep


def review_card(
    *,
    card_id: str,
    user_id: str,
    grade: int,
    time_seconds: int | None = None,
) -> ReviewOutcome:
    """Submit a review. Updates user state + records history.
    Grade is 0..5; <3 counts as a lapse + resets interval."""
    if grade not in VALID_GRADES:
        raise ValueError(f"grade must be in {sorted(VALID_GRADES)}")
    if time_seconds is not None and (
        time_seconds < 0 or time_seconds > 3600
    ):
        raise ValueError("time_seconds must be in [0, 3600]")
    card = _get_card(card_id)
    if not card:
        raise ValueError("card not found")
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT ease_factor, interval_days, repetitions, "
            "       lapses "
            "FROM flashcards_user_state "
            "WHERE card_id = ? AND user_id = ?",
            (card_id, user_id),
        ).fetchone()
        if existing:
            ease, interval, rep, lapses = (
                existing[0], existing[1], existing[2], existing[3],
            )
        else:
            ease = EASE_DEFAULT
            interval = 1.0
            rep = 0
            lapses = 0
        new_interval, new_ease, new_rep = _next_interval(
            ease=ease, interval=interval,
            repetitions=rep, grade=grade,
        )
        new_due = now + new_interval * 86400
        new_lapses = lapses + (1 if grade < 3 else 0)
        if existing:
            conn.execute(
                "UPDATE flashcards_user_state SET "
                " ease_factor = ?, interval_days = ?, "
                " repetitions = ?, lapses = ?, "
                " last_reviewed_at = ?, due_at = ? "
                "WHERE card_id = ? AND user_id = ?",
                (new_ease, new_interval, new_rep, new_lapses,
                 now, new_due, card_id, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO flashcards_user_state "
                "(card_id, user_id, ease_factor, interval_days, "
                " repetitions, lapses, last_reviewed_at, due_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, user_id, new_ease, new_interval,
                 new_rep, new_lapses, now, new_due),
            )
        conn.execute(
            "INSERT INTO flashcard_reviews "
            "(id, card_id, user_id, grade, time_seconds, "
            " new_interval, new_ease, reviewed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, card_id, user_id, grade,
             time_seconds, new_interval, new_ease, now),
        )
    return ReviewOutcome(
        card_id=card_id,
        new_interval=round(new_interval, 2),
        new_ease=round(new_ease, 3),
        new_due_at=new_due, repetitions=new_rep,
        lapses=new_lapses,
    )


def get_card_state(
    *, card_id: str, user_id: str,
) -> CardState | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT card_id, user_id, ease_factor, interval_days, "
            "       repetitions, last_reviewed_at, due_at, lapses "
            "FROM flashcards_user_state "
            "WHERE card_id = ? AND user_id = ?",
            (card_id, user_id),
        ).fetchone()
    if not r:
        return None
    return CardState(
        card_id=r[0], user_id=r[1], ease_factor=r[2],
        interval_days=r[3], repetitions=r[4],
        last_reviewed_at=r[5], due_at=r[6], lapses=r[7],
    )


def due_queue(
    *,
    user_id: str,
    deck_id: str | None = None,
    limit: int = 20,
    include_new: bool = True,
) -> list[Card]:
    """Cards the user should review now. Pulls due cards
    (due_at <= now) ordered by due_at ASC, plus optionally a
    handful of brand-new cards (no state row yet) from their own
    decks.

    The default queue is 'due' cards from any deck the user has
    cards in; UI typically scopes to a deck."""
    limit = max(1, min(limit, 200))
    now = time.time()
    # Due cards
    sql_due_args = []
    sql_due = (
        "SELECT c.id, c.deck_id, c.position, c.front, c.back, "
        "       c.hint, c.source_ref, c.citation_json, c.created_at "
        "FROM flashcards c "
        "JOIN flashcards_user_state s "
        "  ON s.card_id = c.id AND s.user_id = ? "
        "WHERE s.due_at <= ?"
    )
    sql_due_args = [user_id, now]
    if deck_id:
        sql_due += " AND c.deck_id = ?"
        sql_due_args.append(deck_id)
    sql_due += " ORDER BY s.due_at ASC LIMIT ?"
    sql_due_args.append(limit)

    with _conn() as conn:
        due_rows = conn.execute(sql_due, sql_due_args).fetchall()
    out: list[Card] = []
    for r in due_rows:
        out.append(Card(
            id=r[0], deck_id=r[1], position=r[2],
            front=r[3], back=r[4], hint=r[5],
            source_ref=r[6],
            citation=json.loads(r[7]) if r[7] else None,
            created_at=r[8],
        ))
    if include_new and len(out) < limit:
        # New cards = cards in user's decks with no state row yet
        slack = limit - len(out)
        with _conn() as conn:
            args = [user_id]
            sql_new = (
                "SELECT c.id, c.deck_id, c.position, c.front, c.back, "
                "       c.hint, c.source_ref, c.citation_json, "
                "       c.created_at "
                "FROM flashcards c "
                "JOIN flashcard_decks d ON d.id = c.deck_id "
                "LEFT JOIN flashcards_user_state s "
                "  ON s.card_id = c.id AND s.user_id = ? "
                "WHERE d.owner_user_id = ? AND s.card_id IS NULL"
            )
            args.append(user_id)
            if deck_id:
                sql_new += " AND c.deck_id = ?"
                args.append(deck_id)
            sql_new += " ORDER BY c.created_at LIMIT ?"
            args.append(slack)
            new_rows = conn.execute(sql_new, args).fetchall()
        for r in new_rows:
            out.append(Card(
                id=r[0], deck_id=r[1], position=r[2],
                front=r[3], back=r[4], hint=r[5],
                source_ref=r[6],
                citation=json.loads(r[7]) if r[7] else None,
                created_at=r[8],
            ))
    return out


def user_stats(user_id: str) -> dict:
    """Per-user SRS stats — drives the dashboard streak / retention
    widget."""
    now = time.time()
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM flashcards_user_state "
            "WHERE user_id = ?", (user_id,),
        ).fetchone()
        learning = conn.execute(
            "SELECT COUNT(*) FROM flashcards_user_state "
            "WHERE user_id = ? AND due_at <= ?",
            (user_id, now),
        ).fetchone()
        # Reviews in last 24h
        recent = conn.execute(
            "SELECT COUNT(*) FROM flashcard_reviews "
            "WHERE user_id = ? AND reviewed_at >= ?",
            (user_id, now - 86400),
        ).fetchone()
        # Retention = % of recent reviews that got grade ≥ 3
        retention = conn.execute(
            "SELECT COUNT(*), "
            "       SUM(CASE WHEN grade >= 3 THEN 1 ELSE 0 END) "
            "FROM flashcard_reviews "
            "WHERE user_id = ? AND reviewed_at >= ?",
            (user_id, now - 86400 * 30),
        ).fetchone()
        total_30d = int(retention[0] or 0)
        good_30d = int(retention[1] or 0)
    return {
        "tracked_cards": int(r[0] or 0),
        "due_now": int(learning[0] or 0),
        "reviews_24h": int(recent[0] or 0),
        "retention_30d": (
            round(good_30d / total_30d, 3) if total_30d else None
        ),
    }
