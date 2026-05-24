"""K1 — South Indian language rendering polish.

Tamil, Telugu, Kannada, Malayalam are in the language list since
v0.6 but the rendering needs work:

- **Tamil**: certain conjuncts (ksha, sri) break in our default
  font; vowel modifier rendering is off for u/uu/au.
- **Telugu**: TTS pacing is too fast (Bhashini default); commas need
  longer pauses for natural reading.
- **Kannada**: drishti vowel modifiers occasionally drop.
- **Malayalam**: chillu letters need ZWNJ to render correctly.

This module ships per-language preprocessing hooks that run before
TTS + before slide rendering. The bulk of the fix is at config-time
(better fonts, language-specific render constants) — this module
captures the rules so they're testable + reproducible.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class IndicProfile:
    language: str
    font_family: str                 # CSS font-family stack
    line_height: float
    tts_speech_rate: float           # 0.5 .. 2.0
    pause_after_comma_ms: int        # narration tuning
    pause_after_period_ms: int
    requires_zwnj: bool              # Malayalam chillu workaround


PROFILES: dict[str, IndicProfile] = {
    "ta": IndicProfile(
        language="ta",
        font_family='"Noto Sans Tamil", "Latha", Tahoma, sans-serif',
        line_height=1.6,
        tts_speech_rate=0.92,         # slightly slower; Tamil is dense
        pause_after_comma_ms=350,
        pause_after_period_ms=700,
        requires_zwnj=False,
    ),
    "te": IndicProfile(
        language="te",
        font_family='"Noto Sans Telugu", "Gautami", sans-serif',
        line_height=1.7,
        tts_speech_rate=0.85,         # was too fast at default 1.0
        pause_after_comma_ms=400,     # was 250 → users found it rushed
        pause_after_period_ms=800,
        requires_zwnj=False,
    ),
    "kn": IndicProfile(
        language="kn",
        font_family='"Noto Sans Kannada", "Tunga", sans-serif',
        line_height=1.65,
        tts_speech_rate=0.95,
        pause_after_comma_ms=300,
        pause_after_period_ms=650,
        requires_zwnj=False,
    ),
    "ml": IndicProfile(
        language="ml",
        font_family='"Noto Sans Malayalam", "Kartika", sans-serif',
        line_height=1.7,
        tts_speech_rate=0.90,
        pause_after_comma_ms=350,
        pause_after_period_ms=750,
        requires_zwnj=True,            # chillu fix
    ),
    "bn": IndicProfile(
        language="bn",
        font_family='"Noto Sans Bengali", "Vrinda", sans-serif',
        line_height=1.6,
        tts_speech_rate=0.95,
        pause_after_comma_ms=300,
        pause_after_period_ms=650,
        requires_zwnj=False,
    ),
}


# Default profile for languages not in the catalog (Hindi, English,
# etc. — already well-supported in v1.0).
DEFAULT_PROFILE = IndicProfile(
    language="default",
    font_family='"Inter", "Segoe UI", sans-serif',
    line_height=1.5,
    tts_speech_rate=1.0,
    pause_after_comma_ms=250,
    pause_after_period_ms=500,
    requires_zwnj=False,
)


def profile_for(language: str) -> IndicProfile:
    """Resolve the right rendering profile for the language code."""
    base_lang = (language or "").split("-")[0].lower()
    return PROFILES.get(base_lang, DEFAULT_PROFILE)


# ---------- Pre-render fixups ----------

# Malayalam chillu letters need ZWNJ (U+200C) inserted after the
# virama in certain consonant+virama sequences so browsers render
# the chillu form instead of treating it as a regular consonant.
# Pattern: consonant + ് (virama, U+0D4D) → insert ZWNJ unless one
# is already present.
_ML_CHILLU_SOURCES = ("ര", "ല", "ള", "ന", "ണ")
_ML_VIRAMA = "്"
_ZWNJ = "‌"


def normalize_text(text: str, *, language: str) -> str:
    """Apply per-language text normalization. Run BEFORE TTS + before
    rendering. Idempotent — running it twice yields the same result
    as running it once."""
    if not text:
        return text
    base_lang = (language or "").split("-")[0].lower()
    # Unicode NFC normalization for all Indic — collapses precomposed
    # vs decomposed sequences so font lookup is consistent.
    text = unicodedata.normalize("NFC", text)
    if base_lang == "ml":
        # Insert ZWNJ after chillu-producing consonant+virama pairs.
        # Negative lookahead skips sequences already followed by ZWNJ
        # → idempotent.
        for c in _ML_CHILLU_SOURCES:
            text = re.sub(
                f"{c}{_ML_VIRAMA}(?!{_ZWNJ})",
                f"{c}{_ML_VIRAMA}{_ZWNJ}",
                text,
            )
    if base_lang in ("ta", "te", "kn", "ml"):
        # Trim runs of trailing visarga / commas that some PDFs end
        # paragraphs with — TTS reads them as awkward filler.
        text = re.sub(r"[,;:]+\s*$", ".", text)
    return text


def tts_options(language: str) -> dict:
    """Returns a kwargs dict for the TTS provider. Caller spreads
    these into `synthesize(...)` calls."""
    p = profile_for(language)
    return {
        "speech_rate": p.tts_speech_rate,
        "pause_after_comma_ms": p.pause_after_comma_ms,
        "pause_after_period_ms": p.pause_after_period_ms,
    }


def css_overrides(language: str) -> dict:
    """CSS properties to inject into the SPA's `<html lang>` block
    when rendering Indic content. The frontend reads these from
    `/api/indic/profile?language=ta` to pick up font + line-height
    without rebuilding the bundle."""
    p = profile_for(language)
    return {
        "font-family": p.font_family,
        "line-height": str(p.line_height),
        "lang": p.language,
    }
