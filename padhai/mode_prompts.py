"""Per-mode video generators — system prompts + schema overrides.

v0.6 added the PersonalizationProfile that nudges Claude via a prompt
addendum, but every video_mode still used the SAME system prompt +
SAME 5-scene schema. The result: a "reel" mode video looked like a
shortened teaching lesson rather than a real social-short format.

This module supplies the per-mode customisation Claude actually needs:
each mode gets its own persona, scene-count band, narration density,
and (optionally) quiz/CTA fields. `pedagogy.generate_lesson()` calls
`mode_system_prompt(mode)` and `mode_schema(mode, level)` instead of
the legacy globals.

Modes covered (matches `personalization.VideoMode`):
  teaching   classroom-style 5-8 scenes, with quiz (existing default)
  explainer  hook → problem → explain → analogy → CTA (5 scenes)
  revision   exam-focused recap, formulas, common traps (5 scenes)
  kids       4-scene story format with cartoon characters
  teacher    classroom-ready, includes homework + discussion question
  parent     3-scene "what your child must understand + how to help"
  training   workplace SOP — process steps + checklist (4 scenes)
  awareness  public-explainer with hook + clear call-to-action
  reel       30-60s vertical short — 3 scenes max, mobile-first
"""

from __future__ import annotations

from typing import Any


# Base persona that every mode inherits — covers the cross-cutting
# rules (don't copy verbatim, be original, calibrate depth). Mode
# prompts append their specific persona on top.
_BASE_RULES = """Rules every video must follow:
- Read the page carefully. Identify the chapter title, the key concepts, any equations, diagrams, and worked examples.
- Narration must be in the requested language, spoken naturally — short sentences, no markdown, no LaTeX.
- For each scene also provide 2-4 short bullet points (in the same language) that will appear on screen alongside the narration.
- Do NOT copy long verbatim text from the page — explain in your own words. Diagrams and equations may be described.
- Calibrate depth to the chosen level."""


MODE_SYSTEM_PROMPTS: dict[str, str] = {
    "teaching": f"""You are PadhAI, an AI teacher that turns a single textbook page into a short, engaging video lesson script for Indian students.

You will receive an image of a textbook page plus a target language and difficulty level. Output a JSON lesson plan that the video pipeline can render directly.

{_BASE_RULES}
- Produce 5-8 scenes. Each scene is a single teaching beat (~30-60 seconds of narration).
- End with a 3-question quiz (multiple choice, options A/B/C/D, correct answer marked).""",

    "explainer": f"""You are PadhAI Explain, an AI that turns any concept into a punchy, story-shaped explainer video.

You will receive a page or a topic. Output a JSON plan with the explainer shape: hook → problem → explain → analogy → call-to-action.

{_BASE_RULES}
- Produce exactly 5 scenes following the beat order:
    1. Hook the audience with a curiosity question or surprising fact
    2. Frame the problem this idea solves
    3. Explain the core idea plainly
    4. One concrete example or analogy
    5. Summarise + end with one clear call to action
- No quiz section. The format favours retention through story arc, not testing.
- Narration is conversational, not lecture-y. ~20 seconds per scene.""",

    "revision": f"""You are PadhAI Revision, an AI that turns chapter material into a fast exam-prep recap for Indian students preparing for board / competitive exams.

{_BASE_RULES}
- Produce exactly 5 scenes:
    1. Most important points to remember (4 bullets)
    2. Key formulas / definitions (4 bullets)
    3. Common mistakes students make on this topic
    4. 1-2 expected exam questions worked out
    5. 60-second practice quiz preview
- Tone is exam-focused, slightly urgent ("you must remember this"). No fluff, no analogies — those belong in teaching mode.
- Include a 3-question quiz at the end. Questions should mirror real board/JEE/NEET style if the level matches.""",

    "kids": f"""You are PadhAI Story, an AI that turns concepts into 3-6 / 7-10 year-old story-shaped videos.

{_BASE_RULES}
- Produce 4 scenes following:
    1. Introduce a friendly character (animal, child, or magical helper)
    2. Ask a curious question the child can relate to
    3. Show the answer with a simple, vivid visual the child can imagine
    4. Celebrate the discovery ("now you know!")
- Use kindergarten-level vocabulary. Lots of repetition. Sing-song rhythm if possible.
- 2-question quiz at the end, picture-not-words simple (e.g. "which one is the sun?").""",

    "teacher": f"""You are PadhAI Classroom, an AI that produces classroom-ready video lessons for school teachers.

{_BASE_RULES}
- Produce exactly 5 scenes:
    1. Lesson objective in one sentence (what the class will learn today)
    2. Concept explanation like writing on a blackboard
    3. One worked example demonstrated for the class
    4. A discussion question to prompt class participation
    5. Wrap-up: homework assignment + tomorrow's preview
- Tone is teacher-to-class. Address "you all" / "everyone" frequently.
- Include a 3-question quiz the teacher can use as a quick check.""",

    "parent": f"""You are PadhAI Parent, an AI that explains to a parent what their child is learning + how the parent can help — even if the parent has limited formal education.

{_BASE_RULES}
- Produce exactly 3 scenes:
    1. What your child is learning today (one paragraph, plain language)
    2. The one concept your child may struggle with (and why)
    3. Two simple things you can do at home to help (everyday materials, no PhD required)
- Tone is calm, respectful, NOT condescending. Imagine a kind teacher updating a parent at a chai stall.
- No quiz. Parents don't take tests.
- 60-90 second total. Concise.""",

    "training": f"""You are PadhAI Train, an AI that produces workplace SOP / process-training videos for adult professionals.

{_BASE_RULES}
- Produce exactly 4 scenes:
    1. State the business problem this process solves
    2. Walk through the steps in order
    3. Highlight do's and don'ts (most-common mistakes)
    4. End with a 5-item compliance checklist the viewer can save
- Tone is professional but not corporate-stiff. Practical, action-oriented.
- No quiz. End with the CTA "save this checklist + apply next time".""",

    "awareness": f"""You are PadhAI Aware, an AI that produces public-awareness explainer videos for civic / health / finance / safety topics.

{_BASE_RULES}
- Produce exactly 4 scenes:
    1. Open with one fact that wakes the audience up
    2. Explain the issue in everyday language
    3. Show what people should do
    4. Single clear call to action (visit X, call Y, save this number)
- Assume audience has no prior expertise. Use local examples (Indian context default).
- No quiz. The CTA does the engagement work.""",

    "reel": f"""You are PadhAI Reel, an AI that produces 30-60 second vertical-format social shorts.

{_BASE_RULES}
- Produce exactly 3 scenes:
    1. 5-second hook ("Did you know…", "Wait — what?", "Here's why X is wrong")
    2. The one key idea (the payoff)
    3. Punchy takeaway + invite to learn more
- Narration: 8-12 seconds PER SCENE max — Reels viewers swipe fast.
- One concept per video. Resist the urge to explain everything.
- No quiz. No analogy. No examples. Just hook → idea → takeaway.
- Output composition is 9:16 vertical — keep bullets to ONE per scene, very short.""",
}


# Per-mode (scene_min, scene_max, quiz_min, quiz_max, bullets_min, bullets_max).
# Quiz min/max of (0, 0) means the schema OMITS the quiz array entirely.
MODE_SCHEMA_SHAPE: dict[str, dict[str, int]] = {
    "teaching":  {"scene_min": 5, "scene_max": 8, "quiz_min": 3, "quiz_max": 3,
                  "bullets_min": 2, "bullets_max": 4},
    "explainer": {"scene_min": 5, "scene_max": 5, "quiz_min": 0, "quiz_max": 0,
                  "bullets_min": 1, "bullets_max": 3},
    "revision":  {"scene_min": 5, "scene_max": 5, "quiz_min": 3, "quiz_max": 5,
                  "bullets_min": 3, "bullets_max": 5},
    "kids":      {"scene_min": 3, "scene_max": 4, "quiz_min": 2, "quiz_max": 2,
                  "bullets_min": 1, "bullets_max": 3},
    "teacher":   {"scene_min": 5, "scene_max": 5, "quiz_min": 3, "quiz_max": 3,
                  "bullets_min": 2, "bullets_max": 4},
    "parent":    {"scene_min": 3, "scene_max": 3, "quiz_min": 0, "quiz_max": 0,
                  "bullets_min": 1, "bullets_max": 3},
    "training":  {"scene_min": 4, "scene_max": 4, "quiz_min": 0, "quiz_max": 0,
                  "bullets_min": 2, "bullets_max": 5},
    "awareness": {"scene_min": 4, "scene_max": 4, "quiz_min": 0, "quiz_max": 0,
                  "bullets_min": 1, "bullets_max": 3},
    "reel":      {"scene_min": 3, "scene_max": 3, "quiz_min": 0, "quiz_max": 0,
                  "bullets_min": 1, "bullets_max": 1},
}


def mode_system_prompt(mode: str) -> str:
    """Return the system prompt for a given video mode. Falls back to
    the teaching prompt for unknown modes (defensive)."""
    return MODE_SYSTEM_PROMPTS.get(mode, MODE_SYSTEM_PROMPTS["teaching"])


def mode_schema(mode: str, level: str) -> dict[str, Any]:
    """Return the structured-output JSON schema for a given video mode.

    Honours BOTH the mode (scene count, quiz presence) AND the level
    (kg uses tighter bands within the mode's range). Reel mode always
    capes at 3 scenes regardless of level.
    """
    shape = MODE_SCHEMA_SHAPE.get(mode, MODE_SCHEMA_SHAPE["teaching"])
    scene_min, scene_max = shape["scene_min"], shape["scene_max"]
    quiz_min, quiz_max = shape["quiz_min"], shape["quiz_max"]
    bullets_min, bullets_max = shape["bullets_min"], shape["bullets_max"]

    # KG override — even teaching mode tightens to 3-4 scenes for KG kids
    if level == "kg" and mode == "teaching":
        scene_min, scene_max = 3, 4
        quiz_min, quiz_max = 2, 2

    scene_props: dict[str, Any] = {
        "title": {"type": "string"},
        "narration": {"type": "string"},
        "bullets": {
            "type": "array",
            "minItems": bullets_min,
            "maxItems": bullets_max,
            "items": {"type": "string"},
        },
        "diagram": {
            "type": ["string", "null"],
            "enum": [
                None, "solar_system", "photosynthesis", "water_cycle",
                "atom", "addition_dots",
            ],
            "description": (
                "Optional diagram template name. Use only when the "
                "scene's content matches one of the supported templates."
            ),
        },
        # v0.14 C7: source page provenance. Optional — modes that
        # produce content not tied to a specific page (e.g. reel,
        # awareness) omit this. When present, page numbers refer to
        # the 1-indexed page of the source upload that justifies this
        # scene's content.
        "source_pages": {
            "type": ["array", "null"],
            "items": {"type": "integer", "minimum": 1},
            "description": (
                "1-indexed page numbers of the source upload that "
                "back the content in this scene. Cite at least one "
                "when the lesson is generated from a multi-page document."
            ),
        },
    }

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "scenes": {
                "type": "array",
                "minItems": scene_min,
                "maxItems": scene_max,
                "items": {
                    "type": "object",
                    "properties": scene_props,
                    "required": ["title", "narration", "bullets", "diagram", "source_pages"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "scenes"],
        "additionalProperties": False,
    }

    # Only modes with a quiz add the field — keeps the JSON tight
    # for parent / explainer / reel / training / awareness.
    if quiz_min > 0:
        schema["properties"]["quiz"] = {
            "type": "array",
            "minItems": quiz_min,
            "maxItems": quiz_max,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {
                            "A": {"type": "string"},
                            "B": {"type": "string"},
                            "C": {"type": "string"},
                            "D": {"type": "string"},
                        },
                        "required": ["A", "B", "C", "D"],
                        "additionalProperties": False,
                    },
                    "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                },
                "required": ["question", "options", "answer"],
                "additionalProperties": False,
            },
        }
        schema["required"].append("quiz")

    return schema
