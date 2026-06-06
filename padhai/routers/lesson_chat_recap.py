"""Lesson chat + recap router — seventeenth web.py slice.

Three endpoints that polish-14 deferred because they have more
dependencies than the cache-only `lesson_detail.py` slice:

  POST /chat/{lesson_id}                  (RAG chat grounded in the lesson)
  POST /lessons/{lesson_id}/recap         (text + TTS audio, cached)
  GET  /lessons/{lesson_id}/recap.mp3     (stream the cached audio)

Dependencies that move WITH the router (only used here):
- `CHAT_SYSTEM_PROMPT`              — the chat system prompt
- `_parse_citations` + the two re's — extracts `[Scene N]` references

Dependencies that stay in web.py and are late-imported:
- `_claude()`                       — Anthropic client singleton (shared)
- `cache.*`                         — Lesson/recap/text cache helpers
- `_rl.ai_generation`               — per-user/IP rate limit bucket
- `_orgs.has_active_exam`           — S4 anti-cheat doubt-chat lock
- `pedagogy.MODEL`                  — Opus (full lesson model)
- `pedagogy.generate_recap`         — Haiku-backed text generator
- `tts.get_provider`                — Piper / gTTS / ElevenLabs router

Chat is rate-limited via `_rl.ai_generation.try_consume`. The exam-
mode lock (HTTP 423) is the S4 anti-cheat measure — a student in an
active exam attempt can't use doubt chat until they submit. Recap
generation is Haiku + TTS, cached on first call; subsequent listeners
hit the cache so the audio response is free.
"""

from __future__ import annotations

import dataclasses
import json
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


# ---------- Chat system prompt + citation parser ----------

CHAT_SYSTEM_PROMPT = """You are PadhAI's Spark — a friendly tutor that answers \
follow-up questions about a lesson the student has just watched.

You will be given the lesson plan as JSON (title, scenes with bullets, quiz). \
Answer based ONLY on the lesson content; if the student asks something the \
lesson doesn't cover, say so honestly and suggest they re-scan a related page \
rather than guessing. Keep replies short (2-4 sentences) and match the \
student's language.

SOURCE CITATIONS (REQUIRED): Whenever a fact in your answer comes from a \
specific scene, append the scene number in square brackets right after that \
fact — for example "Plants take in CO2 through stomata [Scene 2]". Use the \
exact form [Scene N] where N is the 1-indexed scene number. Cite multiple \
scenes when needed: "...this happens in chloroplasts [Scene 1, Scene 3]". \
The UI parses these citations to show clickable jump-to-scene buttons, so \
the format must be exact."""


# Match a single [Scene N] OR a comma-joined list of scenes inside one
# bracket: [Scene 1], [Scene 2, Scene 3], [Scene 1, 2, 4].
_CITATION_BLOCK_RE = re.compile(r"\[Scene[^\]]*\]")
_CITATION_NUM_RE = re.compile(r"\d+")


def _parse_citations(text: str, total_scenes: int) -> list[dict]:
    """Extract every [Scene N] citation from the model's answer and
    return a deduplicated list of {scene_number} objects. Numbers
    outside the valid range are dropped silently — the citation system
    is best-effort, not load-bearing."""
    seen: set[int] = set()
    out: list[dict] = []
    for block in _CITATION_BLOCK_RE.findall(text):
        for num_match in _CITATION_NUM_RE.findall(block):
            n = int(num_match)
            if 1 <= n <= total_scenes and n not in seen:
                seen.add(n)
                out.append({"scene_number": n})
    return out


# ---------- Endpoints ----------

@router.post("/chat/{lesson_id}")
def chat_about_lesson_route(
    request: Request,
    lesson_id: str,
    question: str = Form(..., min_length=2),
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """Ask a question about a lesson the student watched.

    `lesson_id` comes from the job result returned by GET /jobs/{id}
    once the render completes (look for `result.lesson_id`).
    The endpoint loads the cached Lesson JSON and answers with Claude
    grounded in that material — no general-knowledge hallucination."""
    from .. import web as _web
    from ..pedagogy import MODEL

    rate_key = user.id if user else _web._rl.client_ip_from_request(request)
    if not _web._rl.ai_generation.try_consume(rate_key):
        raise HTTPException(
            429, "Too many requests — please wait before asking again.",
        )

    # S4 anti-cheat: if the user is in the middle of an exam, lock
    # the doubt-chat. The exam attempt has to finish (submit or timer
    # expiry) before chat reopens. We don't 403 silently — the
    # response tells the client WHY so the UI can show a helpful
    # "Doubt chat is locked while you're taking [exam]" message.
    if user is not None:
        active_exam_id = _web._orgs.has_active_exam(user.id)
        if active_exam_id:
            raise HTTPException(
                status_code=423,  # Locked
                detail={
                    "error": "exam_mode_active",
                    "exam_id": active_exam_id,
                    "message": (
                        "Doubt chat is locked while you have an "
                        "active exam attempt. Submit the exam to "
                        "use chat again."
                    ),
                },
            )

    cached = _web.cache.get_lesson_by_key(lesson_id)
    if cached is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    lesson_json = json.dumps(
        dataclasses.asdict(cached), ensure_ascii=False,
    )
    response = _web._claude().messages.create(
        model=MODEL,
        max_tokens=1000,
        system=CHAT_SYSTEM_PROMPT + "\n\nLESSON:\n" + lesson_json,
        messages=[{"role": "user", "content": question}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
    )
    answer = next(
        (b.text for b in response.content if b.type == "text"), "",
    )
    citations = _parse_citations(answer, total_scenes=len(cached.scenes))
    # v0.14 C7: page citations come from each cited scene's
    # source_pages (set by the lesson generator). Dedupe across cited
    # scenes so a parent sees "this answer came from pages 4, 7" once,
    # not 4-then-4 because two scenes reference page 4.
    cited_pages: list[int] = []
    seen_pages: set[int] = set()
    for c in citations:
        scene = cached.scenes[c["scene_number"] - 1]
        for p in (scene.source_pages or []):
            if p not in seen_pages:
                seen_pages.add(p)
                cited_pages.append(p)
    return JSONResponse({
        "lesson_id": lesson_id,
        "question": question,
        "answer": answer,
        # Resolved scene metadata so the UI can render
        # "Scene 2: Let me explain" jump buttons without re-fetching.
        "source_citations": [
            {
                "scene_number": c["scene_number"],
                "scene_title": cached.scenes[c["scene_number"] - 1].title,
                "source_pages": (
                    cached.scenes[c["scene_number"] - 1].source_pages or []
                ),
            }
            for c in citations
        ],
        # Flat list of page numbers across all cited scenes — handy
        # for "this answer came from pages 4, 7" hint at the bottom
        # of the chat bubble.
        "source_pages": cited_pages,
    })


@router.post("/lessons/{lesson_id}/recap")
def make_recap_route(
    lesson_id: str,
    regenerate: bool = False,
    user: AuthUser | None = Depends(current_user),  # noqa: ARG001
):
    """Generate (or fetch cached) podcast-style audio recap.

    First call: Haiku text generation + Piper/gTTS synthesis → cached
    MP3 (~₹0.20). Every subsequent listener hits the cache: instant +
    free. Returns the recap text and a URL the browser can play
    directly."""
    from .. import web as _web
    from ..pedagogy import generate_recap
    from ..tts import get_provider as get_tts_provider

    cached_lesson = _web.cache.get_lesson_by_key(lesson_id)
    if cached_lesson is None:
        raise HTTPException(404, "lesson not found; POST /lessons first")

    provider = get_tts_provider()
    audio_path = _web.cache.recap_audio_path(lesson_id, provider.name)
    text = (
        _web.cache.get_recap_text(lesson_id) if not regenerate else None
    )

    if text is None or not audio_path.exists() or regenerate:
        text = generate_recap(cached_lesson)
        _web.cache.put_recap_text(lesson_id, text)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            provider.synthesise(
                text, cached_lesson.language_code, audio_path,
            )
        except Exception as e:
            # TTS failed — return text-only so UI can still render
            return {
                "lesson_id": lesson_id,
                "text": text,
                "audio_url": None,
                "audio_error": f"tts failed: {e}",
                "cached": False,
            }
        cached = False
    else:
        cached = True

    return {
        "lesson_id": lesson_id,
        "text": text,
        "audio_url": f"/lessons/{lesson_id}/recap.mp3",
        "cached": cached,
    }


@router.get("/lessons/{lesson_id}/recap.mp3")
def get_recap_audio_route(lesson_id: str):
    """Stream the cached recap MP3. POST /lessons/{id}/recap must be
    called once first to populate the cache."""
    from .. import web as _web
    from ..tts import get_provider as get_tts_provider

    provider = get_tts_provider()
    audio_path = _web.cache.recap_audio_path(lesson_id, provider.name)
    if not audio_path.exists():
        raise HTTPException(
            404,
            "recap not generated yet; POST /lessons/{id}/recap first",
        )
    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=f"recap-{lesson_id}.mp3",
    )
