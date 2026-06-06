"""prod-11 — SPA i18n wiring regression.

Locks the contract between the locale catalog and the rendered HTML:
when the user hits /home with a non-English locale (via path,
query, or cookie), the English UI strings get swapped for the
locale's translations.

If a future refactor breaks `localize_template` or the route hookup,
this test fails before CI lets it land.

The test uses 'Sign in' as the canary string because it's:
  * Present in HOME_HTML verbatim (so the swap can find it)
  * In en.json (so all 8 non-English locales have a translation)
  * Long enough (>=4 chars) to clear the substring-guard floor
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from padhai import i18n
from padhai.web import app

SIGN_IN_BY_LOCALE = {
    "en": "Sign in",
    "hi": "साइन इन",
    "ta": "உள்நுழை",
    "te": "సైన్ ఇన్",
    "kn": "ಸೈನ್ ಇನ್",
    "ml": "സൈൻ ഇൻ",
    "mr": "साइन इन",
    "bn": "সাইন ইন",
    "gu": "સાઇન ઇન",
    "pa": "ਸਾਈਨ ਇਨ",
}


def test_normalise_locale_handles_raw_inputs():
    assert i18n.normalise_locale(None) == "en"
    assert i18n.normalise_locale("") == "en"
    assert i18n.normalise_locale("hi") == "hi"
    assert i18n.normalise_locale("hi-IN") == "hi"
    assert i18n.normalise_locale("ta-IN,en;q=0.9") == "ta"
    assert i18n.normalise_locale("XX") == "en"  # unknown -> en
    assert i18n.normalise_locale("zh") == "en"  # not supported


def test_swap_pairs_excludes_meta_and_identical_values():
    """The auto-swap mustn't replace _meta keys or values that are
    identical between EN and the locale (would be a no-op anyway,
    but listing them inflates the cache)."""
    for code in ("ta", "hi", "bn"):
        pairs = i18n._swap_pairs(code)
        for en_val, loc_val in pairs:
            assert not en_val.startswith("_meta")
            assert en_val != loc_val
            assert len(en_val) >= 4


def test_localize_template_replaces_known_string():
    """Pure-function test of the swap, independent of the route layer."""
    tpl = "<a>Sign in</a><a>Settings</a>"
    out = i18n.localize_template(tpl, "ta")
    assert "உள்நுழை" in out  # 'Sign in' -> Tamil
    assert "Sign in" not in out


def test_localize_template_passthrough_for_english():
    tpl = "<a>Sign in</a>"
    assert i18n.localize_template(tpl, "en") == tpl


def test_home_route_defaults_to_english():
    client = TestClient(app)
    r = client.get("/home")
    assert r.status_code == 200
    assert "Sign in" in r.text


def test_home_route_query_string_locale():
    client = TestClient(app)
    r = client.get("/home?lang=hi")
    assert r.status_code == 200
    assert SIGN_IN_BY_LOCALE["hi"] in r.text


def test_home_route_cookie_locale():
    client = TestClient(app)
    client.cookies.set("padhai_lang", "bn")
    r = client.get("/home")
    assert r.status_code == 200
    assert SIGN_IN_BY_LOCALE["bn"] in r.text


def test_home_localized_seo_route_works_for_all_supported():
    """Every supported non-English locale's /home/{lang} variant
    must render with translated strings."""
    client = TestClient(app)
    for code, expected in SIGN_IN_BY_LOCALE.items():
        if code == "en":
            continue
        r = client.get(f"/home/{code}")
        assert r.status_code == 200, f"{code}: {r.status_code}"
        assert expected in r.text, f"{code}: missing {expected!r}"


def test_home_localized_unknown_locale_404():
    client = TestClient(app)
    r = client.get("/home/xx")
    assert r.status_code == 404


def test_home_unknown_lang_query_falls_back_to_english():
    """A malicious or stale ?lang= shouldn't 500 — it should
    silently fall back to the EN template."""
    client = TestClient(app)
    r = client.get("/home?lang=xx")
    assert r.status_code == 200
    # English Sign in should be in the rendered HTML
    assert "Sign in" in r.text


def test_ui_route_also_respects_locale():
    """Two routes serve the same template (/ui and /home). Both
    must accept the locale signal — otherwise users hitting /ui
    get stuck on English."""
    client = TestClient(app)
    r = client.get("/ui?lang=hi")
    assert r.status_code == 200
    assert SIGN_IN_BY_LOCALE["hi"] in r.text
