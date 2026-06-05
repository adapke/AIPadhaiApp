"""Content moderation for user-uploaded topics and source files.

Every user-submitted topic / image / document passes through `classify()`
before any expensive generation work. If the content is education-
inappropriate or carries legal risk (CSAM, hate, scam, political
manipulation, copyright redistribution), the request is rejected with a
clear category — the UI surfaces a friendly message + an admin-review
queue captures it for human follow-up.

Why a separate classifier instead of relying on Claude's built-in
safety? Three reasons:

1. **Cost.** Lessons run on Opus 4.7 (~₹5/call). Moderation runs on
   Haiku 4.5 (~₹0.05/call). Catching bad input upstream is 100× cheaper
   than letting it reach the generator and refusing there.
2. **Audit trail.** The classifier's structured output (category +
   severity + reasoning) is persisted to `moderation_log` for admin
   review. The generator's refusal text is unstructured prose.
3. **Latency.** Runs in parallel with the source-image upload, so the
   user-perceived latency is unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Categories the classifier returns. Keep this list short — the model
# is more reliable with fewer classes. "other" is a catch-all for
# borderline cases that humans should review.
MODERATION_CATEGORIES = (
    "csam",            # child sexual abuse material — auto-block, escalate
    "hate",            # hate speech, harassment, identity-based attacks
    "violence",        # graphic violence, self-harm encouragement
    "scam",            # phishing, get-rich-quick, fake credentials
    "political",       # election manipulation, partisan propaganda
    "copyright",       # verbatim copyrighted textbook redistribution
    "adult",           # adult content not appropriate for K-12 audience
    "other",           # borderline — flag for human review, allow by default
    "ok",              # nothing concerning
)
Category = Literal[
    "csam", "hate", "violence", "scam", "political",
    "copyright", "adult", "other", "ok",
]

from .models import HAIKU_MODEL as MODEL

SCHEMA = """
CREATE TABLE IF NOT EXISTS moderation_log (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    user_id         TEXT,
    org_id          TEXT,
    content_hash    TEXT,             -- sha256 of the moderated text (not the text itself)
    content_kind    TEXT NOT NULL,    -- 'topic' | 'image' | 'document'
    category        TEXT NOT NULL,
    severity        INTEGER NOT NULL, -- 0-5; 5 = block + escalate
    allowed         INTEGER NOT NULL, -- 0/1
    reasoning       TEXT,
    model           TEXT NOT NULL DEFAULT '""" + MODEL + """'
);
CREATE INDEX IF NOT EXISTS idx_mod_log_created ON moderation_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_mod_log_category ON moderation_log(category);
"""


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


@dataclass(frozen=True)
class ModerationResult:
    allowed: bool
    category: Category
    severity: int          # 0 (none) → 5 (block + escalate)
    reasoning: str         # short human-readable explanation
    log_id: str | None     # set when the result was persisted


SYSTEM_PROMPT = """You are a content moderation classifier for an Indian \
K-12 educational platform.

Classify each input text into ONE category from this list (return exactly \
the lowercase keyword):

- csam      child sexual abuse material — anything involving minors and \
            sexual content. Always severity 5.
- hate      hate speech targeting religion, caste, gender, sexuality, \
            disability, or nationality.
- violence  graphic violence, gore, self-harm encouragement, weapons \
            instruction (for real harm; combustion lessons are fine).
- scam      phishing, fake credentials, get-rich-quick, money-doubling, \
            crypto pumps, fake government scheme bait.
- political election manipulation, partisan propaganda, defamation of \
            political figures. NOT: factual history/civics topics like \
            "how Indian elections work".
- copyright requests to extract and redistribute long verbatim passages \
            from a specific copyrighted textbook (NCERT, Pearson, \
            Cambridge). Asking to explain a chapter is fine.
- adult     pornography, sexual content, drug or alcohol promotion. \
            Sex education at age-appropriate level is fine.
- other     borderline — uncomfortable but not in the above categories. \
            Default severity 1-2.
- ok        nothing concerning.

Also rate severity 0-5:
  0 = nothing
  1-2 = mild, allow with review
  3-4 = block with friendly message
  5 = block + escalate to admin immediately

Return JSON: {"category": "...", "severity": N, "allowed": true|false, \
"reasoning": "one short sentence"}.

Default to allowing borderline content with severity 1-2. We'd rather \
under-block than over-block — students should feel safe asking real \
questions including ones about anatomy, religion, politics, or violence \
in a historical/scientific context. Reserve blocking (allowed=false) \
for severity >= 3 in clear cases."""


def classify(
    text: str,
    *,
    content_kind: str = "topic",
    user_id: str | None = None,
    org_id: str | None = None,
    log: bool = True,
) -> ModerationResult:
    """Run Haiku-based moderation on a piece of user-supplied text.

    Empty/whitespace input returns allowed=True without an API call.
    Errors (API down, malformed response) return allowed=True with
    category='ok', severity=0 — i.e. fail-OPEN. Rationale: a broken
    moderator should not block all uploads on an outage. The risk
    of a brief moderation gap is lower than the risk of full
    downtime for legitimate users. The admin queue catches misses.
    """
    text = (text or "").strip()
    if not text:
        return ModerationResult(allowed=True, category="ok", severity=0,
                                reasoning="empty input", log_id=None)

    try:
        result = _call_classifier(text)
    except Exception as e:
        import sys
        print(f"moderation.classify FAILED OPEN: {e}", file=sys.stderr)
        return ModerationResult(allowed=True, category="ok", severity=0,
                                reasoning=f"classifier error: {e}",
                                log_id=None)

    if log:
        log_id = _persist(text, result, content_kind=content_kind,
                          user_id=user_id, org_id=org_id)
        result = ModerationResult(**{**result.__dict__, "log_id": log_id})
    return result


def _call_classifier(text: str) -> ModerationResult:
    """The actual Anthropic call. Separated so tests can monkey-patch."""
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text[:4000]}],  # truncate long inputs
    )
    blob = next((b.text for b in resp.content if b.type == "text"), "{}")
    # Tolerate the model wrapping JSON in code fences or prose.
    blob = blob.strip()
    if blob.startswith("```"):
        blob = blob.strip("`").strip()
        if blob.startswith("json"):
            blob = blob[4:].strip()
    # Find the first { … } object
    start = blob.find("{")
    end = blob.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in classifier output: {blob[:100]!r}")
    data = json.loads(blob[start:end + 1])

    category = str(data.get("category", "ok")).lower().strip()
    if category not in MODERATION_CATEGORIES:
        category = "other"
    severity = int(data.get("severity", 0))
    severity = max(0, min(5, severity))
    allowed = bool(data.get("allowed", severity < 3))
    # CSAM always blocked regardless of model's `allowed` field.
    if category == "csam":
        allowed = False
        severity = 5
    reasoning = str(data.get("reasoning", "")).strip()[:240]
    return ModerationResult(
        allowed=allowed, category=category,  # type: ignore[arg-type]
        severity=severity, reasoning=reasoning, log_id=None,
    )


def _persist(text: str, result: ModerationResult, *,
             content_kind: str, user_id: str | None,
             org_id: str | None) -> str:
    """Write a moderation log row. We never store the original text —
    just its sha256 hash — so an admin reviewing the log can correlate
    repeat offenders without us holding the offending content itself."""
    import hashlib
    log_id = uuid.uuid4().hex
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    with _conn() as conn:
        conn.execute(
            "INSERT INTO moderation_log "
            "(id, created_at, user_id, org_id, content_hash, content_kind, "
            " category, severity, allowed, reasoning) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (log_id, time.time(), user_id, org_id, content_hash, content_kind,
             result.category, result.severity, 1 if result.allowed else 0,
             result.reasoning),
        )
    return log_id


def list_recent(limit: int = 100) -> list[dict]:
    """Admin Console read API — the most recent moderation events."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, user_id, org_id, content_kind, "
            "       category, severity, allowed, reasoning "
            "FROM moderation_log "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "id": r[0], "created_at": r[1], "user_id": r[2], "org_id": r[3],
            "content_kind": r[4], "category": r[5], "severity": r[6],
            "allowed": bool(r[7]), "reasoning": r[8],
        }
        for r in rows
    ]
