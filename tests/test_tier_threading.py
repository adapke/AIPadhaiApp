"""Regression guard: capped AI routes must thread the caller's tier.

The daily-cost cap (`llm_obs.check_daily_cap`) gates Claude calls per
subscription tier. Crucially, `daily_cap_paise(None) == 0` — the SAME as
free-tier M1 — so a route that calls a capped module WITHOUT passing
`user_tier` makes the cap treat EVERY user (even uncapped M4a) as free
tier and blocks the Claude call, silently degrading to the heuristic.

This actually happened: when the learning routes were extracted from
web.py into `routers/learning.py`, the `user_tier=` argument was dropped
from the essay / mock / practice calls. Paid users got the cheap
heuristic (essay scored a flat 0.0; mock used the keyword heuristic;
practice never synthesised). The v3.py copies kept the argument, which
is why the bug hid — the new slice shadowed the correct old routes.

These tests pin the wiring so the regression can't return.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from padhai import llm_obs

_REPO = Path(__file__).resolve().parents[1]
_LEARNING = _REPO / "padhai" / "routers" / "learning.py"

# (receiver-alias, method) pairs that hit a per-tier daily cap. Each MUST
# be called with an explicit user_tier= keyword in the route handler.
_CAPPED_CALLS = {
    ("eg", "grade"),          # essay_grader.grade
    ("mi", "submit_answer"),  # mock_interview.submit_answer
    ("pt", "generate"),       # practice_test.generate
}


def _capped_calls_missing_tier(path: Path) -> list[tuple[str, str, int]]:
    """Return (alias, method, lineno) for every capped call that omits
    the user_tier keyword."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    missing = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)):
            continue
        key = (node.func.value.id, node.func.attr)
        if key not in _CAPPED_CALLS:
            continue
        kwargs = {kw.arg for kw in node.keywords if kw.arg}
        if "user_tier" not in kwargs:
            missing.append((key[0], key[1], node.lineno))
    return missing


def test_none_tier_caps_to_free_allowance():
    """The footgun: an unknown/None tier resolves to the free tier's SMALL
    daily allowance (prod-245: ₹5 = 500 paise, was 0). A paid user whose
    tier is dropped is therefore throttled down to the free taste and then
    over_budget — which is WHY routes must pass the real tier. (The default
    is small, not generous, so dropping the tier is still a real footgun.)"""
    assert llm_obs.daily_cap_paise(None) == 500
    assert llm_obs.daily_cap_paise(None) == llm_obs.daily_cap_paise("M1")
    # Paid tiers are much larger than the free taste (so threading matters).
    assert llm_obs.daily_cap_paise("M2") > llm_obs.daily_cap_paise("M1")
    assert llm_obs.daily_cap_paise("M4a") is None  # uncapped


def test_learning_routes_thread_user_tier():
    """Every capped AI call in the learning router slice passes user_tier."""
    missing = _capped_calls_missing_tier(_LEARNING)
    assert not missing, (
        "capped AI calls in routers/learning.py dropped user_tier "
        f"(would block paid users): {missing}"
    )


def test_at_least_one_capped_call_is_present():
    """Guards the guard: if the calls were renamed/removed, the AST scan
    above would silently pass. Assert we actually found the call sites."""
    src = _LEARNING.read_text(encoding="utf-8")
    assert "eg.grade(" in src
    assert "mi.submit_answer(" in src
    assert "pt.generate(" in src


def test_practice_generate_accepts_user_tier():
    """practice_test.generate must accept user_tier so the route can pass
    it through to the cap pre-flight."""
    from padhai import practice_test
    params = inspect.signature(practice_test.generate).parameters
    assert "user_tier" in params
