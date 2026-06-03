"""P2 — DIKSHA + NDEAR interoperability.

DIKSHA is the Govt of India national education platform with 100M+
users. NDEAR (National Digital Education Architecture) is the
interoperability spec — adoption-driven by govt RFPs.

Two directions:

  1. **Import** — reference DIKSHA content from our lessons (with
     full attribution). The student opens a lesson; if it links to
     DIKSHA content, they can launch it in our viewer or jump out.

  2. **Export** — emit our lessons in NDEAR-compatible JSON so
     DIKSHA can ingest. This is the format the govt RFPs require
     for vendor interop.

Two tables:
  diksha_content_refs   referenced DIKSHA content (id, title,
                        metadata, content_url)
  ndear_exports         tracked exports of our content with timestamp
                        + signed manifest URL

DIKSHA API calls are env-gated. Without `DIKSHA_API_KEY`, the import
endpoint accepts the manifest JSON inline (admin pastes it from the
DIKSHA UI). Same shape; production just adds the fetch.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS diksha_content_refs (
    id              TEXT PRIMARY KEY,
    diksha_id       TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    board           TEXT,
    grade           TEXT,
    subject         TEXT,
    medium          TEXT,                  -- language of instruction
    content_type    TEXT,                  -- 'video' | 'document' | 'collection' | 'practice'
    content_url     TEXT,
    metadata_json   TEXT,                  -- raw DIKSHA manifest
    imported_by     TEXT,
    imported_at     REAL NOT NULL,
    UNIQUE (diksha_id)
);
CREATE INDEX IF NOT EXISTS idx_dcr_search
    ON diksha_content_refs(board, grade, subject);

CREATE TABLE IF NOT EXISTS ndear_exports (
    id              TEXT PRIMARY KEY,
    lesson_id       TEXT NOT NULL,
    manifest_sha    TEXT NOT NULL,
    manifest_url    TEXT,
    ndear_version   TEXT NOT NULL DEFAULT '1.0',
    exported_at     REAL NOT NULL,
    exported_by     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ndear_lesson
    ON ndear_exports(lesson_id, exported_at DESC);
"""


VALID_CONTENT_TYPES = frozenset({
    "video", "document", "collection", "practice",
    "html", "audio", "image",
})


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


def is_api_configured() -> bool:
    """Real DIKSHA API path requires DIKSHA_API_KEY + DIKSHA_BASE_URL.
    Without them, we operate in manifest-paste mode."""
    return bool(os.environ.get("DIKSHA_API_KEY"))


# ---------- dataclasses ----------

@dataclass(frozen=True)
class DikshaContentRef:
    id: str
    diksha_id: str
    title: str
    description: str | None
    board: str | None
    grade: str | None
    subject: str | None
    medium: str | None
    content_type: str | None
    content_url: str | None
    metadata: dict | None
    imported_by: str | None
    imported_at: float


@dataclass(frozen=True)
class NDEARExport:
    id: str
    lesson_id: str
    manifest_sha: str
    manifest_url: str | None
    ndear_version: str
    exported_at: float
    exported_by: str | None


# ---------- import path ----------

def import_from_manifest(
    *,
    diksha_id: str,
    title: str,
    metadata: dict | None = None,
    description: str | None = None,
    board: str | None = None,
    grade: str | None = None,
    subject: str | None = None,
    medium: str | None = None,
    content_type: str | None = None,
    content_url: str | None = None,
    imported_by: str | None = None,
) -> DikshaContentRef:
    """Manifest-paste path: admin provides the DIKSHA content_id +
    metadata directly. Validates content_type against the catalog.
    Idempotent via UNIQUE(diksha_id) — re-importing updates metadata."""
    if not diksha_id or len(diksha_id) > 200:
        raise ValueError("diksha_id required (max 200 chars)")
    if not title or len(title) > 500:
        raise ValueError("title required (max 500 chars)")
    if content_type and content_type not in VALID_CONTENT_TYPES:
        raise ValueError(
            f"content_type must be in {sorted(VALID_CONTENT_TYPES)}"
        )
    now = time.time()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM diksha_content_refs WHERE diksha_id = ?",
            (diksha_id,),
        ).fetchone()
        if existing:
            ref_id = existing[0]
            conn.execute(
                "UPDATE diksha_content_refs SET "
                " title = ?, description = ?, board = ?, grade = ?, "
                " subject = ?, medium = ?, content_type = ?, "
                " content_url = ?, metadata_json = ? "
                "WHERE id = ?",
                (title, description, board, grade, subject, medium,
                 content_type, content_url,
                 json.dumps(metadata) if metadata else None, ref_id),
            )
        else:
            ref_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO diksha_content_refs "
                "(id, diksha_id, title, description, board, grade, "
                " subject, medium, content_type, content_url, "
                " metadata_json, imported_by, imported_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ref_id, diksha_id, title, description, board, grade,
                 subject, medium, content_type, content_url,
                 json.dumps(metadata) if metadata else None,
                 imported_by, now),
            )
    return get_ref(ref_id)  # type: ignore[return-value]


def import_from_api(*, diksha_id: str,
                    imported_by: str | None = None) -> DikshaContentRef:
    """Real API path — calls DIKSHA's public content-read API.
    Lazy-imports requests so this module loads without the lib.

    DIKSHA API:
      GET {base}/api/content/v1/read/{do_id}?fields=name,description,...
    """
    if not is_api_configured():
        raise ValueError(
            "DIKSHA_API_KEY unset — use import_from_manifest() instead"
        )
    base = os.environ.get(
        "DIKSHA_BASE_URL", "https://diksha.gov.in",
    )
    try:
        import requests   # noqa: WPS433
    except ImportError as e:
        raise ValueError("requests lib not installed") from e
    headers = {
        "Authorization": f"Bearer {os.environ['DIKSHA_API_KEY']}",
        "Accept": "application/json",
    }
    fields = (
        "name,description,board,gradeLevel,subject,medium,"
        "contentType,artifactUrl,streamingUrl"
    )
    url = f"{base}/api/content/v1/read/{diksha_id}?fields={fields}"
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"DIKSHA API fetch failed: {e}") from e
    body = r.json()
    content = (
        ((body or {}).get("result") or {}).get("content") or {}
    )
    return import_from_manifest(
        diksha_id=diksha_id,
        title=content.get("name", diksha_id),
        description=content.get("description"),
        board=_first(content.get("board")),
        grade=_first(content.get("gradeLevel")),
        subject=_first(content.get("subject")),
        medium=_first(content.get("medium")),
        content_type=content.get("contentType", "").lower() or None,
        content_url=content.get("streamingUrl") or content.get("artifactUrl"),
        metadata=content,
        imported_by=imported_by,
    )


def _first(value):
    """DIKSHA returns some fields as arrays + others as strings."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def get_ref(ref_id: str) -> DikshaContentRef | None:
    with _conn() as conn:
        r = conn.execute(
            _REF_SEL + " WHERE id = ?", (ref_id,),
        ).fetchone()
    return _row_to_ref(r) if r else None


def get_by_diksha_id(diksha_id: str) -> DikshaContentRef | None:
    with _conn() as conn:
        r = conn.execute(
            _REF_SEL + " WHERE diksha_id = ?", (diksha_id,),
        ).fetchone()
    return _row_to_ref(r) if r else None


_REF_SEL = (
    "SELECT id, diksha_id, title, description, board, grade, "
    "       subject, medium, content_type, content_url, "
    "       metadata_json, imported_by, imported_at "
    "FROM diksha_content_refs"
)


def _row_to_ref(r) -> DikshaContentRef:
    return DikshaContentRef(
        id=r[0], diksha_id=r[1], title=r[2], description=r[3],
        board=r[4], grade=r[5], subject=r[6], medium=r[7],
        content_type=r[8], content_url=r[9],
        metadata=json.loads(r[10]) if r[10] else None,
        imported_by=r[11], imported_at=r[12],
    )


def list_refs(
    *,
    board: str | None = None,
    grade: str | None = None,
    subject: str | None = None,
    limit: int = 50,
) -> list[DikshaContentRef]:
    limit = max(1, min(limit, 500))
    sql = _REF_SEL + " WHERE 1=1"
    params: list = []
    if board:
        sql += " AND board = ?"; params.append(board)
    if grade:
        sql += " AND grade = ?"; params.append(grade)
    if subject:
        sql += " AND subject = ?"; params.append(subject)
    sql += " ORDER BY imported_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_ref(r) for r in rows]


# ---------- NDEAR export ----------

NDEAR_VERSION = "1.0"


def build_ndear_manifest(
    *,
    lesson_id: str,
    title: str,
    description: str | None,
    board: str | None,
    grade: int | None,
    subject: str | None,
    language: str,
    content_url: str,
    duration_seconds: int | None = None,
    license: str = "CC-BY-SA-4.0",
    publisher: str = "AI Pathshala",
    nep_alignment: list[dict] | None = None,
    ncf_alignment: list[dict] | None = None,
) -> dict:
    """Generate an NDEAR 1.0-compatible manifest dict. Caller persists
    it via `record_export()`.

    NDEAR shape (subset of the official spec, the parts that matter
    for govt RFP submission). Real spec includes additional fields
    for accessibility + licensing — we add those as the spec evolves.
    """
    return {
        "schema": "ndear/v1.0",
        "identifier": f"aipathshala-{lesson_id}",
        "name": title,
        "description": description or "",
        "publisher": publisher,
        "license": license,
        "language": [language],
        "board": [board] if board else [],
        "gradeLevel": [str(grade)] if grade is not None else [],
        "subject": [subject] if subject else [],
        "contentType": "video",
        "mimeType": "video/mp4",
        "streamingUrl": content_url,
        "downloadUrl": content_url,
        "durationSeconds": duration_seconds,
        "frameworks": {
            "nep_2020": nep_alignment or [],
            "ncf_2023": ncf_alignment or [],
        },
        "generatedAt": int(time.time()),
        "generatedBy": "AI Pathshala (https://aipathshala.in)",
    }


def record_export(
    *,
    lesson_id: str,
    manifest: dict,
    manifest_url: str | None = None,
    exported_by: str | None = None,
) -> NDEARExport:
    """Record that we generated an NDEAR manifest for this lesson +
    return the export record. SHA of the manifest JSON serves as
    the dedup key for "is this the same content I exported last
    time" — useful when DIKSHA re-asks for the catalog."""
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be a dict")
    serialized = json.dumps(manifest, sort_keys=True)
    sha = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]
    eid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ndear_exports "
            "(id, lesson_id, manifest_sha, manifest_url, "
            " ndear_version, exported_at, exported_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, lesson_id, sha, manifest_url,
             manifest.get("schema", "ndear/v1.0").split("/")[-1],
             now, exported_by),
        )
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, lesson_id, manifest_sha, manifest_url, "
            "       ndear_version, exported_at, exported_by "
            "FROM ndear_exports WHERE id = ?", (eid,),
        ).fetchone()
    return NDEARExport(
        id=r[0], lesson_id=r[1], manifest_sha=r[2],
        manifest_url=r[3], ndear_version=r[4],
        exported_at=r[5], exported_by=r[6],
    )


def latest_export(lesson_id: str) -> NDEARExport | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, lesson_id, manifest_sha, manifest_url, "
            "       ndear_version, exported_at, exported_by "
            "FROM ndear_exports WHERE lesson_id = ? "
            "ORDER BY exported_at DESC LIMIT 1",
            (lesson_id,),
        ).fetchone()
    if not r:
        return None
    return NDEARExport(
        id=r[0], lesson_id=r[1], manifest_sha=r[2],
        manifest_url=r[3], ndear_version=r[4],
        exported_at=r[5], exported_by=r[6],
    )
