"""Regression guard for the Haiku `effort` 500.

The lesson / explainer / flashcard / recap surfaces all set a reasoning
`effort` knob inside `output_config`, but route to different tier models.
Cheaper models (Haiku 4.5) reject `effort` with a 400 "This model does
not support the effort parameter." — which used to surface as a 500 on
POST /explain (EXPLAINER_MODEL = HAIKU_MODEL).

`llm_call._create_with_effort_fallback` strips `effort` and retries once
when (and only when) the API rejects it. A 400 bills no tokens, so the
retry is free. These tests pin that behaviour with a fake client — no
real Anthropic call, no key needed.
"""
from __future__ import annotations

import os

import pytest

from padhai import llm_call


class _Resp:
    """Minimal stand-in for an Anthropic SDK response."""
    class _Block:
        type = "text"
        text = "ok"

    class _Usage:
        input_tokens = 10
        output_tokens = 5
        cache_read_input_tokens = 0

    content = [_Block()]  # noqa: RUF012 — test fixture, read-only
    usage = _Usage()


class _EffortRejectingClient:
    """Rejects any create call that carries output_config.effort (like
    Haiku), succeeds once effort is gone. Records every call's kwargs."""

    def __init__(self):
        self.calls: list[dict] = []
        self.messages = self  # client.messages.create -> self.create

    def create(self, **kwargs):
        self.calls.append(kwargs)
        oc = kwargs.get("output_config") or {}
        if isinstance(oc, dict) and "effort" in oc:
            raise RuntimeError(
                "Error code: 400 - {'type': 'error', 'error': {'type': "
                "'invalid_request_error', 'message': 'This model does not "
                "support the effort parameter.'}}"
            )
        return _Resp()


class _AlwaysFailsClient:
    """Fails with an unrelated error — fallback must NOT swallow it."""
    def __init__(self):
        self.messages = self
        self.calls = 0

    def create(self, **kwargs):  # noqa: ARG002 — fake ignores args
        self.calls += 1
        raise RuntimeError("Error code: 529 - overloaded")


def test_effort_stripped_and_retried_when_model_rejects_it():
    client = _EffortRejectingClient()
    out = llm_call._create_with_effort_fallback(
        client,
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "output_config": {
                "format": {"type": "json_schema", "schema": {}},
                "effort": "low",
            },
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert out is not None
    # Two attempts: first with effort (rejected), second without.
    assert len(client.calls) == 2
    assert "effort" in client.calls[0]["output_config"]
    # Retry kept the rest of output_config (the json_schema format) intact.
    assert "effort" not in client.calls[1]["output_config"]
    assert client.calls[1]["output_config"]["format"]["type"] == "json_schema"


def test_output_config_dropped_entirely_when_only_effort():
    """output_config={"effort": ...} alone -> retry sends no output_config
    at all (not an empty {} which some models reject)."""
    client = _EffortRejectingClient()
    llm_call._create_with_effort_fallback(
        client,
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert len(client.calls) == 2
    assert "output_config" not in client.calls[1]


def test_unrelated_error_is_not_swallowed():
    client = _AlwaysFailsClient()
    with pytest.raises(RuntimeError, match="529"):
        llm_call._create_with_effort_fallback(
            client,
            {
                "model": "claude-opus-4-8",
                "max_tokens": 100,
                "output_config": {"effort": "high"},
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    # No retry on a non-effort error.
    assert client.calls == 1


def test_no_effort_means_single_call():
    """A call without effort should pass straight through, no retry."""
    client = _EffortRejectingClient()
    llm_call._create_with_effort_fallback(
        client,
        {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "output_config": {"format": {"type": "json_schema", "schema": {}}},
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert len(client.calls) == 1


def test_call_claude_end_to_end_recovers_from_effort_rejection(monkeypatch):
    """The full call_claude path (with the fake client injected) returns a
    real result despite the first attempt's effort rejection."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    client = _EffortRejectingClient()
    result = llm_call.call_claude(
        module="explainer",
        prompt_version="v1",
        model="claude-haiku-4-5-20251001",
        enforce_cap=False,
        client=client,
        max_tokens=100,
        output_config={
            "format": {"type": "json_schema", "schema": {}},
            "effort": "low",
        },
        messages=[{"role": "user", "content": "Explain photosynthesis."}],
    )
    assert result.text == "ok"
    assert len(client.calls) == 2
