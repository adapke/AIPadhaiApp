"""prod-131 — AI-Resistant Assignment Generator.

Lifts a CK-12 teacher-tool idea: produce homework / quiz items that
LLMs can't trivially solve, by leaning on:

  1. **Student's own context** — assignments reference the student's
     own data, drawing, photograph, or local observations. ("Measure
     the kitchen pillar in your home and …") — ChatGPT doesn't know
     the student's pillar.
  2. **Process-showing rubric** — full marks require showing all
     intermediate steps. Final-answer-only attempts forfeit credit.
     ChatGPT outputs are usually polished final answers without
     genuine intermediate work.
  3. **Hyper-local examples** — Indian board context: NCERT chapter
     references, Indian historical figures, ₹ amounts, kilometres,
     state-board terminology.
  4. **Multi-modal asks** — require a hand-drawn diagram, an audio
     explanation, or a photograph of physical setup. Plain-text
     LLMs can't fake these.
  5. **Open-ended reflection** — "Why do YOU think …", "Describe a
     situation in your village/town where …". Forces a personal voice.

The endpoint sits under `/api/admin/assignments/generate-ai-resistant/`
so it inherits the router-level admin dep injection. Teachers in
an org context will reach it through the school-portal UI; ops can
hit it directly during pilot tests.

This is NOT a content moderation tool — it does not detect AI cheating
after the fact. It's a *design pattern* tool: ask Claude to write
assignments that are hard for Claude (and friends) to solve well.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from . import llm_call, llm_obs, models


@dataclass(frozen=True)
class GeneratedAssignment:
    title: str
    instructions_md: str
    questions: list[dict]
    rubric_md: str
    anti_cheat_techniques: list[str]
    estimated_time_min: int
    grade: int | None
    subject: str
    language: str
    board: str | None


_SYSTEM_PROMPT = """\
You are a senior Indian-school assignment designer. Your job is to
produce homework / classroom assignments that are inherently hard
for Large Language Models to solve well, because they require:

  1. Personal context the student has but an LLM doesn't (their
     own kitchen, photo, drawing, neighbourhood observation).
  2. Multi-step work-showing graded against a rubric where only
     the journey earns credit — the final answer alone fails.
  3. Hyper-local Indian framing (NCERT chapter labels, ₹, km,
     Indian place names, state-board terminology in {board}).
  4. Multi-modal asks the student must fulfil offline: hand-drawn
     diagram, audio recording, photograph, observation log.
  5. Open-ended reflection in the student's own voice ("Why do
     YOU think …", "In YOUR neighbourhood, where could …").

You must produce a single JSON object — no extra prose — matching
exactly this schema:

{{
  "title": "<3-7 word assignment title>",
  "instructions_md": "<Markdown — overview, expectations, total marks>",
  "questions": [
    {{
      "id": "q1",
      "marks": <integer>,
      "prompt": "<the question itself; in {language}>",
      "anti_cheat_pattern": "<which of the 5 patterns above this uses>",
      "expected_artifacts": ["text", "photo", "drawing", "audio"],
      "grading_notes": "<what the teacher should look for>"
    }}
  ],
  "rubric_md": "<Markdown rubric; each row = (criterion, weight, descriptor)>",
  "anti_cheat_techniques": ["<which patterns this assignment uses>"],
  "estimated_time_min": <integer>
}}

Total marks across questions must equal {total_marks}.
Generate exactly {count} questions.
Difficulty: {difficulty}.
Subject: {subject}.
Grade: Class {grade}.
Language for all student-facing text: {language}.
Topic: {topic}.
Board: {board}.

CRITICAL: every question must use at least one of the 5 anti-cheat
patterns. Mix patterns across the question set; don't repeat the
same one. Use Devanagari / Tamil / Bengali / Marathi script for
non-English `{language}` values (don't transliterate).
"""


def _strip_to_json(text: str) -> str:
    """Defensive: peel off code fences if Claude wraps the response."""
    text = (text or "").strip()
    # ```json … ``` or ``` … ```
    m = re.match(
        r"^\s*```(?:json)?\s*\n([\s\S]+?)\n\s*```\s*$", text,
    )
    if m:
        text = m.group(1).strip()
    return text


def generate(
    *,
    topic: str,
    grade: int | None = None,
    subject: str = "general",
    board: str = "CBSE",
    language: str = "en",
    count: int = 5,
    total_marks: int = 20,
    difficulty: str = "medium",
    user_id: str | None = None,
    user_tier: str | None = None,
    client: Any | None = None,
) -> GeneratedAssignment:
    """Generate one assignment via Claude.

    Raises:
      • `llm_obs.BudgetExceeded` — caller is over their daily cap.
      • `RuntimeError` — anthropic SDK / key / Claude call failed.
      • `ValueError` — Claude returned malformed JSON.

    The caller is responsible for catching `BudgetExceeded` and
    rendering a graceful fallback (e.g. "AI quota exhausted; upgrade
    to M2"). See `routers/orgs_assignments.py` for a usage example.
    """
    count = max(1, min(count, 20))
    total_marks = max(1, min(total_marks, 100))
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    system = _SYSTEM_PROMPT.format(
        board=board or "CBSE",
        language=language or "en",
        total_marks=total_marks,
        count=count,
        difficulty=difficulty,
        subject=subject or "general",
        grade=grade if grade is not None else "(grade unspecified)",
        topic=topic,
    )

    model = os.environ.get(
        "PADHAI_AI_RESIST_MODEL", models.SONNET_MODEL,
    )

    result = llm_call.call_claude(
        module="ai_resistant_assignments",
        prompt_version="v1",
        model=model,
        user_id=user_id,
        subscription_tier=user_tier,
        client=client,
        max_tokens=3500,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Generate the AI-resistant assignment now. "
                f"Topic: {topic}. Output JSON only."
            ),
        }],
    )

    # ClaudeCallResult exposes the extracted text as .text (the raw anthropic
    # Message is .resp) — there is no .response attribute.
    raw = _strip_to_json(result.text or "")
    if not raw:
        raise ValueError("Claude returned an empty assignment")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returned malformed JSON: {e}; first 200 chars: {raw[:200]!r}",
        ) from e

    # Defensive type-coerce + default-fill the contract
    questions = data.get("questions") or []
    if not isinstance(questions, list) or not questions:
        raise ValueError("response missing 'questions' array")
    for q in questions:
        q.setdefault("anti_cheat_pattern", "context-specific")
        q.setdefault("expected_artifacts", ["text"])
        q.setdefault("grading_notes", "")
        if not isinstance(q.get("marks"), int):
            q["marks"] = 1

    return GeneratedAssignment(
        title=str(data.get("title") or topic),
        instructions_md=str(data.get("instructions_md") or ""),
        questions=questions,
        rubric_md=str(data.get("rubric_md") or ""),
        anti_cheat_techniques=list(data.get("anti_cheat_techniques") or []),
        estimated_time_min=int(data.get("estimated_time_min") or 30),
        grade=grade,
        subject=subject,
        language=language,
        board=board,
    )


# Surface for tests / non-LLM callers that just want a deterministic
# stub (useful in CI structural mode where no API key is set).
def stub(*, topic: str, count: int = 3, **_kw) -> GeneratedAssignment:
    """Deterministic stub for tests. Mirrors the contract without
    calling Claude."""
    qs = [
        {
            "id": f"q{i+1}",
            "marks": max(1, 20 // count),
            "prompt": f"Stub question {i+1} about {topic}",
            "anti_cheat_pattern": [
                "context-specific", "process-showing",
                "hyper-local", "multi-modal", "open-ended",
            ][i % 5],
            "expected_artifacts": ["text"],
            "grading_notes": "stub",
        }
        for i in range(count)
    ]
    return GeneratedAssignment(
        title=f"Stub assignment: {topic}",
        instructions_md=f"Complete all {count} questions about {topic}.",
        questions=qs,
        rubric_md="| Criterion | Weight | Descriptor |\n|---|---|---|\n| Stub | 100% | Stub rubric |",
        anti_cheat_techniques=["context-specific", "process-showing"],
        estimated_time_min=30,
        grade=None,
        subject="general",
        language="en",
        board="CBSE",
    )


# Re-export the exception for callers
BudgetExceeded = llm_obs.BudgetExceeded
