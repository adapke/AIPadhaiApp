"""i18n coverage regression tests.

Locks the i18n key set + Hindi parity from prod-3. If a future PR
adds an English key without the Hindi translation, the test fails.
If the SPA gains a new hardcoded English string, the audit script
will surface it but won't fail — that's a measurement, not a gate.

The floor numbers below are what prod-3 shipped. Future sprints
raise the floor as they catalogue more strings.
"""

from __future__ import annotations

from pathlib import Path

from padhai import i18n

REPO_ROOT = Path(__file__).resolve().parent.parent


# Floors set in prod-3 (i18n audit sprint). Bump these when you
# catalogue more strings; never lower.
MIN_EN_KEYS = 94
HI_PARITY_REQUIRED = True


def test_english_key_count_at_or_above_floor():
    """en.json carries the canonical key set. We never delete keys
    (only deprecate-then-remove in a follow-up release) so the count
    is monotonic across sprints."""
    en = i18n.load("en")
    assert len(en) >= MIN_EN_KEYS, (
        f"en.json has {len(en)} keys; floor is {MIN_EN_KEYS}. "
        "Either a key was deleted (don't — deprecate first) or "
        "the floor needs updating in this test."
    )


def test_hindi_has_full_parity_with_english():
    """Hindi is the launch language for the Indian market — every
    English key MUST have a Hindi value. Other locales can lag
    (Kannada / Malayalam / Gujarati / Punjabi are tracked separately
    and shown in coverage())."""
    en = i18n.load("en")
    hi = i18n.load("hi")
    missing = [k for k in en if not hi.get(k)]
    assert not missing, (
        f"Hindi missing {len(missing)} keys present in English: "
        f"{missing[:10]}{'...' if len(missing) > 10 else ''}. "
        "Translate them before merging, or remove the key from en.json."
    )


def test_meta_locales_present_for_supported_languages():
    """Every locale in SUPPORTED_LOCALES must have at least the
    `_meta_name` / `_meta_native` keys — those drive the language
    picker in the SPA."""
    for lc in i18n.SUPPORTED_LOCALES:
        loc = i18n.load(lc)
        assert "_meta_name" in loc, f"{lc}.json missing _meta_name"
        assert "_meta_native" in loc, f"{lc}.json missing _meta_native"


def test_no_empty_hindi_values():
    """An empty string is not a translation. The fallback to English
    via `merged()` masks this, so a CI gate is needed."""
    hi = i18n.load("hi")
    empty = [k for k, v in hi.items() if isinstance(v, str) and not v.strip()]
    assert not empty, (
        f"Hindi keys with empty values: {empty}. "
        "Use the English string or actually translate."
    )
