"""Upload-AI router — turns any uploaded PDF / textbook image into an
interactive learning artifact:

  POST /api/uploads/{uid}/chat        — RAG chat over upload content
  POST /api/uploads/{uid}/flashcards  — auto-generate SRS deck
  POST /api/uploads/{uid}/quiz        — auto-generate practice MCQs
  POST /api/uploads/{uid}/summary     — TL;DR + key points
  POST /api/uploads/{uid}/index       — force-rebuild retrieval index

This is the StudyFetch-equivalent feature: "drop your PDF, talk to it".
Underlying primitives already exist in padhai/retrieval.py (TF-IDF
retrieval) and padhai/spaced_repetition.py (deck creation from chunks).
This router stitches them into a clean public API.

Ownership: every route resolves the upload via the upload-ownership
resolver registered in api_deps so a user can't chat-over someone
else's PDF.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException

from .. import models as _models
from ..api_deps import require_user
from ..web import current_user

router = APIRouter()


def _resolve_upload(uid: str, user) -> Any:
    """Common upload-fetch + ownership check used by every handler."""
    from .. import uploads as _up
    user = require_user(user)
    upload = _up.get(uid)
    if not upload:
        raise HTTPException(404, "upload not found")
    if upload.user_id and upload.user_id != user.id:
        raise HTTPException(403, "not your upload")
    return user, upload


def _ensure_indexed(upload) -> int:
    """Make sure retrieval chunks exist for this upload. Returns the
    current chunk count. No-op if already indexed."""
    from .. import retrieval as _retr
    existing = _retr.chunk_count(upload_id=upload.id)
    if existing > 0:
        return existing
    return _retr.index_upload(upload.id)


# ============================================================================
# Chat over uploaded document
# ============================================================================

@router.post("/api/uploads/{uid}/chat")
def chat_over_upload(
    uid: str,
    question: str = Form(..., min_length=1, max_length=4000),
    top_k: int = Form(5, ge=1, le=15),
    session_id: str | None = Form(None, description="Optional tutor session to thread the chat into"),
    user=Depends(current_user),
):
    """Ask a question grounded in this upload's content. Returns the
    assistant's reply plus the chunks it cited.

    Flow:
      1. Ensure upload is indexed (retrieval chunks exist)
      2. Retrieve top-k chunks for the question
      3. Send a grounded message to Claude with the chunks as context
      4. Persist provenance + return the reply with citations

    Falls back to a citation-only response (no LLM) when Claude is
    unavailable so the dev path still works.
    """
    user, upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr
    from .. import tutor_grounding as _tg

    chunk_count = _ensure_indexed(upload)
    if chunk_count == 0:
        raise HTTPException(
            422,
            "upload has no indexed content yet — run "
            "POST /api/uploads/{uid}/analyze first or wait for ingest",
        )

    hits = _retr.retrieve(
        query=question, upload_ids=[uid], top_k=top_k, min_score=0.05,
    )
    citations = _retr.hits_to_citations(hits)

    if not hits:
        return {
            "reply": (
                "I couldn't find anything in this document that matches "
                "your question. Try rephrasing, or ask about a topic the "
                "document actually covers."
            ),
            "citations": [],
            "grounded": False,
            "method": "no_match",
        }

    answer_text, ai_call_id, method = _generate_grounded_answer(
        question=question,
        chunks=[h.chunk.chunk_text for h in hits],
        user_id=user.id,
    )

    # Persist with the grounding module so provenance is auditable
    sid = session_id or f"upload-{uid}"
    try:
        from .. import tutor_grounding as _tg_mod
        _tg_mod.migrate()
    except Exception:
        pass

    # Grounding wrapper is best-effort — falls back to the raw answer
    # text if the provenance recorder throws (e.g. schema not migrated
    # on this deployment yet).
    try:
        reply = _tg.send_grounded_message(
            session_id=sid,
            user_id=user.id,
            question_text=question,
            answer_text=answer_text,
            retrieved_chunks=citations,
            ai_call_id=ai_call_id,
            confidence=hits[0].score if hits else None,
            surface="upload_chat",
        )
    except Exception:
        reply = None

    citations_out = [
        {
            "page_number": h.chunk.page_number,
            "section": h.chunk.section,
            "preview": h.chunk.chunk_text[:300],
            "score": h.score,
            "matched_tokens": h.matched_tokens,
        }
        for h in hits
    ]

    if reply is None:
        return {
            "reply": answer_text,
            "grounded": bool(hits),
            "answer_mode": "general",
            "citation_count": len(hits),
            "citations": citations_out,
            "method": method,
            "provenance_id": None,
            "note": "grounding recorder unavailable; answer returned without provenance",
        }

    return {
        "reply": reply.text,
        "grounded": reply.grounded,
        "answer_mode": reply.answer_mode,
        "citation_count": reply.citation_count,
        "citations": citations_out,
        "method": method,
        "provenance_id": reply.provenance_id,
    }


def _generate_grounded_answer(
    *, question: str, chunks: list[str], user_id: str,
) -> tuple[str, str | None, str]:
    """Call Claude with the chunks as system context. Falls back to a
    concatenated-chunk reply when Claude unavailable so the dev path
    still produces something useful."""
    if not chunks:
        return ("I have no source material to answer from.", None, "no_chunks")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _fallback_extractive_answer(chunks), None, "extractive"
    try:
        from anthropic import Anthropic
    except ImportError:
        return _fallback_extractive_answer(chunks), None, "extractive"

    from .. import llm_cache, llm_obs
    model = os.environ.get("PADHAI_UPLOAD_CHAT_MODEL", _models.SONNET_MODEL)
    system_text = (
        "You are a study tutor answering questions about a student's "
        "uploaded study material. ONLY use facts present in the "
        "provided source chunks. If the chunks don't contain the answer, "
        "say so honestly — do not invent facts.\n\n"
        "Style: clear, concise, exam-ready. Indian student audience. "
        "If the question is in Hindi or another Indian language, "
        "answer in that language; else default to English.\n\n"
        "Source chunks from the student's document:\n---\n"
        + "\n---\n".join(c[:1800] for c in chunks[:5])
        + "\n---"
    )
    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=question,
    )
    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=900, **kwargs,
        )
    except Exception as e:
        return (
            f"(Couldn't reach Claude: {str(e)[:120]}) "
            + _fallback_extractive_answer(chunks),
            None,
            "extractive_after_error",
        )
    latency_ms = int((time.time() - started) * 1000)
    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    call_id = llm_obs.record_call(
        module="upload_chat", prompt_version="v1",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=user_id, cached=cached,
    )
    return body or _fallback_extractive_answer(chunks), call_id, "claude"


def _fallback_extractive_answer(chunks: list[str]) -> str:
    """No-Claude fallback: return the most relevant chunk verbatim so
    the user at least sees the source text."""
    top = chunks[0][:1200] if chunks else ""
    return (
        "From your document:\n\n" + top
        + "\n\n(Configure ANTHROPIC_API_KEY for AI-synthesised answers.)"
    )


# ============================================================================
# Flashcard generation
# ============================================================================

@router.post("/api/uploads/{uid}/flashcards", status_code=201)
def flashcards_from_upload(
    uid: str,
    deck_title: str = Form(...),
    max_cards: int = Form(20, ge=1, le=100),
    pack_code: str | None = Form(None),
    topic_code: str | None = Form(None),
    user=Depends(current_user),
):
    """Generate an SRS flashcard deck from this upload's content.

    Strategy:
      • Index the upload if not already
      • Pull the top-N chunks (by token-density / position heuristic)
      • Hand them to spaced_repetition.generate_from_chunks which makes
        one card per chunk (front = page-section, back = chunk text)

    Returns the new deck with card_count + the deck_id for follow-up
    /api/flashcards/{card_id}/review calls.
    """
    user, upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr
    from .. import spaced_repetition as _srs

    count = _ensure_indexed(upload)
    if count == 0:
        raise HTTPException(422, "upload has no indexed content")

    chunks = _retr.list_chunks_for_upload(uid)
    if not chunks:
        raise HTTPException(422, "no chunks available for this upload")

    # Pick `max_cards` chunks evenly distributed across the document
    selected = _evenly_sample(chunks, max_cards)
    chunk_dicts = [
        {
            "source_kind": "upload",
            "source_id": uid,
            "page_number": c.page_number,
            "section": c.section,
            "citation_text": c.chunk_text,
        }
        for c in selected
    ]
    try:
        deck = _srs.generate_from_chunks(
            owner_user_id=user.id,
            deck_title=deck_title,
            chunks=chunk_dicts,
            pack_code=pack_code,
            topic_code=topic_code,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "deck_id": deck.id,
        "title": deck.title,
        "card_count": deck.card_count,
        "pack_code": deck.pack_code,
        "topic_code": deck.topic_code,
        "source_upload_id": uid,
    }


def _evenly_sample(items: list, k: int) -> list:
    """Pick k items evenly distributed across the sequence so the
    deck covers the whole document instead of clustering on page 1."""
    if not items:
        return []
    if len(items) <= k:
        return list(items)
    step = len(items) / k
    return [items[int(i * step)] for i in range(k)]


# ============================================================================
# Quiz generation
# ============================================================================

@router.post("/api/uploads/{uid}/quiz", status_code=201)
def quiz_from_upload(
    uid: str,
    question_count: int = Form(10, ge=3, le=30),
    difficulty: str = Form("medium"),
    user=Depends(current_user),
):
    """Generate practice MCQs from this upload's content.

    Falls back to a "fill-in-the-blank from chunk" mode when Claude
    isn't available — still produces something usable for the dev
    path."""
    user, upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr

    count = _ensure_indexed(upload)
    if count == 0:
        raise HTTPException(422, "upload has no indexed content")

    chunks = _retr.list_chunks_for_upload(uid)
    if not chunks:
        raise HTTPException(422, "no chunks available")

    selected = _evenly_sample(chunks, question_count)
    chunk_texts = [c.chunk_text for c in selected]

    if os.environ.get("ANTHROPIC_API_KEY"):
        questions = _generate_quiz_via_claude(
            chunk_texts=chunk_texts,
            count=question_count,
            difficulty=difficulty,
            user_id=user.id,
        )
    else:
        questions = []

    if not questions:
        # Fallback: produce cloze-style fill-in-the-blank questions
        questions = _cloze_fallback(chunk_texts, count=question_count)

    quiz_id = uuid.uuid4().hex
    return {
        "quiz_id": quiz_id,
        "upload_id": uid,
        "question_count": len(questions),
        "difficulty": difficulty,
        "questions": questions,
        "method": "claude" if os.environ.get("ANTHROPIC_API_KEY") else "cloze_fallback",
    }


_QUIZ_SYSTEM = """You generate multiple-choice questions from a
student's uploaded study material.

Rules:
- Each question has exactly 4 options (a, b, c, d) and ONE correct.
- Questions must be answerable strictly from the source text — no
  outside knowledge required.
- Mix question types: factual recall, conceptual application,
  inference.
- Indian student audience; English-medium unless source is in another
  language.

Return STRICT JSON only:
[
  {
    "question_text": "...",
    "options": ["a) ...", "b) ...", "c) ...", "d) ..."],
    "correct_answer": "a",
    "explanation": "Brief 1-line reason why this is correct",
    "source_chunk_index": 0
  },
  ...
]
"""


def _generate_quiz_via_claude(
    *,
    chunk_texts: list[str],
    count: int,
    difficulty: str,
    user_id: str,
) -> list[dict]:
    try:
        from anthropic import Anthropic
    except ImportError:
        return []
    from .. import llm_cache, llm_obs
    model = os.environ.get("PADHAI_UPLOAD_QUIZ_MODEL", _models.HAIKU_MODEL)
    chunks_text = "\n\n".join(
        f"[Chunk {i}]: {c[:1200]}" for i, c in enumerate(chunk_texts[:10])
    )
    system_text = _QUIZ_SYSTEM + "\n\nSOURCE CHUNKS:\n" + chunks_text
    user_text = (
        f"Generate {count} MCQs at {difficulty} difficulty from the "
        "chunks above."
    )
    kwargs = llm_cache.with_caching(
        system_text=system_text, user_text=user_text,
    )
    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=3000, **kwargs,
        )
    except Exception:
        return []
    latency_ms = int((time.time() - started) * 1000)
    body = "".join(b.text for b in resp.content if b.type == "text")
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    llm_obs.record_call(
        module="upload_quiz", prompt_version="v1",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=user_id, cached=cached,
    )
    return _parse_quiz_json(body, count)


def _parse_quiz_json(body: str, max_count: int) -> list[dict]:
    body = body.strip()
    candidates = [body]
    if "```" in body:
        for fence in body.split("```"):
            s = fence.strip()
            if s.startswith("[") or s.startswith("json\n["):
                candidates.append(s[5:] if s.startswith("json\n") else s)
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, list):
                out = []
                for i, q in enumerate(parsed[:max_count]):
                    if not isinstance(q, dict):
                        continue
                    q.setdefault("id", f"upload-q-{i+1}")
                    out.append(q)
                return out
        except (ValueError, TypeError):
            continue
    return []


def _cloze_fallback(chunks: list[str], *, count: int) -> list[dict]:
    """Cheap fallback: take the longest noun-like word from each chunk
    and blank it out. Crude but functional dev-path output."""
    import re
    out: list[dict] = []
    for i, c in enumerate(chunks[:count]):
        sentences = re.split(r"(?<=[.!?])\s+", c.strip())
        if not sentences:
            continue
        sentence = max(sentences, key=len)
        words = [w for w in re.findall(r"\b[A-Za-z]{5,}\b", sentence)]
        if not words:
            continue
        target = max(words, key=len)
        blanked = sentence.replace(target, "_____", 1)
        out.append({
            "id": f"cloze-q-{i+1}",
            "question_text": f"Fill in the blank: {blanked}",
            "options": [
                f"a) {target}",
                "b) (some other plausible term)",
                "c) (another distractor)",
                "d) None of the above",
            ],
            "correct_answer": "a",
            "explanation": f"Source says: {sentence[:200]}",
            "source_chunk_index": i,
            "method": "cloze",
        })
    return out


# ============================================================================
# TL;DR Summary
# ============================================================================

@router.post("/api/uploads/{uid}/summary")
def summary_of_upload(
    uid: str,
    max_words: int = Form(200, ge=50, le=800),
    user=Depends(current_user),
):
    """Generate a TL;DR + key-points summary of the upload. Uses
    Claude when available; falls back to first-paragraph extraction."""
    user, upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr

    count = _ensure_indexed(upload)
    if count == 0:
        raise HTTPException(422, "upload has no indexed content")
    chunks = _retr.list_chunks_for_upload(uid)
    if not chunks:
        raise HTTPException(422, "no chunks available")

    full_text = "\n".join(c.chunk_text for c in chunks[:30])

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "summary": full_text[: max_words * 6],
            "key_points": [],
            "method": "extractive_first_chunks",
        }

    try:
        from anthropic import Anthropic

        from .. import llm_cache, llm_obs
    except ImportError:
        return {"summary": full_text[: max_words * 6], "key_points": [], "method": "extractive"}

    model = os.environ.get("PADHAI_UPLOAD_SUMMARY_MODEL", _models.HAIKU_MODEL)
    system_text = (
        "You produce study summaries for Indian students. Return JSON:\n"
        "{ \"summary\": \"<paragraph, ~{max_words} words>\","
        "  \"key_points\": [\"<5-8 bullet points>\", ...] }\n"
        "No markdown, strict JSON only."
    ).replace("{max_words}", str(max_words))
    user_text = "Summarise the following study material:\n\n" + full_text[:18000]
    kwargs = llm_cache.with_caching(system_text=system_text, user_text=user_text)
    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(model=model, max_tokens=1500, **kwargs)
    except Exception as e:
        return {
            "summary": full_text[: max_words * 6],
            "key_points": [],
            "method": "extractive_after_error",
            "error": str(e)[:200],
        }
    latency_ms = int((time.time() - started) * 1000)
    body = "".join(b.text for b in resp.content if b.type == "text").strip()
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    llm_obs.record_call(
        module="upload_summary", prompt_version="v1",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=user.id, cached=cached,
    )
    parsed = _parse_summary_json(body)
    return {
        "summary": parsed.get("summary") or body[: max_words * 6],
        "key_points": parsed.get("key_points") or [],
        "method": "claude",
    }


def _parse_summary_json(body: str) -> dict:
    body = body.strip()
    candidates = [body]
    if "```" in body:
        for fence in body.split("```"):
            s = fence.strip()
            if s.startswith("{"):
                candidates.append(s)
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            continue
    return {}


# ============================================================================
# Force re-index
# ============================================================================

@router.post("/api/uploads/{uid}/index")
def force_reindex(uid: str, user=Depends(current_user)):
    """Force-rebuild the retrieval index for this upload. Used when
    the upload's extracted_text has changed or when initial ingest
    didn't populate retrieval (older uploads)."""
    user, _upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr
    _retr.delete_upload_chunks(uid)
    n = _retr.index_upload(uid)
    return {"upload_id": uid, "chunks_indexed": n}


@router.get("/api/uploads/{uid}/index/status")
def index_status(uid: str, user=Depends(current_user)):
    user, _upload = _resolve_upload(uid, user)
    from .. import retrieval as _retr
    n = _retr.chunk_count(upload_id=uid)
    return {"upload_id": uid, "chunk_count": n, "indexed": n > 0}
