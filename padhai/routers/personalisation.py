"""Personalisation router — twenty-third web.py slice.

Two endpoints driving the per-user dashboard + planning surface:

  GET  /me/stats        (7-day activity rollup)
  POST /learning-path   (multi-week personalised study plan via Opus)

`/me/stats` is a thin wrapper around the shared
`_compute_user_stats` helper — `/api/parents/children/{cid}/stats`
(in `routers/parents.py`) reads the same function, scoped to the
child's user_id.

`/learning-path` is the most expensive Claude call in the codebase
(~₹4-6 per call — Opus + adaptive thinking) so it's deterministically
cached on the input key. The user's library (recent succeeded lesson
jobs) is folded into the plan so the planner can recommend a re-watch
when relevant rather than always proposing new generation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/me/stats")
def my_stats_route(
    days: int = 7,
    user: AuthUser | None = Depends(current_user),
):
    """Aggregated activity for the dashboard. Thin wrapper around
    `_compute_user_stats` so `/api/parents/children/{cid}/stats`
    shares the same logic."""
    from .. import web as _web
    return _web._compute_user_stats(
        user.id if user else None, days,
    )


@router.post("/learning-path")
def make_learning_path_route(
    student_class: int = Form(...),
    # comma-separated, e.g. "Maths,Science"
    subjects: str = Form(...),
    weeks: int = Form(4),
    daily_minutes: int = Form(30),
    # comma-separated, optional
    focus_topics: str = Form(""),
    regenerate: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Generate a multi-week personalised study plan.

    Reuses the user's library (existing lessons) + the curriculum
    index seeded in padhai/curriculum.py. Uses Claude Opus 4.7 with
    adaptive thinking — this is a real planning task (Haiku gets
    task-mix wrong in evals). ~₹4-6/call, cached by deterministic
    input key so the same student request returns instantly."""
    from .. import web as _web
    from ..curriculum import CURRICULUM
    from ..pedagogy import generate_learning_path

    subject_list = [s.strip() for s in subjects.split(",") if s.strip()]
    focus_list = [s.strip() for s in focus_topics.split(",") if s.strip()]
    if not subject_list:
        raise HTTPException(
            400, "subjects must be non-empty (e.g. 'Maths,Science')",
        )
    if student_class < 1 or student_class > 12:
        raise HTTPException(400, "student_class must be 1..12")

    # Pull user's library to seed the planner; anonymous → empty
    if user is not None:
        recent = _web.store.recent_jobs(limit=30, filter_user_id=user.id)
    else:
        recent = _web.store.recent_jobs(limit=5)
    library = []
    for j in recent:
        if j.status != "succeeded":
            continue
        r = j.result or {}
        if r.get("lesson_id"):
            library.append({
                "lesson_id": r["lesson_id"],
                "language": j.payload.get("language"),
                "level": j.payload.get("level"),
            })

    key = _web.cache.learning_path_key(
        student_class, subject_list, weeks, daily_minutes,
        focus_list, len(library),
    )
    if not regenerate:
        cached = _web.cache.get_learning_path(key)
        if cached is not None:
            return {"plan": cached, "cached": True, "key": key}

    plan = generate_learning_path(
        student_class=student_class,
        subjects=subject_list,
        weeks=weeks,
        daily_minutes=daily_minutes,
        focus_topics=focus_list,
        library_lessons=library,
        catalogue=CURRICULUM,
    )
    _web.cache.put_learning_path(key, plan)
    return {"plan": plan, "cached": False, "key": key}
