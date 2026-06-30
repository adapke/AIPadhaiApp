"""prod-194 — AI answer-explanation generator.

prod-193 added curated answer explanations (the 64 SAT questions) and
surfaced them after a practice submit. The Indian PYQ bank
(CBSE / JEE / NEET / state boards / ... ~2500 questions) has none, and
hand-writing thousands is impractical. This module generates a concise
worked-solution explanation for any question via Claude (Haiku —
~Rs 0.01 / question), which the backfill worker caches into
`question_bank.explanation`.

Curated explanations are never overwritten: the backfill only reads
`question_bank.list_without_explanation()`.
"""

from __future__ import annotations

import os

from . import models as _models

# Cheap + fast — explanations are short. Env override per the models.py
# convention (PADHAI_<SURFACE>_MODEL).
_MODEL = os.environ.get("PADHAI_EXPLAIN_MODEL", _models.HAIKU_MODEL)

_SYSTEM = (
    "You are an expert exam tutor. You are given one multiple-choice "
    "question, its answer options, and which option is correct. Write a "
    "single concise explanation (1-2 sentences, at most ~45 words) of WHY "
    "the correct answer is right - the key step or worked reasoning. Do "
    "NOT restate the question or re-list the options. Do NOT begin with a "
    "preamble like 'The correct answer is'. Output only the explanation "
    "as plain text, no markdown."
)


def _letter(idx: int) -> str:
    return "ABCDEFGH"[idx] if 0 <= idx < 8 else "?"


def generate_explanation(
    *,
    question_text: str,
    options: list[str] | None = None,
    correct_answer: str | None = None,
    subject: str | None = None,
    client=None,
) -> str:
    """Generate a concise worked-solution explanation for one question.

    Returns the explanation text (stripped). Raises ``ValueError`` on an
    empty question and ``RuntimeError`` when the Anthropic SDK / key is
    missing or the call fails — the backfill worker catches and skips.
    ``client`` is injectable for tests.
    """
    if not (question_text or "").strip():
        raise ValueError("question_text is required")

    from . import llm_call

    lines: list[str] = []
    if subject:
        lines.append(f"Subject: {subject}")
    lines.append(f"Question: {question_text.strip()}")
    if options:
        lines.append("Options:")
        lines.extend(f"  {_letter(i)}. {o}" for i, o in enumerate(options))
    if correct_answer:
        lines.append(f"Correct answer: {correct_answer}")
    user_text = "\n".join(lines)

    result = llm_call.call_claude(
        module="explanation",
        prompt_version="v1",
        model=_MODEL,
        enforce_cap=False,  # system batch — not user-attributable
        client=client,
        max_tokens=160,
        system=[{
            "type": "text", "text": _SYSTEM,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_text}],
    )
    return (result.text or "").strip()
