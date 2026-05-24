"""v3.5 — Community moderation primitives.

Per gap-review §6 — community modules ship (forums.py, study_buddies.py,
mentorship.py, doubt_clearing.py) but the moderation policy + reviewer
queue + reaction throttling are missing. Without these, the community
loop falls over the moment it has actual users — especially with the
mix of minors + adults that comes with a K-PhD platform.

Three concerns this module solves:

  1. **Auto-flag pipeline** — rule-based pre-filter that runs on
     every user-generated content write (forum post, study buddy
     message, doubt reply). Catches the easy ones (banned keywords,
     URL spam, all-caps shouting) without an LLM call.

  2. **Reviewer queue** — when auto-flag scores above the threshold,
     content lands in a queue. Trained reviewers approve / remove /
     escalate. Queue items have SLA timers; aged items page on-call.

  3. **Reaction throttling** — rate-limits "👍 / ❤️ / 🚀" reactions
     per (user, target) to prevent gaming + brigading. Token-bucket
     style; existing `padhai/rate_limit.py` is too coarse for
     per-target throttling.

Three tables:
  mod_flagged_content   one row per item the auto-filter scored above threshold
  mod_actions           one row per reviewer decision (audit trail)
  mod_reactions         one row per (user, target, kind) — natural rate-limit

Public APIs:
  scan(content_kind, content_id, body, author_user_id) → ScanResult
  list_queue(status='open', limit=50) → list[FlaggedItem]
  decide(item_id, action, reviewer_user_id, reason)
  react(target_kind, target_id, user_id, kind) → ok|False
  reaction_counts(target_kind, target_id) → {kind: count}

Hook points: forums + study_buddies + doubt_clearing call `scan()`
on every write. Override list of auto-block keywords lives in
`PADHAI_MODERATION_BLOCKLIST` env var (comma-sep).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS mod_flagged_content (
    id              TEXT PRIMARY KEY,
    content_kind    TEXT NOT NULL,                   -- 'forum_post' / 'study_buddy_msg' / 'doubt_reply' / 'mentor_msg'
    content_id      TEXT NOT NULL,                   -- FK in originating table
    author_user_id  TEXT NOT NULL,
    body_snippet    TEXT NOT NULL,                   -- first 500 chars
    score           REAL NOT NULL,                   -- 0..1, higher = riskier
    rules_triggered TEXT,                             -- JSON list ['blocklist', 'allcaps']
    severity        TEXT NOT NULL DEFAULT 'flag',
    -- 'flag' (manual review) | 'auto_remove' | 'shadow' (hide from feeds)
    status          TEXT NOT NULL DEFAULT 'open',
    -- 'open' | 'approved' | 'removed' | 'escalated' | 'auto_resolved'
    sla_due_at      REAL,
    created_at      REAL NOT NULL,
    UNIQUE (content_kind, content_id)
);
CREATE INDEX IF NOT EXISTS idx_mfc_status
    ON mod_flagged_content(status, sla_due_at);
CREATE INDEX IF NOT EXISTS idx_mfc_author
    ON mod_flagged_content(author_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mod_actions (
    id              TEXT PRIMARY KEY,
    item_id         TEXT NOT NULL,
    reviewer_user_id TEXT NOT NULL,
    action          TEXT NOT NULL,                   -- 'approve' | 'remove' | 'escalate' | 'restore'
    reason          TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ma_item
    ON mod_actions(item_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mod_reactions (
    id              TEXT PRIMARY KEY,
    target_kind     TEXT NOT NULL,                   -- 'forum_post' / 'doubt_reply' / ...
    target_id       TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    kind            TEXT NOT NULL,                   -- 'like' / 'helpful' / 'thank' / 'report'
    created_at      REAL NOT NULL,
    UNIQUE (target_kind, target_id, user_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_mr_target
    ON mod_reactions(target_kind, target_id, kind);
CREATE INDEX IF NOT EXISTS idx_mr_user
    ON mod_reactions(user_id, created_at DESC);
"""


VALID_CONTENT_KINDS = frozenset({
    "forum_post", "study_buddy_msg", "doubt_reply",
    "mentor_msg", "tutor_session_msg",
})

VALID_SEVERITIES = frozenset({"flag", "auto_remove", "shadow"})

VALID_STATUSES = frozenset({
    "open", "approved", "removed", "escalated", "auto_resolved",
})

VALID_ACTIONS = frozenset({
    "approve", "remove", "escalate", "restore",
})

VALID_REACTION_KINDS = frozenset({
    "like", "helpful", "thank", "report",
})


# Score thresholds. Tuned conservatively — we'd rather flag a few
# borderline messages and let reviewers approve them than miss the
# obviously harmful ones.
AUTO_REMOVE_THRESHOLD = 0.90
FLAG_THRESHOLD = 0.40

# SLA — auto-flagged content should be reviewed within this window
# before the auditor pages on-call.
SLA_HOURS_DEFAULT = 24


# Default blocklist — slur stand-ins + the worst URL spam patterns.
# Production deployments extend this via env var; we ship a tiny
# baseline so dev/test work out of the box.
_DEFAULT_BLOCKLIST = frozenset({
    "spam_link_xyz", "buy_followers", "free_money_now",
    "click_here_fast", "guaranteed_win",
})


def _blocklist() -> frozenset[str]:
    env = os.environ.get("PADHAI_MODERATION_BLOCKLIST", "")
    extra = {t.strip().lower() for t in env.split(",") if t.strip()}
    return frozenset(_DEFAULT_BLOCKLIST | extra)


# URL-spam pattern: 3+ URLs in a single short message
_URL_RE = re.compile(r"https?://\S+")


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


# ---------- dataclasses ----------

@dataclass(frozen=True)
class ScanResult:
    score: float                       # 0..1
    rules_triggered: list[str]
    severity: str                       # 'flag' / 'auto_remove' / None ('clean')
    flag_id: str | None                 # id of persisted row, or None for clean


@dataclass(frozen=True)
class FlaggedItem:
    id: str
    content_kind: str
    content_id: str
    author_user_id: str
    body_snippet: str
    score: float
    rules_triggered: list[str]
    severity: str
    status: str
    sla_due_at: float | None
    created_at: float


# ---------- the scanner ----------

def _score_blocklist(text: str) -> tuple[float, list[str]]:
    bl = _blocklist()
    lowered = text.lower()
    hits = [t for t in bl if t in lowered]
    if not hits:
        return 0.0, []
    # Each hit adds 0.5, capped at 1.0. Two hits → auto-remove.
    return min(1.0, 0.5 * len(hits)), [f"blocklist:{h}" for h in hits[:3]]


def _score_url_spam(text: str) -> tuple[float, list[str]]:
    urls = _URL_RE.findall(text)
    if len(urls) >= 3 and len(text) < 500:
        return 0.7, ["url_spam"]
    if len(urls) >= 5:
        return 0.5, ["many_urls"]
    return 0.0, []


def _score_allcaps(text: str) -> tuple[float, list[str]]:
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) < 40:
        return 0.0, []
    upper = sum(1 for c in alpha if c.isupper())
    if upper / len(alpha) >= 0.75:
        return 0.25, ["allcaps"]
    return 0.0, []


def _score_repetition(text: str) -> tuple[float, list[str]]:
    """Repeated characters / words ('aaaaaaaaa' or 'win win win win win')."""
    # Repeated char run >= 8
    if re.search(r"(.)\1{7,}", text):
        return 0.3, ["repeated_chars"]
    # Same word 5+ times in a row
    if re.search(r"\b(\w+)(\s+\1){4,}\b", text, re.IGNORECASE):
        return 0.3, ["repeated_word"]
    return 0.0, []


def scan(
    *,
    content_kind: str,
    content_id: str,
    body: str,
    author_user_id: str,
    persist: bool = True,
    sla_hours: int = SLA_HOURS_DEFAULT,
) -> ScanResult:
    """Run the auto-filter over a piece of UGC. If `persist=True`
    (default) and the score crosses `FLAG_THRESHOLD`, drop a row
    into `mod_flagged_content`.

    Caller is the originating module (forums/study_buddies/etc) —
    it inspects the ScanResult.severity and decides whether to
    accept-as-is, hide-from-feed (shadow), or refuse-the-write
    (auto_remove)."""
    if content_kind not in VALID_CONTENT_KINDS:
        raise ValueError(
            f"content_kind must be in {sorted(VALID_CONTENT_KINDS)}",
        )
    body = body or ""
    if len(body) > 100_000:
        raise ValueError("body must be <= 100k chars")
    rules: list[str] = []
    score = 0.0
    for fn in (
        _score_blocklist, _score_url_spam,
        _score_allcaps, _score_repetition,
    ):
        s, r = fn(body)
        score = min(1.0, score + s)
        rules.extend(r)
    if score >= AUTO_REMOVE_THRESHOLD:
        severity = "auto_remove"
    elif score >= FLAG_THRESHOLD:
        severity = "flag"
    else:
        return ScanResult(
            score=round(score, 3), rules_triggered=rules,
            severity="clean", flag_id=None,
        )
    if not persist:
        return ScanResult(
            score=round(score, 3), rules_triggered=rules,
            severity=severity, flag_id=None,
        )
    fid = uuid.uuid4().hex
    now = time.time()
    sla = now + sla_hours * 3600
    initial_status = (
        "auto_resolved" if severity == "auto_remove" else "open"
    )
    with _conn() as conn:
        try:
            conn.execute(
                "INSERT INTO mod_flagged_content "
                "(id, content_kind, content_id, author_user_id, "
                " body_snippet, score, rules_triggered, severity, "
                " status, sla_due_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, content_kind, content_id, author_user_id,
                 body[:500], round(score, 3),
                 json.dumps(rules), severity,
                 initial_status, sla, now),
            )
        except sqlite3.IntegrityError:
            # Duplicate scan for same content — keep the existing row
            r = conn.execute(
                "SELECT id FROM mod_flagged_content "
                "WHERE content_kind = ? AND content_id = ?",
                (content_kind, content_id),
            ).fetchone()
            if r:
                fid = r[0]
    return ScanResult(
        score=round(score, 3), rules_triggered=rules,
        severity=severity, flag_id=fid,
    )


# ---------- queue + decisions ----------

def list_queue(
    *,
    status: str = "open",
    severity: str | None = None,
    content_kind: str | None = None,
    sla_breached_only: bool = False,
    limit: int = 50,
) -> list[FlaggedItem]:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    if severity and severity not in VALID_SEVERITIES:
        raise ValueError(
            f"severity must be in {sorted(VALID_SEVERITIES)}",
        )
    if content_kind and content_kind not in VALID_CONTENT_KINDS:
        raise ValueError(
            f"content_kind must be in {sorted(VALID_CONTENT_KINDS)}",
        )
    limit = max(1, min(limit, 500))
    sql = (
        "SELECT id, content_kind, content_id, author_user_id, "
        "       body_snippet, score, rules_triggered, severity, "
        "       status, sla_due_at, created_at "
        "FROM mod_flagged_content WHERE status = ?"
    )
    params: list = [status]
    if severity:
        sql += " AND severity = ?"; params.append(severity)
    if content_kind:
        sql += " AND content_kind = ?"; params.append(content_kind)
    if sla_breached_only:
        sql += " AND sla_due_at IS NOT NULL AND sla_due_at < ?"
        params.append(time.time())
    sql += " ORDER BY score DESC, created_at LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        FlaggedItem(
            id=r[0], content_kind=r[1], content_id=r[2],
            author_user_id=r[3], body_snippet=r[4], score=r[5],
            rules_triggered=json.loads(r[6]) if r[6] else [],
            severity=r[7], status=r[8],
            sla_due_at=r[9], created_at=r[10],
        )
        for r in rows
    ]


def get_item(item_id: str) -> FlaggedItem | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, content_kind, content_id, author_user_id, "
            "       body_snippet, score, rules_triggered, severity, "
            "       status, sla_due_at, created_at "
            "FROM mod_flagged_content WHERE id = ?",
            (item_id,),
        ).fetchone()
    if not r:
        return None
    return FlaggedItem(
        id=r[0], content_kind=r[1], content_id=r[2],
        author_user_id=r[3], body_snippet=r[4], score=r[5],
        rules_triggered=json.loads(r[6]) if r[6] else [],
        severity=r[7], status=r[8],
        sla_due_at=r[9], created_at=r[10],
    )


def decide(
    *,
    item_id: str,
    action: str,
    reviewer_user_id: str,
    reason: str | None = None,
) -> bool:
    """Reviewer takes a decision. Maps action → resulting status:
        approve → 'approved'
        remove → 'removed'
        escalate → 'escalated' (passes up to senior reviewer)
        restore → 'approved' (used on auto_resolved auto_removes
                  that turn out to be false positives)

    Records the action in `mod_actions` for audit."""
    if action not in VALID_ACTIONS:
        raise ValueError(f"action must be in {sorted(VALID_ACTIONS)}")
    item = get_item(item_id)
    if not item:
        return False
    status_map = {
        "approve": "approved", "remove": "removed",
        "escalate": "escalated", "restore": "approved",
    }
    new_status = status_map[action]
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE mod_flagged_content SET status = ? "
            "WHERE id = ? AND status IN ('open', 'escalated', 'auto_resolved')",
            (new_status, item_id),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO mod_actions "
            "(id, item_id, reviewer_user_id, action, reason, "
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, item_id, reviewer_user_id,
             action, (reason or "")[:500], now),
        )
    return True


def list_actions_for_item(item_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, reviewer_user_id, action, reason, "
            "       created_at "
            "FROM mod_actions WHERE item_id = ? "
            "ORDER BY created_at DESC",
            (item_id,),
        ).fetchall()
    return [
        {"id": r[0], "reviewer_user_id": r[1],
         "action": r[2], "reason": r[3], "created_at": r[4]}
        for r in rows
    ]


def queue_stats() -> dict:
    """Dashboard rollup — drives the moderator's home view."""
    now = time.time()
    with _conn() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM mod_flagged_content "
            "GROUP BY status",
        ).fetchall()
        breached = conn.execute(
            "SELECT COUNT(*) FROM mod_flagged_content "
            "WHERE status = 'open' AND sla_due_at < ?",
            (now,),
        ).fetchone()
    return {
        "by_status": {r[0]: r[1] for r in by_status},
        "sla_breached_open": int(breached[0] or 0),
    }


# ---------- reactions + throttle ----------

def react(
    *,
    target_kind: str,
    target_id: str,
    user_id: str,
    kind: str,
) -> bool:
    """Idempotent: same user reacting again with the same kind on
    the same target is a no-op. Different kinds are independent.
    UNIQUE constraint enforces the throttle naturally."""
    if target_kind not in VALID_CONTENT_KINDS:
        raise ValueError(
            f"target_kind must be in {sorted(VALID_CONTENT_KINDS)}",
        )
    if kind not in VALID_REACTION_KINDS:
        raise ValueError(
            f"kind must be in {sorted(VALID_REACTION_KINDS)}",
        )
    rid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO mod_reactions "
                "(id, target_kind, target_id, user_id, kind, "
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (rid, target_kind, target_id, user_id, kind,
                 time.time()),
            )
    except sqlite3.IntegrityError:
        return False    # already reacted with this kind
    # 'report' reactions feed back into the moderation queue
    if kind == "report":
        # Build a synthetic FlaggedItem if the target isn't already
        # in the queue. Reviewers see 'user_report' in the rules.
        existing = get_item_for_target(target_kind, target_id)
        if not existing:
            with _conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO mod_flagged_content "
                    "(id, content_kind, content_id, author_user_id, "
                    " body_snippet, score, rules_triggered, "
                    " severity, status, sla_due_at, created_at) "
                    "VALUES (?, ?, ?, '', '(reported by user)', "
                    "        0.5, ?, 'flag', 'open', ?, ?)",
                    (uuid.uuid4().hex, target_kind, target_id,
                     json.dumps(["user_report"]),
                     time.time() + SLA_HOURS_DEFAULT * 3600,
                     time.time()),
                )
    return True


def unreact(
    *,
    target_kind: str,
    target_id: str,
    user_id: str,
    kind: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM mod_reactions "
            "WHERE target_kind = ? AND target_id = ? "
            "  AND user_id = ? AND kind = ?",
            (target_kind, target_id, user_id, kind),
        )
        return cur.rowcount > 0


def reaction_counts(
    *,
    target_kind: str,
    target_id: str,
) -> dict:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) FROM mod_reactions "
            "WHERE target_kind = ? AND target_id = ? "
            "GROUP BY kind",
            (target_kind, target_id),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def user_reactions_for_target(
    *,
    target_kind: str,
    target_id: str,
    user_id: str,
) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT kind FROM mod_reactions "
            "WHERE target_kind = ? AND target_id = ? "
            "  AND user_id = ?",
            (target_kind, target_id, user_id),
        ).fetchall()
    return [r[0] for r in rows]


def get_item_for_target(
    content_kind: str, content_id: str,
) -> FlaggedItem | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, content_kind, content_id, author_user_id, "
            "       body_snippet, score, rules_triggered, severity, "
            "       status, sla_due_at, created_at "
            "FROM mod_flagged_content "
            "WHERE content_kind = ? AND content_id = ?",
            (content_kind, content_id),
        ).fetchone()
    if not r:
        return None
    return FlaggedItem(
        id=r[0], content_kind=r[1], content_id=r[2],
        author_user_id=r[3], body_snippet=r[4], score=r[5],
        rules_triggered=json.loads(r[6]) if r[6] else [],
        severity=r[7], status=r[8],
        sla_due_at=r[9], created_at=r[10],
    )
