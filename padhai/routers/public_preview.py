"""Public preview endpoints (no auth, rate-limited).

Extracted from web.py in v2.0.2. These exercise the v1.5 math + diagram
renderers and the v1.8 curriculum scorer. All three are abuseable if
left unguarded — rate limits + size caps live here, applied per IP.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import Response


router = APIRouter()


# Size caps on user-supplied input — picked well above realistic
# lesson use (quadratic formula ~60 chars, water cycle Mermaid ~500).
# Bigger inputs reject early via 422 before any render work.
_MATH_LATEX_MAX = 2000
_DIAGRAM_SPEC_MAX = 8000
_LESSON_TEXT_MAX = 20000


@router.post("/api/render/math")
def render_math(
    request: Request,
    latex: str = Form(..., min_length=1, max_length=_MATH_LATEX_MAX),
    inline: bool = Form(False),
):
    """Server-side render a LaTeX string to SVG/HTML for the Studio
    preview pane. Falls back to text when KaTeX isn't configured."""
    from .. import math_render, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    if not rate_limit.preview_math.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    return {
        "html": math_render.render_to_html(latex, inline=inline),
        "alt_text": math_render.latex_to_spoken(latex),
        "katex_available": math_render.is_katex_available(),
    }


@router.post("/api/render/diagram")
def render_diagram(
    request: Request,
    spec_lang: str = Form("svg_spec"),
    spec: str = Form(..., max_length=_DIAGRAM_SPEC_MAX),
    alt_text: str = Form("diagram", max_length=300),
    diagram_type: str = Form("cycle"),
    width: int = Form(600, ge=100, le=2400),
    height: int = Form(400, ge=100, le=2400),
):
    """Preview-render a Claude-emitted diagram spec to SVG."""
    from .. import diagram_generator as _dgen, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    if not rate_limit.preview_diagram.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    if diagram_type not in _dgen.SUPPORTED_TYPES:
        raise HTTPException(
            400,
            f"diagram_type must be in {sorted(_dgen.SUPPORTED_TYPES)}",
        )
    d = _dgen.normalize({
        "spec_lang": spec_lang, "spec": spec, "alt_text": alt_text,
        "diagram_type": diagram_type, "width": width, "height": height,
    })
    return Response(content=_dgen.render(d), media_type="image/svg+xml")


@router.post("/api/curriculum/score")
def score_lesson_alignment(
    request: Request,
    lesson_text: str = Form(..., min_length=20, max_length=_LESSON_TEXT_MAX),
    board: str = Form(..., max_length=32),
    grade: int = Form(..., ge=1, le=12),
    subject: str = Form(..., max_length=64),
    chapter: str | None = Form(None, max_length=200),
):
    """Score a lesson against the board's curriculum objectives."""
    from .. import curriculum_scorer as _scorer, rate_limit
    ip = rate_limit.client_ip_from_request(request)
    if not rate_limit.preview_scorer.try_consume(ip):
        raise HTTPException(429, "rate limit exceeded — slow down")
    result = _scorer.score_lesson(
        lesson_text=lesson_text, board=board, grade=grade,
        subject=subject, chapter=chapter,
    )
    return {
        "score": result.score,
        "covered": result.covered,
        "partial": result.partial,
        "missed": result.missed,
        "method": result.method,
        "syllabus_revision": result.syllabus_revision,
    }
