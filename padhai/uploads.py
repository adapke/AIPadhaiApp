"""Upload + analyze (PRD §13.1-2).

Splits the monolithic `POST /lessons` flow (upload + analyze + generate
in one call) into the three explicit steps the PRD calls for:

  1. POST /api/uploads             → upload file, get upload_id
  2. POST /api/uploads/{id}/analyze → detect topic/grade/modes
  3. POST /api/v2/video-requests   → generate, referencing upload_id

Benefits:
  - Studio can show a "Content preview" screen between Source and
    Customize (PRD §15 Screen 2) with detected_topic + suggested_grade
    + suggested_modes — without firing the expensive video render
  - Same upload can drive multiple videos ("try this as both teaching
    AND reel mode")
  - Future: resumable / chunked uploads; for now, single-shot multipart

Data model:
  - `uploads` table tracks the file on disk + ingest output + analysis
    result + retention metadata
  - Same SQLite/Postgres backend as everything else
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import models as _models

SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    org_id          TEXT,
    original_filename TEXT,
    file_path       TEXT NOT NULL,        -- local FS path (or storage_key for R2)
    storage_backend TEXT NOT NULL DEFAULT 'local',
    content_kind    TEXT NOT NULL,        -- pdf | image | document | scan
    size_bytes      INTEGER,
    page_count      INTEGER,              -- after ingest fan-out
    analysis_json   TEXT,                 -- result of /analyze
    status          TEXT NOT NULL DEFAULT 'uploaded',
                                          -- uploaded | analyzed | failed
    created_at      REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id);
CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status);
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
class Upload:
    id: str
    user_id: str | None
    org_id: str | None
    original_filename: str | None
    file_path: str
    storage_backend: str
    content_kind: str
    size_bytes: int | None
    page_count: int | None
    analysis_json: dict | None
    status: str
    created_at: float
    last_accessed_at: float
    error: str | None


# ---------- create + read ----------

def register(
    *,
    file_path: str,
    content_kind: str,
    original_filename: str | None = None,
    size_bytes: int | None = None,
    page_count: int | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    storage_backend: str = "local",
) -> Upload:
    """Insert an `uploads` row for a newly-saved file."""
    uid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO uploads "
            "(id, user_id, org_id, original_filename, file_path, "
            " storage_backend, content_kind, size_bytes, page_count, "
            " status, created_at, last_accessed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded', ?, ?)",
            (uid, user_id, org_id, original_filename, file_path,
             storage_backend, content_kind, size_bytes, page_count,
             now, now),
        )
    return _by_id(uid)


def _by_id(uid: str) -> Upload:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, org_id, original_filename, file_path, "
            "       storage_backend, content_kind, size_bytes, page_count, "
            "       analysis_json, status, created_at, last_accessed_at, "
            "       error FROM uploads WHERE id = ?", (uid,),
        ).fetchone()
    if not r:
        raise KeyError(f"upload {uid!r} not found")
    analysis = json.loads(r[9]) if r[9] else None
    return Upload(
        id=r[0], user_id=r[1], org_id=r[2], original_filename=r[3],
        file_path=r[4], storage_backend=r[5], content_kind=r[6],
        size_bytes=r[7], page_count=r[8], analysis_json=analysis,
        status=r[10], created_at=r[11], last_accessed_at=r[12], error=r[13],
    )


def get(uid: str) -> Upload | None:
    try:
        return _by_id(uid)
    except KeyError:
        return None


def touch(uid: str) -> None:
    """Bump last_accessed_at — called by /analyze and /video-requests
    so retention policy (S3) doesn't purge a file that's actively in use."""
    with _conn() as conn:
        conn.execute(
            "UPDATE uploads SET last_accessed_at = ? WHERE id = ?",
            (time.time(), uid),
        )


def set_analysis(uid: str, analysis: dict) -> None:
    """Persist the /analyze result + flip status to 'analyzed'."""
    with _conn() as conn:
        conn.execute(
            "UPDATE uploads SET analysis_json = ?, status = 'analyzed', "
            "last_accessed_at = ? WHERE id = ?",
            (json.dumps(analysis), time.time(), uid),
        )


def list_for_user(user_id: str, limit: int = 50) -> list[Upload]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id FROM uploads WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_by_id(r[0]) for r in rows]


# ---------- the analyzer ----------

# Mode suggestions per detected subject. These map subjects to the
# 2-3 video modes that historically work best. Used as a fallback
# when Claude's structured output doesn't suggest modes explicitly.
SUBJECT_MODE_SUGGESTIONS: dict[str, list[str]] = {
    "science":      ["teaching", "explainer", "revision"],
    "mathematics":  ["teaching", "revision", "explainer"],
    "history":      ["teaching", "explainer", "kids"],
    "geography":    ["teaching", "explainer", "kids"],
    "civics":       ["teaching", "awareness", "explainer"],
    "language":     ["teaching", "kids", "explainer"],
    "computer":     ["teaching", "explainer", "training"],
    "general":      ["teaching", "explainer", "reel"],
}


_DEFAULT_SYSTEM = (
    "You are a content analyzer for an Indian K-12 educational "
    "platform. Given a textbook-page image, return the topic, "
    "subject, grade, and language. Be concise — single-phrase "
    "values, not paragraphs."
)

# C5: handwritten content (whiteboard, notebook page, blackboard photo)
# has different signals than typeset text. The model needs explicit
# permission to ignore OCR noise + figure out the worked-on-board
# topic from context + diagrams rather than from clean printed text.
_WHITEBOARD_SYSTEM = (
    "You are a content analyzer specialised in handwritten classroom "
    "material. The image is most likely a photo of a whiteboard, "
    "blackboard, or notebook page — expect uneven lighting, perspective "
    "skew, smudges, mixed handwriting + diagrams, and partial erasure.\n\n"
    "Identify the topic, subject, grade, and language by reading the "
    "WORKED CONTENT (formulas, derivations, key terms, diagrams), not "
    "by trying to OCR every word. If a worked example dominates the "
    "image, the topic is whatever that example demonstrates. Indian "
    "scripts (Devanagari, Tamil, Telugu, etc.) may appear alongside "
    "English — return the dominant language.\n\n"
    "Be concise — single-phrase values, not paragraphs."
)


def suggested_modes_for(subject: str | None) -> list[str]:
    if not subject:
        return SUBJECT_MODE_SUGGESTIONS["general"]
    s = subject.strip().lower()
    for key, modes in SUBJECT_MODE_SUGGESTIONS.items():
        if key in s:
            return modes
    return SUBJECT_MODE_SUGGESTIONS["general"]


def analyze_via_claude(image_path: Path, *, content_kind: str = "image") -> dict:
    """Claude-vision-based content understanding (PRD §13.2).

    `content_kind` biases the system prompt:
      - 'image' / 'pdf' / 'document' — typeset textbook page (default)
      - 'whiteboard' — handwritten content; the model knows to expect
        OCR-style noise, faded ink, perspective skew, and to look for
        worked-on-the-board explanations rather than verbatim text

    Returns a dict with detected_topic, detected_subject, detected_grade,
    detected_language, suggested_modes, confidence. Failure-tolerant —
    caller wraps in try/except.
    """
    import base64
    import mimetypes

    import anthropic

    client = anthropic.Anthropic()
    media_type, _ = mimetypes.guess_type(image_path.name)
    if media_type is None:
        media_type = "image/jpeg"
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()

    schema = {
        "type": "object",
        "properties": {
            "detected_topic": {"type": "string"},
            "detected_subject": {
                "type": "string",
                "description": "one of: science, mathematics, history, "
                               "geography, civics, language, computer, "
                               "general",
            },
            "detected_grade": {
                "type": "string",
                "description": "Class 1-12, College, NEET/JEE, General",
            },
            "detected_language": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "detected_topic", "detected_subject", "detected_grade",
            "detected_language", "confidence",
        ],
        "additionalProperties": False,
    }

    response = client.messages.create(
        model=_models.HAIKU_MODEL,  # Haiku is fine — this is cheap
        max_tokens=800,
        system=(
            _WHITEBOARD_SYSTEM if content_kind == "whiteboard"
            else _DEFAULT_SYSTEM
        ),
        output_config={
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": "Analyze this page and return the structured fields.",
                },
            ],
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    data = json.loads(text[start:end + 1])

    # Augment with our suggested modes
    data["suggested_modes"] = suggested_modes_for(data.get("detected_subject"))
    return data
