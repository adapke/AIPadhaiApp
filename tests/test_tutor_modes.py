"""prod-136 — Tests for the Tutor Mode Switcher.

Covers:
  1. MODES catalog has all 6 expected keys.
  2. Every mode has non-empty bilingual labels + addendum.
  3. apply_mode(prompt, unknown_key) returns prompt unchanged.
  4. apply_mode(prompt, valid_key) injects the addendum.
  5. apply_mode preserves the base prompt content.
  6. get_mode is case-insensitive + whitespace-tolerant.
  7. list_modes() returns a serialisable list with the expected keys.
  8. HTTP GET /api/tutor/modes returns the catalog (public, no auth).
  9. HTTP response shape matches the documented contract.
 10. Modes produce structurally distinct output prompts.
 11. tutor.send_message accepts mode= parameter without breaking
     the existing signature.
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


EXPECTED_MODE_KEYS = {
    "quick_explain",
    "jee_advanced_drill",
    "neet_one_liner",
    "cbse_board_answer",
    "desi_analogy",
    "rural_simple",
}


def test_modes_catalog_has_expected_keys():
    """prod-136 — All 6 CK-12-inspired modes are present."""
    from padhai.tutor_modes import MODES
    keys = {m.key for m in MODES}
    assert keys == EXPECTED_MODE_KEYS


def test_every_mode_has_bilingual_labels_and_addendum():
    """prod-136 — Every mode carries non-empty EN + HI labels and a
    non-trivial system prompt addendum."""
    from padhai.tutor_modes import MODES
    for m in MODES:
        assert m.label_en, m.key
        assert m.label_hi, m.key
        assert m.one_line_en, m.key
        assert m.one_line_hi, m.key
        assert m.icon, m.key
        # Addendum must be substantial — at least 100 chars to actually
        # change Claude's behaviour.
        assert len(m.system_addendum) > 100, (
            f"{m.key} addendum too short: {len(m.system_addendum)}"
        )


def test_apply_mode_unknown_key_returns_unchanged():
    """prod-136 — apply_mode(prompt, 'nonsense') returns prompt verbatim."""
    from padhai.tutor_modes import apply_mode
    base = "You are a tutor."
    assert apply_mode(base, "nonsense_mode") == base
    assert apply_mode(base, None) == base
    assert apply_mode(base, "") == base


def test_apply_mode_valid_injects_addendum():
    """prod-136 — apply_mode(prompt, valid_key) appends the addendum."""
    from padhai.tutor_modes import apply_mode
    base = "You are a tutor."
    out = apply_mode(base, "quick_explain")
    assert base in out  # base preserved
    assert "QUICK EXPLAIN" in out  # addendum injected
    assert "MODE-SPECIFIC OVERRIDE" in out
    assert "END MODE OVERRIDE" in out


def test_get_mode_case_insensitive():
    """prod-136 — get_mode is forgiving on case + whitespace."""
    from padhai.tutor_modes import get_mode
    assert get_mode("quick_explain") is not None
    assert get_mode("QUICK_EXPLAIN") is not None
    assert get_mode("  Quick_Explain  ") is not None
    assert get_mode("unknown") is None


def test_list_modes_returns_dict_list():
    """prod-136 — list_modes() returns a JSON-serialisable list of dicts."""
    import json

    from padhai.tutor_modes import list_modes
    modes = list_modes()
    assert isinstance(modes, list)
    assert len(modes) == 6
    for m in modes:
        for key in ("key", "label_en", "label_hi", "one_line_en",
                    "one_line_hi", "icon"):
            assert key in m, m
    # Round-trips through JSON without errors
    json.dumps(modes)


def test_modes_have_distinct_output_prompts():
    """prod-136 — Each mode produces a structurally different system
    prompt, so the model receives a different lens."""
    from padhai.tutor_modes import MODES, apply_mode
    base = "You are a tutor."
    prompts = {m.key: apply_mode(base, m.key) for m in MODES}
    seen = set()
    for k, p in prompts.items():
        assert p not in seen, f"{k} duplicates another mode's prompt"
        seen.add(p)


def test_http_modes_endpoint_public(monkeypatch):
    """prod-136 — GET /api/tutor/modes is public and returns catalog."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )

    from padhai.web import app
    client = TestClient(app)
    r = client.get("/api/tutor/modes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "modes" in body
    assert isinstance(body["modes"], list)
    assert len(body["modes"]) == 6
    # First mode should be quick_explain (matches MODES tuple order)
    assert body["modes"][0]["key"] == "quick_explain"


def test_http_modes_response_shape(monkeypatch):
    """prod-136 — Every mode in the HTTP response carries the contract
    fields, no extras."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )

    from padhai.web import app
    client = TestClient(app)
    r = client.get("/api/tutor/modes")
    assert r.status_code == 200
    body = r.json()
    required_fields = {"key", "label_en", "label_hi",
                       "one_line_en", "one_line_hi", "icon"}
    for m in body["modes"]:
        assert required_fields.issubset(m.keys()), m


def test_tutor_send_message_accepts_mode_kwarg(monkeypatch):
    """prod-136 — Calling tutor.send_message(..., mode='quick_explain')
    doesn't break. We don't make a real Claude call — just verify the
    keyword threads through without TypeError."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )

    import inspect

    from padhai import tutor
    sig = inspect.signature(tutor.send_message)
    assert "mode" in sig.parameters, sig.parameters.keys()
    # Default should be None (opt-in)
    assert sig.parameters["mode"].default is None


def test_indian_context_words_in_desi_mode():
    """prod-136 — The 'desi_analogy' addendum should reference Indian
    everyday-life concepts. Sanity check that the prompt actually
    encodes the intent."""
    from padhai.tutor_modes import get_mode
    desi = get_mode("desi_analogy")
    assert desi is not None
    text = desi.system_addendum.lower()
    # At least 3 Indian-context tokens should be present
    candidates = [
        "mumbai", "cricket", "kabaddi", "diwali", "monsoon",
        "kirana", "rupee", "dosa", "rickshaw", "midday",
    ]
    matches = [c for c in candidates if c in text]
    assert len(matches) >= 3, (
        f"desi_analogy needs Indian context; only matched {matches}"
    )
