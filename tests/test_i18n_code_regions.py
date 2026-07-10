"""Regression guard for the i18n localize_template code-region bug.

Root cause of a "page hangs on its loading spinner" report: the server-side
`localize_template` did a blind str-replace of every English catalog value
across the WHOLE template. A catalog value like "Edit" would then land inside
a JS identifier — e.g. `toggleGoalEditor` became `toggleGoalसंपादित करेंor` —
which is a syntax error, so the page's entire inline <script> failed to parse
and the loading spinner never resolved.

The fix masks <script>/<style>/inline-on* regions before the swap and restores
them afterwards. These tests pin that contract: visible prose still gets
translated, but code regions come through byte-for-byte identical.

Also guards a sibling bug in home_ui.py where `today\'s` (a single backslash in
the Python source) collapsed to a bare apostrophe in the served JS, breaking
the single-quoted string it lived in — in every locale, including English.
"""

from __future__ import annotations

import re

import pytest

from padhai import i18n

# Locales that actually carry translations (skip 'en' — it's a no-op).
_TRANSLATED_LOCALES = [lc for lc in i18n.SUPPORTED_LOCALES if lc != "en"]


def _sample_pair(locale: str) -> tuple[str, str]:
    """A (english, translated) catalog pair for `locale`: a value ≥4 chars
    (so it participates in the swap) that the locale actually translates."""
    en = i18n.load("en")
    loc = i18n.load(locale)
    for key, en_val in en.items():
        if key.startswith("_meta") or not isinstance(en_val, str):
            continue
        loc_val = loc.get(key)
        if len(en_val) >= 4 and loc_val and loc_val != en_val:
            return en_val, loc_val
    pytest.skip(f"no translatable catalog value for {locale}")


@pytest.mark.parametrize("locale", _TRANSLATED_LOCALES)
def test_script_and_style_bodies_survive_localize(locale: str):
    en_val, loc_val = _sample_pair(locale)
    ident = "toggle" + re.sub(r"\W", "", en_val) + "Handler"
    script = f'<script>var s="{en_val}"; function {ident}(){{return "{en_val}";}}</script>'
    style = f'<style>.a::after{{content:"{en_val}";}}</style>'
    handler = f"<button onclick=\"go('{en_val}')\">x</button>"
    html = f"<h1>{en_val}</h1>{script}{style}{handler}<p>{en_val}</p>"

    out = i18n.localize_template(html, locale)

    # Code regions come through byte-for-byte — the English value is NOT
    # swapped inside them (which is what used to corrupt the JS).
    assert script in out, "inline <script> body was mutated by localize"
    assert style in out, "inline <style> body was mutated by localize"
    assert handler in out, "inline on* handler was mutated by localize"

    # ...but visible prose outside code regions IS translated.
    assert f"<h1>{loc_val}</h1>" in out
    assert f"<p>{loc_val}</p>" in out


@pytest.mark.parametrize("locale", _TRANSLATED_LOCALES)
def test_localized_page_has_no_broken_js_identifier(locale: str):
    """The exact failure shape: a catalog value fused into a JS identifier."""
    en_val, loc_val = _sample_pair(locale)
    html = f"<script>window.on{en_val.replace(' ', '')}Ready = 1;</script>"
    out = i18n.localize_template(html, locale)
    assert loc_val not in out, "translated text leaked into a <script> identifier"
    assert html in out


def test_home_html_escapes_apostrophes_in_inline_js():
    """home_ui.py builds JS strings with apostrophes ("today's"). The Python
    source must carry `\\'` so the served JS gets a properly-escaped `\\'` —
    a single `\\'` collapses to a bare apostrophe and breaks the JS string."""
    from padhai import home_ui

    html = home_ui.get_home_html()
    # The served page must not contain an unescaped `today's` inside JS.
    assert "today's" not in html, "unescaped apostrophe in served inline JS"
    # The correctly-escaped form is what should appear.
    assert "today\\'s" in html
