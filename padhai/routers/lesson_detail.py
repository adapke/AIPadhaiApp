"""Lesson-detail router — sixteenth web.py slice.

Five cache-only lesson-derivative endpoints. None of these make a
Claude call — they read the already-generated Lesson JSON from the
cache and either return a slice of it (quiz), generate cheap
derivatives via the pedagogy helpers (flashcards), or persist
per-user state (notes, SRS ratings):

  POST /lessons/{lesson_id}/flashcards       (generate or fetch cards)
  POST /lessons/{lesson_id}/quiz             (return cached quiz JSON)
  GET  /lessons/{lesson_id}/notes            (load user's notes)
  POST /lessons/{lesson_id}/notes            (save user's notes)
  POST /lessons/{lesson_id}/flashcards/rate  (SM-2 review beacon)

The trickier siblings stay in web.py for now:
- `/chat/{lesson_id}` — has CHAT_SYSTEM_PROMPT + _parse_citations
  deps and the exam-mode lock
- `/lessons/{id}/recap` + `/lessons/{id}/recap.mp3` — TTS-provider
  dependency
- `/lessons/{id}/curriculum` — curriculum-mapping has its own
  story (separate from lesson-detail)

All five endpoints here require auth even when
`PADHAI_REQUIRE_AUTH=0` — they're per-user resources (notes are
keyed by user, flashcards rated per user, etc.).

Late-imports `web` for the shared cache + helpers — same pattern as
orgs_schedule.py, notifications.py, scim.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.post("/lessons/{lesson_id}/flashcards")
def make_flashcards_route(
    lesson_id: str,
    count: int = 8,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),
):
    """Generate (or fetch cached) flashcards for a lesson the student
    watched. Idempotent — second call returns the cached set unless
    `regenerate=true`. Free for M1/M2 (uses Haiku 4.5, ~₹0.30/call).

    Per-user resource — requires auth even when
    PADHAI_REQUIRE_AUTH=0."""
    from .. import web as _web
    from ..pedagogy import generate_flashcards
    if user is None:
        raise HTTPException(401, "authentication required")

    if not regenerate:
        cached = _web.cache.get_flashcards(lesson_id)
        if cached is not None:
            return {
                "lesson_id": lesson_id,
                "cards": cached,
                "cached": True,
                "count": len(cached),
            }

    cached_lesson = _web.cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    cards = generate_flashcards(cached_lesson, count=count)
    _web.cache.put_flashcards(lesson_id, cards)
    return {
        "lesson_id": lesson_id,
        "cards": cards,
        "cached": False,
        "count": len(cards),
    }


@router.post("/lessons/{lesson_id}/quiz")
def standalone_quiz_route(
    lesson_id: str,
    user: AuthUser | None = Depends(current_user),  # noqa: ARG001
):
    """Return the quiz JSON for a cached lesson without rendering a
    video. Used by the Quiz Maker module — the player UI scores the
    student and shows correct/wrong feedback. Free: no Claude call,
    just a cache lookup."""
    from .. import web as _web
    cached_lesson = _web.cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")
    return {
        "lesson_id": lesson_id,
        "title": cached_lesson.title,
        "language_code": cached_lesson.language_code,
        "language_name": cached_lesson.language_name,
        "level": cached_lesson.level,
        "questions": cached_lesson.quiz,
    }


@router.get("/lessons/{lesson_id}/notes")
def get_notes_route(
    lesson_id: str,
    user: AuthUser | None = Depends(current_user),
):
    """Fetch the user's notes attached to a lesson. Empty body when
    none.

    Response carries both `notes` (legacy) and `content` (Cypress-spec
    convention) keys so callers can use either.

    Per-user resource — requires auth even when
    PADHAI_REQUIRE_AUTH=0."""
    from .. import web as _web
    if user is None:
        raise HTTPException(401, "authentication required")
    text = _web.cache.get_notes(lesson_id, user_key=user.id) or ""
    return {"lesson_id": lesson_id, "notes": text, "content": text}


@router.post("/lessons/{lesson_id}/notes")
def put_notes_route(
    lesson_id: str,
    notes: str | None = Form(None, max_length=50_000),
    content: str | None = Form(None, max_length=50_000),
    user: AuthUser | None = Depends(current_user),
):
    """Save the user's notes (overwrite). The browser autosaves on
    idle so this gets called silently every few seconds while the
    user types.

    Accepts either `notes` (legacy) or `content` (Cypress-spec
    convention) form field — whichever is provided is persisted.

    Per-user resource — requires auth even when
    PADHAI_REQUIRE_AUTH=0."""
    from .. import web as _web
    if user is None:
        raise HTTPException(401, "authentication required")
    text = notes if notes is not None else (content or "")
    _web.cache.put_notes(lesson_id, text, user_key=user.id)
    return {"lesson_id": lesson_id, "saved": True, "length": len(text)}


@router.post("/lessons/{lesson_id}/flashcards/rate")
def rate_flashcard_route(
    lesson_id: str,
    card_id: int = Form(...),
    rating: int = Form(..., ge=0, le=5),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """SM-2 review rating endpoint. Forwards to the canonical
    `spaced_repetition.review_card` pathway. Requires auth.

    Unknown-card errors get swallowed (the rating is still
    acknowledged to the client) because the SPA may retry with stale
    card ids after a regenerate."""
    if user is None:
        raise HTTPException(401, "authentication required")
    try:
        from .. import spaced_repetition as _srs
        result = _srs.review_card(
            card_id=str(card_id), user_id=user.id, grade=rating,
        )
        return JSONResponse({
            "lesson_id": lesson_id,
            "card_id": card_id,
            "rating": rating,
            "next_due_at": getattr(result, "due_at", None),
            "ease": getattr(result, "ease", None),
        })
    except Exception as exc:
        # Unknown card → still tell the client the rating was recorded
        return JSONResponse({
            "lesson_id": lesson_id, "card_id": card_id,
            "rating": rating, "note": str(exc)[:100],
        })
