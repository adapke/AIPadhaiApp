"""prod-212 — board/grade grounding for the AI explainer.

generate_explainer used to receive only a bare topic, so "Trigonometry" for a
CBSE-10 student was explained at a generic (often too-advanced) depth. These
tests pin: (1) the syllabus scope + board hint are injected into the prompt
when supplied and absent otherwise; (2) the explainer cache key is
scope-distinct yet byte-compatible with the pre-prod-212 key when ungrounded;
(3) the route scope resolver degrades to ungrounded on any failure.
"""

from __future__ import annotations

import json
import types

# Import web first so the full router graph is initialized before we reference
# the explainer router module (avoids a partially-initialized circular import).
from padhai import llm_call, pedagogy, web  # noqa: F401
from padhai.auth import AuthUser
from padhai.routers import explainer as explainer_router


def _fake_call(captured):
    def fake_call_claude(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return types.SimpleNamespace(
            text=json.dumps({
                "topic": "T", "one_liner": "o", "explanation": "e",
                "key_points": ["k"], "worked_example": "w",
                "common_mistakes": [], "analogy": "a",
            })
        )
    return fake_call_claude


def test_scope_and_board_injected_when_supplied(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(llm_call, "call_claude", _fake_call(cap))
    pedagogy.generate_explainer(
        "Trigonometry",
        syllabus_scope="In-scope chapters: Introduction to Trigonometry.",
        board_hint="cbse",
    )
    msg = cap["messages"][0]["content"]
    assert "In-scope chapters: Introduction to Trigonometry." in msg
    assert "cbse" in msg
    assert "Curriculum scope:" in msg


def test_no_injection_without_scope(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(llm_call, "call_claude", _fake_call(cap))
    pedagogy.generate_explainer("Trigonometry")
    msg = cap["messages"][0]["content"]
    assert "Curriculum scope:" not in msg
    assert "Board/exam context:" not in msg


def test_cache_key_scope_distinct_but_backward_compatible():
    from padhai import web

    c = web.cache
    base = c.explainer_key("Trigonometry", "en", "middle")
    # empty scope must NOT change the key (existing ungrounded cache preserved)
    assert c.explainer_key("Trigonometry", "en", "middle", "") == base
    # a real scope must produce a distinct key
    grounded = c.explainer_key("Trigonometry", "en", "middle", "cbse_10")
    assert grounded != base
    # deterministic
    assert grounded == c.explainer_key("Trigonometry", "en", "middle", "cbse_10")


def _user() -> AuthUser:
    return AuthUser(
        id="u1", email="e@x.com",
        subscription_tier="M1", subscription_level="L1",
    )


def test_scope_none_for_anonymous():
    assert explainer_router._explainer_scope(None) == ("", None, None)


def test_scope_degrades_on_exception(monkeypatch):
    import padhai.exam_taxonomy as et

    def boom(_uid):
        raise RuntimeError("taxonomy tables not migrated")

    monkeypatch.setattr(et, "taxonomy_scope_for_user", boom)
    assert explainer_router._explainer_scope(_user()) == ("", None, None)


def test_scope_maps_dict(monkeypatch):
    import padhai.exam_taxonomy as et

    monkeypatch.setattr(
        et, "taxonomy_scope_for_user",
        lambda _uid: {
            "exam_code": "cbse_10", "scope_summary": "S", "board_hint": "cbse",
        },
    )
    assert explainer_router._explainer_scope(_user()) == ("cbse_10", "S", "cbse")


def test_scope_none_when_no_enrollment(monkeypatch):
    import padhai.exam_taxonomy as et

    monkeypatch.setattr(et, "taxonomy_scope_for_user", lambda _uid: None)
    assert explainer_router._explainer_scope(_user()) == ("", None, None)
