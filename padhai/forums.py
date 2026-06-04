"""N1 — Parent / student community forums.

Per-org + per-grade + public discussion forums. PTA-style. Replaces
the WhatsApp chaos parents currently navigate.

Three tables:
  forum_threads     one row per top-level thread
  forum_posts       one row per post (reply tree via parent_post_id)
  forum_flags       moderation flags; auto-hide at threshold

Scoping:
  scope='org'         restricted to org members
  scope='class'       restricted to that class's roster
  scope='grade'       cross-org, students of that grade (public)
  scope='public'      anyone

Moderation:
  - Any user can flag a post (one flag per (post, user) via UNIQUE)
  - At AUTO_HIDE_FLAG_COUNT distinct flags, post is auto-hidden
  - Org admin can hard-delete + lock threads
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS forum_threads (
    id              TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,           -- 'org' | 'class' | 'grade' | 'public'
    scope_key       TEXT,                     -- org_id | class_id | 'grade-8' | NULL
    title           TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      REAL NOT NULL,
    last_activity_at REAL NOT NULL,
    post_count      INTEGER NOT NULL DEFAULT 1,
    locked          INTEGER NOT NULL DEFAULT 0,
    pinned          INTEGER NOT NULL DEFAULT 0,
    deleted_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_threads_scope
    ON forum_threads(scope, scope_key, last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_threads_pinned
    ON forum_threads(pinned DESC, last_activity_at DESC);

CREATE TABLE IF NOT EXISTS forum_posts (
    id              TEXT PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    author_user_id  TEXT NOT NULL,
    body            TEXT NOT NULL,
    parent_post_id  TEXT,                     -- NULL = top-level reply to thread
    created_at      REAL NOT NULL,
    edited_at       REAL,
    flag_count      INTEGER NOT NULL DEFAULT 0,
    hidden          INTEGER NOT NULL DEFAULT 0,
    deleted_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_posts_thread
    ON forum_posts(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_author
    ON forum_posts(author_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS forum_flags (
    post_id         TEXT NOT NULL,
    flagger_user_id TEXT NOT NULL,
    reason          TEXT,
    created_at      REAL NOT NULL,
    PRIMARY KEY (post_id, flagger_user_id)
);
"""


AUTO_HIDE_FLAG_COUNT = 3   # post hidden once it hits this distinct flag count

VALID_SCOPES = frozenset({"org", "class", "grade", "public"})

MAX_TITLE_LEN = 200
MAX_BODY_LEN = 16000


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
class Thread:
    id: str
    scope: str
    scope_key: str | None
    title: str
    created_by: str
    created_at: float
    last_activity_at: float
    post_count: int
    locked: bool
    pinned: bool


@dataclass(frozen=True)
class Post:
    id: str
    thread_id: str
    author_user_id: str
    body: str
    parent_post_id: str | None
    created_at: float
    edited_at: float | None
    flag_count: int
    hidden: bool


_T_SEL = (
    "id, scope, scope_key, title, created_by, created_at, "
    "last_activity_at, post_count, locked, pinned"
)
_P_SEL = (
    "id, thread_id, author_user_id, body, parent_post_id, "
    "created_at, edited_at, flag_count, hidden"
)


def _row_to_thread(r) -> Thread:
    return Thread(
        id=r[0], scope=r[1], scope_key=r[2], title=r[3],
        created_by=r[4], created_at=r[5], last_activity_at=r[6],
        post_count=r[7], locked=bool(r[8]), pinned=bool(r[9]),
    )


def _row_to_post(r) -> Post:
    return Post(
        id=r[0], thread_id=r[1], author_user_id=r[2], body=r[3],
        parent_post_id=r[4], created_at=r[5], edited_at=r[6],
        flag_count=r[7], hidden=bool(r[8]),
    )


# ---------- threads ----------

def create_thread(
    *,
    scope: str,
    scope_key: str | None,
    title: str,
    body: str,
    author_user_id: str,
) -> tuple[Thread, Post]:
    """Open a new thread + insert the opening post atomically. Scope
    rules are enforced by the caller (we don't know org membership
    from here)."""
    if scope not in VALID_SCOPES:
        raise ValueError(f"scope must be in {sorted(VALID_SCOPES)}")
    if scope in ("org", "class") and not scope_key:
        raise ValueError(f"scope_key required for scope={scope!r}")
    title = (title or "").strip()
    body = (body or "").strip()
    if len(title) < 4 or len(title) > MAX_TITLE_LEN:
        raise ValueError(f"title must be [4, {MAX_TITLE_LEN}] chars")
    if len(body) < 1 or len(body) > MAX_BODY_LEN:
        raise ValueError(f"body must be [1, {MAX_BODY_LEN}] chars")
    tid = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO forum_threads "
            "(id, scope, scope_key, title, created_by, created_at, "
            " last_activity_at, post_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (tid, scope, scope_key, title, author_user_id, now, now),
        )
        conn.execute(
            "INSERT INTO forum_posts "
            "(id, thread_id, author_user_id, body, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, tid, author_user_id, body, now),
        )
    return get_thread(tid), get_post(pid)  # type: ignore[return-value]


def get_thread(thread_id: str) -> Thread | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_T_SEL} FROM forum_threads "
            "WHERE id = ? AND deleted_at IS NULL",
            (thread_id,),
        ).fetchone()
    return _row_to_thread(r) if r else None


def list_threads(
    *,
    scope: str,
    scope_key: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list[Thread]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    sql = (
        f"SELECT {_T_SEL} FROM forum_threads "
        "WHERE scope = ? AND deleted_at IS NULL"
    )
    params: list = [scope]
    if scope_key is not None:
        sql += " AND scope_key = ?"; params.append(scope_key)
    sql += " ORDER BY pinned DESC, last_activity_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_thread(r) for r in rows]


def lock_thread(*, thread_id: str, locked: bool = True) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE forum_threads SET locked = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (1 if locked else 0, thread_id),
        )
        return cur.rowcount > 0


def pin_thread(*, thread_id: str, pinned: bool = True) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE forum_threads SET pinned = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (1 if pinned else 0, thread_id),
        )
        return cur.rowcount > 0


def delete_thread(*, thread_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE forum_threads SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (time.time(), thread_id),
        )
        return cur.rowcount > 0


# ---------- posts ----------

def reply(
    *,
    thread_id: str,
    author_user_id: str,
    body: str,
    parent_post_id: str | None = None,
) -> Post:
    body = (body or "").strip()
    if len(body) < 1 or len(body) > MAX_BODY_LEN:
        raise ValueError(f"body must be [1, {MAX_BODY_LEN}] chars")
    t = get_thread(thread_id)
    if not t:
        raise ValueError(f"thread {thread_id!r} not found")
    if t.locked:
        raise ValueError("thread is locked")
    pid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO forum_posts "
            "(id, thread_id, author_user_id, body, parent_post_id, "
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, thread_id, author_user_id, body, parent_post_id, now),
        )
        conn.execute(
            "UPDATE forum_threads SET "
            " post_count = post_count + 1, last_activity_at = ? "
            "WHERE id = ?",
            (now, thread_id),
        )
    return get_post(pid)  # type: ignore[return-value]


def get_post(post_id: str) -> Post | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_P_SEL} FROM forum_posts "
            "WHERE id = ? AND deleted_at IS NULL",
            (post_id,),
        ).fetchone()
    return _row_to_post(r) if r else None


def list_posts(
    *,
    thread_id: str,
    include_hidden: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Post]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    sql = (
        f"SELECT {_P_SEL} FROM forum_posts "
        "WHERE thread_id = ? AND deleted_at IS NULL"
    )
    params: list = [thread_id]
    if not include_hidden:
        sql += " AND hidden = 0"
    sql += " ORDER BY created_at LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_post(r) for r in rows]


def edit_post(*, post_id: str, author_user_id: str, body: str) -> bool:
    """Only the author can edit. Edits update `edited_at` so the UI
    can show "edited" badge."""
    body = (body or "").strip()
    if len(body) < 1 or len(body) > MAX_BODY_LEN:
        raise ValueError(f"body must be [1, {MAX_BODY_LEN}] chars")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE forum_posts SET body = ?, edited_at = ? "
            "WHERE id = ? AND author_user_id = ? AND deleted_at IS NULL",
            (body, time.time(), post_id, author_user_id),
        )
        return cur.rowcount > 0


def delete_post(*, post_id: str, author_user_id: str | None = None) -> bool:
    """Author can soft-delete their own; if author_user_id is None,
    treat as moderator-level delete."""
    sql = "UPDATE forum_posts SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL"
    params: list = [time.time(), post_id]
    if author_user_id is not None:
        sql += " AND author_user_id = ?"
        params.append(author_user_id)
    with _conn() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0


# ---------- moderation: flags ----------

def flag_post(
    *,
    post_id: str,
    flagger_user_id: str,
    reason: str | None = None,
) -> dict:
    """One flag per (post, user) — UNIQUE constraint. Auto-hides the
    post once `AUTO_HIDE_FLAG_COUNT` distinct flags accumulate.
    Returns {flag_count, auto_hidden}."""
    if not get_post(post_id):
        raise ValueError("post not found")
    auto_hidden = False
    with _conn() as conn:
        try:  # noqa: SIM105
            conn.execute(
                "INSERT INTO forum_flags "
                "(post_id, flagger_user_id, reason, created_at) "
                "VALUES (?, ?, ?, ?)",
                (post_id, flagger_user_id, (reason or "")[:500],
                 time.time()),
            )
        except sqlite3.IntegrityError:
            # Already flagged by this user — idempotent
            pass
        r = conn.execute(
            "SELECT COUNT(*) FROM forum_flags WHERE post_id = ?",
            (post_id,),
        ).fetchone()
        new_count = int(r[0] or 0)
        cur = conn.execute(
            "UPDATE forum_posts SET flag_count = ?, "
            " hidden = CASE WHEN ? >= ? THEN 1 ELSE hidden END "
            "WHERE id = ?",
            (new_count, new_count, AUTO_HIDE_FLAG_COUNT, post_id),
        )
        if cur.rowcount > 0 and new_count >= AUTO_HIDE_FLAG_COUNT:
            auto_hidden = True
    return {
        "flag_count": new_count,
        "auto_hidden": auto_hidden,
        "auto_hide_threshold": AUTO_HIDE_FLAG_COUNT,
    }


def unhide_post(*, post_id: str) -> bool:
    """Moderator override — clears flag count + un-hides."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE forum_posts SET hidden = 0, flag_count = 0 "
            "WHERE id = ? AND deleted_at IS NULL",
            (post_id,),
        )
        # Also clear flags so the user can't immediately re-trigger
        conn.execute(
            "DELETE FROM forum_flags WHERE post_id = ?",
            (post_id,),
        )
        return cur.rowcount > 0


def flagged_queue(*, limit: int = 50) -> list[Post]:
    """Moderator queue — currently-hidden posts, most recent first.
    Also includes posts close to threshold (>=2 flags) so a moderator
    can dismiss before auto-hide kicks in."""
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_P_SEL} FROM forum_posts "
            "WHERE deleted_at IS NULL "
            "AND (hidden = 1 OR flag_count >= ?) "
            "ORDER BY flag_count DESC, created_at DESC LIMIT ?",
            (max(1, AUTO_HIDE_FLAG_COUNT - 1), limit),
        ).fetchall()
    return [_row_to_post(r) for r in rows]
