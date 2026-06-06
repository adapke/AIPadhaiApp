"""prod-5 — LLM-judge accuracy-bench scorer regression tests.

Verifies the judge logic without burning real Claude calls. The
mock returns canned verdict tokens (CORRECT / PARTIAL / WRONG) and
we assert the score mapping + reason field.

The real baseline run still requires ANTHROPIC_API_KEY and is
documented in scripts/run_accuracy_bench.py — these tests only
lock the deterministic glue around the call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from padhai import accuracy_bench as bench


def _stub_client_returning(text: str):
    """Build a fake Anthropic client whose messages.create returns
    a response object shaped like the real SDK's."""
    def _create(**_kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
        )
    return SimpleNamespace(messages=SimpleNamespace(create=_create))


@pytest.fixture()
def fake_client(monkeypatch):
    """Patch _llm_judge_client to return a controllable stub."""
    holder = {"verdict": "CORRECT"}

    def _patched():
        return _stub_client_returning(holder["verdict"]), "fake-model"

    monkeypatch.setattr(bench, "_llm_judge_client", _patched)
    return holder


def test_llm_judge_correct_returns_1(fake_client):
    fake_client["verdict"] = "CORRECT"
    score, reason = bench._llm_judge(
        {"answer": "Newton's first law"},
        {"answer": "law of inertia"},
        prompt="State Newton's first law.",
    )
    assert score == 1.0
    assert reason is None


def test_llm_judge_partial_returns_half(fake_client):
    fake_client["verdict"] = "PARTIAL"
    score, reason = bench._llm_judge(
        {"answer": "Newton's first law"},
        {"answer": "something about motion"},
        prompt="State Newton's first law.",
    )
    assert score == 0.5
    assert reason == "judge: PARTIAL"


def test_llm_judge_wrong_returns_0(fake_client):
    fake_client["verdict"] = "WRONG"
    score, reason = bench._llm_judge(
        {"answer": "Newton's first law"},
        {"answer": "E = mc^2"},
        prompt="State Newton's first law.",
    )
    assert score == 0.0
    assert reason == "judge: WRONG"


def test_llm_judge_tolerates_trailing_punctuation(fake_client):
    """Models sometimes emit `CORRECT.` or `CORRECT\n` despite the
    system prompt. Verdict parsing must tolerate this."""
    fake_client["verdict"] = "CORRECT.\n"
    score, _ = bench._llm_judge(
        {"answer": "Delhi"}, {"answer": "New Delhi"}, prompt="Capital of India?",
    )
    assert score == 1.0


def test_llm_judge_unparseable_verdict_returns_0(fake_client):
    """A surprise output ('I think the answer is correct') shouldn't
    spuriously pass."""
    fake_client["verdict"] = "I think it's right"
    score, reason = bench._llm_judge(
        {"answer": "Delhi"}, {"answer": "New Delhi"}, prompt="Capital of India?",
    )
    assert score == 0.0
    assert reason and "unparseable" in reason


def test_llm_judge_empty_actual_returns_0_without_calling_claude(monkeypatch):
    """If the system-under-test returned an empty string, we know
    it's wrong — short-circuit before paying for a judge call."""
    called = {"count": 0}

    def _patched():
        called["count"] += 1
        return _stub_client_returning("CORRECT"), "fake-model"

    monkeypatch.setattr(bench, "_llm_judge_client", _patched)
    score, reason = bench._llm_judge(
        {"answer": "Delhi"}, {"answer": "   "}, prompt="Capital of India?",
    )
    assert score == 0.0
    assert reason == "actual.answer empty"
    assert called["count"] == 0, "should not have called Claude"


def test_llm_judge_no_api_key_raises(monkeypatch):
    """Without ANTHROPIC_API_KEY, the judge raises a clear error
    (vs. a confusing anthropic SDK exception 3 frames deep)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        bench._llm_judge_client()


def test_llm_judge_registered_in_valid_judges():
    """The runner script validates --judge against VALID_JUDGES;
    if llm_judge isn't there, the CLI flag is silently inert."""
    assert "llm_judge" in bench.VALID_JUDGES
    assert "llm_judge" in bench._JUDGES


def test_existing_judges_accept_prompt_kwarg():
    """The judge dispatch site now passes prompt= to every judge.
    Existing judges must accept it without complaint."""
    expected = {"answer": "Delhi"}
    actual = {"answer": "Delhi"}
    # Should not raise TypeError("unexpected keyword argument 'prompt'")
    bench._exact_match(expected, actual, prompt="Capital?")
    bench._rouge_l_lite(expected, actual, prompt="Capital?")
    bench._quiz_key_check(
        {"correct_option": "A"}, {"correct_option": "A"}, prompt="?",
    )
    bench._citation_check(
        {"citations": [{"source_id": "s1", "page_number": 1}]},
        {"citations": [{"source_id": "s1", "page_number": 1}]},
        prompt="?",
    )
