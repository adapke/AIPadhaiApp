"""Verify the render pipeline (slides + gTTS + ffmpeg) with a hand-built
Lesson — no Claude API key required."""

from pathlib import Path

from padhai.pedagogy import Lesson, Scene
from padhai.render import render_lesson

lesson = Lesson(
    title="Photosynthesis",
    language_code="en",
    language_name="English",
    level="middle",
    scenes=[
        Scene(
            title="What is photosynthesis?",
            narration=(
                "Photosynthesis is the process that green plants use to make their own food. "
                "The word comes from two Greek words: photo, meaning light, and synthesis, "
                "meaning to put together. Plants are the only living things on Earth that can "
                "do this."
            ),
            bullets=[
                "Plants make their own food",
                "Uses sunlight as the energy source",
                "Happens mainly in green leaves",
            ],
        ),
        Scene(
            title="Where does it happen?",
            narration=(
                "Photosynthesis happens inside the leaves. The green color of leaves comes "
                "from a pigment called chlorophyll. Chlorophyll lives inside tiny structures "
                "called chloroplasts, and it traps sunlight to power the reaction."
            ),
            bullets=[
                "Inside the leaves",
                "Chlorophyll = the green pigment",
                "Chloroplasts trap sunlight",
            ],
        ),
        Scene(
            title="The simple equation",
            narration=(
                "The recipe is straightforward. Water comes up from the roots. Carbon dioxide "
                "comes in from the air through tiny holes called stomata. With sunlight and "
                "chlorophyll, the plant turns these into glucose, which it stores as food, "
                "and oxygen, which it releases back into the air."
            ),
            bullets=[
                "Inputs: water + carbon dioxide",
                "Energy: sunlight + chlorophyll",
                "Outputs: glucose + oxygen",
            ],
        ),
    ],
    quiz=[
        {
            "question": "Which pigment traps sunlight in plant leaves?",
            "options": {
                "A": "Hemoglobin", "B": "Chlorophyll",
                "C": "Melanin", "D": "Carotene",
            },
            "answer": "B",
        },
    ],
)

out = render_lesson(lesson, Path("test_render.mp4"))
print(f"OK -> {out} ({out.stat().st_size:,} bytes)")
