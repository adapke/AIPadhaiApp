"""prod-138 — Claude-backed NCERT-code batch tagger.

Reads untagged questions from `question_bank`, asks Claude to assign
an NCERT learning-outcome code (e.g. `CBSE.10.SCI.CH06.LO03`), and
writes the code back. Uses Sonnet because subject/chapter inference
needs more than Haiku's IQ for borderline cases (cross-chapter
overlap, multi-topic word problems).

Per-call cost: ~₹0.02-0.05 per question. Tagging the full 2500-PYQ
catalog: ~₹100. Cheap relative to the value (chapter-wise practice
sheets, mastery-map joins for prod-135).

Idempotent: re-running the tagger on already-tagged questions is a
no-op via `list_untagged()`.
"""
from __future__ import annotations

import json
import os
import re

from . import llm_call, llm_obs, models, question_bank

_SYSTEM_PROMPT = """\
You are an Indian K-12 syllabus expert. Your job: assign a single
hierarchical NCERT/board chapter code to each question.

CODE FORMAT:
  <BOARD>.<GRADE>.<SUBJECT_CODE>.<CHAPTER>[.LO<NUM>]

  BOARD: CBSE, ICSE, STATE_MH, STATE_TN, STATE_KA, STATE_AP, STATE_TS,
         STATE_BIHAR, STATE_KERALA, STATE_GUJARAT, STATE_PUNJAB,
         STATE_WB, STATE_UP, STATE_HARYANA, STATE_ODISHA, STATE_ASSAM,
         JEE_MAIN, JEE_ADV, NEET, UPSC_PRELIMS, UPSC_MAINS, SSC, GATE,
         BANK_PO, RRB
  GRADE: integer 1-12 for school boards; 0 for entrance exams.
  SUBJECT_CODE (3-6 letters, all caps):
    MATH, SCI, PHY, CHEM, BIO, ENG, HIN, SST, ECO, CS, GEO, HIST,
    POL, GK, REASON, QUANT, VARC
  CHAPTER: 'CH' + two-digit chapter number (CH01..CH20)
  LO (optional): 'LO' + two-digit learning-outcome number (LO01..LO20)

EXAMPLES:
  CBSE.10.SCI.CH06           — CBSE Class 10 Science, Chapter 6 (Life Processes)
  CBSE.10.SCI.CH06.LO03      — same chapter, Learning Outcome 3
  ICSE.12.PHY.CH02           — ICSE Class 12 Physics, Chapter 2
  JEE_MAIN.0.MATH.CH04       — JEE Main maths, Chapter 4 (Sequences & Series)
  NEET.0.BIO.CH08            — NEET biology, Chapter 8 (Reproduction)
  UPSC_PRELIMS.0.POL.CH02    — UPSC Prelims polity, Chapter 2

ASSIGN BASED ON:
  1. The question's `board`, `grade`, `subject` fields (board+grade hint).
  2. The `chapter` field if present (use exactly).
  3. The `question_text` content (resolves ambiguous chapters).
  4. The `topic_tags` array.

RULES:
  • If chapter is unknown / ambiguous, return null instead of guessing.
  • If the question is multi-chapter, pick the PRIMARY chapter.
  • LO is optional — only include if you can pin a specific learning
    outcome from the question. Default: drop LO.
  • Output JSON ONLY — no preamble, no markdown.

Output schema:
  {"code": "<NCERT code>", "confidence": "high" | "medium" | "low"}

If you can't assign with at least medium confidence, return
  {"code": null, "confidence": "low"}
"""


_CODE_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*\.\d{1,2}\.[A-Z]{2,6}(?:\.[A-Z]{2,4}\d{1,3}){1,2}$"
)


def _strip_to_json(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^\s*```(?:json)?\s*\n([\s\S]+?)\n\s*```\s*$", text)
    if m:
        text = m.group(1).strip()
    return text


def _tag_one(
    question: question_bank.Question,
    *,
    user_id: str | None,
    user_tier: str | None,
) -> tuple[str | None, str]:
    """Call Claude on a single question. Returns (code | None, status)
    where status is 'tagged' | 'skipped' | 'error: <msg>'."""
    board = (question.board or "").upper()
    user_msg = (
        f"Board: {board}\n"
        f"Grade: {question.grade}\n"
        f"Subject: {question.subject}\n"
        f"Chapter (if known): {question.chapter or '(unknown)'}\n"
        f"Year: {question.year or '(unknown)'}\n"
        f"Topic tags: {json.dumps(question.topic_tags or [])}\n"
        f"Question:\n{question.question_text[:1500]}\n\n"
        "Assign an NCERT-format code. Output JSON only."
    )

    model = os.environ.get(
        "PADHAI_NCERT_TAGGER_MODEL", models.SONNET_MODEL,
    )

    try:
        result = llm_call.call_claude(
            module="ncert_tagger",
            prompt_version="v1",
            model=model,
            user_id=user_id,
            subscription_tier=user_tier,
            max_tokens=200,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
    except llm_obs.BudgetExceeded:
        raise
    except RuntimeError as e:
        return None, f"error: claude_call_failed: {e}"

    raw = ""
    try:
        for block in getattr(result.response, "content", []) or []:
            if getattr(block, "type", "") == "text":
                raw += block.text
    except Exception as e:
        return None, f"error: response_unreadable: {e}"

    raw = _strip_to_json(raw)
    if not raw:
        return None, "error: empty_response"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, "error: malformed_json"

    code = data.get("code")
    confidence = data.get("confidence", "low")
    if not code or confidence == "low":
        return None, "skipped"
    if not isinstance(code, str):
        return None, "skipped"
    code = code.strip().upper()
    if not _CODE_RE.match(code):
        return None, f"skipped: bad code shape: {code}"
    return code, "tagged"


def tag_batch(
    *,
    limit: int = 10,
    user_id: str | None = None,
    user_tier: str | None = None,
) -> dict:
    """Tag up to `limit` untagged questions. Idempotent.

    Returns:
        {
          "limit": N,
          "tagged": M,
          "skipped": K,
          "errors": [<msg>, ...],
          "results": [{"question_id", "code", "status"}, ...]
        }
    """
    untagged = question_bank.list_untagged(limit=limit)
    tagged_count = 0
    skipped_count = 0
    errors: list[str] = []
    results: list[dict] = []

    for q in untagged:
        try:
            code, status = _tag_one(q, user_id=user_id, user_tier=user_tier)
        except llm_obs.BudgetExceeded:
            raise  # bubble up to the router for 429
        if code:
            try:
                question_bank.set_ncert_code(q.id, code)
                tagged_count += 1
                results.append({
                    "question_id": q.id, "code": code, "status": "tagged",
                })
            except ValueError as e:
                errors.append(f"{q.id}: persistence rejected: {e}")
                results.append({
                    "question_id": q.id,
                    "code": None,
                    "status": f"error: {e}",
                })
        else:
            skipped_count += 1
            if status.startswith("error:"):
                errors.append(f"{q.id}: {status}")
            results.append({
                "question_id": q.id, "code": None, "status": status,
            })

    return {
        "limit": limit,
        "tagged": tagged_count,
        "skipped": skipped_count,
        "errors": errors,
        "results": results,
    }
