"""Thin wrapper around Anthropic.messages.create + llm_obs.record_call.

Every Claude-calling module in this codebase had grown the same
boilerplate around its real call:

    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(model=..., max_tokens=..., ...)
    except Exception as e:
        ...
    latency_ms = int((time.time() - started) * 1000)
    text = "".join(b.text for b in resp.content if b.type == "text")
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    llm_obs.record_call(module=..., model=..., tokens_in=tokens_in,
                        tokens_out=tokens_out, latency_ms=latency_ms,
                        user_id=..., cached=cached,
                        subscription_tier=...)

That's a lot of code per surface, and the failure mode is silent: a
new surface that forgets `record_call` makes its Anthropic spend
invisible to the cost dashboard. This module collapses the whole
pattern into one helper.

The wrapper is deliberately thin — every kwarg you'd pass to
`messages.create` flows through, the daily-cap pre-flight is opt-out,
the SDK import stays lazy. Callers still own their prompt text +
schema; we just stop them forgetting to log.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClaudeCallResult:
    """Outcome of a Claude messages.create call.

    `resp` is the raw Anthropic SDK response (so callers needing the
    full structured-output payload can reach for `resp.content`).
    `text` is the joined text of every text-block — the most common
    case. Cost + tokens come straight from `resp.usage`."""
    resp: Any
    text: str
    tokens_in: int
    tokens_out: int
    cached: bool
    latency_ms: int
    call_id: str
    cost_inr_paise: int
    model: str


def _create_with_effort_fallback(client: Any, kwargs: dict[str, Any]) -> Any:
    """Call messages.create, transparently dropping the reasoning-`effort`
    knob when the target model rejects it.

    Cheaper models (Haiku 4.5) don't support the `effort` field inside
    `output_config`; sending it returns a 400 "This model does not support
    the effort parameter." The lesson/explainer/flashcard/recap surfaces
    set `effort` uniformly regardless of which tier model they route to,
    so the Haiku-tier ones used to 500.

    A 400 is rejected before inference, so no tokens are billed — retrying
    without `effort` is free. The match is on the API's own error text, so
    this stays correct if a future model gains or loses effort support
    (no hand-maintained capability table to drift). Any other error
    re-raises unchanged."""
    try:
        return client.messages.create(**kwargs)
    except Exception as e:
        oc = kwargs.get("output_config")
        if not (isinstance(oc, dict) and "effort" in oc
                and "effort parameter" in str(e)):
            raise
        retry = dict(kwargs)
        stripped = {k: v for k, v in oc.items() if k != "effort"}
        # output_config={"effort": ...} alone → drop the now-empty dict
        # rather than send output_config={} (which some models reject).
        if stripped:
            retry["output_config"] = stripped
        else:
            retry.pop("output_config", None)
        return client.messages.create(**retry)


def call_claude(
    *,
    module: str,
    prompt_version: str,
    model: str,
    user_id: str | None = None,
    subscription_tier: str | None = None,
    enforce_cap: bool = True,
    client: Any | None = None,
    **messages_create_kwargs: Any,
) -> ClaudeCallResult:
    """Make one Claude call and log it to llm_obs in one go.

    Required:
      • `module` — short name for the LLM-obs dashboard ('tutor',
        'essay_grader', 'lesson', …)
      • `prompt_version` — caller-owned version string ('v1',
        'v2-grounded', …) so prompt-AB-tests show up cleanly
      • `model` — Anthropic model id; also forwarded to messages.create
        if not already in `messages_create_kwargs`
      • everything Anthropic.messages.create wants:
        max_tokens, system, messages, output_config, …

    Optional:
      • `user_id` + `subscription_tier` — when both set, the daily-cap
        helper fires before the Claude call. Caller catches
        `llm_obs.BudgetExceeded` to render a graceful fallback.
      • `enforce_cap=False` — for non-user-attributable system calls
        (e.g. cron-rendered audio recaps) that shouldn't hit a cap.
      • `client` — inject an Anthropic instance (or a fake) for tests.

    Raises:
      • `llm_obs.BudgetExceeded` — caller is over their daily cap.
      • `RuntimeError` — anthropic SDK missing / API key missing /
        Claude call itself failed. The caller wraps these with
        surface-specific fallback copy.
    """
    from . import llm_obs

    if enforce_cap and user_id:
        # Will raise BudgetExceeded for caller to catch
        llm_obs.check_daily_cap(
            user_id=user_id, subscription_tier=subscription_tier,
        )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed; pip install anthropic"
        ) from e

    if client is None:
        client = Anthropic()
    messages_create_kwargs.setdefault("model", model)

    started = time.time()
    try:
        resp = _create_with_effort_fallback(client, messages_create_kwargs)
    except Exception as e:
        raise RuntimeError(f"Claude call failed: {e}") from e
    latency_ms = int((time.time() - started) * 1000)

    text = "".join(
        getattr(b, "text", "") for b in resp.content
        if getattr(b, "type", "") == "text"
    )
    tokens_in = int(getattr(resp.usage, "input_tokens", 0) or 0)
    tokens_out = int(getattr(resp.usage, "output_tokens", 0) or 0)
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    cost_inr_paise = llm_obs.estimate_cost_paise(
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        cached=cached,
    )
    call_id = llm_obs.record_call(
        module=module,
        prompt_version=prompt_version,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        user_id=user_id,
        cached=cached,
        cost_inr_paise=cost_inr_paise,
        subscription_tier=subscription_tier,
    )
    return ClaudeCallResult(
        resp=resp,
        text=text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached=cached,
        latency_ms=latency_ms,
        call_id=call_id,
        cost_inr_paise=cost_inr_paise,
        model=model,
    )
