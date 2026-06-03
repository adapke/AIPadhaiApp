"""P3 — State board partnerships.

Pilot deployments with state govts. Each partnership has its own
syllabus + branding + content packs + pilot org list. Mostly a sales
+ content ingest function — engineering scope is the registry +
status tracking that the CSM + ops teams operate against.

Single table: `state_partnerships`. Catalog seeded for the 5
priority states + 3 more we field inbound from. Status flow:
  prospect → discovery → pilot → contracted → live
  any → paused → resumed (loops back to live)
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
CREATE TABLE IF NOT EXISTS state_partnerships (
    state_code         TEXT PRIMARY KEY,        -- 'UP' | 'MH' | 'KA' | ...
    name               TEXT NOT NULL,
    region             TEXT NOT NULL,           -- 'north' | 'south' | 'east' | 'west' | 'central' | 'ne'
    primary_language   TEXT NOT NULL,
    student_population INTEGER,                  -- est. K-12 students in state
    status             TEXT NOT NULL DEFAULT 'prospect',
    -- prospect | discovery | pilot | contracted | live | paused
    pilot_org_ids      TEXT,                     -- JSON array
    syllabus_pack_ids  TEXT,                     -- JSON array — O2 content pack ids
    contract_value_inr INTEGER,                  -- annual contract value
    start_date         REAL,
    end_date           REAL,
    contact_name       TEXT,
    contact_email      TEXT,
    contact_phone      TEXT,
    branding_json      TEXT,                     -- {logo_url, primary_color, accent}
    notes              TEXT,
    created_at         REAL NOT NULL,
    updated_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sp_status ON state_partnerships(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sp_region ON state_partnerships(region);
"""


VALID_STATUSES = frozenset({
    "prospect", "discovery", "pilot", "contracted", "live", "paused",
})

VALID_REGIONS = frozenset({
    "north", "south", "east", "west", "central", "ne",
})


# Starter catalog — the 5 priority states from ROADMAP_V3 P3 +
# Delhi / Telangana (high-inbound). Seeded on migrate.
_STATE_SEED = [
    {"state_code": "UP", "name": "Uttar Pradesh",
     "region": "north", "primary_language": "hi",
     "student_population": 50_000_000, "status": "prospect"},
    {"state_code": "MH", "name": "Maharashtra",
     "region": "west", "primary_language": "mr",
     "student_population": 22_000_000, "status": "discovery"},
    {"state_code": "KA", "name": "Karnataka",
     "region": "south", "primary_language": "kn",
     "student_population": 13_000_000, "status": "prospect"},
    {"state_code": "TN", "name": "Tamil Nadu",
     "region": "south", "primary_language": "ta",
     "student_population": 14_000_000, "status": "prospect"},
    {"state_code": "WB", "name": "West Bengal",
     "region": "east", "primary_language": "bn",
     "student_population": 18_000_000, "status": "prospect"},
    {"state_code": "DL", "name": "Delhi",
     "region": "north", "primary_language": "hi",
     "student_population": 3_500_000, "status": "discovery"},
    {"state_code": "TG", "name": "Telangana",
     "region": "south", "primary_language": "te",
     "student_population": 6_500_000, "status": "prospect"},
]


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
    with _conn() as conn:
        now = time.time()
        for s in _STATE_SEED:
            conn.execute(
                "INSERT OR IGNORE INTO state_partnerships "
                "(state_code, name, region, primary_language, "
                " student_population, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (s["state_code"], s["name"], s["region"],
                 s["primary_language"], s.get("student_population"),
                 s["status"], now, now),
            )


@dataclass(frozen=True)
class Partnership:
    state_code: str
    name: str
    region: str
    primary_language: str
    student_population: int | None
    status: str
    pilot_org_ids: list[str]
    syllabus_pack_ids: list[str]
    contract_value_inr: int | None
    start_date: float | None
    end_date: float | None
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    branding: dict | None
    notes: str | None
    created_at: float
    updated_at: float


_SEL = (
    "SELECT state_code, name, region, primary_language, "
    "       student_population, status, pilot_org_ids, "
    "       syllabus_pack_ids, contract_value_inr, start_date, "
    "       end_date, contact_name, contact_email, contact_phone, "
    "       branding_json, notes, created_at, updated_at "
    "FROM state_partnerships"
)


def _row_to_partnership(r) -> Partnership:
    return Partnership(
        state_code=r[0], name=r[1], region=r[2],
        primary_language=r[3], student_population=r[4],
        status=r[5],
        pilot_org_ids=json.loads(r[6]) if r[6] else [],
        syllabus_pack_ids=json.loads(r[7]) if r[7] else [],
        contract_value_inr=r[8],
        start_date=r[9], end_date=r[10],
        contact_name=r[11], contact_email=r[12],
        contact_phone=r[13],
        branding=json.loads(r[14]) if r[14] else None,
        notes=r[15], created_at=r[16], updated_at=r[17],
    )


def get(state_code: str) -> Partnership | None:
    with _conn() as conn:
        r = conn.execute(
            _SEL + " WHERE state_code = ?", (state_code.upper(),),
        ).fetchone()
    return _row_to_partnership(r) if r else None


def list_all(
    *,
    status: str | None = None,
    region: str | None = None,
) -> list[Partnership]:
    sql = _SEL + " WHERE 1=1"
    params: list = []
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
        sql += " AND status = ?"; params.append(status)
    if region:
        if region not in VALID_REGIONS:
            raise ValueError(f"region must be in {sorted(VALID_REGIONS)}")
        sql += " AND region = ?"; params.append(region)
    sql += " ORDER BY student_population DESC NULLS LAST, name"
    with _conn() as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # SQLite version < 3.30 doesn't support NULLS LAST
            sql = sql.replace(" NULLS LAST", "")
            rows = conn.execute(sql, params).fetchall()
    return [_row_to_partnership(r) for r in rows]


def upsert(
    *,
    state_code: str,
    name: str | None = None,
    region: str | None = None,
    primary_language: str | None = None,
    student_population: int | None = None,
    status: str | None = None,
    pilot_org_ids: list[str] | None = None,
    syllabus_pack_ids: list[str] | None = None,
    contract_value_inr: int | None = None,
    start_date: float | None = None,
    end_date: float | None = None,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    branding: dict | None = None,
    notes: str | None = None,
) -> Partnership:
    """Upsert by state_code. New rows require name + region +
    primary_language; updates patch only the fields supplied."""
    state_code = (state_code or "").upper().strip()
    if not state_code or len(state_code) > 4:
        raise ValueError("state_code required (max 4 chars)")
    if status and status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    if region and region not in VALID_REGIONS:
        raise ValueError(f"region must be in {sorted(VALID_REGIONS)}")
    existing = get(state_code)
    now = time.time()
    if not existing:
        if not (name and region and primary_language):
            raise ValueError(
                "new state requires name + region + primary_language",
            )
        with _conn() as conn:
            conn.execute(
                "INSERT INTO state_partnerships "
                "(state_code, name, region, primary_language, "
                " student_population, status, pilot_org_ids, "
                " syllabus_pack_ids, contract_value_inr, "
                " start_date, end_date, contact_name, contact_email, "
                " contact_phone, branding_json, notes, "
                " created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (state_code, name, region, primary_language,
                 student_population, status or "prospect",
                 json.dumps(pilot_org_ids) if pilot_org_ids else None,
                 json.dumps(syllabus_pack_ids) if syllabus_pack_ids else None,
                 contract_value_inr, start_date, end_date,
                 contact_name, contact_email, contact_phone,
                 json.dumps(branding) if branding else None,
                 notes, now, now),
            )
        return get(state_code)  # type: ignore[return-value]
    # Patch path — only fields explicitly passed (non-None)
    updates: list[tuple[str, object]] = []
    if name is not None: updates.append(("name", name))
    if region is not None: updates.append(("region", region))
    if primary_language is not None:
        updates.append(("primary_language", primary_language))
    if student_population is not None:
        updates.append(("student_population", student_population))
    if status is not None: updates.append(("status", status))
    if pilot_org_ids is not None:
        updates.append(("pilot_org_ids", json.dumps(pilot_org_ids)))
    if syllabus_pack_ids is not None:
        updates.append(("syllabus_pack_ids", json.dumps(syllabus_pack_ids)))
    if contract_value_inr is not None:
        updates.append(("contract_value_inr", contract_value_inr))
    if start_date is not None: updates.append(("start_date", start_date))
    if end_date is not None: updates.append(("end_date", end_date))
    if contact_name is not None: updates.append(("contact_name", contact_name))
    if contact_email is not None: updates.append(("contact_email", contact_email))
    if contact_phone is not None: updates.append(("contact_phone", contact_phone))
    if branding is not None:
        updates.append(("branding_json", json.dumps(branding)))
    if notes is not None: updates.append(("notes", notes))
    if not updates:
        return existing
    set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
    params = [v for _, v in updates] + [now, state_code]
    with _conn() as conn:
        conn.execute(
            f"UPDATE state_partnerships SET {set_clause}, updated_at = ? "
            "WHERE state_code = ?",
            params,
        )
    return get(state_code)  # type: ignore[return-value]


def set_status(*, state_code: str, status: str) -> bool:
    if status not in VALID_STATUSES:
        raise ValueError(f"status must be in {sorted(VALID_STATUSES)}")
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE state_partnerships SET status = ?, updated_at = ? "
            "WHERE state_code = ?",
            (status, time.time(), state_code.upper()),
        )
        return cur.rowcount > 0


def pipeline_summary() -> dict:
    """Aggregate view: state count + total contract value + total
    reachable students by status. Drives the leadership dashboard."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*), "
            "       COALESCE(SUM(contract_value_inr), 0), "
            "       COALESCE(SUM(student_population), 0) "
            "FROM state_partnerships GROUP BY status",
        ).fetchall()
    return {
        r[0]: {
            "states": r[1],
            "contract_value_inr": int(r[2] or 0),
            "reachable_students": int(r[3] or 0),
        }
        for r in rows
    }
