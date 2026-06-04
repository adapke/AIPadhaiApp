"""Personalization Engine — single source of truth for adaptive video generation.

The PRD (§6) requires that the same uploaded material produce meaningfully
different videos depending on user choices: a Class 5 student in Hindi
gets a kindergarten story; a NEET aspirant in English gets formula
derivations; a parent in Marathi gets a one-paragraph weak-topic
explanation. Without a central profile this logic would scatter across
the script generator, render pipeline, TTS routing, and quiz module.

This module is the brain. Every downstream service (`generate_lesson`,
`render_lesson`, `tts.get_provider`, `generate_quiz`) reads from a
`PersonalizationProfile`. Add a new mode? Add an entry to
`VIDEO_MODE_TEMPLATES`. Add a new user type? Add to `USER_TYPE_TONE`.
Nothing else changes.

Design choices:
- Frozen dataclass — profiles are immutable after build, so the pipeline
  can hash them as cache keys without worrying about mutation.
- Pure function builder — `build_profile()` takes raw user inputs and
  returns a fully-validated profile. No side effects, no LLM calls.
- One profile, many languages — `language_code` is the only field that
  changes between same-content multi-language renders, so the rest of
  the profile becomes the cache key for translation/render reuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------- enums (kept as Literals for type-checker happiness) ----------

VideoMode = Literal[
    "teaching",       # §4.1 — chapter/topic explanation, quiz at end
    "explainer",      # §4.2 — concept, product, scheme; hook→explain→CTA
    "revision",       # §4.3 — exam prep; important points + recap quiz
    "kids",           # §4.4 — kindergarten/primary; story + cartoon
    "teacher",        # §4.5 — classroom-ready; lesson plan + homework
    "parent",         # §4.6 — what the child must know + how to help
    "training",       # §4.7 — workplace SOP; process steps + checklist
    "awareness",      # §4.8 — public health/finance/policy explainer
    "reel",           # §4.9 — 30-60s vertical short, one key idea
]

UserType = Literal[
    "student", "teacher", "parent", "professor",
    "school_admin", "coaching", "professional", "general",
]

Tone = Literal[
    "friendly", "fun", "serious", "professional",
    "storytelling", "cartoon", "teacher_like", "professor_like",
    "exam_focused", "parent_friendly", "corporate",
]

OutputFormat = Literal["16:9", "9:16", "1:1"]
RenderTier = Literal["m1", "m2", "m3", "m4"]


# ---------- domain detection ----------

# Topics in these areas need legal disclaimers in the rendered video
# (§18.4-18.6 of the PRD). Matched case-insensitively against the topic
# string. Conservative match — false positives just add a disclaimer
# (harmless); false negatives mean missing a required warning (bad).
SENSITIVE_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "medical": (
        "medicine", "medication", "drug", "diagnosis", "symptom",
        "treatment", "disease", "cancer", "diabetes", "covid", "vaccine",
        "surgery", "therapy", "doctor", "patient", "health condition",
        "mental health", "depression", "anxiety", "prescription",
    ),
    "finance": (
        "invest", "stock", "mutual fund", "ipo", "crypto", "bitcoin",
        "loan", "mortgage", "tax saving", "tax planning", "insurance",
        "retirement", "portfolio", "trading", "options trading",
    ),
    "legal": (
        "lawsuit", "legal advice", "contract law", "litigation",
        "criminal case", "constitutional", "court case", "divorce law",
        "property dispute", "fir ", "ipc ", "crpc",
    ),
    "policy": (
        "government scheme", "yojana", "subsidy", "ration card",
        "aadhaar enrol", "voter registration", "passport applic",
        "visa applic", "rti ", "election",
    ),
}


def detect_sensitive_domain(topic: str, scene_text: str = "") -> str | None:
    """Return the sensitive-domain name if the topic/content matches one,
    else None. Used to decide whether to append a disclaimer scene."""
    combined = (topic + " " + scene_text).lower()
    for domain, keywords in SENSITIVE_DOMAIN_KEYWORDS.items():
        if any(k in combined for k in keywords):
            return domain
    return None


DISCLAIMERS: dict[str, str] = {
    "medical": (
        "Disclaimer: This video is for general awareness only. It is "
        "not medical advice. Please consult a qualified doctor for "
        "diagnosis or treatment of any health condition."
    ),
    "finance": (
        "Disclaimer: This video is for general financial education only. "
        "It is not personalised investment advice. Please consult a "
        "SEBI-registered financial advisor before making investment "
        "decisions."
    ),
    "legal": (
        "Disclaimer: This video is for general legal awareness only. "
        "It is not legal advice. Please consult a qualified lawyer for "
        "advice on your specific situation."
    ),
    "policy": (
        "Note: Government scheme rules can change. Please verify the "
        "latest eligibility, documents, and deadlines on the official "
        "government website before applying."
    ),
}


# ---------- video-mode templates ----------

@dataclass(frozen=True)
class VideoModeTemplate:
    """How a video of this mode is structured. Used by the script
    generator to choose scene beats and by the renderer to choose
    pacing knobs.

    `scene_beats` is the ordered list of scene goals the generator
    should produce. The model is told to fill in narration/bullets for
    each beat in order. Length of this list = number of scenes.
    """
    name: str
    description: str
    scene_beats: tuple[str, ...]
    needs_quiz: bool
    needs_cta: bool
    default_duration_seconds: int
    default_render_mode: Literal["animated", "reveal", "static"]
    narration_density: Literal["light", "medium", "heavy"]


VIDEO_MODE_TEMPLATES: dict[VideoMode, VideoModeTemplate] = {
    "teaching": VideoModeTemplate(
        name="Teaching Video",
        description="Chapter or topic explained step by step.",
        scene_beats=(
            "introduce the topic with a relatable hook",
            "explain the core concept clearly",
            "show a worked example or diagram",
            "give a real-world application",
            "summarise the takeaway",
        ),
        needs_quiz=True, needs_cta=False,
        default_duration_seconds=300,
        default_render_mode="animated",
        narration_density="medium",
    ),
    "explainer": VideoModeTemplate(
        name="Explainer Video",
        description="Concept, product, or process explained simply.",
        scene_beats=(
            "open with a hook the audience cares about",
            "frame the problem this idea solves",
            "explain in plain language",
            "show one concrete example or analogy",
            "summarise and end with a clear call to action",
        ),
        needs_quiz=False, needs_cta=True,
        default_duration_seconds=120,
        default_render_mode="animated",
        narration_density="medium",
    ),
    "revision": VideoModeTemplate(
        name="Exam Revision",
        description="Fast recap for exam prep — formulas, common traps, quick practice.",
        scene_beats=(
            "list the most important points to remember",
            "recap key formulas or definitions",
            "flag the common mistakes students make",
            "show one or two expected exam questions",
            "end with a 60-second practice quiz",
        ),
        needs_quiz=True, needs_cta=False,
        default_duration_seconds=240,
        default_render_mode="animated",
        narration_density="heavy",
    ),
    "kids": VideoModeTemplate(
        name="Kids Animated Story",
        description="3-6 / 7-10 year olds; story + cartoon + small quiz.",
        scene_beats=(
            "introduce a friendly character",
            "ask a curious question the child relates to",
            "show the answer with a simple visual",
            "celebrate the discovery",
        ),
        needs_quiz=True, needs_cta=False,
        default_duration_seconds=90,
        default_render_mode="animated",
        narration_density="light",
    ),
    "teacher": VideoModeTemplate(
        name="Teacher Classroom Video",
        description="Classroom-ready: lesson objectives, board-style, homework, quiz.",
        scene_beats=(
            "state the lesson objective in one sentence",
            "explain the concept like writing on a board",
            "demonstrate one example for the class",
            "give a discussion question",
            "assign homework and end with a wrap-up",
        ),
        needs_quiz=True, needs_cta=False,
        default_duration_seconds=420,
        default_render_mode="animated",
        narration_density="medium",
    ),
    "parent": VideoModeTemplate(
        name="Parent Explanation",
        description="What the child must understand, how the parent can help.",
        scene_beats=(
            "explain what your child is learning in one minute",
            "highlight the one concept they may struggle with",
            "suggest two simple things you can do at home",
        ),
        needs_quiz=False, needs_cta=False,
        default_duration_seconds=120,
        default_render_mode="animated",
        narration_density="light",
    ),
    "training": VideoModeTemplate(
        name="Professional Training",
        description="Workplace SOP / process explainer with checklist.",
        scene_beats=(
            "state the business problem this process solves",
            "walk through the steps in order",
            "highlight do's and don'ts",
            "end with a 5-item compliance checklist",
        ),
        needs_quiz=False, needs_cta=True,
        default_duration_seconds=180,
        default_render_mode="animated",
        narration_density="medium",
    ),
    "awareness": VideoModeTemplate(
        name="Public Awareness",
        description="Safety / civic / health / finance public explainer.",
        scene_beats=(
            "open with one fact that wakes the audience up",
            "explain the issue in everyday language",
            "show what people should do",
            "give a single clear call to action",
        ),
        needs_quiz=False, needs_cta=True,
        default_duration_seconds=90,
        default_render_mode="animated",
        narration_density="light",
    ),
    "reel": VideoModeTemplate(
        name="Concept Reel",
        description="30-60s vertical short with one key idea + hook.",
        scene_beats=(
            "hook in 5 seconds",
            "deliver the one key idea",
            "end with a punchy takeaway",
        ),
        needs_quiz=False, needs_cta=False,
        default_duration_seconds=45,
        default_render_mode="animated",
        narration_density="heavy",
    ),
}


# ---------- pedagogy profile (vocabulary, examples, pace) ----------

# PRD §6.2 maps age → explanation style. We collapse age to a small set
# of pedagogy profiles so the script generator gets one stable input
# instead of trying to interpret age + grade independently.
PEDAGOGY_BY_AGE: list[tuple[range, str, str]] = [
    # (age range, profile_name, prompt addendum)
    (range(3, 7),   "early_childhood",
        "Use simple story format. Vocabulary of a 4-year-old. Use animals, "
        "toys, family. Repeat key words. 2-3 sentences per scene."),
    (range(7, 11),  "primary",
        "Use cartoon-style examples and games. Class 3-5 vocabulary. "
        "Concrete examples from daily life. Avoid technical jargon."),
    (range(11, 14), "middle_school",
        "School-level teaching with light diagrams. Introduce technical "
        "terms with one-line definitions."),
    (range(14, 17), "secondary",
        "Concept depth + exam framing. Reference board-exam style "
        "questions. Use technical vocabulary correctly."),
    (range(17, 19), "board_competitive",
        "Board / JEE / NEET rigour. Show derivations, common traps, "
        "expected exam questions."),
    (range(19, 26), "college",
        "College-level explanation. Practical, structured, can use "
        "research-style framing."),
]


def pedagogy_for_age(age: int) -> tuple[str, str]:
    """Returns (profile_name, prompt_addendum) for a given age."""
    for rng, name, addendum in PEDAGOGY_BY_AGE:
        if age in rng:
            return name, addendum
    return ("adult", "Use a practical, concise, professional explanation.")


# ---------- user-type tone defaults ----------

USER_TYPE_TONE: dict[UserType, Tone] = {
    "student":      "friendly",
    "teacher":      "teacher_like",
    "parent":       "parent_friendly",
    "professor":    "professor_like",
    "school_admin": "professional",
    "coaching":     "exam_focused",
    "professional": "corporate",
    "general":      "friendly",
}


# ---------- the profile itself ----------

@dataclass(frozen=True)
class PersonalizationProfile:
    """Fully-resolved instructions for one video generation request.

    All downstream services read from this. The script generator builds
    its prompt from `prompt_addendum`; the renderer uses `render_tier`,
    `output_format`, and `output_dimensions`; TTS uses `language_code`
    and `tone`; the post-processor uses `disclaimer_text` to decide
    whether to append a disclaimer scene.

    Cache key: hash everything except `language_code` (which is varied
    when re-rendering the same content into other languages).
    """
    # — what to make —
    video_mode: VideoMode
    duration_seconds: int
    output_format: OutputFormat
    output_dimensions: tuple[int, int]
    render_tier: RenderTier
    render_mode: str               # animated / reveal / static
    needs_quiz: bool
    needs_cta: bool

    # — for whom —
    user_type: UserType
    age: int
    grade: str                     # "Class 8", "College", "Professional"
    pedagogy_profile: str          # primary / middle_school / etc.

    # — how to speak —
    language_code: str             # "en", "hi", "ta", …
    tone: Tone
    narration_density: str         # light / medium / heavy

    # — what the model needs to know —
    scene_beats: tuple[str, ...]
    prompt_addendum: str           # appended to SYSTEM_PROMPT

    # — safety —
    sensitive_domain: str | None
    disclaimer_text: str | None


# ---------- format → dimensions ----------

OUTPUT_DIMENSIONS: dict[OutputFormat, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1":  (720, 720),
}


# ---------- tier limits (PRD §17.4, §17.5) ----------

# Subscription tier → hard caps. The user can request a longer video or
# a higher render tier, but `build_profile` will silently clamp it to
# what their plan allows. We don't 400 because the UI shouldn't have
# offered the option in the first place; clamping is the safe fallback.
#
# Keep this in lockstep with padhai/auth.py TIER_TO_PROVIDER — when a
# new tier is added there, add its limits here.
TIER_LIMITS: dict[str, dict] = {
    # M1 — Free
    "M1": {"max_duration_seconds": 300, "max_render_tier": "m1",
           "allowed_render_modes": {"animated", "static"}},
    # M2 — Student Basic (₹49/mo)
    "M2": {"max_duration_seconds": 420, "max_render_tier": "m2",
           "allowed_render_modes": {"animated", "static", "reveal"}},
    # M3 — Student Pro / Premium (₹199/mo) — photoreal
    "M3": {"max_duration_seconds": 600, "max_render_tier": "m3",
           "allowed_render_modes": {"animated", "static", "reveal"}},
    # M4a-e — School / Coaching / Enterprise tiers
    "M4a": {"max_duration_seconds": 600, "max_render_tier": "m4",
            "allowed_render_modes": {"animated", "static", "reveal"}},
    "M4b": {"max_duration_seconds": 900, "max_render_tier": "m4",
            "allowed_render_modes": {"animated", "static", "reveal"}},
    "M4c": {"max_duration_seconds": 900, "max_render_tier": "m4",
            "allowed_render_modes": {"animated", "static", "reveal"}},
    "M4d": {"max_duration_seconds": 1200, "max_render_tier": "m4",
            "allowed_render_modes": {"animated", "static", "reveal"}},
    "M4e": {"max_duration_seconds": 1800, "max_render_tier": "m4",
            "allowed_render_modes": {"animated", "static", "reveal"}},
}

_RENDER_TIER_ORDER = {"m1": 1, "m2": 2, "m3": 3, "m4": 4}


def _clamp_tier(requested: str, max_allowed: str) -> str:
    """Return the lower of requested vs max_allowed (by tier order)."""
    if _RENDER_TIER_ORDER.get(requested, 1) <= _RENDER_TIER_ORDER.get(max_allowed, 1):
        return requested
    return max_allowed


# ---------- the builder ----------

def build_profile(
    *,
    video_mode: VideoMode = "teaching",
    user_type: UserType = "student",
    age: int = 13,
    grade: str = "Class 8",
    language_code: str = "en",
    tone: Tone | None = None,
    duration_seconds: int | None = None,
    output_format: OutputFormat = "16:9",
    render_tier: RenderTier = "m1",
    topic_hint: str = "",
    user_subscription_tier: str = "M1",
) -> PersonalizationProfile:
    """Build a PersonalizationProfile from raw user inputs.

    The only required input is sensible defaults. Pure function — no
    LLM calls, no I/O. Safe to call from request handlers.

    Tier enforcement (PRD §17.4-5): clamps `duration_seconds` and
    `render_tier` to what the caller's `user_subscription_tier` allows.
    A free user requesting a 10-minute M4 photoreal video silently gets
    a 5-minute M1 cartoon — the UI shouldn't have offered the option,
    but the backend stays defensive.

    Raises ValueError on unknown video_mode, user_type, or output_format
    so callers get a clean 400 instead of a 500 deep in the pipeline.
    """
    if video_mode not in VIDEO_MODE_TEMPLATES:
        raise ValueError(
            f"video_mode {video_mode!r} not in "
            f"{sorted(VIDEO_MODE_TEMPLATES)}"
        )
    if output_format not in OUTPUT_DIMENSIONS:
        raise ValueError(
            f"output_format {output_format!r} not in "
            f"{sorted(OUTPUT_DIMENSIONS)}"
        )
    if user_type not in USER_TYPE_TONE:
        raise ValueError(
            f"user_type {user_type!r} not in {sorted(USER_TYPE_TONE)}"
        )

    template = VIDEO_MODE_TEMPLATES[video_mode]
    pedagogy_name, pedagogy_addendum = pedagogy_for_age(age)
    resolved_tone: Tone = tone or USER_TYPE_TONE[user_type]
    resolved_duration = duration_seconds or template.default_duration_seconds

    # Tier-enforce duration + render_tier. Default to M1 (Free) limits
    # when the tier code is unknown so a typo in subscription_tier
    # doesn't quietly unlock everything.
    limits = TIER_LIMITS.get(user_subscription_tier, TIER_LIMITS["M1"])
    resolved_duration = min(resolved_duration, limits["max_duration_seconds"])
    resolved_render_tier = _clamp_tier(render_tier, limits["max_render_tier"])

    # Kids mode forces the youngest pedagogy regardless of `age` field —
    # the mode is the stronger signal here than the age input (which
    # may be set by a parent picking content for their child).
    if video_mode == "kids":
        pedagogy_name, pedagogy_addendum = pedagogy_for_age(5)

    sensitive = detect_sensitive_domain(topic_hint)
    disclaimer = DISCLAIMERS.get(sensitive) if sensitive else None

    prompt_addendum = "\n\n".join(filter(None, [
        f"VIDEO MODE: {template.name}. {template.description}",
        f"USER TYPE: {user_type}. Speak in a {resolved_tone} tone.",
        f"PEDAGOGY: {pedagogy_addendum}",
        (
            "SCENE STRUCTURE: produce one scene for each beat in order. "
            "Beats: " + " → ".join(template.scene_beats) + "."
        ),
        f"TARGET DURATION: ~{resolved_duration} seconds total.",
        (
            f"OUTPUT FORMAT: {output_format}. " +
            ("Use short single-thought sentences suited for a vertical "
             "phone screen." if output_format == "9:16" else
             "Use full landscape composition.")
        ),
        (
            "INCLUDE A 3-QUESTION QUIZ at the end."
            if template.needs_quiz else
            "Do NOT include a quiz section."
        ),
        (
            "END with one clear call to action."
            if template.needs_cta else ""
        ),
        (
            f"SENSITIVE DOMAIN — {sensitive}. The video MUST end with "
            f"a clear disclaimer scene that says: {disclaimer}"
            if disclaimer else ""
        ),
    ]))

    return PersonalizationProfile(
        video_mode=video_mode,
        duration_seconds=resolved_duration,
        output_format=output_format,
        output_dimensions=OUTPUT_DIMENSIONS[output_format],
        render_tier=resolved_render_tier,
        render_mode=template.default_render_mode,
        needs_quiz=template.needs_quiz,
        needs_cta=template.needs_cta,
        user_type=user_type,
        age=age,
        grade=grade,
        pedagogy_profile=pedagogy_name,
        language_code=language_code,
        tone=resolved_tone,
        narration_density=template.narration_density,
        scene_beats=template.scene_beats,
        prompt_addendum=prompt_addendum,
        sensitive_domain=sensitive,
        disclaimer_text=disclaimer,
    )


# ---------- regeneration intents ----------

RegenerateAction = Literal[
    "make_easier", "make_advanced", "change_language",
    "shorten", "exam_focused", "create_short",
]


def apply_regenerate(
    profile: PersonalizationProfile,
    action: RegenerateAction,
    new_language: str | None = None,
    new_duration_seconds: int | None = None,
) -> PersonalizationProfile:
    """Return a new profile with the regeneration intent applied.

    Used by /api/v2/video-requests/{id}/regenerate — the PRD-spec
    regenerate flow that produces a *linked* video, not a fresh
    request, so the cache layer can reuse upstream analysis.
    """
    fields = {**profile.__dict__}
    if action == "make_easier":
        fields["age"] = max(7, profile.age - 4)
        fields["pedagogy_profile"], _ = pedagogy_for_age(fields["age"])
    elif action == "make_advanced":
        fields["age"] = min(22, profile.age + 4)
        fields["pedagogy_profile"], _ = pedagogy_for_age(fields["age"])
    elif action == "change_language":
        if not new_language:
            raise ValueError("change_language requires new_language")
        fields["language_code"] = new_language
    elif action == "shorten":
        fields["duration_seconds"] = new_duration_seconds or max(
            45, profile.duration_seconds // 2,
        )
    elif action == "exam_focused":
        fields["video_mode"] = "revision"
        fields["scene_beats"] = VIDEO_MODE_TEMPLATES["revision"].scene_beats
        fields["needs_quiz"] = True
    elif action == "create_short":
        fields["video_mode"] = "reel"
        fields["scene_beats"] = VIDEO_MODE_TEMPLATES["reel"].scene_beats
        fields["duration_seconds"] = 45
        fields["output_format"] = "9:16"
        fields["output_dimensions"] = OUTPUT_DIMENSIONS["9:16"]
    else:
        raise ValueError(f"unknown regenerate action: {action!r}")
    return PersonalizationProfile(**fields)
