"""Render a real explainer-video end-to-end using the offline Piper TTS
that ships in this sandbox. Bypasses the web layer to avoid network."""
import os
import sys
from pathlib import Path

# Force Piper TTS provider (offline, ships with Amy voice model)
os.environ["PIPER_MODEL"] = "/root/.piper_models/en-us-amy-low.onnx"

from padhai.cache import Cache
from padhai.pedagogy import explainer_to_lesson, pick_diagram
from padhai.render import render_lesson
from padhai.themes import BINOCS

EXPLAINER = {
    "topic": "Photosynthesis",
    "one_liner": "Green plants use sunlight, water, and carbon dioxide to make their own food and release oxygen.",
    "explanation": (
        "Plants are nature's tiny food factories. Inside the green parts of a plant — "
        "mostly the leaves — there are little structures called chloroplasts that "
        "contain a green pigment called chlorophyll. Chlorophyll acts like a solar "
        "panel. Using the energy it absorbs from sunlight, the plant takes water "
        "from the soil and carbon dioxide from the air, and combines them to make "
        "glucose. That glucose is the plant's food. As a bonus, oxygen is released "
        "back into the air — the same oxygen we and most animals breathe."
    ),
    "key_points": [
        "Happens inside chloroplasts in green leaves",
        "Chlorophyll absorbs energy from sunlight",
        "Inputs: water plus carbon dioxide plus light",
        "Outputs: glucose plus oxygen",
    ],
    "worked_example": (
        "Six molecules of carbon dioxide plus six molecules of water plus light "
        "produces one molecule of glucose plus six molecules of oxygen."
    ),
    "common_mistakes": [
        "Plants take in carbon dioxide, not oxygen",
        "Only the green parts of a plant photosynthesise",
    ],
    "analogy": (
        "A leaf is like a tiny solar-powered kitchen — sunlight is the stove, "
        "water and carbon dioxide are the ingredients, and sugar is the meal."
    ),
}


def main():
    out_dir = Path("samples")
    out_dir.mkdir(exist_ok=True)
    output = out_dir / "explainer_photosynthesis.mp4"

    diagram = pick_diagram(EXPLAINER["topic"])
    print(f"Step 1/3 — Explainer JSON → 5-scene Lesson (diagram: {diagram})")
    lesson = explainer_to_lesson(EXPLAINER, language_code="en", level="primary")
    print(f"  ✓ {len(lesson.scenes)} scenes:")
    for i, s in enumerate(lesson.scenes, 1):
        diag = f" [diagram: {s.diagram}]" if s.diagram else ""
        print(f"    {i}. {s.title}{diag}")

    print("Step 2/3 — Render (Piper voice + BINOCS theme + animated diagrams)")
    cache = Cache()
    render_lesson(
        lesson, output,
        cache=cache,
        theme=BINOCS,
        render_mode="animated",
        think_time_seconds=2.5,
        show_teacher=True,
    )
    print(f"  ✓ wrote {output}")

    print("Step 3/3 — Inspect output")
    print(f"  Size: {output.stat().st_size:,} bytes")
    print(f"  Cache stats: {cache.stats()}")
    print()
    print(f"Open: {output.resolve()}")


if __name__ == "__main__":
    main()
