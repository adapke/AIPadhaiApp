"""Visual themes for PadhAI slides.

A theme is a small bundle of colors + font preferences + decoration knobs.
Slide-drawing code reads the active theme rather than hard-coding any of
this, so we can swap the entire look (dark-academic ↔ whiteboard ↔
kindergarten) without touching the layout logic."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    name: str
    bg: tuple[int, int, int]
    fg: tuple[int, int, int]
    accent: tuple[int, int, int]
    muted: tuple[int, int, int]
    # Title-font keyword preferences in order. The first match in `fc-list`
    # wins; falls back to DejaVu if none match.
    title_font_keywords: tuple[str, ...] = ()
    body_font_keywords: tuple[str, ...] = ()
    title_size: int = 44
    body_size: int = 30
    header_size: int = 22
    # decoration toggles
    show_accent_stripe: bool = True
    bullet_marker: str = "•"
    show_emoji: bool = False
    rounded_corners: bool = False


DARK_ACADEMIC = Theme(
    name="dark_academic",
    bg=(20, 25, 40),
    fg=(240, 240, 245),
    accent=(255, 180, 60),
    muted=(170, 175, 195),
    title_size=44,
    body_size=30,
    header_size=22,
)

WHITEBOARD = Theme(
    name="whiteboard",
    bg=(248, 244, 230),         # warm cream
    fg=(40, 38, 35),             # near-black ink
    accent=(180, 60, 40),        # terracotta
    muted=(120, 115, 105),
    title_font_keywords=("Comic", "Patrick", "Caveat", "PT Sans", "Architects"),
    body_font_keywords=("Comic", "Patrick", "Caveat", "PT Sans"),
    title_size=44,
    body_size=30,
    header_size=22,
    bullet_marker="✎",
    rounded_corners=True,
)

KINDERGARTEN = Theme(
    name="kindergarten",
    bg=(255, 250, 220),          # pale buttercream
    fg=(40, 40, 60),
    accent=(232, 75, 90),         # bright coral
    muted=(120, 130, 160),
    title_font_keywords=("Comic", "Patrick", "Caveat", "PT Sans"),
    body_font_keywords=("Comic", "Patrick", "Caveat", "PT Sans"),
    title_size=60,
    body_size=42,
    header_size=24,
    bullet_marker="★",
    show_emoji=True,
    rounded_corners=True,
)


# BINOCS — bright, character-driven explainer style inspired by Dr. Binocs Show.
# Used by /explain/video so the Explainer feels like the cheerful science kids
# already know from YouTube, rather than the textbook whiteboard look.
BINOCS = Theme(
    name="binocs",
    bg=(178, 220, 255),          # sky blue (slightly desaturated for text contrast)
    fg=(28, 38, 78),              # deep navy ink for high readability
    accent=(255, 165, 50),        # bright orange (sun / arrows / highlights)
    muted=(85, 100, 145),
    title_font_keywords=("Comic", "Patrick", "Caveat", "Fredoka", "PT Sans"),
    body_font_keywords=("Comic", "Patrick", "Caveat", "Fredoka", "PT Sans"),
    title_size=52,
    body_size=34,
    header_size=24,
    bullet_marker="•",           # plain bullet — renders in every Latin AND Indic font
    show_emoji=False,             # turn off celebration interludes (cleaner pacing)
    rounded_corners=True,
)


REGISTRY: dict[str, Theme] = {
    "dark_academic": DARK_ACADEMIC,
    "whiteboard": WHITEBOARD,
    "kindergarten": KINDERGARTEN,
    "binocs": BINOCS,
}


def theme_for_level(level: str, override: str | None = None) -> Theme:
    """Pick a sensible theme for a given difficulty level unless overridden."""
    if override:
        if override not in REGISTRY:
            raise ValueError(
                f"theme {override!r} not found. choose from: {sorted(REGISTRY)}"
            )
        return REGISTRY[override]
    if level == "kg":
        return KINDERGARTEN
    if level in ("eli5", "primary"):
        return WHITEBOARD
    return DARK_ACADEMIC
