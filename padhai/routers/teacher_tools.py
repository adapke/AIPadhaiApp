"""prod-131 / prod-132 — Teacher AI tools router.

CK-12 has 9 distinct AI authoring tools for teachers (Lesson Planner,
AI-Resistant Assignment Generator, Reading-Level Adjuster, etc.).
This slice ships the two highest-leverage ones first:

  POST /api/admin/teacher-tools/ai-resistant-assignment
  POST /api/admin/teacher-tools/adjust-reading-level

Both gated by the prod-9 router-level admin dep (path starts with
/api/admin/*) — superuser-emails or org-admins only. The "admin"
constraint is per CK-12's positioning: it's a teacher tool, not a
student tool. Org-admin role + per-tier daily-cost cap enforce
usage limits.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .. import ai_resistant_assignments as _ar
from .. import llm_obs

router = APIRouter()


@router.post("/api/admin/teacher-tools/ai-resistant-assignment")
def generate_ai_resistant_assignment(
    payload: dict = Body(...),
) -> dict:
    """prod-131 — generate an assignment designed to resist
    chatGPT-style cheating.

    Body (all optional except `topic`):
        topic         — required, e.g. "Newton's first law"
        grade         — int 1..12, default None
        subject       — "physics" / "math" / etc, default "general"
        board         — "CBSE" / "ICSE" / state board key, default "CBSE"
        language      — "en" / "hi" / "ta" / ..., default "en"
        count         — number of questions, 1-20, default 5
        total_marks   — total marks for assignment, 1-100, default 20
        difficulty    — "easy" / "medium" / "hard", default "medium"

    Returns:
        {
          "title": "...",
          "instructions_md": "...",
          "questions": [...],
          "rubric_md": "...",
          "anti_cheat_techniques": [...],
          "estimated_time_min": N,
          ...
        }

    Per-user daily Claude cost cap applies (M1=blocked, M2=₹100/day,
    M3=₹400/day). Cap breach returns 429 with a graceful message.
    """
    topic = (payload.get("topic") or "").strip()
    if not topic:
        raise HTTPException(400, "topic is required")

    # Best-effort: the router-level admin gate already attached `user`
    # to the request; we extract user_id + tier for cost-cap tracking.
    # If the gate path didn't run (shouldn't happen in production), we
    # fall back to enforce_cap=False via missing user_id.
    user_id = payload.get("_admin_user_id")
    user_tier = payload.get("_admin_user_tier")

    try:
        out = _ar.generate(
            topic=topic,
            grade=payload.get("grade"),
            subject=payload.get("subject") or "general",
            board=payload.get("board") or "CBSE",
            language=payload.get("language") or "en",
            count=int(payload.get("count") or 5),
            total_marks=int(payload.get("total_marks") or 20),
            difficulty=payload.get("difficulty") or "medium",
            user_id=user_id,
            user_tier=user_tier,
        )
    except llm_obs.BudgetExceeded as e:
        # 429 Too Many Requests with a friendly upgrade-path message
        raise HTTPException(
            status_code=429,
            detail={
                "error": "daily_ai_quota_exhausted",
                "message": str(e),
                "upgrade_url": "/pricing",
            },
        ) from e
    except (RuntimeError, ValueError) as e:
        raise HTTPException(502, f"assignment generator failed: {e}") from e

    return {
        "title": out.title,
        "instructions_md": out.instructions_md,
        "questions": out.questions,
        "rubric_md": out.rubric_md,
        "anti_cheat_techniques": out.anti_cheat_techniques,
        "estimated_time_min": out.estimated_time_min,
        "grade": out.grade,
        "subject": out.subject,
        "language": out.language,
        "board": out.board,
    }


@router.post("/api/admin/teacher-tools/adjust-reading-level")
def adjust_reading_level(
    payload: dict = Body(...),
) -> dict:
    """prod-132 — rewrite text for a target grade level / language /
    board variant.

    Body:
        text         — required, the source text to rewrite
        target_grade — int 1..12, required (the reading level to target)
        language     — "en" / "hi" / "ta" / ... (default "en")
        board        — optional, for state-board terminology hints
        style        — "simplify" / "translate" / "esl" (default "simplify")

    Returns:
        {
          "rewritten_text": "...",
          "target_grade": N,
          "original_chars": N,
          "rewritten_chars": N,
          "language": "...",
          "style": "..."
        }
    """
    from .. import llm_call, models

    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > 8000:
        raise HTTPException(413, "text too long (max 8000 chars)")

    target_grade = payload.get("target_grade")
    if not isinstance(target_grade, int) or not (1 <= target_grade <= 12):
        raise HTTPException(400, "target_grade must be int in [1, 12]")

    language = (payload.get("language") or "en").strip().lower()
    style = (payload.get("style") or "simplify").strip().lower()
    if style not in ("simplify", "translate", "esl"):
        style = "simplify"

    board = (payload.get("board") or "").strip()
    user_id = payload.get("_admin_user_id")
    user_tier = payload.get("_admin_user_tier")

    # Compact prompt — this is a fast, common operation that should
    # use Haiku to keep cost down.
    style_instruction = {
        "simplify": (
            f"Rewrite for a Class {target_grade} Indian student. Use "
            f"shorter sentences, simpler vocabulary, and concrete "
            f"Indian-context examples (₹, km, NCERT chapter refs, "
            f"Indian names)."
        ),
        "translate": (
            f"Translate into {language} preserving meaning. Target a "
            f"Class {target_grade} reading level. Keep technical terms "
            f"in English where conventional (e.g. 'DNA', 'AI', 'pH')."
        ),
        "esl": (
            f"Rewrite for an English-as-second-language student in "
            f"Class {target_grade}. Use Plain English: short sentences, "
            f"common words, definitions in parentheses for any jargon."
        ),
    }[style]

    board_hint = (
        f" Use {board} board terminology where it differs from generic "
        f"NCERT English."
        if board else ""
    )

    system = (
        f"You are an Indian-school textbook editor. {style_instruction}"
        f"{board_hint} Preserve mathematical formulae verbatim. Output "
        f"only the rewritten text — no preamble, no markdown headers."
    )

    # Haiku for speed; cost-conscious. Use the registry constant.
    model = models.HAIKU_MODEL

    try:
        result = llm_call.call_claude(
            module="reading_level_adjuster",
            prompt_version="v1",
            model=model,
            user_id=user_id,
            subscription_tier=user_tier,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": text}],
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
        raise HTTPException(502, f"reading-level adjuster failed: {e}") from e

    # ClaudeCallResult exposes the extracted text as .text (the raw anthropic
    # Message is .resp) — there is no .response attribute.
    rewritten = (result.text or "").strip()
    if not rewritten:
        raise HTTPException(502, "Claude returned empty output")

    return {
        "rewritten_text": rewritten,
        "target_grade": target_grade,
        "original_chars": len(text),
        "rewritten_chars": len(rewritten),
        "language": language,
        "style": style,
        "board": board or None,
    }
