"""prod-137 — Real-World Examples router.

Three endpoint groups:

Admin (gated by prod-9 router-level admin dep on /api/admin/*):
    POST /api/admin/teacher-tools/generate-examples
        body: {concept_slug, count=3, locale='en'}
        → generate 3 India-rooted examples via Claude, insert as pending
    GET  /api/admin/teacher-tools/examples-queue
        → list pending examples awaiting curator review
    POST /api/admin/teacher-tools/examples/{example_id}/approve
        body: {note?}
        → flip status to 'approved'
    POST /api/admin/teacher-tools/examples/{example_id}/reject
        body: {note?}
        → flip status to 'rejected'

Public read:
    GET /api/concept-examples?slug=<concept>&locale=en
        → approved examples only — feeds /concept/{slug} SEO page
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from .. import concept_examples, llm_obs

router = APIRouter()


def _serialise(ex: concept_examples.ConceptExample) -> dict:
    return concept_examples.to_dict(ex)


# ---------- Admin: generation + curation ----------


@router.post("/api/admin/teacher-tools/generate-examples")
def generate_examples(payload: dict = Body(...)) -> dict:
    """prod-137 — Generate 3 India-rooted examples for a concept.

    Body:
        concept_slug    — required, e.g. "Newton's First Law"
        concept_display — optional human-readable name (defaults to slug)
        count           — 1..6, default 3
        locale          — 'en' | 'hi' | 'ta' | 'te' | 'kn' | 'ml' |
                          'mr' | 'bn' | 'gu' | 'pa' (default 'en')

    Returns:
        {
          "concept_slug": "...",
          "locale": "en",
          "inserted_ids": [...],          # rows in concept_examples (pending)
          "examples": [<markdown>, ...]   # for immediate preview
        }
    """
    concept_examples.migrate()
    slug = (payload.get("concept_slug") or "").strip()
    if not slug:
        raise HTTPException(400, "concept_slug is required")

    user_id = payload.get("_admin_user_id")
    user_tier = payload.get("_admin_user_tier")

    from .. import concept_examples_generator as _gen
    try:
        result = _gen.generate_and_insert(
            concept_slug=slug,
            concept_display=payload.get("concept_display"),
            count=int(payload.get("count") or 3),
            locale=payload.get("locale") or "en",
            user_id=user_id,
            user_tier=user_tier,
        )
    except llm_obs.BudgetExceeded as e:
        raise HTTPException(
            429,
            detail={
                "error": "daily_ai_quota_exhausted",
                "message": str(e),
                "upgrade_url": "/pricing",
            },
        ) from e
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, f"example generator failed: {e}") from e

    return {
        "concept_slug": result.concept_slug,
        "locale": result.locale,
        "examples": result.examples,
        "inserted_ids": result.inserted_ids,
    }


@router.get("/api/admin/teacher-tools/examples-queue")
def examples_queue(limit: int = Query(50, ge=1, le=200)) -> dict:
    """prod-137 — Curator inbox: list pending examples."""
    concept_examples.migrate()
    rows = concept_examples.list_pending_queue(limit=limit)
    return {
        "queue": [_serialise(r) for r in rows],
        "count": len(rows),
    }


@router.post("/api/admin/teacher-tools/examples/{example_id}/approve")
def approve_example(
    example_id: str,
    payload: dict = Body(default={}),
) -> dict:
    """prod-137 — Curator approves an example. Becomes visible at
    /concept/{slug} immediately."""
    concept_examples.migrate()
    reviewer = (payload or {}).get("_admin_user_id") or "system"
    note = (payload or {}).get("note")
    ex = concept_examples.review(
        example_id=example_id,
        reviewer_user_id=reviewer,
        new_status="approved",
        note=note,
    )
    if ex is None:
        raise HTTPException(404, "example not found")
    return _serialise(ex)


@router.post("/api/admin/teacher-tools/examples/{example_id}/reject")
def reject_example(
    example_id: str,
    payload: dict = Body(default={}),
) -> dict:
    """prod-137 — Curator rejects an example. Soft-deleted (kept in DB
    for audit + future re-review)."""
    concept_examples.migrate()
    reviewer = (payload or {}).get("_admin_user_id") or "system"
    note = (payload or {}).get("note")
    ex = concept_examples.review(
        example_id=example_id,
        reviewer_user_id=reviewer,
        new_status="rejected",
        note=note,
    )
    if ex is None:
        raise HTTPException(404, "example not found")
    return _serialise(ex)


# ---------- Public: read-side for /concept/{slug} page ----------


@router.get("/api/concept-examples")
def list_concept_examples(
    slug: str = Query(..., description="Concept slug (normalised)"),
    locale: str = Query("en", description="Locale, default 'en'"),
    limit: int = Query(10, ge=1, le=20),
) -> dict:
    """prod-137 — Public read endpoint. Returns only `approved`
    examples — pending/rejected never leak.

    Returns:
        {
          "slug": "...",
          "locale": "en",
          "examples": [{"id","example_md","created_at",...}, ...]
        }
    """
    concept_examples.migrate()
    rows = concept_examples.list_for_slug(
        slug, locale=locale, status="approved", limit=limit,
    )
    return {
        "slug": slug,
        "locale": locale,
        "examples": [_serialise(r) for r in rows],
        "count": len(rows),
    }
