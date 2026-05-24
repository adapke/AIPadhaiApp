"""Hindi explainer video — same Photosynthesis topic, fully in हिन्दी.

Demonstrates the multilingual side of the Explainer. In production:
- Hindi narration uses Bhashini (free for DPIIT startups) or a downloaded
  Piper Hindi voice (hi_IN-pratham-medium from rhasspy/piper-voices).
- Hindi explainer text comes from Claude (the same generate_explainer
  call, just with language_code='hi').

In this sandbox neither is reachable, so:
- Narration falls back to espeak-ng (robotic but functional — see
  EspeakProvider in padhai/tts.py).
- Explainer text is pre-translated below.
"""
import os
import sys
from pathlib import Path

# Use the offline EspeakProvider — works in this sandbox AND in production
# as a last-ditch fallback for languages without a Piper voice installed.
os.environ["PADHAI_TTS_PROVIDER"] = "espeak"

from padhai import diagrams as _diag_mod
from padhai.cache import Cache
from padhai.pedagogy import explainer_to_lesson, pick_diagram
from padhai.render import render_lesson
from padhai.themes import BINOCS
from padhai.tts import EspeakProvider


# Hindi-translated explainer (same shape as the English one, just हिन्दी text).
EXPLAINER_HI = {
    "topic": "प्रकाश संश्लेषण",  # "Photosynthesis"
    "one_liner": (
        "हरे पौधे सूर्य के प्रकाश, पानी और कार्बन डाइऑक्साइड का उपयोग करके "
        "अपना भोजन स्वयं बनाते हैं और ऑक्सीजन छोड़ते हैं।"
    ),
    "explanation": (
        "पौधे प्रकृति की छोटी भोजन फैक्ट्रियाँ हैं। पत्तियों के अंदर "
        "क्लोरोप्लास्ट नाम की छोटी संरचनाएँ होती हैं। उनमें क्लोरोफिल नाम "
        "का हरा रंगद्रव्य होता है, जो सूर्य के प्रकाश से ऊर्जा अवशोषित "
        "करता है। इस ऊर्जा का उपयोग करके पौधा जड़ों से पानी और हवा से "
        "कार्बन डाइऑक्साइड लेता है, उन्हें मिलाता है और ग्लूकोज बनाता "
        "है। एक उपहार के रूप में, हवा में ऑक्सीजन छोड़ी जाती है — वही "
        "ऑक्सीजन जिसे हम और लगभग सभी जानवर साँस लेते हैं।"
    ),
    "key_points": [
        "क्लोरोप्लास्ट के अंदर होता है, मुख्यतः पत्तियों में",
        "क्लोरोफिल सूर्य के प्रकाश से ऊर्जा अवशोषित करता है",
        "इनपुट: पानी + कार्बन डाइऑक्साइड + प्रकाश",
        "आउटपुट: ग्लूकोज + ऑक्सीजन",
    ],
    "worked_example": (
        "छह अणु कार्बन डाइऑक्साइड और छह अणु पानी, प्रकाश के साथ मिलकर, "
        "एक अणु ग्लूकोज और छह अणु ऑक्सीजन बनाते हैं।"
    ),
    "common_mistakes": [
        "पौधे कार्बन डाइऑक्साइड लेते हैं, ऑक्सीजन नहीं",
        "केवल पौधे के हरे भाग ही प्रकाश संश्लेषण करते हैं",
    ],
    "analogy": (
        "पत्ता एक छोटी सौर ऊर्जा से चलने वाली रसोई की तरह है — सूर्य का "
        "प्रकाश चूल्हा है, पानी और कार्बन डाइऑक्साइड सामग्री हैं, और "
        "चीनी पका हुआ भोजन है।"
    ),
}


# Hindi label translations for the photosynthesis diagram. Monkey-patch
# is intentionally narrow — we keep this demo self-contained instead of
# refactoring the diagrams.py callsite signature (which would be the
# right production fix once we have a UX request to lock in).
HI_LABELS = {
    "Sunlight": "सूर्य का प्रकाश",
    "CO2 in": "CO2 अंदर",        # plain "2" — Lohit Devanagari lacks subscript glyphs
    "Water from roots": "जड़ों से पानी",
    "O2 out": "O2 बाहर",
    "Glucose": "ग्लूकोज",
}
_orig_photosynthesis = _diag_mod.photosynthesis


def hindi_photosynthesis(draw, x, y, w, h, theme, title_font_path, body_font_path):
    """Same diagram, Hindi labels. Wraps the original by replacing the
    _label helper's text strings via a tiny dispatch."""
    orig_label = _diag_mod._label

    def hi_label(d, text, lx, ly, font_path, size, color):
        return orig_label(d, HI_LABELS.get(text, text), lx, ly,
                          font_path, size, color)

    _diag_mod._label = hi_label
    try:
        _orig_photosynthesis(draw, x, y, w, h, theme,
                             title_font_path, body_font_path)
    finally:
        _diag_mod._label = orig_label


# Register the Hindi variant under the same template name so
# scene.diagram="photosynthesis" picks it up.
_diag_mod.REGISTRY["photosynthesis"] = hindi_photosynthesis


def main():
    out_dir = Path("samples")
    out_dir.mkdir(exist_ok=True)
    output = out_dir / "explainer_photosynthesis_hi.mp4"

    diagram = pick_diagram("photosynthesis")  # English keyword still routes
    print(f"Step 1/3 — Hindi Explainer → 5-scene Lesson (diagram: {diagram})")
    # The diagram lookup is keyword-based on English so we pass the
    # topic alongside the Hindi title in pick_diagram. Here we know it
    # matches; in production, `pick_diagram` runs against the original
    # topic the student typed.
    lesson = explainer_to_lesson(EXPLAINER_HI, language_code="hi", level="primary")
    # Override the diagram on each scene that needs one (since the
    # Hindi topic string "प्रकाश संश्लेषण" doesn't contain the English
    # keyword "photosynth" that pick_diagram looks for).
    for s in lesson.scenes[:3]:
        s.diagram = "photosynthesis"
    print(f"  ✓ {len(lesson.scenes)} scenes:")
    for i, s in enumerate(lesson.scenes, 1):
        diag = f" [diagram: {s.diagram}]" if s.diagram else ""
        print(f"    {i}. {s.title}{diag}")

    print("Step 2/3 — Render (espeak-ng Hindi + BINOCS + animated diagram)")
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
    print("Note: voice is espeak-ng formant synthesis (robotic). In")
    print("production this would be Bhashini or a Piper Hindi voice.")
    print(f"Open: {output.resolve()}")


if __name__ == "__main__":
    main()
