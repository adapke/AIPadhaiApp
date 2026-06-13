"""Seed the curriculum_topics table from the static CURRICULUM list.

/curriculum/index merges DB rows over the static seed in
padhai/curriculum.py. The Postgres table starts empty on every fresh
deploy, so the only way to keep our seeded chapter data after a DB
rebuild is to load it into the table itself.

This script is idempotent — it uses ON CONFLICT to skip rows that
already match (board, class, subject, chapter_no).

Run after liquibase apply, or any time the static CURRICULUM grows:
    PYTHONPATH=. python scripts/seed_curriculum_topics.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid


def _load_env() -> None:
    """Read DATABASE_URL from .env without needing python-dotenv."""
    if "DATABASE_URL" in os.environ:
        return
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
                    return
    except FileNotFoundError:
        pass


def main() -> int:
    _load_env()
    if not os.environ.get("DATABASE_URL"):
        print("[err] DATABASE_URL not set (in env or .env). Skipping.")
        return 1

    import psycopg

    from padhai.curriculum import CURRICULUM

    conn = psycopg.connect(
        os.environ["DATABASE_URL"], options="-c search_path=public",
    )
    cur = conn.cursor()

    # Create the table if it doesn't exist. Schema mirrors what the
    # /curriculum/index route SELECTs from it.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS curriculum_topics (
            id            TEXT PRIMARY KEY,
            board         TEXT NOT NULL,
            class         INTEGER NOT NULL,
            subject       TEXT NOT NULL,
            chapter_no    INTEGER NOT NULL,
            chapter_title TEXT NOT NULL,
            level         TEXT,
            summary       TEXT,
            topics        TEXT,
            created_at    DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW()),
            UNIQUE (board, class, subject, chapter_no)
        )
        """,
    )

    cur.execute("SELECT COUNT(*) FROM curriculum_topics")
    before = cur.fetchone()[0]
    print(f"[seed] curriculum_topics before: {before} rows")

    inserted = 0
    skipped = 0
    for entry in CURRICULUM:
        topics_json = json.dumps(entry.get("topics", []))
        try:
            cur.execute(
                """
                INSERT INTO curriculum_topics
                  (id, board, class, subject, chapter_no, chapter_title,
                   level, summary, topics)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (board, class, subject, chapter_no) DO NOTHING
                """,
                (
                    uuid.uuid4().hex,
                    entry["board"],
                    entry["class"],
                    entry["subject"],
                    entry["chapter_no"],
                    entry["chapter_title"],
                    entry.get("level"),
                    entry.get("summary"),
                    topics_json,
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"[err] failed to insert {entry.get('chapter_title')}: {e}")

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM curriculum_topics")
    after = cur.fetchone()[0]
    print(f"[seed] inserted: {inserted}, skipped (already present): {skipped}")
    print(f"[seed] curriculum_topics after:  {after} rows")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
