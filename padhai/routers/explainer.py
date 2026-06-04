"""Explainer router — second slice of the web.py split.

Endpoints:
  POST /explain         — type-a-topic explainer (returns structured JSON)
  POST /explain/video   — generate a cartoon-video explainer

Same late-import pattern as `multipage.py`: web.py owns the
expensive globals (cache, runner, moderation classifier, talking-head
provider routing), this router reads them via `from .. import web`.

Why split this out:
  • /explain + /explain/video are ~150 lines of self-contained
    HTTP handling
  • They share `generate_explainer` from pedagogy + the cartoon
    render pipeline; lifting them into a router doesn't change the
    runtime path
  • Makes web.py easier to read — every extracted slice lowers the
    cognitive cost of finding things in the remaining file
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.post("/explain")
def explain_topic(
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    regenerate: bool = Form(False),
    user: AuthUser | None = Depends(current_user),
):
    """Type-a-topic explainer. No file upload needed.

    Returns a 7-field structured explanation (one-liner, paragraphs,
    key points, worked example, common mistakes, analogy). Cached
    by (topic, language, level) so the same 'photosynthesis' from
    1000 students hits the cache 999 times — total cost ~Rs 0.30 for
    all."""
    from .. import web as _web
    from ..pedagogy import LEVEL_GUIDANCE, SUPPORTED_LANGUAGES, generate_explainer

    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of: {sorted(SUPPORTED_LANGUAGES)}")
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of: {sorted(LEVEL_GUIDANCE)}")

    # Moderation gate — block scams, hate, etc. before we spend
    # Opus tokens on the generation. Fail-open on classifier outage;
    # results are logged for admin review either way.
    mod = _web._moderation.classify(
        topic, content_kind="topic",
        user_id=(user.id if user else None),
    )
    if not mod.allowed:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "content_blocked",
                "category": mod.category,
                "reasoning": mod.reasoning or "This topic isn't supported here.",
                "log_id": mod.log_id,
            },
        )

    if not regenerate:
        cached = _web.cache.get_explainer(topic, language, level)
        if cached is not None:
            return {**cached, "cached": True, "language": language, "level": level}

    payload = generate_explainer(topic, language_code=language, level=level)
    _web.cache.put_explainer(topic, language, level, payload)
    return {**payload, "cached": False, "language": language, "level": level}


@router.post("/explain/video")
def explain_video(
    topic: str = Form(..., min_length=2, max_length=200),
    language: str = Form("en"),
    level: str = Form("middle"),
    teacher: bool = Form(True),
    image: UploadFile | None = File(
        None,
        description=(
            "Optional reference image — e.g. a diagram the student wants "
            "explained. When present the request is rerouted through the "
            "lesson pipeline with video_mode='explainer' so the image is "
            "interpreted by vision while keeping the punchier 5-scene "
            "explainer structure."
        ),
    ),
    user: AuthUser | None = Depends(current_user),
):
    """Generate a cartoon-video explainer for a topic.

    Two modes:
      • topic only — fast path. generate_explainer (Haiku) →
        Explainer JSON → explainer_to_lesson → render.
        Cheapest, ~Rs 0.30 per render.
      • topic + image — image-grounded path. Routes through the
        lessons worker with `video_mode='explainer'` so Claude
        Opus reads the image but the prompt+schema use the
        explainer's 5-scene hook→problem→explain→analogy→CTA
        structure rather than the teaching 5-8 scene format.

    Returns a job_id the client polls just like /lessons. Video
    cache shared with /lessons so the same (topic, lang, level)
    only renders once."""
    from .. import web as _web
    from ..auth import resolve_provider_for_tier
    from ..pedagogy import LEVEL_GUIDANCE, SUPPORTED_LANGUAGES, generate_explainer

    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"language must be one of: {sorted(SUPPORTED_LANGUAGES)}")
    if level not in LEVEL_GUIDANCE:
        raise HTTPException(400, f"level must be one of: {sorted(LEVEL_GUIDANCE)}")

    # topic + image — image-grounded explainer via the lessons pipeline
    if image is not None and image.filename:
        suffix = Path(image.filename or "page.jpg").suffix.lower() or ".jpg"
        if suffix not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            raise HTTPException(
                400,
                "explainer images must be PNG/JPG/WEBP — PDFs go through /lessons",
            )
        _SIZE_LIMIT = 25 * 1024 * 1024
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            upload_path = Path(f.name)
            total = 0
            for chunk in iter(lambda: image.file.read(65536), b""):
                total += len(chunk)
                if total > _SIZE_LIMIT:
                    f.close()
                    upload_path.unlink(missing_ok=True)
                    raise HTTPException(413, "file too large (limit 25 MB)")
                f.write(chunk)

        if teacher:
            entitled = resolve_provider_for_tier(user)
            os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = entitled
            provider = _web.get_talking_head_provider()
            provider_name = provider.name
        else:
            provider_name = "none"

        payload = {
            "image_path": str(upload_path),
            "language": language,
            "level": level,
            "teacher": teacher,
            "include_quiz": False,
            "render_mode": "animated",
            "talking_head_provider": provider_name,
            "profile_json": {
                "video_mode": "explainer",
                "output_dimensions": [1280, 720],
                "prompt_addendum": f"Topic the learner asked about: {topic}",
            },
            "page_number": 1,
            "total_pages": 1,
        }
        if user is not None:
            payload["user_id"] = user.id
            payload["subscription_tier"] = user.subscription_tier
        job = _web.runner.enqueue(payload)
        return JSONResponse(status_code=202, content={
            "job_id": job.id,
            "status": job.status,
            "status_url": f"/jobs/{job.id}",
            "video_url": f"/jobs/{job.id}/video",
            "topic": topic,
            "mode": "image_grounded_explainer",
        })

    # topic only — fast Haiku-only path
    explainer = _web.cache.get_explainer(topic, language, level)
    if explainer is None:
        explainer = generate_explainer(topic, language_code=language, level=level)
        _web.cache.put_explainer(topic, language, level, explainer)

    if teacher:
        entitled = resolve_provider_for_tier(user)
        os.environ["PADHAI_TALKING_HEAD_PROVIDER"] = entitled
        provider = _web.get_talking_head_provider()
        provider_name = provider.name
    else:
        provider_name = "none"

    payload = {
        "kind": "explainer",
        "topic": topic,
        "explainer": explainer,
        "language": language,
        "level": level,
        "teacher": teacher,
        "render_mode": "animated",
        "talking_head_provider": provider_name,
    }
    if user is not None:
        payload["user_id"] = user.id
        payload["subscription_tier"] = user.subscription_tier
    job = _web.runner.enqueue(payload)
    return JSONResponse(status_code=202, content={
        "job_id": job.id,
        "status": job.status,
        "status_url": f"/jobs/{job.id}",
        "video_url": f"/jobs/{job.id}/video",
        "topic": topic,
        "mode": "topic_only_explainer",
    })
