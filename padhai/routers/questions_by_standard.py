"""prod-138 — Filter questions by NCERT standards code.

CK-12 tags every question with a hierarchical standards code
(US.CCSS.6.RP.A.1). Pathshala adapts: every question_bank row gets
an `ncert_code` like `CBSE.10.SCI.CH06.LO03` so teachers can pull
chapter- or learning-outcome-scoped practice sheets.

Endpoints:
    GET /api/questions/by-standard?code=<prefix>&limit=50
        → public — filter PYQs by NCERT code prefix
    GET /api/admin/teacher-tools/ncert-coverage
        → admin — tagging coverage stats
    POST /api/admin/teacher-tools/tag-questions
        body: {limit=20}
        → admin — run the Claude batch tagger over `limit` untagged
          questions; returns the per-question outcome
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from .. import llm_obs, question_bank

router = APIRouter()


def _question_to_dict(q: question_bank.Question) -> dict:
    return {
        "id": q.id,
        "board": q.board,
        "grade": q.grade,
        "subject": q.subject,
        "chapter": q.chapter,
        "year": q.year,
        "paper": q.paper,
        "question_text": q.question_text,
        "options": q.options,
        "correct_answer": q.correct_answer,
        "marks": q.marks,
        "difficulty": q.difficulty,
        "topic_tags": q.topic_tags,
        "source": q.source,
    }


@router.get("/api/questions/by-standard")
def questions_by_standard(
    code: str = Query(
        ..., min_length=2,
        description=(
            "NCERT code prefix — e.g. 'CBSE.10.SCI' for all Class 10 "
            "Science, or 'CBSE.10.SCI.CH06' for chapter 6 only. "
            "Case-insensitive."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """prod-138 — Public read endpoint. Returns tagged questions
    matching the NCERT code prefix.

    Returns:
        {
          "code": "CBSE.10.SCI",
          "count": N,
          "limit": 50,
          "offset": 0,
          "questions": [{...}, ...]
        }
    """
    question_bank.migrate()
    code = code.strip().upper()
    rows = question_bank.list_by_standard(code, limit=limit, offset=offset)
    total = question_bank.count_by_standard(code)
    return {
        "code": code,
        "count": len(rows),
        "total_matching": total,
        "limit": limit,
        "offset": offset,
        "questions": [_question_to_dict(r) for r in rows],
    }


@router.get("/api/admin/teacher-tools/ncert-coverage")
def ncert_coverage() -> dict:
    """prod-138 — Coverage stats: how many of our PYQs are tagged?"""
    question_bank.migrate()
    return question_bank.ncert_coverage_stats()


@router.post("/api/admin/teacher-tools/tag-questions")
def batch_tag_questions(payload: dict = Body(default={})) -> dict:
    """prod-138 — Run the Claude batch tagger over up to `limit`
    untagged questions. Returns the per-question outcome.

    Body:
        limit  — int 1..50, default 10. Higher → more cost per call.

    Returns:
        {
          "tagged": N,
          "skipped": M,
          "errors": [...],
          "results": [{question_id, ncert_code, ...}, ...]
        }
    """
    question_bank.migrate()
    limit = max(1, min(int((payload or {}).get("limit") or 10), 50))
    user_id = (payload or {}).get("_admin_user_id")
    user_tier = (payload or {}).get("_admin_user_tier")

    from .. import ncert_tagger
    try:
        return ncert_tagger.tag_batch(
            limit=limit, user_id=user_id, user_tier=user_tier,
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
        raise HTTPException(502, f"NCERT tagger failed: {e}") from e
