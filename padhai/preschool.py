"""K4 — K-2 preschool content + 'Kids Mode v2'.

4-6 year olds. Different UI metaphor: parent-driven, swipe-only,
big-tap targets, audio-first. The existing 'kids' video mode
(theme_for_level('kg') from v0.4) handles content rendering; this
module adds the K4 surface — a curated catalog of preschool
activities + a simplified quiz format.

Catalog content types:
- phonics       (audio-first, single-letter focus)
- counting      (1-20 number recognition + arithmetic)
- shapes        (basic geometric shapes + colours)
- nursery       (sing-along nursery rhymes)
- alphabet      (Hindi varnamala + English)
- stories       (1-2 min Panchatantra / Aesop simplified)
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
CREATE TABLE IF NOT EXISTS preschool_activities (
    id           TEXT PRIMARY KEY,
    activity_type TEXT NOT NULL,        -- phonics | counting | shapes | nursery | alphabet | stories
    title        TEXT NOT NULL,
    language     TEXT NOT NULL DEFAULT 'en',
    age_min      INTEGER NOT NULL DEFAULT 4,
    age_max      INTEGER NOT NULL DEFAULT 6,
    media_url    TEXT,                   -- video URL once rendered
    asset_json   TEXT,                   -- pre-rendered SVG + audio refs
    duration_seconds INTEGER,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_preschool_type_lang
    ON preschool_activities(activity_type, language, active);
"""

VALID_TYPES = {
    "phonics", "counting", "shapes", "nursery", "alphabet", "stories",
}


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
    # Seed with a handful of starter activities so a fresh deploy has
    # something to render for first-time users. Idempotent — uses
    # title as the natural key for the seed dedup.
    _seed_starter_catalog()


_STARTER_CATALOG = [
    {"activity_type": "phonics", "title": "A is for Apple",
     "language": "en", "duration_seconds": 30, "sort_order": 1},
    {"activity_type": "phonics", "title": "B is for Ball",
     "language": "en", "duration_seconds": 30, "sort_order": 2},
    {"activity_type": "counting", "title": "Count 1 to 5",
     "language": "en", "duration_seconds": 45, "sort_order": 1},
    {"activity_type": "counting", "title": "1 से 5 तक गिनती",
     "language": "hi", "duration_seconds": 45, "sort_order": 1},
    {"activity_type": "shapes", "title": "Circle, Square, Triangle",
     "language": "en", "duration_seconds": 60, "sort_order": 1},
    {"activity_type": "alphabet", "title": "हिंदी वर्णमाला — अ से ज्ञ",
     "language": "hi", "duration_seconds": 180, "sort_order": 1},
    {"activity_type": "nursery", "title": "Twinkle Twinkle Little Star",
     "language": "en", "duration_seconds": 90, "sort_order": 1},
    {"activity_type": "nursery", "title": "मछली जल की रानी है",
     "language": "hi", "duration_seconds": 90, "sort_order": 1},
    {"activity_type": "stories", "title": "The Thirsty Crow",
     "language": "en", "duration_seconds": 120, "sort_order": 1},
    {"activity_type": "stories", "title": "खरगोश और कछुआ",
     "language": "hi", "duration_seconds": 120, "sort_order": 1},
]


def _seed_starter_catalog() -> None:
    """Insert each starter row if (activity_type, language, title)
    not yet present. Idempotent."""
    with _conn() as conn:
        for entry in _STARTER_CATALOG:
            existing = conn.execute(
                "SELECT id FROM preschool_activities "
                "WHERE activity_type = ? AND language = ? AND title = ?",
                (entry["activity_type"], entry["language"], entry["title"]),
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO preschool_activities "
                "(id, activity_type, title, language, age_min, age_max, "
                " duration_seconds, sort_order, active, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,?)",
                (uuid.uuid4().hex, entry["activity_type"],
                 entry["title"], entry["language"],
                 entry.get("age_min", 4), entry.get("age_max", 6),
                 entry["duration_seconds"], entry["sort_order"],
                 time.time()),
            )


@dataclass(frozen=True)
class Activity:
    id: str
    activity_type: str
    title: str
    language: str
    age_min: int
    age_max: int
    duration_seconds: int
    media_url: str | None
    sort_order: int
    active: bool


def _row_to_activity(r) -> Activity:
    return Activity(
        id=r[0], activity_type=r[1], title=r[2], language=r[3],
        age_min=r[4], age_max=r[5], duration_seconds=r[6],
        media_url=r[7], sort_order=r[8], active=bool(r[9]),
    )


def list_activities(
    *, language: str | None = None,
    activity_type: str | None = None,
    age: int | None = None,
) -> list[Activity]:
    sql = ("SELECT id, activity_type, title, language, age_min, "
           "age_max, duration_seconds, media_url, sort_order, active "
           "FROM preschool_activities WHERE active = 1")
    params: list = []
    if language:
        sql += " AND language = ?"; params.append(language)
    if activity_type:
        if activity_type not in VALID_TYPES:
            raise ValueError(
                f"activity_type must be in {sorted(VALID_TYPES)}"
            )
        sql += " AND activity_type = ?"; params.append(activity_type)
    if age is not None:
        sql += " AND age_min <= ? AND age_max >= ?"
        params.extend([age, age])
    sql += " ORDER BY activity_type, sort_order, title"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_activity(r) for r in rows]


def categories() -> dict[str, int]:
    """Counts by activity_type. Drives the kids-mode home screen
    pills."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT activity_type, COUNT(*) FROM preschool_activities "
            "WHERE active = 1 GROUP BY activity_type",
        ).fetchall()
    return {r[0]: r[1] for r in rows}
