"""prod-211 — primary/middle-school maths diagram templates.

Before prod-211 the only drawable maths concept was addition, so an explainer
video for "division of numbers" / "multiplication" / "fractions" fell back to a
plain bulleted whiteboard (correct narration, bland/mismatched-feeling visuals).
These tests pin the topic->template routing, the registry wiring, and a
font-best-effort render smoke so the new templates can't silently regress.
"""

from __future__ import annotations

import os

import pytest

from padhai import diagrams
from padhai.pedagogy import pick_diagram

NEW_TEMPLATES = (
    "division_groups",
    "multiplication_array",
    "subtraction_dots",
    "fraction_circle",
)

# topic phrase -> expected template
TOPIC_MAP = {
    "division of numbers": "division_groups",
    "long division": "division_groups",
    "how to divide fractions": "division_groups",  # 'divide' wins (first match)
    "multiplication tables": "multiplication_array",
    "learn to multiply": "multiplication_array",
    "times table of 7": "multiplication_array",
    "subtraction with borrowing": "subtraction_dots",
    "subtract two numbers": "subtraction_dots",
    "fractions for class 5": "fraction_circle",
    "numerator and denominator": "fraction_circle",
}


@pytest.mark.parametrize("topic,expected", TOPIC_MAP.items())
def test_pick_diagram_maps_math_topics(topic, expected):
    assert pick_diagram(topic) == expected


def test_new_templates_registered_and_callable():
    for name in NEW_TEMPLATES:
        assert callable(diagrams.get(name)), f"{name} not registered"


def test_addition_still_wins_and_no_math_collision():
    # The pre-existing addition template must not be shadowed by the new maths
    # keys, and division must NOT be caught by the addition keyword group.
    assert pick_diagram("addition") == "addition_dots"
    assert pick_diagram("adding numbers") == "addition_dots"
    assert pick_diagram("division") == "division_groups"


def test_unmatched_topic_returns_none():
    # A non-maths, non-templated topic still falls through to plain slides.
    assert pick_diagram("the french revolution") is None


def _find_font() -> str | None:
    for p in (
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        if os.path.exists(p):
            return p
    try:  # matplotlib bundles DejaVuSans on most CI images
        from matplotlib import font_manager

        return font_manager.findfont("DejaVu Sans")
    except Exception:
        return None


def test_render_smoke_no_exceptions():
    """Each new template must draw into a bounding box without raising."""
    font = _find_font()
    if not font:
        pytest.skip("no truetype font available for render smoke")
    from PIL import Image, ImageDraw

    from padhai.themes import BINOCS

    for name in NEW_TEMPLATES:
        img = Image.new("RGB", (1000, 320), BINOCS.bg)
        draw = ImageDraw.Draw(img)
        diagrams.get(name)(draw, 30, 20, 940, 280, BINOCS, font, font)
        # sanity: something was drawn (not a blank canvas)
        assert img.getbbox() is not None, f"{name} drew nothing"
