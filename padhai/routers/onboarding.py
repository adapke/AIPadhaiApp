"""Onboarding funnel router.

Multi-step student onboarding that's been missing — the SPA previously
let users hit POST /lessons with zero profile context. Now we have a
proper funnel:

  Step 1  Class / grade        (Class 6 → Class 12 → JEE/NEET aspirant → UPSC)
  Step 2  Board                (CBSE / ICSE / State / Open / NA)
  Step 3  Target exam          (NEET, JEE Main, JEE Adv, UPSC CSE, CBSE Board, ...)
  Step 4  Preferred language   (English + 9 Indic)
  Step 5  Daily goal           (15min / 30min / 1hr / 2hr+)
  Step 6  Done — redirect to /home with personalised pack created

Two extra columns added to `users` table for board + class_grade + exam +
goal_minutes_daily — applied idempotently the first time the endpoints
are hit (same pattern as preferred_language already in web.py).

Endpoints:
  GET  /api/onboarding/options       — option enums for each step
  GET  /api/onboarding/status        — current user's progress + next step
  POST /api/onboarding/step          — submit one step, returns next step
  POST /api/onboarding/complete      — finalise + create personalised pack
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()
_log = logging.getLogger("padhai.onboarding")


# ============================================================================
# Schema migration — extend the users table with onboarding columns
# ============================================================================

_ONBOARDING_COLS_SQL = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS class_grade TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS board TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS target_exam TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language TEXT",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS goal_minutes_daily INTEGER",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_step INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_completed_at REAL",
]

_migrated = False


def _ensure_onboarding_cols() -> None:
    global _migrated
    if _migrated:
        return
    try:
        from ..db import get_db_url
        db_url = get_db_url()
        if not db_url:
            _migrated = True  # SQLite path: handled per-session via best-effort below
            return
        import psycopg
        with psycopg.connect(db_url, autocommit=True, options="-c search_path=public") as conn:
            for stmt in _ONBOARDING_COLS_SQL:
                try:
                    conn.execute(stmt)
                except Exception as e:
                    _log.debug("[onboarding] stmt failed: %s — %s", stmt, e)
        _migrated = True
    except Exception as e:
        _log.warning("[onboarding] migrate non-fatal: %s", e)


# ============================================================================
# Option catalog
# ============================================================================

CLASS_GRADES = [
    {"code": "class_6", "label": "Class 6"},
    {"code": "class_7", "label": "Class 7"},
    {"code": "class_8", "label": "Class 8"},
    {"code": "class_9", "label": "Class 9"},
    {"code": "class_10", "label": "Class 10"},
    {"code": "class_11", "label": "Class 11"},
    {"code": "class_12", "label": "Class 12"},
    {"code": "jee_aspirant", "label": "JEE Aspirant"},
    {"code": "neet_aspirant", "label": "NEET Aspirant"},
    {"code": "upsc_aspirant", "label": "UPSC Aspirant"},
    {"code": "college", "label": "College / University"},
    {"code": "professional", "label": "Working professional"},
]

BOARDS = [
    {"code": "cbse", "label": "CBSE"},
    {"code": "icse", "label": "ICSE / ISC"},
    {"code": "state_maharashtra", "label": "Maharashtra State Board"},
    {"code": "state_karnataka", "label": "Karnataka State Board"},
    {"code": "state_tamilnadu", "label": "Tamil Nadu State Board"},
    {"code": "state_andhra_telangana", "label": "AP / Telangana"},
    {"code": "state_up", "label": "Uttar Pradesh State Board"},
    {"code": "state_west_bengal", "label": "West Bengal Board"},
    {"code": "state_gujarat", "label": "Gujarat State Board"},
    {"code": "state_kerala", "label": "Kerala Board"},
    {"code": "state_rajasthan", "label": "Rajasthan Board"},
    {"code": "state_bihar", "label": "Bihar Board"},
    {"code": "igcse", "label": "Cambridge / IGCSE"},
    {"code": "ib", "label": "International Baccalaureate"},
    {"code": "open", "label": "NIOS / Open"},
    {"code": "na", "label": "Not applicable"},
]

TARGET_EXAMS = [
    {"code": "neet_ug", "label": "NEET UG (Medical)"},
    {"code": "jee_main", "label": "JEE Main"},
    {"code": "jee_advanced", "label": "JEE Advanced"},
    {"code": "cuet_ug", "label": "CUET UG"},
    {"code": "upsc_cse", "label": "UPSC Civil Services"},
    {"code": "ssc_cgl", "label": "SSC CGL"},
    {"code": "ibps_po", "label": "IBPS PO / Bank exams"},
    {"code": "cat", "label": "CAT (MBA)"},
    {"code": "gate", "label": "GATE"},
    {"code": "neet_pg", "label": "NEET PG"},
    {"code": "cbse_board_10", "label": "CBSE Class 10 Board"},
    {"code": "cbse_board_12", "label": "CBSE Class 12 Board"},
    {"code": "state_board", "label": "State Board exam"},
    {"code": "sat", "label": "SAT (US College Admissions)"},
    {"code": "none", "label": "No exam right now — just learning"},
]

LANGUAGES = [
    {"code": "en", "label": "English"},
    {"code": "hi", "label": "हिन्दी (Hindi)"},
    {"code": "ta", "label": "தமிழ் (Tamil)"},
    {"code": "te", "label": "తెలుగు (Telugu)"},
    {"code": "kn", "label": "ಕನ್ನಡ (Kannada)"},
    {"code": "ml", "label": "മലയാളം (Malayalam)"},
    {"code": "mr", "label": "मराठी (Marathi)"},
    {"code": "bn", "label": "বাংলা (Bengali)"},
    {"code": "gu", "label": "ગુજરાતી (Gujarati)"},
    {"code": "pa", "label": "ਪੰਜਾਬੀ (Punjabi)"},
]

GOAL_MINUTES = [
    {"code": 15, "label": "15 min / day — quick wins"},
    {"code": 30, "label": "30 min / day — steady"},
    {"code": 60, "label": "1 hour / day — committed"},
    {"code": 120, "label": "2 hours / day — exam push"},
    {"code": 240, "label": "4+ hours / day — full prep"},
]


# Map (class_grade, target_exam) → base pack code if a personalised
# pack should be auto-provisioned on completion. Loose mapping; the
# user can add more later.
PACK_MAPPING = {
    "neet_ug": "neet_2026",
    "jee_main": "jee_main_2026",
    "jee_advanced": "jee_adv_2026",
    "upsc_cse": "upsc_cse_2026",
    "cbse_board_10": "cbse_10_2026",
    "cbse_board_12": "cbse_12_2026",
    "ssc_cgl": "ssc_cgl_2026",
    "ibps_po": "ibps_po_2026",
}


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/api/onboarding/options")
def onboarding_options():
    """Return the option catalog so the UI can render all five steps
    without hard-coded enums."""
    return {
        "steps": [
            {"step": 1, "field": "class_grade", "label": "What class are you in?", "options": CLASS_GRADES},
            {"step": 2, "field": "board", "label": "Which board?", "options": BOARDS},
            {"step": 3, "field": "target_exam", "label": "What's your target exam?", "options": TARGET_EXAMS},
            {"step": 4, "field": "preferred_language", "label": "Preferred language?", "options": LANGUAGES},
            {"step": 5, "field": "goal_minutes_daily", "label": "How much time can you study daily?", "options": GOAL_MINUTES},
        ],
        "total_steps": 5,
    }


@router.get("/api/onboarding/status")
def onboarding_status(user=Depends(current_user)):
    user = require_user(user)
    _ensure_onboarding_cols()
    state = _load_state(user.id)
    next_step = _next_step_for(state)
    return {
        "state": state,
        "next_step": next_step,
        "completed": state.get("onboarding_completed_at") is not None,
    }


@router.post("/api/onboarding/step")
def onboarding_step(
    field: str = Form(..., description="One of: class_grade, board, target_exam, preferred_language, goal_minutes_daily"),
    value: str = Form(...),
    user=Depends(current_user),
):
    """Persist one field; advance onboarding_step counter."""
    user = require_user(user)
    _ensure_onboarding_cols()

    allowed_fields = {
        "class_grade": {o["code"] for o in CLASS_GRADES},
        "board": {o["code"] for o in BOARDS},
        "target_exam": {o["code"] for o in TARGET_EXAMS},
        "preferred_language": {o["code"] for o in LANGUAGES},
        "goal_minutes_daily": {str(o["code"]) for o in GOAL_MINUTES},
    }
    if field not in allowed_fields:
        raise HTTPException(400, f"unknown field {field!r}")
    if value not in allowed_fields[field]:
        raise HTTPException(400, f"invalid value for {field}")

    # Cast goal_minutes_daily to int
    cast_value: Any = int(value) if field == "goal_minutes_daily" else value
    _persist_field(user.id, field, cast_value)

    state = _load_state(user.id)
    next_step = _next_step_for(state)
    return {
        "ok": True,
        "field": field,
        "value": cast_value,
        "state": state,
        "next_step": next_step,
    }


@router.post("/api/onboarding/complete")
def onboarding_complete(user=Depends(current_user)):
    """Mark onboarding done + auto-provision a personalised pack if
    the target exam maps to one."""
    user = require_user(user)
    _ensure_onboarding_cols()
    state = _load_state(user.id)
    missing = [
        f for f in ("class_grade", "board", "target_exam",
                    "preferred_language", "goal_minutes_daily")
        if not state.get(f)
    ]
    if missing:
        raise HTTPException(
            400, f"missing onboarding fields: {', '.join(missing)}",
        )
    import time
    _mark_complete(user.id, ts=time.time())

    # Try to auto-provision a personalised pack if exam is mapped
    pack_id = None
    target_exam = state.get("target_exam")
    base_pack = PACK_MAPPING.get(target_exam)
    if base_pack:
        try:
            from .. import adaptive_packs as ap
            pp = ap.create_personalised_pack(
                user_id=user.id, base_pack_code=base_pack,
            )
            # Try an initial re-adapt; ignore failures (new user has no
            # mastery signals yet, so this is a no-op anyway)
            try:
                ap.re_adapt(user_id=user.id, base_pack_code=base_pack)
            except Exception as e:
                _log.debug("[onboarding] initial re_adapt skipped: %s", e)
            pack_id = pp.id
        except Exception as e:
            _log.warning("[onboarding] pack auto-provision failed: %s", e)

    return {
        "ok": True,
        "completed_at": time.time(),
        "state": _load_state(user.id),
        "personalised_pack_id": pack_id,
        "redirect_to": "/home",
    }


# ============================================================================
# Internals
# ============================================================================

_FIELDS = (
    "class_grade", "board", "target_exam",
    "preferred_language", "goal_minutes_daily",
    "onboarding_step", "onboarding_completed_at",
)


def _load_state(user_id: str) -> dict:
    """Read the onboarding-relevant columns from the users row.
    Returns an empty-ish dict on any error so the funnel still works."""
    from ..db import get_db_url
    db_url = get_db_url()
    if not db_url:
        return {f: None for f in _FIELDS}
    try:
        import psycopg
        with psycopg.connect(db_url, options="-c search_path=public") as conn:
            r = conn.execute(
                "SELECT class_grade, board, target_exam, "
                "       preferred_language, goal_minutes_daily, "
                "       onboarding_step, onboarding_completed_at "
                "FROM users WHERE id = %s",
                (user_id,),
            ).fetchone()
        if not r:
            return {f: None for f in _FIELDS}
        return {
            "class_grade": r[0], "board": r[1], "target_exam": r[2],
            "preferred_language": r[3], "goal_minutes_daily": r[4],
            "onboarding_step": r[5], "onboarding_completed_at": r[6],
        }
    except Exception as e:
        _log.warning("[onboarding] _load_state failed: %s", e)
        return {f: None for f in _FIELDS}


def _persist_field(user_id: str, field: str, value: Any) -> None:
    from ..db import get_db_url
    db_url = get_db_url()
    if not db_url:
        return  # SQLite dev: silently no-op so UI still progresses
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True, options="-c search_path=public") as conn:
            conn.execute(
                f"UPDATE users SET {field} = %s, "
                "  onboarding_step = GREATEST(COALESCE(onboarding_step, 0), %s) "
                "WHERE id = %s",
                (value, _step_index(field), user_id),
            )
    except Exception as e:
        _log.warning("[onboarding] _persist_field failed: %s", e)


def _mark_complete(user_id: str, *, ts: float) -> None:
    from ..db import get_db_url
    db_url = get_db_url()
    if not db_url:
        return
    try:
        import psycopg
        with psycopg.connect(db_url, autocommit=True, options="-c search_path=public") as conn:
            conn.execute(
                "UPDATE users SET onboarding_completed_at = %s, "
                "  onboarding_step = 5 WHERE id = %s",
                (ts, user_id),
            )
    except Exception as e:
        _log.warning("[onboarding] _mark_complete failed: %s", e)


def _step_index(field: str) -> int:
    return {
        "class_grade": 1, "board": 2, "target_exam": 3,
        "preferred_language": 4, "goal_minutes_daily": 5,
    }.get(field, 0)


def _next_step_for(state: dict) -> dict | None:
    if state.get("onboarding_completed_at"):
        return None
    order = (
        ("class_grade", 1, CLASS_GRADES, "What class are you in?"),
        ("board", 2, BOARDS, "Which board?"),
        ("target_exam", 3, TARGET_EXAMS, "What's your target exam?"),
        ("preferred_language", 4, LANGUAGES, "Preferred language?"),
        ("goal_minutes_daily", 5, GOAL_MINUTES, "How much time can you study daily?"),
    )
    for field, step, options, label in order:
        if not state.get(field):
            return {
                "step": step,
                "field": field,
                "label": label,
                "options": options,
            }
    return None
