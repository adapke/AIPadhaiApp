"""Page image → structured teaching script via Claude vision."""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic

from .models import HAIKU_MODEL, OPUS_MODEL

if TYPE_CHECKING:
    from .cache import Cache

MODEL = OPUS_MODEL

# Language code → human-readable name passed to the model and to gTTS.
SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "mr": "Marathi",
    "bn": "Bengali",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ml": "Malayalam",
}

LEVEL_GUIDANCE = {
    "kg": (
        "Pitch this at a kindergarten student (age 3-6). Treat the lesson as a "
        "'learn-and-fun' moment, not a textbook chapter. RULES: produce only "
        "3-4 scenes total (not 5-8). Each scene's narration is 2-4 sentences, "
        "and each sentence is 5-10 words. Use everyday comparisons the child "
        "knows — animals, food, toys, family. Repeat key words. Be cheerful: "
        "narration may include phrases like 'wow!', 'see?', 'so cool!'. "
        "Bullets should be ultra-short (3-6 words). Quiz: 2 questions only, "
        "pictures-not-words simple."
    ),
    "eli5": "Explain like the student is 5 years old. Tiny words, lots of analogies, no jargon.",
    "primary": "Pitch this at a primary-school student (grades 3-5). Simple vocabulary, concrete examples.",
    "middle": "Pitch this at a middle-school student (grades 6-8). Introduce technical terms with definitions.",
    "secondary": "Pitch this at a secondary-school / board-exam student (grades 9-12). Full technical depth, exam-relevant framing.",
    "neet_jee": "Pitch this at a NEET/JEE aspirant. Maximum rigour, derivations, common exam traps.",
}

# Board / exam-specific prompt addenda injected into the user turn.
# Kept separate from LEVEL_GUIDANCE so the two axes compose independently.
BOARD_GUIDANCE: dict[str, str] = {
    "CBSE": (
        "This student follows the CBSE curriculum (Central Board of Secondary Education). "
        "Frame examples and explanations around NCERT textbook language. Reference CBSE "
        "examination patterns (1-mark, 3-mark, 5-mark questions). Use CBSE marking-scheme "
        "terminology ('diagram', 'tabular form', 'define and explain')."
    ),
    "ICSE": (
        "This student follows the ICSE curriculum (Indian Certificate of Secondary Education). "
        "ICSE favours depth and analytical reasoning over rote recall. Use precise scientific "
        "vocabulary. Include application-based examples and comparisons. ICSE questions often "
        "ask 'Give reasons' or 'Distinguish between' — build that analytical framing."
    ),
    "IGCSE": (
        "This student follows the Cambridge IGCSE curriculum. Use internationally neutral "
        "examples alongside Indian context. Follow Cambridge command words (Describe, Explain, "
        "Evaluate, Analyse, Calculate). Include worked examples in the Cambridge style."
    ),
    "Maharashtra": (
        "This student follows the Maharashtra State Board curriculum (SSC/HSC). "
        "Align terminology with Maharashtra Textbook Bureau (Balbharati) textbooks. "
        "Include Maharashtra Board exam question patterns and chapter numbering. "
        "Use Marathi words for key concepts where appropriate."
    ),
    "Karnataka": (
        "This student follows the Karnataka State Board curriculum. "
        "Align with KTBS (Karnataka Textbook Society) textbook structure. "
        "Include Karnataka SSLC/PUC exam patterns. Use Kannada words for key concepts "
        "where helpful."
    ),
    "TamilNadu": (
        "This student follows the Tamil Nadu State Board curriculum (Samacheer Kalvi). "
        "Use the unified Samacheer Kalvi book structure and terminology. "
        "Reference Tamil Nadu public exam question patterns. Include Tamil terms for "
        "key concepts where appropriate."
    ),
    "AP_Telangana": (
        "This student follows the Andhra Pradesh / Telangana State Board curriculum. "
        "Align with AP SCERT / TS SCERT textbook chapters and terminology. "
        "Use Telugu words for key concepts where helpful. Include SSC board exam patterns."
    ),
    "UP": (
        "This student follows the Uttar Pradesh Board curriculum (UP Madhyamik Shiksha Parishad). "
        "Align with UP Board textbook structure. Use Hindi for key technical terms where "
        "appropriate. Reference UP Board High School / Intermediate exam question patterns."
    ),
    "NEET": (
        "This is a NEET aspirant preparing for the National Eligibility cum Entrance Test (medical). "
        "Prioritise Biology (Botany + Zoology) with NCERT line-by-line accuracy. For Physics and "
        "Chemistry, emphasise concepts that appear frequently in NEET. Flag common NEET trap questions "
        "and distinction-based answers. Use NEET Previous Year Question (PYQ) patterns. "
        "Accuracy over speed — every fact must be verifiable in NCERT."
    ),
    "JEE": (
        "This is a JEE aspirant preparing for IIT Joint Entrance Examination. "
        "Apply maximum mathematical rigour — show full derivations from first principles. "
        "Highlight JEE Main and JEE Advanced previous-year question patterns. "
        "Stress conceptual depth: multiple-approach solutions, edge cases, and common pitfalls "
        "that trap JEE students. Include numerical problem-solving steps."
    ),
    "UPSC": (
        "This is a UPSC Civil Services aspirant. Connect every concept to governance, policy, "
        "current affairs, or constitutional provisions where relevant. Use UPSC answer-writing "
        "structure: Introduction (define/context) → Body (analysis, examples, linkages) → "
        "Conclusion (way forward). Aim for inter-disciplinary connections. "
        "Flag GS Paper relevance (GS1/GS2/GS3/GS4)."
    ),
    "SSC": (
        "This is an SSC aspirant (Staff Selection Commission — CGL/CHSL/MTS). "
        "Cover GK, reasoning, and quantitative aptitude angles for the topic. "
        "Use SSC CGL/CHSL exam patterns with short, direct MCQ-style explanations. "
        "Emphasise speed and accuracy: short-cuts, tricks, and common SSC question types."
    ),
}


@dataclass
class Scene:
    """A single teaching beat — one slide in the storyboard.

    Required: title + narration + bullets. The rest are PRD §11
    Lesson-Blueprint v2 fields, all optional so v1 Lessons keep
    deserializing cleanly. New generators (v0.7+) populate them; the
    renderer reads them when present and falls back to the old layout
    when they're missing."""
    title: str
    narration: str
    bullets: list[str]
    diagram: str | None = None              # animated-template name (photosynthesis, atom, …)
    # — v0.7 storyboard v2 fields (all optional, backward-compatible) —
    scene_goal: str | None = None           # one-line intent ("hook the learner")
    character_action: str | None = None     # what the teacher avatar does ("point to leaf")
    animation_type: str | None = None       # template hint ("character_intro", "diagram_walk")
    assets: list[str] | None = None         # named asset references ("student", "plant", "sun")
    on_screen_text: str | None = None       # large text overlay (separate from bullets)
    subtitle: str | None = None             # caption track text, may differ from narration in length
    # — v0.14 C7: page-level provenance for source citations —
    source_pages: list[int] | None = None   # page numbers in the upload this scene draws from


@dataclass
class Lesson:
    title: str
    language_code: str
    language_name: str
    level: str
    scenes: list[Scene]
    quiz: list[dict]


SYSTEM_PROMPT = """You are PadhAI, an AI teacher that turns a single textbook page into a short, engaging video lesson script for Indian students.

You will receive an image of a textbook page plus a target language and difficulty level. Output a JSON lesson plan that the video pipeline can render directly.

Rules:
- Read the page carefully. Identify the chapter title, the key concepts, any equations, diagrams, and worked examples.
- Produce 5-8 scenes. Each scene is a single teaching beat (~30-60 seconds of narration).
- Narration must be in the requested language, spoken naturally — short sentences, no markdown, no LaTeX.
- For each scene also provide 2-4 short bullet points (in the same language) that will appear on screen alongside the narration.
- End with a 3-question quiz (multiple choice, options A/B/C/D, correct answer marked).
- Do NOT copy long verbatim text from the page — explain in your own words. Diagrams and equations may be described.
- Calibrate depth to the chosen level."""


def _image_to_block(path: Path) -> dict:
    media_type, _ = mimetypes.guess_type(path.name)
    if media_type is None:
        media_type = "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def build_user_text(
    language_code: str,
    level: str,
    target_duration_seconds: int | None = None,
    profile_addendum: str | None = None,
    board_hint: str | None = None,
    taxonomy_scope: str | None = None,
) -> str:
    """The user-turn prompt that pairs with the image in the request body.

    `target_duration_seconds` is honoured as a soft constraint — at
    typical narration pace of ~140 words/minute, that's a target word
    budget the model can fit into. `profile_addendum` is the
    PersonalizationProfile.prompt_addendum that carries video_mode,
    user_type, tone, scene_beats, disclaimers, etc.
    `board_hint` is a free-form board/exam key that maps into BOARD_GUIDANCE.
    `taxonomy_scope` is the in-scope syllabus summary from
    exam_taxonomy.taxonomy_scope_for_user — when present the model is
    instructed to stay within the enrolled exam pack."""
    language_name = SUPPORTED_LANGUAGES[language_code]
    parts = [
        f"Target language: {language_name} ({language_code}).",
        f"Difficulty level: {level}. {LEVEL_GUIDANCE[level]}",
    ]
    if board_hint:
        guidance = BOARD_GUIDANCE.get(board_hint)
        if guidance:
            parts.append(f"Board / exam context: {guidance}")
    if taxonomy_scope:
        parts.append(f"Syllabus scope: {taxonomy_scope}")
    if target_duration_seconds:
        word_budget = int(target_duration_seconds * 2.3)  # ~140 wpm
        parts.append(
            f"TARGET DURATION: ~{target_duration_seconds} seconds total. "
            f"Aim for around {word_budget} words of narration across all "
            f"scenes combined. Do NOT exceed this materially."
        )
    if profile_addendum:
        parts.append(profile_addendum)
    parts.append("Generate the lesson plan for the attached page.")
    return "\n\n".join(parts)


def build_schema(level: str) -> dict:
    """The JSON-schema for output_config.format. KG levels get a shorter
    catalogue than middle-school+ to match LEVEL_GUIDANCE['kg']."""
    if level == "kg":
        scene_min, scene_max = 3, 4
        quiz_min, quiz_max = 2, 2
    else:
        scene_min, scene_max = 5, 8
        quiz_min, quiz_max = 3, 3
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "scenes": {
                "type": "array",
                # Anthropic structured output only allows minItems 0 or 1.
                # Target count communicated via system prompt instead.
                "minItems": 1,

                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "narration": {"type": "string"},
                        "bullets": {
                            "type": "array",
                            "minItems": 1,  # Anthropic constraint; ask for 2-4 in prompt

                            "items": {"type": "string"},
                        },
                        "diagram": {
                            # Anthropic structured output rejects enums that
                            # mix null with strings; keep the allow-list in
                            # the description and validate downstream.
                            "type": ["string", "null"],
                            "description": (
                                "Optional diagram template name. Must be one of: "
                                "solar_system, photosynthesis, water_cycle, atom, "
                                "addition_dots. Use null when the scene's content "
                                "does not match a supported template."
                            ),
                        },
                    },
                    "required": ["title", "narration", "bullets", "diagram"],
                    "additionalProperties": False,
                },
            },
            "quiz": {
                "type": "array",
                # Anthropic structured output only allows minItems 0 or 1.
                "minItems": 1,

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
            },
        },
        "required": ["title", "scenes", "quiz"],
        "additionalProperties": False,
    }


def parse_lesson_json(
    data: dict, language_code: str, level: str,
) -> Lesson:
    """Turn the JSON the model returns into a Lesson dataclass. Used by
    both the synchronous and batch paths.

    v0.12: modes like 'explainer', 'parent', 'reel' omit the `quiz`
    field entirely in their schema — `quiz` defaults to an empty list
    when the model didn't return one."""
    return Lesson(
        title=data["title"],
        language_code=language_code,
        language_name=SUPPORTED_LANGUAGES[language_code],
        level=level,
        scenes=[Scene(**s) for s in data["scenes"]],
        quiz=data.get("quiz", []),
    )


def image_to_block(image_path: Path) -> dict:
    """Re-export of the (formerly private) image-block builder so the
    batch path can use it."""
    return _image_to_block(image_path)


RECAP_MODEL = HAIKU_MODEL  # cheap remix from Lesson JSON, ~₹0.20/call
RECAP_SYSTEM = """You write a podcast-style audio recap of a finished lesson.

Rules:
- Open with a warm greeting like a friendly tutor recapping at the end of class.
- 120-160 words total. Read time ~60-80 seconds at gentle pace.
- One paragraph. No bullet points, no markdown, no headings.
- Cover the 3-5 most important takeaways in plain spoken language.
- Use the EXACT language of the lesson (match language_code / language_name).
- End with one encouraging follow-up question the student can think about.
- Never invent facts that aren't in the lesson scenes. If a concept wasn't taught, skip it."""


EXPLAINER_MODEL = HAIKU_MODEL  # topic-to-explanation, ~₹0.30/call
EXPLAINER_SYSTEM = """You generate a short, focused explainer for a single concept.

Output JSON shape (keys exactly as named, all required):
{
  "topic":          short canonical name of the concept (echo back, polished),
  "one_liner":      one sentence (~15-25 words) capturing the heart of it,
  "explanation":    2-3 short paragraphs in plain language, no jargon walls,
  "key_points":     ["3-5 short bullet phrases"],
  "worked_example": one concrete example with the steps shown,
  "common_mistakes":["2-3 short pitfalls students fall into"],
  "analogy":        a single one-sentence everyday-life analogy
}

Rules:
- Write EVERYTHING in the requested language (echo language_code).
- Calibrate vocabulary and depth to the level (kg / eli5 / primary / middle / secondary / neet_jee).
- No markdown formatting in field values. No bullet characters — just plain strings.
- For maths topics, write equations in plain text (e.g. "x^2 + 2x + 1") not LaTeX.
- Stay strictly on-topic. If the topic isn't academic, give a one-line refusal in 'explanation' and empty arrays.
- Be concrete: students need a worked example, not philosophy."""


VOICE_TUTOR_SYSTEM = """You are PadhAI's Voice Tutor — a kind, patient AI teacher in a spoken conversation with a student.

Rules:
- Reply in the SAME language as the student's message. Detect it from the transcript.
- Keep the answer SHORT — 2-4 sentences, ~30-60 spoken words. The student is listening, not reading.
- Speak naturally: contractions, simple words, no markdown, no LaTeX, no bullet points.
- If lesson material is provided, ground your answer in it and mention "as we covered in this lesson" when relevant.
- If the lesson doesn't cover the question, say so briefly and offer what you know from general knowledge.
- For numerical problems, walk through ONE clean step at a time.
- Never start with 'As an AI…'. Just answer like a warm teacher would.
- If the student asks a non-academic question, gently redirect to studies in one sentence."""

LIVE_TUTOR_SYSTEM = """You are a kind, patient AI tutor speaking aloud with a student in a live conversation.

Rules:
- Reply in the SAME language as the student's message. Detect it from the transcript.
- Keep the answer SHORT — 2-4 sentences, ~30-60 spoken words. The student is listening, not reading.
- Speak naturally: contractions, simple words, no markdown, no LaTeX, no bullet points.
- If the question is vague, ask one short clarifying question instead of guessing.
- For numerical problems, walk through ONE clean step at a time, not a wall of math.
- Never start with 'As an AI...'. Just answer like a warm teacher would.
- If the student asks a non-academic question, gently redirect to studies in one sentence."""


FLASHCARD_MODEL = HAIKU_MODEL  # cheap remix — Lesson JSON already has the work
FLASHCARD_SYSTEM = """You convert a structured lesson into spaced-repetition flashcards.

Rules:
- Each card has a concise question or concept name on the front (5-15 words)
  and a clear answer/explanation on the back (1-3 sentences).
- Cover the MOST important concepts from the lesson — quality over quantity.
- Match the lesson's language for both front and back exactly.
- Cards should be self-contained — readable without seeing the lesson.
- Add 1-3 short tags per card (subject area, sub-topic).
- An optional `hint` field can give a memory aid for hard concepts.
- Never repeat the same concept across multiple cards.
- Cards for kindergarten/primary: simpler language, fewer cards.
- Cards for secondary/exam: more rigorous, can reference equations/dates."""


CURRICULUM_MATCH_SYSTEM = """You match a generated lesson against a curriculum catalogue.

You will be given:
  - The student's lesson title + scene summary.
  - A list of candidate curriculum entries, each with id, board, class,
    subject, chapter title, summary, and topic tags.

Return the top 3 entries that BEST match the lesson, ranked by relevance.
For each match include:
  - id: the candidate's id
  - confidence: 0.0 to 1.0
  - reason: one-sentence why it matches (mention specific topic overlap)

If nothing matches well, return an empty list with empty `matches`. Don't
force matches that aren't there — students upload material from many
sources, not all of which align to Indian school syllabi."""


def match_curriculum(
    lesson: Lesson,
    catalogue: list[dict],
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """Return ranked curriculum matches for a lesson.

    Catalogue rows come from padhai/curriculum.py CURRICULUM. We attach
    a stable `id` (board+class+subject+chapter_no) to each so the model
    can reference them by id rather than re-emitting the metadata.

    Uses Haiku 4.5 — single short prompt, ~₹0.20/call. Cached in the
    lesson_curriculum tier; second call returns instantly."""
    import dataclasses

    client = client or anthropic.Anthropic()
    # Filter catalogue to plausible candidates by level alignment +
    # subject keywords from the lesson title. Cuts the prompt size by
    # ~80%, drops cost commensurately.
    title_lower = lesson.title.lower()
    candidates = []
    for c in catalogue:
        cid = f"{c['board']}-c{c['class']}-{c['subject']}-{c['chapter_no']}"
        score = 0
        if c["level"] == lesson.level:
            score += 2
        if c["subject"].lower() in title_lower:
            score += 1
        for t in c.get("topics", []):
            if t.lower() in title_lower:
                score += 2
        candidates.append((score, {**c, "id": cid}))
    # Top 30 by score so Haiku doesn't have to read the entire catalogue
    candidates.sort(key=lambda x: x[0], reverse=True)
    short = [c for _, c in candidates[:30]]

    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",

                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["matches"],
        "additionalProperties": False,
    }

    lesson_summary = (
        f"Title: {lesson.title}\n"
        f"Level: {lesson.level}\n"
        f"Scenes: {', '.join(s.title for s in lesson.scenes)}\n"
        f"Bullets: {' | '.join(b for s in lesson.scenes for b in s.bullets)}"
    )
    catalogue_text = json.dumps(short, ensure_ascii=False)

    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1500,
        system=CURRICULUM_MATCH_SYSTEM,
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": "low",
        },
        messages=[{
            "role": "user",
            "content": (
                f"LESSON:\n{lesson_summary}\n\nCATALOGUE (top candidates):\n"
                f"{catalogue_text}\n\nRank the top 3 matches by relevance."
            ),
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    matches = json.loads(text)["matches"]

    # Hydrate matches with the full catalogue row so the UI doesn't have
    # to round-trip back to look them up.
    by_id = {c["id"]: c for c in short}
    return [
        {**m, **{k: v for k, v in by_id.get(m["id"], {}).items() if k != "id"}}
        for m in matches
    ]


LEARNING_PATH_SYSTEM = """You generate a personalised study plan for an Indian student.

You will be given:
  - Target class (Class 6-12 or college-prep)
  - Subjects the student wants to study
  - Daily time budget (typically 20-60 minutes)
  - Number of weeks available before the target date
  - Optional weak topics the student flagged
  - The curriculum catalogue entries for the chosen class/subjects
  - The student's existing library (already-generated lessons they can re-watch)

Generate a structured study plan:
  - One theme per week (the headline concept that ties the week together)
  - 5-6 daily tasks per week, NOT 7 — leave one day as buffer/rest
  - Task types: watch (a video lesson), quiz (test recall), study (read a chapter),
    practice (work problems), revision (review earlier material)
  - Mix new content with revision — by week 2+, ~25% of tasks should revisit
    earlier weeks
  - Reference chapters by their `id` from the catalogue when relevant; null
    when the task is general (e.g. "watch your uploaded photosynthesis lesson")
  - Each task: realistic time estimate (default 15 min for watch, 10 for quiz,
    20 for study, 25 for practice, 10 for revision)
  - Total per week must roughly fit the daily budget × 5 days
  - If weak topics are provided, weight Week 1-2 more heavily toward them

Don't fabricate chapter ids. Only use ids from the catalogue you're given.
Don't over-plan — 4 weeks is the sweet spot for most students; longer plans
get ignored. Cap at 8 weeks."""


def generate_learning_path(
    student_class: int,
    subjects: list[str],
    weeks: int,
    daily_minutes: int = 30,
    focus_topics: list[str] | None = None,
    library_lessons: list[dict] | None = None,
    catalogue: list[dict] | None = None,
    client: anthropic.Anthropic | None = None,
) -> dict:
    """Build a structured multi-week study plan via Claude Opus 4.7
    (this is a real planning task — Haiku gets it wrong).

    Library lessons + catalogue entries are given to the model so it
    can reference real content. ~₹4-6/call; cached aggressively in
    the learning_paths/ tier so the same inputs return instantly."""
    client = client or anthropic.Anthropic()

    weeks = max(2, min(8, weeks))
    catalogue = catalogue or []
    library_lessons = library_lessons or []
    focus_topics = focus_topics or []

    # Filter catalogue to chosen class + neighbouring classes (one above
    # and below for prerequisite/extension topics)
    cls_range = {student_class - 1, student_class, student_class + 1}
    short_cat = [
        {
            "id": f"{c['board']}-c{c['class']}-{c['subject']}-{c['chapter_no']}",
            "class": c["class"], "subject": c["subject"],
            "chapter_title": c["chapter_title"],
            "topics": c.get("topics", []),
        }
        for c in catalogue
        if c["class"] in cls_range and c["subject"] in subjects
    ]

    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "total_weeks": {"type": "integer"},
            "weeks": {
                "type": "array",
                # Anthropic structured output only allows minItems 0 or 1.
                # Target count enforced via prompt instead.
                "minItems": 1,

                "items": {
                    "type": "object",
                    "properties": {
                        "week_number": {"type": "integer"},
                        "theme": {"type": "string"},
                        "daily_tasks": {
                            "type": "array",
                            "minItems": 1,  # Anthropic constraint; ask for 4-6 in prompt

                            "items": {
                                "type": "object",
                                "properties": {
                                    "day": {
                                        "type": "string",
                                        "enum": ["Mon","Tue","Wed","Thu","Fri","Sat"],
                                    },
                                    "type": {
                                        "type": "string",
                                        "enum": ["watch","quiz","study","practice","revision"],
                                    },
                                    "topic": {"type": "string"},
                                    "estimated_minutes": {"type": "integer"},
                                    "chapter_ref": {"type": ["string","null"]},
                                    "lesson_id": {"type": ["string","null"]},
                                },
                                "required": ["day","type","topic","estimated_minutes",
                                             "chapter_ref","lesson_id"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["week_number","theme","daily_tasks"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title","summary","total_weeks","weeks"],
        "additionalProperties": False,
    }

    user_msg = (
        f"Student: Class {student_class}, studying {', '.join(subjects)}.\n"
        f"Time budget: {daily_minutes} minutes/day.\n"
        f"Plan length: {weeks} weeks.\n"
        f"Weak topics to focus on: {', '.join(focus_topics) if focus_topics else 'none specified'}\n\n"
        f"Curriculum catalogue ({len(short_cat)} chapters available for this class):\n"
        f"{json.dumps(short_cat, ensure_ascii=False)}\n\n"
        f"Student's library ({len(library_lessons)} lessons they've generated):\n"
        f"{json.dumps(library_lessons, ensure_ascii=False)}\n\n"
        f"Build the {weeks}-week study plan now."
    )

    response = client.messages.create(
        model=MODEL,  # Opus 4.7 — this is real planning
        max_tokens=8000,
        system=LEARNING_PATH_SYSTEM,
        thinking={"type": "adaptive"},
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": "medium",
        },
        messages=[{"role": "user", "content": user_msg}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def generate_flashcards(
    lesson: Lesson,
    count: int = 8,
    client: anthropic.Anthropic | None = None,
) -> list[dict]:
    """Convert a Lesson into spaced-repetition flashcards.

    Returns a list of dicts: [{"front", "back", "hint"?, "tags"}].

    Uses Claude Haiku 4.5 because the structured Lesson JSON is already
    in hand — this is a cheap remix task, not heavy reasoning. Typical
    cost per call: ~₹0.30."""
    import dataclasses

    client = client or anthropic.Anthropic()

    # Adjust card count for younger learners
    if lesson.level in ("kg", "primary"):
        count = min(count, 5)
    elif lesson.level in ("neet_jee",):
        count = min(count + 4, 14)

    schema = {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                # Anthropic structured output only allows minItems 0 or 1.
                "minItems": 1,

                "items": {
                    "type": "object",
                    "properties": {
                        "front": {
                            "type": "string",
                            "description": "concise question or concept name (5-15 words)",
                        },
                        "back": {
                            "type": "string",
                            "description": "answer or explanation (1-3 sentences)",
                        },
                        "hint": {
                            "type": ["string", "null"],
                            "description": "optional memory aid; null when not needed",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,

                        },
                    },
                    "required": ["front", "back", "hint", "tags"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["cards"],
        "additionalProperties": False,
    }

    scenes_json = json.dumps(
        [dataclasses.asdict(s) for s in lesson.scenes], ensure_ascii=False,
    )
    response = client.messages.create(
        model=FLASHCARD_MODEL,
        max_tokens=4000,
        system=FLASHCARD_SYSTEM,
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": "low",
        },
        messages=[{
            "role": "user",
            "content": (
                f"Lesson title: {lesson.title}\n"
                f"Language: {lesson.language_name} ({lesson.language_code})\n"
                f"Difficulty level: {lesson.level}\n\n"
                f"Lesson scenes:\n{scenes_json}\n\n"
                f"Generate {count} high-quality flashcards covering the key "
                "concepts from this lesson."
            ),
        }],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["cards"]


def generate_recap(
    lesson: Lesson,
    client: anthropic.Anthropic | None = None,
) -> str:
    """One-paragraph spoken recap of a Lesson, ready to feed to TTS.

    Returns plain text in the lesson's language. Costs ~₹0.20 (Haiku 4.5
    on the Lesson JSON we already paid for). Cached by lesson_id in
    Cache.put_recap_text so the second listener is free."""
    import dataclasses

    client = client or anthropic.Anthropic()

    scenes_json = json.dumps(
        [dataclasses.asdict(s) for s in lesson.scenes], ensure_ascii=False,
    )
    response = client.messages.create(
        model=RECAP_MODEL,
        max_tokens=600,
        system=RECAP_SYSTEM,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": (
                f"Lesson title: {lesson.title}\n"
                f"Language: {lesson.language_name} ({lesson.language_code})\n"
                f"Level: {lesson.level}\n\n"
                f"Scenes:\n{scenes_json}\n\n"
                "Write the recap now."
            ),
        }],
    )
    return next(b.text for b in response.content if b.type == "text").strip()


def generate_explainer(
    topic: str,
    language_code: str = "en",
    level: str = "middle",
    client: anthropic.Anthropic | None = None,
    user_id: str | None = None,
    user_tier: str | None = None,
    syllabus_scope: str | None = None,
    board_hint: str | None = None,
) -> dict:
    """Topic-to-explanation: type a concept name, get a structured mini-lesson.

    Free-form input, structured JSON output. Costs ~₹0.30 (Haiku 4.5).
    Caching is by (topic, language, level) — the same 'photosynthesis'
    request from 1000 students hits the cache 999 times.

    `user_id` + `user_tier` (optional) gate the call behind
    llm_obs.check_daily_cap so a runaway loop can't burn the budget.

    prod-212 — `syllabus_scope` (from exam_taxonomy.taxonomy_scope_for_user)
    and `board_hint` ground the explanation in the student's actual
    board/grade/chapter so a bare topic like "Trigonometry" is explained at
    the right depth for their curriculum instead of drifting to a generic
    (often higher-level) version. Both optional — omitted for anonymous /
    no-enrollment users (generic explainer, unchanged behaviour)."""
    if language_code not in SUPPORTED_LANGUAGES:
        raise ValueError(f"language {language_code!r} not supported")
    if level not in LEVEL_GUIDANCE:
        raise ValueError(f"level {level!r} not supported")

    if user_id:
        from . import llm_obs as _llm_obs
        try:
            _llm_obs.check_daily_cap(
                user_id=user_id, subscription_tier=user_tier,
            )
        except _llm_obs.BudgetExceeded as e:
            raise RuntimeError(
                f"daily_ai_budget_{e.reason}: spent={e.spent_today_paise}p "
                f"cap={e.cap_paise}p"
            ) from e

    schema = {
        "type": "object",
        "properties": {
            "topic": {"type": "string"},
            "one_liner": {"type": "string"},
            "explanation": {"type": "string"},
            "key_points": {
                # Anthropic constraint: minItems must be 0 or 1.
                "type": "array", "minItems": 1,
                "items": {"type": "string"},
            },
            "worked_example": {"type": "string"},
            "common_mistakes": {
                "type": "array", "minItems": 0,
                "items": {"type": "string"},
            },
            "analogy": {"type": "string"},
        },
        "required": [
            "topic", "one_liner", "explanation", "key_points",
            "worked_example", "common_mistakes", "analogy",
        ],
        "additionalProperties": False,
    }
    # Routed through llm_call.call_claude so the Haiku cost lands on
    # the admin dashboard. Cap pre-flight handled upstream.
    from . import llm_call
    call = llm_call.call_claude(
        module="explainer",
        prompt_version="v1",
        model=EXPLAINER_MODEL,
        user_id=user_id,
        subscription_tier=user_tier,
        enforce_cap=False,
        client=client,
        max_tokens=2000,
        system=EXPLAINER_SYSTEM,
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": "low",
        },
        messages=[{
            "role": "user",
            "content": (
                f"Topic: {topic}\n"
                f"Language: {SUPPORTED_LANGUAGES[language_code]} ({language_code})\n"
                f"Level: {level} — {LEVEL_GUIDANCE[level]}\n"
                + (
                    f"Board/exam context: {board_hint}. Explain this topic at "
                    f"the depth and framing this board expects at this level.\n"
                    if board_hint else ""
                )
                + (
                    f"Curriculum scope: {syllabus_scope}\n"
                    "Keep the explanation within this syllabus — do not drift to "
                    "a more advanced version of the topic than this scope covers.\n"
                    if syllabus_scope else ""
                )
                + "\nGenerate the explainer JSON now."
            ),
        }],
    )
    text = call.text
    return json.loads(text)


def live_tutor_reply(
    transcript: str,
    history: list[dict] | None = None,
    client: anthropic.Anthropic | None = None,
) -> str:
    """Short conversational reply for the Live Lecture loop.

    `transcript` is what the student just said (via browser SpeechRecognition).
    `history` is the running conversation: list of {role, content} dicts.
    Returns 2-4 sentences in the student's detected language."""
    client = client or anthropic.Anthropic()
    messages = list(history or [])
    messages.append({"role": "user", "content": transcript})
    response = client.messages.create(
        model=FLASHCARD_MODEL,  # Haiku 4.5 — fast, cheap, conversational
        max_tokens=400,
        system=LIVE_TUTOR_SYSTEM,
        messages=messages,
    )
    return next(b.text for b in response.content if b.type == "text").strip()


def voice_tutor_reply(
    transcript: str,
    history: list[dict] | None = None,
    lesson_json: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> str:
    """Lesson-grounded voice reply for the Voice Tutor module.

    If `lesson_json` is provided the model answers in the context of that
    lesson (same grounding as the text Doubt Chat). Without a lesson it
    falls back to LIVE_TUTOR_SYSTEM general tutoring.
    Returns 2-4 sentences in the student's detected language."""
    client = client or anthropic.Anthropic()
    system = VOICE_TUTOR_SYSTEM
    if lesson_json:
        system = VOICE_TUTOR_SYSTEM + "\n\nLESSON MATERIAL (answer from this first):\n" + lesson_json
    messages = list(history or [])
    messages.append({"role": "user", "content": transcript})
    response = client.messages.create(
        model=FLASHCARD_MODEL,  # Haiku 4.5 — fast, cheap, conversational
        max_tokens=400,
        system=system,
        messages=messages,
    )
    return next(b.text for b in response.content if b.type == "text").strip()


# Topic → animated-diagram template. Matched against the lowercased topic
# string before falling back to text-only whiteboard slides. Order matters:
# more specific keywords come first so "solar system" wins over "solar".
DIAGRAM_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("photosynth", "chloroplast", "chlorophyll"), "photosynthesis"),
    (("solar system", "planet", "orbit"), "solar_system"),
    (("water cycle", "evaporation", "precipitation", "condensation"),
     "water_cycle"),
    (("atom", "electron", "proton", "neutron", "nucleus"), "atom"),
    (("addition", "plus", "sum", "add ", "adding"), "addition_dots"),
    # prod-211 — primary/middle-school maths visuals. Multi-word keys
    # ("times table") guard against false hits ("sometimes"). "multiply" and
    # "multiplication" are BOTH listed (one is not a substring of the other).
    (("multiplication", "multiply", "multiplying", "times table"),
     "multiplication_array"),
    (("division", "divide", "dividing", "quotient", "divisor"),
     "division_groups"),
    (("subtraction", "subtract", "minus", "take away"), "subtraction_dots"),
    (("fraction", "numerator", "denominator"), "fraction_circle"),
    # prod-213 — secondary-school geometry / coordinate maths. "pythagoras"
    # is listed BEFORE the generic triangle so "pythagoras theorem" (no
    # "triangle" token) resolves to the labelled a²+b²=c² figure, while a bare
    # "triangle" / "area of a triangle" gets the base-height area figure.
    (("pythagoras", "pythagorean", "hypotenuse"), "pythagoras"),
    (("area of a triangle", "area of triangle", "triangle"), "triangle_area"),
    (("number line", "integers", "negative numbers"), "number_line"),
    (("linear equation", "straight line", "slope", "coordinate geometry",
      "cartesian"), "linear_graph"),
]


def pick_diagram(topic: str) -> str | None:
    """Return the diagram-template name that best matches the topic, or
    None when none of our drawable concepts apply. Pure string-match —
    no LLM call needed for the common cases."""
    t = topic.lower()
    for keywords, name in DIAGRAM_KEYWORDS:
        if any(k in t for k in keywords):
            return name
    return None


# Localized scene-title templates. Used by explainer_to_lesson() to keep
# the headings in the same language as the narration. Falls back to
# English for languages we haven't translated yet.
EXPLAINER_SCENE_TITLES: dict[str, dict[str, str]] = {
    "en": {
        "intro":   "What is {topic}?",
        "explain": "Let me explain",
        "example": "Worked example",
        "analogy": "Think of it like…",
        "mistakes": "Common mistakes",
        "mistakes_intro": "Before we close, here are mistakes many students make. ",
    },
    "hi": {
        "intro":   "{topic} क्या है?",
        "explain": "आइए समझते हैं",
        "example": "उदाहरण के साथ",
        "analogy": "इसे ऐसे समझें…",
        "mistakes": "आम गलतियाँ",
        "mistakes_intro": "अंत में, कुछ गलतियाँ जो छात्र अक्सर करते हैं — ",
    },
    "mr": {
        "intro":   "{topic} म्हणजे काय?",
        "explain": "मला समजावून सांगा",
        "example": "उदाहरणासह",
        "analogy": "असे विचार करा…",
        "mistakes": "सामान्य चुका",
        "mistakes_intro": "शेवटी, विद्यार्थी अनेकदा करत असलेल्या काही चुका — ",
    },
    "ta": {
        "intro":   "{topic} என்றால் என்ன?",
        "explain": "விளக்கம்",
        "example": "உதாரணத்துடன்",
        "analogy": "இதை இப்படி நினைத்துப் பாருங்கள்…",
        "mistakes": "பொதுவான தவறுகள்",
        "mistakes_intro": "முடிவில், மாணவர்கள் அடிக்கடி செய்யும் சில தவறுகள் — ",
    },
}


def explainer_to_lesson(
    explainer: dict, language_code: str, level: str,
) -> Lesson:
    """Convert an Explainer JSON (topic + structured fields) into a Lesson
    dataclass that the existing render pipeline can turn into a cartoon
    video. No Claude vision call needed — pure shape conversion.

    When the topic matches one of the known animated diagrams (solar
    system, photosynthesis, water cycle, atom, addition), the worked-
    example and 'let me explain' scenes get that diagram attached so the
    render produces an illustrated concept board (Khan Academy style)
    instead of a plain bulleted whiteboard."""
    topic = explainer.get("topic") or "Explainer"
    one_liner = explainer.get("one_liner", "")
    explanation = explainer.get("explanation", "")
    key_points = explainer.get("key_points", []) or []
    worked_example = explainer.get("worked_example", "")
    analogy = explainer.get("analogy", "")
    common_mistakes = explainer.get("common_mistakes", []) or []
    diagram_name = pick_diagram(topic)
    titles = EXPLAINER_SCENE_TITLES.get(
        language_code, EXPLAINER_SCENE_TITLES["en"],
    )

    def short_bullets(items: list[str], limit: int = 4) -> list[str]:
        cleaned = [str(b).strip() for b in items if str(b).strip()]
        return cleaned[:limit]

    def truncate_bullet(b: str, n: int = 80) -> str:
        b = str(b).strip()
        return (b[: n - 1] + "…") if len(b) > n else b

    scenes: list[Scene] = []

    intro_bullets = [truncate_bullet(b) for b in short_bullets(key_points, 3)]
    if not intro_bullets:
        intro_bullets = [topic]
    scenes.append(Scene(
        title=titles["intro"].format(topic=topic),
        narration=one_liner or f"{topic}",
        bullets=intro_bullets,
        diagram=diagram_name,
    ))

    if explanation:
        explain_bullets = [truncate_bullet(b) for b in short_bullets(key_points, 4)]
        if not explain_bullets:
            explain_bullets = [topic]
        scenes.append(Scene(
            title=titles["explain"],
            narration=explanation,
            bullets=explain_bullets,
            diagram=diagram_name,
        ))

    if worked_example:
        ex_lines = [ln.strip() for ln in worked_example.splitlines() if ln.strip()]
        ex_bullets = [truncate_bullet(ln) for ln in ex_lines[:4]] or [topic]
        scenes.append(Scene(
            title=titles["example"],
            narration=worked_example,
            bullets=ex_bullets,
            diagram=diagram_name,
        ))

    if analogy:
        scenes.append(Scene(
            title=titles["analogy"],
            narration=analogy,
            bullets=[truncate_bullet(analogy, 90)],
        ))

    if common_mistakes:
        mistake_bullets = [truncate_bullet(m) for m in short_bullets(common_mistakes, 3)]
        scenes.append(Scene(
            title=titles["mistakes"],
            narration=(
                titles["mistakes_intro"] +
                " ".join(common_mistakes)
            ),
            bullets=mistake_bullets,
        ))

    return Lesson(
        title=topic,
        language_code=language_code,
        language_name=SUPPORTED_LANGUAGES.get(language_code, language_code),
        level=level,
        scenes=scenes,
        quiz=[],
    )


def generate_lesson(
    image_path: Path,
    language_code: str,
    level: str,
    client: anthropic.Anthropic | None = None,
    cache: Cache | None = None,
    target_duration_seconds: int | None = None,
    profile_addendum: str | None = None,
    video_mode: str = "teaching",
    board_hint: str | None = None,
    user_id: str | None = None,
    source_upload_id: str | None = None,
    source_page_number: int | None = None,
    user_tier: str | None = None,
) -> Lesson:
    """Generate a video lesson from a textbook-page image.

    v0.12 (C1): the `video_mode` arg now dispatches to a mode-specific
    SYSTEM prompt + JSON schema. So `mode='reel'` returns a 3-scene
    punchy short, `mode='parent'` returns a 3-scene "what your child
    must understand" walk, etc. — not just the teaching-default
    5-scene structure with a different prompt addendum.
    """
    from . import mode_prompts as _mp

    if language_code not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"language {language_code!r} not supported. choose from: {sorted(SUPPORTED_LANGUAGES)}"
        )
    if level not in LEVEL_GUIDANCE:
        raise ValueError(
            f"level {level!r} not supported. choose from: {sorted(LEVEL_GUIDANCE)}"
        )

    image_bytes = image_path.read_bytes()

    # Cache key includes board_hint so CBSE and NEET lessons over the same
    # page get separate cache entries and don't collide.
    profile_sig = (profile_addendum or "") + f"|mode={video_mode}|board={board_hint or ''}"
    if cache is not None and not profile_addendum and video_mode == "teaching" and not board_hint:
        # Old path — pure-teaching, no profile, no board — falls back to legacy
        # cache key so existing cached lessons stay reusable.
        hit = cache.get_lesson(image_bytes, language_code, level, MODEL)
        if hit is not None:
            return hit

    # Daily cost cap — runs AFTER cache check so cache hits still serve
    # without burning the user's daily budget (a hit doesn't cost
    # anything new). Misses go through the cap; a hard refusal raises
    # so the worker can mark the job failed cleanly.
    if user_id:
        from . import llm_obs as _llm_obs
        try:
            _llm_obs.check_daily_cap(
                user_id=user_id, subscription_tier=user_tier,
            )
        except _llm_obs.BudgetExceeded as e:
            raise RuntimeError(
                f"daily_ai_budget_{e.reason}: spent={e.spent_today_paise}p "
                f"cap={e.cap_paise}p"
            ) from e

    client = client or anthropic.Anthropic()

    taxonomy_scope = None
    if user_id:
        try:
            from . import exam_taxonomy as _et
            scope = _et.taxonomy_scope_for_user(user_id)
            if scope:
                taxonomy_scope = scope.get("scope_summary")
                if not board_hint and scope.get("board_hint"):
                    board_hint = scope["board_hint"]
        except Exception as e:
            print(f"[pedagogy] taxonomy scope non-fatal: {e}")

    user_text = build_user_text(
        language_code, level,
        target_duration_seconds=target_duration_seconds,
        profile_addendum=profile_addendum,
        board_hint=board_hint,
        taxonomy_scope=taxonomy_scope,
    )
    # v0.12 C1: per-mode prompts + schemas
    system_prompt = _mp.mode_system_prompt(video_mode)
    schema = _mp.mode_schema(video_mode, level)

    # Routed through llm_call.call_claude so the cost lands on the
    # admin dashboard. Before this migration, generate_lesson was the
    # most-expensive Claude call (Opus + adaptive thinking + JSON
    # schema) AND the only one that didn't call llm_obs.record_call —
    # every lesson render was silently uncosted.
    # cap pre-flight runs upstream (caller passes user_tier from web).
    from . import llm_call
    call = llm_call.call_claude(
        module="lesson",
        prompt_version=f"v3-{video_mode}",
        model=MODEL,
        user_id=user_id,
        subscription_tier=user_tier,
        enforce_cap=False,
        client=client,
        max_tokens=8000,
        system=system_prompt,
        thinking={"type": "adaptive"},
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[
            {
                "role": "user",
                "content": [
                    _image_to_block(image_path),
                    {"type": "text", "text": user_text},
                ],
            }
        ],
    )

    data = json.loads(call.text)
    lesson = parse_lesson_json(data, language_code, level)

    if cache is not None:
        cache.put_lesson(image_bytes, language_code, level, MODEL, lesson)

    if user_id:
        _record_lesson_provenance(
            lesson=lesson, user_id=user_id,
            source_upload_id=source_upload_id,
            source_page_number=source_page_number,
            board_hint=board_hint, level=level,
        )

    return lesson


def _record_lesson_provenance(
    *,
    lesson: Lesson,
    user_id: str,
    source_upload_id: str | None,
    source_page_number: int | None,
    board_hint: str | None,
    level: str,
) -> None:
    """Persist a citations.ai_answer_provenance row for the lesson so the
    trust dashboard can audit grounding rate. Best-effort: failures here
    must never bubble up and break the lesson render."""
    try:
        from . import citations as _cit
        narration = " | ".join(s.narration for s in lesson.scenes)
        answer_text = (lesson.title + "\n\n" + narration)[:32000]
        question_text = (
            f"Generate {level} lesson"
            + (f" for {board_hint} curriculum" if board_hint else "")
            + f" in {lesson.language_name}"
        )
        cites: list[dict] = []
        if source_upload_id:
            cites.append({
                "source_kind": "upload",
                "source_id": source_upload_id,
                "page_number": source_page_number,
                "section": lesson.title,
                "citation_text": lesson.title[:2000] or "uploaded page",
                "relevance": 1.0,
            })
        _cit.record_answer(
            surface="lesson", user_id=user_id,
            question_text=question_text,
            answer_text=answer_text,
            citations=cites or None,
            answer_mode="general",
        )
    except Exception as e:
        print(f"[pedagogy] lesson provenance non-fatal: {e}")
