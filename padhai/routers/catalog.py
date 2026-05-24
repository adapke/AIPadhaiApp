"""Catalog endpoints — public, read-mostly. No auth gates.

Collects the small subsystem-info endpoints into one router:
- preschool activities + categories (K4)
- procurement SKUs (H7)
- region info (G4)
- indic profile + normalize (K1)
- TTS provider catalog (J4)
- SOC 2 evidence dashboard (H6)
- country profile catalog (K2)
- curriculum objectives list (J3)
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException


router = APIRouter()


# ---------- K4: Preschool ----------

@router.get("/api/preschool/activities")
def list_preschool_activities(
    language: str | None = None,
    activity_type: str | None = None,
    age: int | None = None,
):
    from .. import preschool
    try:
        rows = preschool.list_activities(
            language=language, activity_type=activity_type, age=age,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {
        "rows": [
            {"id": a.id, "activity_type": a.activity_type,
             "title": a.title, "language": a.language,
             "age_min": a.age_min, "age_max": a.age_max,
             "duration_seconds": a.duration_seconds,
             "media_url": a.media_url}
            for a in rows
        ],
    }


@router.get("/api/preschool/categories")
def preschool_categories():
    from .. import preschool
    return preschool.categories()


# ---------- H7: GeM procurement ----------

@router.get("/api/procurement/skus")
def procurement_skus():
    from .. import procurement
    return {
        "skus": [procurement.to_dict(s) for s in procurement.list_skus()],
        "currency": "INR",
        "gem_listed": False,
    }


@router.get("/api/procurement/skus/{code}")
def procurement_sku(code: str):
    from .. import procurement
    s = procurement.get_sku(code)
    if not s:
        raise HTTPException(404, "SKU not found")
    return procurement.to_dict(s)


# ---------- G4: Region info ----------

@router.get("/api/region")
def get_region():
    from .. import region
    info = region.current()
    return {
        "region": info.region, "label": info.label,
        "is_primary": info.is_primary,
    }


# ---------- K1: Indic rendering profile ----------

@router.get("/api/indic/profile")
def indic_profile(language: str):
    from .. import indic_polish as _indic
    p = _indic.profile_for(language)
    return {
        "language": p.language,
        "font_family": p.font_family,
        "line_height": p.line_height,
        "tts_speech_rate": p.tts_speech_rate,
        "pause_after_comma_ms": p.pause_after_comma_ms,
        "pause_after_period_ms": p.pause_after_period_ms,
        "requires_zwnj": p.requires_zwnj,
    }


@router.post("/api/indic/normalize")
def indic_normalize(
    text: str = Form(..., max_length=10000),
    language: str = Form(..., max_length=10),
):
    from .. import indic_polish as _indic
    return {
        "language": language,
        "normalized": _indic.normalize_text(text, language=language),
    }


# ---------- J4: TTS provider catalog ----------

@router.get("/api/tts/providers")
def list_tts_providers():
    from .. import voice_sarvam
    return {
        "providers": [
            {"name": "bhashini", "available": True,
             "languages_supported": [
                 "hi", "bn", "ta", "te", "mr", "kn", "ml", "pa", "gu",
                 "or", "en",
             ],
             "tier_eligibility": "M1+",
             "cost_per_min_inr": 0.0},
            {"name": "sarvam_bulbul",
             "available": voice_sarvam.is_available(),
             "languages_supported": sorted(voice_sarvam.SUPPORTED_LANGUAGES),
             "tier_eligibility": "M2+ (paid tiers)",
             "cost_per_min_inr": 0.40},
            {"name": "piper", "available": True,
             "languages_supported": ["en", "hi"],
             "tier_eligibility": "M1+",
             "cost_per_min_inr": 0.0},
        ],
    }


# ---------- H6: SOC 2 dashboard ----------

@router.get("/api/admin/soc2/dashboard")
def soc2_dashboard():
    from .. import soc2
    return {
        "readiness": soc2.readiness_score(),
        "metrics": [
            {"criteria": m.criteria, "name": m.name, "value": m.value,
             "target": m.target, "status": m.status,
             "evidence": m.evidence}
            for m in soc2.gather_metrics()
        ],
        "policies": soc2.policies(),
    }


# ---------- K2: Country catalog (public list only) ----------

@router.get("/api/countries")
def list_countries():
    from .. import countries
    return {
        "countries": [
            {"code": p.code, "name": p.name, "currency": p.currency,
             "payment_provider": p.payment_provider,
             "primary_language": p.primary_language,
             "secondary_languages": list(p.secondary_languages),
             "timezone": p.timezone,
             "education_boards": list(p.education_boards)}
            for p in countries.PROFILES.values()
        ],
    }


# ---------- J3: Curriculum objectives list (public read) ----------

@router.get("/api/curriculum/objectives")
def list_curriculum_objectives(
    board: str, grade: int, subject: str,
    chapter: str | None = None,
):
    from .. import curriculum_scorer as _scorer
    objectives = _scorer.list_objectives(
        board=board, grade=grade, subject=subject, chapter=chapter,
    )
    return {
        "rows": [
            {"id": o.id, "board": o.board, "grade": o.grade,
             "subject": o.subject, "chapter": o.chapter,
             "objective": o.objective, "source": o.source,
             "revision": o.revision}
            for o in objectives
        ],
    }
