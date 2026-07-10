"""Lightweight server-side i18n for AI Pathshala.

JSON locale files live in padhai/locales/{lang}.json. Each file maps
canonical English keys to translated strings. Served via:

  GET /api/i18n/{lang}.json    → full dict for the client to cache
  GET /api/i18n/keys           → list of canonical keys (for tooling)

Why custom (not gettext or Fluent):
  • Small initial surface (~30 keys), grows organically.
  • Client-side hydration is simpler than .po compilation.
  • Existing language switcher uses ISO codes — no extra mapping.

Fallback chain:
  1. requested locale (e.g. 'hi')
  2. 'en' (canonical)
  3. the key itself (no English fallback file exists; the key IS English)

When a key is missing in a locale, the English string from `en.json` is
returned with a `_missing_keys` audit field so translators can be alerted.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

# Regions of an HTML template that carry code, not user-visible prose, and
# must NEVER be run through the catalog string-swap: a catalog value like
# "Edit" would otherwise corrupt a JS identifier such as `toggleGoalEditor`
# into `toggleGoalसंपादित करेंor`, breaking the whole <script> (the page then
# hangs on its loading spinner because the inline JS never parses).
#   • <script>…</script>  — inline JavaScript
#   • <style>…</style>    — inline CSS
#   • on*="…" / on*='…'   — inline event-handler attributes (also JS)
_CODE_REGION_RE = re.compile(
    r"<script\b[^>]*>.*?</script>"
    r"|<style\b[^>]*>.*?</style>"
    r"|\son[a-zA-Z]+\s*=\s*\"[^\"]*\""
    r"|\son[a-zA-Z]+\s*=\s*'[^']*'",
    re.IGNORECASE | re.DOTALL,
)

SUPPORTED_LOCALES = (
    "en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa",
)


def _locales_dir() -> Path:
    return Path(__file__).resolve().parent / "locales"


@lru_cache(maxsize=32)
def load(locale: str) -> dict:
    """Load one locale's JSON. Returns {} for unknown locales (the
    caller will fall back to 'en')."""
    if locale not in SUPPORTED_LOCALES:
        return {}
    f = _locales_dir() / f"{locale}.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def t(key: str, *, locale: str = "en") -> str:
    """Translate one key. Falls back to English, then the key itself."""
    s = load(locale).get(key)
    if s:
        return s
    s = load("en").get(key)
    if s:
        return s
    return key


def merged(locale: str) -> dict:
    """Return the locale's dict overlaid on English defaults — so the
    client always has every key, with English fallback for missing ones."""
    base = dict(load("en"))
    overlay = load(locale)
    base.update({k: v for k, v in overlay.items() if v})
    return base


def missing_keys(locale: str) -> list[str]:
    """Keys present in 'en' but missing or empty in the locale."""
    if locale == "en":
        return []
    en = load("en")
    loc = load(locale)
    return [k for k in en if not loc.get(k)]


@lru_cache(maxsize=32)
def _swap_pairs(locale: str) -> tuple[tuple[str, str], ...]:
    """Return ordered (en_value, localized_value) pairs for the
    given locale. Longer values first so 'AI Tutor' replaces before
    'AI'. Excludes meta keys, identical mappings (locale value ==
    English), and values shorter than 4 chars (too risky to
    substring-match — could hit code identifiers like 'Up' or 'In').
    """
    if locale == "en" or locale not in SUPPORTED_LOCALES:
        return ()
    en = load("en")
    loc = load(locale)
    pairs: list[tuple[str, str]] = []
    for key, en_val in en.items():
        if key.startswith("_meta"):
            continue
        loc_val = loc.get(key)
        if not loc_val or loc_val == en_val:
            continue
        if not isinstance(en_val, str) or len(en_val) < 4:
            continue
        pairs.append((en_val, loc_val))
    # Sort longest-first so partial overlaps don't cause double-replace.
    pairs.sort(key=lambda p: -len(p[0]))
    return tuple(pairs)


@lru_cache(maxsize=64)
def localize_template(html: str, locale: str) -> str:
    """Server-side render — swap every English string in `html` for
    its translation in `locale`. Naive string-replace, but sufficient
    for the SPA's static UI labels which match en.json verbatim.

    Cached per (html, locale) tuple. Since the templates are
    module-level constants, the cache stays warm across requests.

    Falls back to returning `html` unchanged when locale == 'en' or
    the locale has no swap pairs (unknown locale, or all values
    identical to English).

    Code regions (<script>, <style>, inline on*= handlers) are masked
    out before the swap and restored afterwards, so a catalog value can
    never corrupt a JS identifier or CSS token — the class of bug that
    silently broke a page's inline script and left it hanging on its
    loading spinner."""
    if locale == "en":
        return html
    pairs = _swap_pairs(locale)
    if not pairs:
        return html

    # Stash code regions behind sentinels the swap can't touch. Null
    # bytes never appear in the English UI catalog, so they're safe.
    stash: list[str] = []

    def _mask(m: re.Match) -> str:
        stash.append(m.group(0))
        return f"\x00{len(stash) - 1}\x00"

    out = _CODE_REGION_RE.sub(_mask, html)
    for en_val, loc_val in pairs:
        if en_val in out:
            out = out.replace(en_val, loc_val)
    # Restore the untouched code regions.
    for i, original in enumerate(stash):
        out = out.replace(f"\x00{i}\x00", original)
    return out


def normalise_locale(value: str | None) -> str:
    """Map a raw header/cookie/path value to a supported locale code.
    Strips region tags (e.g. 'hi-IN' → 'hi'), lowercases, falls back
    to 'en' for anything unrecognised.
    """
    if not value:
        return "en"
    code = value.split(",")[0].split("-")[0].strip().lower()
    return code if code in SUPPORTED_LOCALES else "en"


def coverage() -> dict:
    """Translation coverage stats for all locales."""
    en = load("en")
    total = len(en) or 1
    out = {}
    for lc in SUPPORTED_LOCALES:
        if lc == "en":
            out[lc] = {"keys": total, "translated": total,
                       "pct": 1.0, "missing": []}
            continue
        loc = load(lc)
        translated = sum(1 for k in en if loc.get(k))
        out[lc] = {
            "keys": total,
            "translated": translated,
            "pct": round(translated / total, 3),
            "missing": [k for k in en if not loc.get(k)][:10],
        }
    return out
