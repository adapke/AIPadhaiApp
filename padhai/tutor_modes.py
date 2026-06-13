"""prod-136 — Tutor Mode Switcher (CK-12 Flexi pattern).

CK-12 Flexi exposes 10-12 conversation "modes" — quick-explain,
quiz-me, real-world-analogy, etc. — each a different system-prompt
skin over the same model. Students pick the lens before asking.

Pathshala adapts the idea for Indian exam contexts. 6 modes tuned
to NEET / JEE / UPSC / CBSE / desi everyday-language students:

  - quick_explain     90-second board-exam recall
  - jee_advanced_drill multi-step problem-solving, no shortcuts
  - neet_one_liner    MCQ-style elimination thinking
  - cbse_board_answer 5-mark CBSE-style structured answer
  - desi_analogy      cricket / dosa / monsoon / Diwali everyday examples
  - rural_simple      Class 6-8 vocab, Hindi/regional fallback

The catalog is just data — `apply_mode(system_prompt, mode_key)`
returns the augmented system prompt. The caller stays in control;
no SDK or DB coupling here.

Surfaces using this:
  - `padhai/tutor.py:send_message(mode=...)` — per-turn mode override
  - `GET /api/tutor/modes` — public catalog so SPA can render chips
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TutorMode:
    """One conversation mode."""

    key: str
    label_en: str         # English label shown in mode chip
    label_hi: str         # Hindi label (devanagari)
    one_line_en: str      # 1-line description (English)
    one_line_hi: str      # 1-line description (Hindi)
    system_addendum: str  # Appended to the default tutor system prompt
    icon: str             # Emoji for the chip


# Default order = the order chips appear in the SPA.
MODES: tuple[TutorMode, ...] = (
    TutorMode(
        key="quick_explain",
        label_en="Quick explain",
        label_hi="जल्दी समझाओ",
        one_line_en="90-second board-exam recall — get to the point.",
        one_line_hi="90-सेकंड में बोर्ड परीक्षा का जवाब — सीधा मुद्दे पर।",
        icon="⚡",
        system_addendum=(
            "Mode override: QUICK EXPLAIN. The student wants a 90-second "
            "answer for board-exam recall. Constraints: under 120 words. "
            "Lead with the definition. Then 2-3 key facts. Then 1 example. "
            "Skip derivations. No 'let me ask you a question' — just "
            "answer cleanly. Don't refuse to answer; refusing to teach a "
            "factual point is the WRONG mode for this lens."
        ),
    ),
    TutorMode(
        key="jee_advanced_drill",
        label_en="JEE Advanced drill",
        label_hi="JEE Advanced अभ्यास",
        one_line_en="Multi-step problem solving. Show every line of work.",
        one_line_hi="बहु-चरण समस्या समाधान। हर पंक्ति दिखाओ।",
        icon="🧮",
        system_addendum=(
            "Mode override: JEE ADVANCED DRILL. The student is prepping "
            "for JEE Advanced (top 2.5 lakh nationally; problems require "
            "multi-step thinking). Constraints: always show ALL "
            "intermediate steps. Never skip algebra ('it follows that'). "
            "After the solution, mention which JEE chapter this maps to "
            "(e.g. 'Rotational Mechanics / Class 11'). Use proper "
            "mathematical notation. Then ask the student to attempt a "
            "minor variation themselves before moving on."
        ),
    ),
    TutorMode(
        key="neet_one_liner",
        label_en="NEET one-liner",
        label_hi="NEET एक-पंक्ति",
        one_line_en="MCQ-style. Right answer + why each wrong one is wrong.",
        one_line_hi="MCQ शैली। सही उत्तर + प्रत्येक गलत क्यों गलत है।",
        icon="🎯",
        system_addendum=(
            "Mode override: NEET ONE-LINER. The student is prepping for "
            "NEET (180 MCQs in 200 minutes — speed + elimination matter). "
            "Constraints: output as if this is an MCQ. The first line is "
            "the answer in 1 sentence (under 25 words). The second line "
            "is the SINGLE keyword/fact that makes it correct. If the "
            "student supplied 4 options, briefly mark each: ✓ right one "
            "(why), ✗ each wrong one (which fact rules it out). Skip "
            "derivations entirely. NEET marks each item +4 / -1, so "
            "elimination logic matters more than first-principles."
        ),
    ),
    TutorMode(
        key="cbse_board_answer",
        label_en="CBSE board answer",
        label_hi="CBSE बोर्ड उत्तर",
        one_line_en="5-mark structured answer in CBSE marking format.",
        one_line_hi="5-अंक संरचित उत्तर CBSE अंकन प्रारूप में।",
        icon="📝",
        system_addendum=(
            "Mode override: CBSE BOARD ANSWER. The student is prepping for "
            "CBSE Class 10/12 board exams (5-mark long-answer questions "
            "are graded against marking schemes). Constraints: structure "
            "the answer for a 5-mark question. Use headings (Definition, "
            "Explanation, Example, Diagram description, Conclusion). "
            "Reference the NCERT chapter/page where possible. Include the "
            "key technical terms in BOTH English and Hindi if the topic "
            "is in a hindi-medium subject. End with a one-line summary "
            "the student can highlight in their answer sheet."
        ),
    ),
    TutorMode(
        key="desi_analogy",
        label_en="Desi analogy",
        label_hi="देसी उदाहरण",
        one_line_en="Explain via cricket / dosa / Diwali / monsoon examples.",
        one_line_hi="क्रिकेट / डोसा / दिवाली / मानसून के उदाहरणों से।",
        icon="🇮🇳",
        system_addendum=(
            "Mode override: DESI ANALOGY. Explain the concept using an "
            "everyday Indian example that the student would actually "
            "recognise. Constraints: lead with a concrete Indian scene "
            "(Mumbai local train, cricket match, kabaddi raid, Diwali "
            "fireworks, monsoon flood, mid-day meal queue, autorickshaw "
            "ride, kirana shop accounting, dosa flipping). Then map the "
            "scene's mechanics back to the concept. Use rupees and "
            "kilometres, never dollars and miles. Avoid Western analogies "
            "(baseball, hot dogs, freeways, Thanksgiving) — they don't "
            "land for an Indian student. Keep it under 250 words."
        ),
    ),
    TutorMode(
        key="rural_simple",
        label_en="Rural simple",
        label_hi="ग्रामीण सरल",
        one_line_en="Class 6-8 vocabulary. First-gen learner friendly.",
        one_line_hi="कक्षा 6-8 शब्दावली। पहली पीढ़ी के सीखने वाले के लिए।",
        icon="🏡",
        system_addendum=(
            "Mode override: RURAL SIMPLE. The student is a first-generation "
            "learner in a Tier-3/Tier-4 town or village. Many technical "
            "terms in English will be alienating. Constraints: vocabulary "
            "must fit Class 6-8 (use 'Newton ki pehli niyam' instead of "
            "'first law of motion' if the language is Hindi). Replace "
            "jargon with everyday words: 'force' becomes 'dhakka', "
            "'acceleration' becomes 'jaldi se tezi'. Sentences under 12 "
            "words. Avoid acronyms. If the student typed in English but "
            "their grammar suggests a regional-medium school, mix "
            "Hindi-English freely. The goal is comprehension > polish."
        ),
    ),
)


_BY_KEY = {m.key: m for m in MODES}


def get_mode(key: str | None) -> TutorMode | None:
    """Return the TutorMode for `key`, or None if not found / not set."""
    if not key:
        return None
    return _BY_KEY.get(key.strip().lower())


def apply_mode(base_system_prompt: str, mode_key: str | None) -> str:
    """Append the mode-specific addendum to the base tutor system prompt.

    If `mode_key` is None, blank, or unknown → returns base unchanged.
    """
    mode = get_mode(mode_key)
    if mode is None:
        return base_system_prompt
    return (
        base_system_prompt
        + "\n\n--- MODE-SPECIFIC OVERRIDE ---\n"
        + mode.system_addendum
        + "\n--- END MODE OVERRIDE ---"
    )


def list_modes() -> list[dict]:
    """Public catalog — render each mode as a chip in the SPA."""
    return [
        {
            "key": m.key,
            "label_en": m.label_en,
            "label_hi": m.label_hi,
            "one_line_en": m.one_line_en,
            "one_line_hi": m.one_line_hi,
            "icon": m.icon,
        }
        for m in MODES
    ]


# Stable identifier used as the "no mode" sentinel; matches "None".
DEFAULT_MODE = None
