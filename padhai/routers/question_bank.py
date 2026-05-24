"""J6 — Question bank read endpoints (public).

Extracted from web.py in v2.0.2. Search/stats/get. Public — teachers
and students both browse past papers."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException


router = APIRouter()


@router.get("/api/question-bank/search")
def qbank_search(
    board: str | None = None,
    grade: int | None = None,
    subject: str | None = None,
    chapter: str | None = None,
    difficulty: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    from .. import question_bank as _qbank
    rows = _qbank.search(
        board=board, grade=grade, subject=subject, chapter=chapter,
        difficulty=difficulty, text_query=q,
        limit=limit, offset=offset,
    )
    return {
        "rows": [
            {
                "id": q_.id, "board": q_.board, "grade": q_.grade,
                "subject": q_.subject, "chapter": q_.chapter,
                "year": q_.year, "paper": q_.paper,
                "question_text": q_.question_text,
                "options": q_.options,
                "correct_answer": q_.correct_answer,
                "marks": q_.marks, "difficulty": q_.difficulty,
                "topic_tags": q_.topic_tags, "source": q_.source,
            }
            for q_ in rows
        ],
        "limit": limit, "offset": offset,
    }


# /stats MUST be declared before /{qid} so FastAPI matches it
# before falling through to the parameter route. (Same reason the
# v1.7 reorder was needed in web.py.)
@router.get("/api/question-bank/stats")
def qbank_stats():
    from .. import question_bank as _qbank
    return _qbank.stats()


@router.get("/api/question-bank/{qid}")
def qbank_get(qid: str):
    from .. import question_bank as _qbank
    q_ = _qbank.get_by_id(qid)
    if q_ is None:
        raise HTTPException(404, "question not found")
    return {
        "id": q_.id, "board": q_.board, "grade": q_.grade,
        "subject": q_.subject, "chapter": q_.chapter,
        "year": q_.year, "paper": q_.paper,
        "question_text": q_.question_text, "options": q_.options,
        "correct_answer": q_.correct_answer, "marks": q_.marks,
        "difficulty": q_.difficulty, "topic_tags": q_.topic_tags,
        "source": q_.source,
    }
