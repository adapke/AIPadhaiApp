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


# Floors set in prod-3 (i18n audit sprint), raised at prod-10, prod-79.
# Bump these when you catalogue more strings; never lower.
MIN_EN_KEYS = 125
HI_PARITY_REQUIRED = True

# prod-10 — every supported locale must be at least 90% of the EN
# catalogue. Floor is monotonic; raise it together with new translation
# work, never lower.
MIN_PARITY_PCT_OTHER_LOCALES = 90.0

# Locales beyond Hindi that prod-10 brought to 100%. Listed so a
# future PR that breaks one (eg deletes a key) gets a clear test
# failure pointing at which locale.
LOCALES_AT_FULL_PARITY = ("ta", "te", "kn", "ml", "mr", "bn", "gu", "pa")


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


def test_supported_locales_at_or_above_floor():
    """prod-10 — every locale beyond English/Hindi must be at >=90%
    parity with the EN catalogue. Hindi has its own stricter test
    (test_hindi_has_full_parity); this catches future drift on the
    other 8."""
    en = i18n.load("en")
    en_keys = set(en.keys())
    below_floor: list[tuple[str, float, int]] = []
    for code in LOCALES_AT_FULL_PARITY:
        data = i18n.load(code)
        covered = sum(1 for k in en_keys if data.get(k))
        pct = round(100 * covered / len(en_keys), 1)
        if pct < MIN_PARITY_PCT_OTHER_LOCALES:
            below_floor.append((code, pct, len(en_keys) - covered))
    assert not below_floor, (
        f"Locales below {MIN_PARITY_PCT_OTHER_LOCALES}% parity: "
        f"{below_floor}. Re-run "
        "`python -X utf8 scripts/build_locales.py` after editing the "
        "translations table."
    )


def test_no_empty_values_in_supported_locales():
    """An empty string is not a translation. Catch the silent-fallback
    case across all 8 prod-10 locales."""
    bad: list[tuple[str, str]] = []
    for code in LOCALES_AT_FULL_PARITY:
        data = i18n.load(code)
        for k, v in data.items():
            if isinstance(v, str) and not v.strip():
                bad.append((code, k))
    assert not bad, f"empty translation strings: {bad}"
