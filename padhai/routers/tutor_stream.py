"""Tutor streaming router — SSE endpoint that streams Claude's reply
token-by-token. Companion to the synchronous /api/tutor/sessions/{sid}/message
in routers/v3.py — both persist the same message into the session.

  GET  /api/tutor/sessions/{sid}/stream?text=...   (SSE)
  POST /api/tutor/sessions/{sid}/stream            (form text, SSE response)

SSE shape:
  data: {"type": "delta", "text": "..."}
  data: {"type": "done", "tokens_in": N, "tokens_out": M, "cost_inr_paise": K}
  data: {"type": "error", "message": "..."}

Tier cap is checked BEFORE we open the stream — so over-budget users get
an error event instantly instead of partial tokens.

Falls back to a non-streaming response when Anthropic SDK or API key is
unavailable: emits the canned reply as a single delta then done.
"""

from __future__ import annotations

import json
import os
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import StreamingResponse

from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


@router.get("/api/tutor/sessions/{sid}/stream")
async def tutor_stream_get(
    sid: str,
    text: str = Query(..., min_length=1, max_length=4000),
    upload_ids: str | None = Query(None, description="Comma-separated upload ids for RAG"),
    auto_ground: bool = Query(False),
    user=Depends(current_user),
):
    """GET variant — easy to consume via EventSource in browsers.
    Browsers can't send Authorization headers with EventSource, so this
    accepts the same token via the `Cookie` or `Authorization` header
    that current_user already validates."""
    user = require_user(user)
    return _make_stream_response(
        sid=sid, user=user, text=text,
        upload_ids=upload_ids, auto_ground=auto_ground,
    )


@router.post("/api/tutor/sessions/{sid}/stream")
async def tutor_stream_post(
    sid: str,
    text: str = Form(..., min_length=1, max_length=4000),
    upload_ids: str | None = Form(None),
    auto_ground: bool = Form(False),
    user=Depends(current_user),
):
    """POST variant — used by `fetch` clients that read the
    ReadableStream from the response body."""
    user = require_user(user)
    return _make_stream_response(
        sid=sid, user=user, text=text,
        upload_ids=upload_ids, auto_ground=auto_ground,
    )


def _make_stream_response(
    *, sid: str, user, text: str,
    upload_ids: str | None, auto_ground: bool,
) -> StreamingResponse:
    return StreamingResponse(
        _stream(
            sid=sid, user=user, user_text=text,
            upload_ids_str=upload_ids, auto_ground=auto_ground,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


async def _stream(
    *, sid: str, user, user_text: str,
    upload_ids_str: str | None = None, auto_ground: bool = False,
) -> AsyncGenerator[bytes, None]:
    from .. import tutor

    session = tutor.get_session(sid)
    if not session:
        yield _sse({"type": "error", "message": "session not found"})
        return
    if session.user_id != user.id:
        yield _sse({"type": "error", "message": "not your session"})
        return
    if session.ended_at is not None:
        yield _sse({"type": "error", "message": "session already ended"})
        return

    user_tier = getattr(user, "subscription_tier", "M2") or "M2"

    # Daily cost cap check — same as send_message
    cap = tutor.DAILY_COST_CAP_PAISE.get(user_tier)
    if cap is not None and cap == 0 and user_tier == "M1":
        yield _sse({
            "type": "error",
            "message": "AI tutor is a premium feature. Upgrade to chat.",
            "over_budget": True,
        })
        return
    if cap and cap > 0:
        from .. import llm_obs
        spent_paise = int(round(llm_obs.user_cost_today(user.id) * 100))
        if spent_paise >= cap:
            yield _sse({
                "type": "error",
                "message": "Daily AI tutor budget reached. Try again tomorrow.",
                "over_budget": True,
            })
            return

    # No Claude key → canned reply as a single delta + done event so
    # the client UX is consistent.
    if not tutor.is_available():
        yield _sse({
            "type": "delta",
            "text": "(AI tutor not configured — ANTHROPIC_API_KEY missing.)",
        })
        yield _sse({
            "type": "done",
            "tokens_in": 0, "tokens_out": 0,
            "cost_inr_paise": 0, "cached": False,
        })
        return

    try:
        from anthropic import Anthropic
    except ImportError:
        yield _sse({
            "type": "delta",
            "text": "(anthropic SDK not installed)",
        })
        yield _sse({"type": "done", "tokens_in": 0, "tokens_out": 0, "cost_inr_paise": 0, "cached": False})
        return

    model = os.environ.get("PADHAI_TUTOR_MODEL", "claude-haiku-4-5-20251001")
    # `_build_system_prompt` is a private helper in tutor.py; if a future
    # refactor renames it, fall back to a minimal system prompt so the
    # stream still works (just without the long-memory enrichment).
    if hasattr(tutor, "_build_system_prompt"):
        system_prompt = tutor._build_system_prompt(session)
    else:
        system_prompt = (
            "You are an AI tutor for Indian students. Be concise, "
            "exam-focused, and supportive. Answer in the student's "
            "preferred language when set."
        )

    # ---- Source grounding (v3.x) ----
    # Resolve + authorise upload ids, retrieve top-k chunks, splice into
    # the system prompt. Citations are emitted to the client BEFORE the
    # first delta so the UI can render a "Searching your notes…" hint
    # and reserve real estate for citation tiles.
    citations_for_client: list[dict] = []
    if upload_ids_str or auto_ground:
        upload_ids_list: list[str] = []
        if upload_ids_str:
            upload_ids_list = [
                uid.strip() for uid in upload_ids_str.split(",") if uid.strip()
            ]
            try:
                from .. import uploads as _up
                for uid in upload_ids_list:
                    u = _up.get(uid)
                    if not u:
                        yield _sse({"type": "error", "message": f"upload {uid!r} not found"})
                        return
                    if u.user_id and u.user_id != user.id:
                        yield _sse({"type": "error", "message": f"upload {uid!r} not yours"})
                        return
            except Exception as _e:
                yield _sse({"type": "error", "message": f"upload check failed: {_e}"})
                return
        elif auto_ground and hasattr(tutor, "_recent_indexed_uploads_for_user"):
            upload_ids_list = tutor._recent_indexed_uploads_for_user(user.id)

        if upload_ids_list and hasattr(tutor, "_retrieve_chunks"):
            retrieved_hits = tutor._retrieve_chunks(
                query=user_text, upload_ids=upload_ids_list,
            )
            if retrieved_hits and hasattr(tutor, "_format_chunks_for_prompt"):
                chunks_block, citations_tuple = tutor._format_chunks_for_prompt(retrieved_hits)
                system_prompt = (
                    system_prompt
                    + "\n\n--- STUDENT'S UPLOADED STUDY MATERIAL (cite when used) ---\n"
                    + chunks_block
                    + "\n--- END OF SOURCE MATERIAL ---\n\n"
                    + "When you use facts from the source material, append "
                    + "a citation like [page N, section X]. If the source "
                    + "doesn't cover the question, say so honestly."
                )
                citations_for_client = list(citations_tuple)
                yield _sse({
                    "type": "citations",
                    "citations": citations_for_client,
                })

    max_verbatim = getattr(tutor, "MAX_VERBATIM_MESSAGES", 12)
    history = list(session.messages or [])
    history.append({"role": "user", "content": user_text, "ts": time.time()})
    verbatim = history[-max_verbatim:]
    api_messages = [
        {"role": m["role"], "content": m["content"]} for m in verbatim
    ]

    started = time.time()
    reply_text_parts: list[str] = []
    tokens_in = 0
    tokens_out = 0
    cached = False

    try:
        client = Anthropic()
        with client.messages.stream(
            model=model,
            max_tokens=600,
            system=system_prompt,
            messages=api_messages,
        ) as stream:
            for delta in stream.text_stream:
                if not delta:
                    continue
                reply_text_parts.append(delta)
                yield _sse({"type": "delta", "text": delta})
            final = stream.get_final_message()
            tokens_in = getattr(final.usage, "input_tokens", 0) or 0
            tokens_out = getattr(final.usage, "output_tokens", 0) or 0
            cached = bool(getattr(final.usage, "cache_read_input_tokens", 0))
    except Exception as e:
        yield _sse({
            "type": "error",
            "message": f"Claude stream failed: {str(e)[:200]}",
        })
        return

    full_reply = "".join(reply_text_parts)
    latency_ms = int((time.time() - started) * 1000)

    # Cost + observability — same as the synchronous path
    from .. import llm_obs
    cost_paise = llm_obs.estimate_cost_paise(
        model=model, tokens_in=tokens_in, tokens_out=tokens_out, cached=cached,
    )
    llm_obs.record_call(
        module="tutor", prompt_version="v1-stream",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms,
        user_id=session.user_id, cached=cached,
        cost_inr_paise=cost_paise,
    )

    # Persist into tutor_sessions like send_message would have
    history.append({
        "role": "assistant", "content": full_reply, "ts": time.time(),
    })
    tutor._save_turn(
        sid=session.id,
        messages=history,
        delta_tokens_in=tokens_in,
        delta_tokens_out=tokens_out,
        delta_cost_paise=cost_paise,
    )

    yield _sse({
        "type": "done",
        "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cost_inr_paise": cost_paise, "cached": cached,
        "latency_ms": latency_ms,
        "grounded": bool(citations_for_client),
        "citation_count": len(citations_for_client),
    })


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode("utf-8")
