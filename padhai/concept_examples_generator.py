"""prod-137 — Claude-backed generator for India-rooted concept examples.

Calls `llm_call.call_claude()` with a Sonnet system prompt tuned for:
  - India-rooted examples (₹, km, NCERT, Indian places/people)
  - Locale-aware output (Devanagari for Hindi, Tamil for ta, etc.)
  - 50-200 word example bodies (long enough to be substantive,
    short enough to read on a phone)
  - One concrete scene per example, mapped back to the concept

Returns 3 examples by default — the curator queue then filters to
the best 1-2 before publishing.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from . import concept_examples, llm_call, llm_obs, models


@dataclass(frozen=True)
class GeneratedExamples:
    concept_slug: str
    locale: str
    examples: list[str]      # raw markdown strings
    inserted_ids: list[str]  # rows now in concept_examples (pending)


_SYSTEM_PROMPT = """\
You write **real-world examples** for Indian K-12 / exam-prep
students. Each example takes a single concept and shows where it
appears in DAILY INDIAN LIFE — not a textbook, not a Western context.

Rules:
  1. Use Indian-context scenes ONLY: cricket / kabaddi / Mumbai
     locals / autorickshaws / Diwali fireworks / monsoon floods /
     mid-day meal / dosa / kirana shop / village pond / cyclone /
     Holi / Republic Day parade / Indian rail / Bollywood.
     Forbidden: baseball / hot dogs / Thanksgiving / freeways /
     European cities / Western brand names.
  2. Use ₹ (rupees), km (kilometres), kg (kilograms). Never $, miles,
     feet.
  3. Use Indian names for people in the scene (Aman, Priya, Raj,
     Asha, Kavita, Vikram, Lakshmi). Avoid John/Mary/Tom.
  4. Each example: 60-180 words. ONE concrete scene. Then 1-2
     sentences mapping the scene back to the concept.
  5. Output language: {locale} ({locale_name}). For non-English
     locales, use the appropriate script (Devanagari for hi/mr,
     Tamil for ta, Telugu for te, etc.). Keep mathematical formulae
     in standard notation.
  6. Tone: relatable to a Class 8-12 Indian student.

Return JSON ONLY — no preamble, no markdown wrapper:

{{
  "examples": [
    "<example 1 markdown>",
    "<example 2 markdown>",
    "<example 3 markdown>"
  ]
}}

Generate exactly {count} distinct examples. Each must use a DIFFERENT
Indian scene (don't repeat cricket three times).
"""


_LOCALE_NAMES = {
    "en": "English",
    "hi": "Hindi (Devanagari)",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "ml": "Malayalam",
    "mr": "Marathi (Devanagari)",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Punjabi (Gurmukhi)",
}


def _strip_to_json(text: str) -> str:
    """Defensive: peel off code fences if Claude wraps the response."""
    text = (text or "").strip()
    m = re.match(r"^\s*```(?:json)?\s*\n([\s\S]+?)\n\s*```\s*$", text)
    if m:
        text = m.group(1).strip()
    return text


def generate_and_insert(
    *,
    concept_slug: str,
    concept_display: str | None = None,
    count: int = 3,
    locale: str = "en",
    user_id: str | None = None,
    user_tier: str | None = None,
) -> GeneratedExamples:
    """Generate `count` examples for the concept and insert them as
    `pending` rows. The curator approves/rejects via the queue.

    Raises:
      • `llm_obs.BudgetExceeded` — caller over daily cap.
      • `RuntimeError` — Claude call failure.
      • `ValueError` — malformed JSON / empty response.
    """
    if not concept_slug:
        raise ValueError("concept_slug is required")
    locale = (locale or "en").strip().lower()
    if locale not in _LOCALE_NAMES:
        locale = "en"
    count = max(1, min(int(count or 3), 6))

    concept_display = concept_display or concept_slug.replace("-", " ").title()
    system = _SYSTEM_PROMPT.format(
        locale=locale,
        locale_name=_LOCALE_NAMES[locale],
        count=count,
    )
    user_msg = (
        f"Concept: {concept_display}\n"
        f"Output JSON only with exactly {count} distinct Indian-scene examples."
    )

    model = os.environ.get(
        "PADHAI_REAL_WORLD_EXAMPLES_MODEL", models.SONNET_MODEL,
    )

    result = llm_call.call_claude(
        module="real_world_examples",
        prompt_version="v1",
        model=model,
        user_id=user_id,
        subscription_tier=user_tier,
        max_tokens=2500,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = ""
    try:
        msg = result.response
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "text":
                raw += block.text
    except Exception as e:
        raise RuntimeError(f"could not read Claude response: {e}") from e

    raw = _strip_to_json(raw)
    if not raw:
        raise ValueError("Claude returned an empty response")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned malformed JSON: {e}; "
            f"first 200 chars: {raw[:200]!r}"
        ) from e

    examples_md = data.get("examples") or []
    if not isinstance(examples_md, list) or not examples_md:
        raise ValueError("response missing 'examples' array")

    inserted_ids: list[str] = []
    for ex_md in examples_md:
        if not isinstance(ex_md, str) or not ex_md.strip():
            continue
        row = concept_examples.insert(
            concept_slug=concept_slug,
            example_md=ex_md.strip(),
            locale=locale,
            source="claude",
            generator_call_id=getattr(result, "call_id", None),
            status="pending",
        )
        inserted_ids.append(row.id)

    return GeneratedExamples(
        concept_slug=concept_slug,
        locale=locale,
        examples=[e.strip() for e in examples_md if isinstance(e, str)],
        inserted_ids=inserted_ids,
    )


# Re-export for callers
BudgetExceeded = llm_obs.BudgetExceeded
