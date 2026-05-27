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
from functools import lru_cache
from pathlib import Path


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
