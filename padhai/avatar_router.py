"""Talking-head provider routing with fallback + observability.

The existing `padhai.talking_head.get_provider()` resolves ONE provider
based on env vars + the explicit selector. That's right for tier-aware
routing (a paying customer's Synthesia contract wins). But it doesn't
handle the case where the chosen provider is *down* or *quota-exhausted*
right now — we'd 500 the request even though three other providers
could have rendered the same clip.

This module wraps the existing selector with:

  1. A configurable provider chain (set via PADHAI_AVATAR_FALLBACK env
     var, comma-separated). When the primary errors, we walk the chain
     and try each in order; the FIRST success wins.

  2. Per-provider counters (success / failure / total latency) in
     memory. Surfaced at GET /api/avatar-stats for ops to see which
     provider is healthy. Resets per-process, same as the
     observability metrics.

  3. A short circuit-breaker — if a provider has failed N times in
     a row in the last 60s, skip it for 60s without attempting. Keeps
     us from hammering a degraded provider on every render.

The router is deliberately NOT in talking_head.py because:
  - It introduces in-memory state (counters) we don't want in the
    pure-provider module.
  - Future work: persist counters to the DB so multi-instance deploys
    share circuit-breaker state. That belongs in its own module.

Public API:
  RouterProvider() — a TalkingHeadProvider that routes through the
                     chain on every render_clip call.
  fallback_chain() — read the chain in resolution order.
  snapshot() — current stats; admin / /metrics consumer.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import talking_head as _th


# ---------- chain resolution ----------

# Provider names in the order the router will try them when the
# primary fails. Override via env to match your subscription mix.
DEFAULT_FALLBACK_CHAIN = (
    "wav2lip", "deepbrain", "synthesia", "tavus", "heygen", "d-id", "cartoon",
)


def fallback_chain() -> tuple[str, ...]:
    raw = os.environ.get("PADHAI_AVATAR_FALLBACK", "").strip()
    if raw:
        return tuple(p.strip() for p in raw.split(",") if p.strip())
    return DEFAULT_FALLBACK_CHAIN


def _configured(provider_name: str) -> bool:
    """Check whether a provider's env vars are set. Mirrors the
    detection logic in talking_head.get_provider but as a predicate
    rather than a builder."""
    p = provider_name.lower()
    if p == "cartoon":
        return True
    if p == "wav2lip":
        return all(os.environ.get(k) for k in (
            "WAV2LIP_REPO_PATH", "WAV2LIP_CHECKPOINT", "WAV2LIP_SOURCE_PHOTO",
        ))
    if p == "deepbrain":
        return bool(os.environ.get("DEEPBRAIN_API_KEY") and
                    os.environ.get("DEEPBRAIN_MODEL_ID"))
    if p == "synthesia":
        return bool(os.environ.get("SYNTHESIA_API_KEY"))
    if p == "tavus":
        return bool(os.environ.get("TAVUS_API_KEY") and
                    os.environ.get("TAVUS_REPLICA_ID"))
    if p == "heygen":
        return bool(os.environ.get("HEYGEN_API_KEY"))
    if p == "d-id":
        return bool(os.environ.get("DID_API_KEY") and
                    os.environ.get("DID_SOURCE_PHOTO_URL"))
    return False


def configured_providers() -> list[str]:
    """List of provider names whose env vars are set. Used by the
    `/api/avatar-providers` endpoint so the UI can show what's
    actually available on this deploy."""
    return [p for p in DEFAULT_FALLBACK_CHAIN if _configured(p)]


# ---------- in-memory stats ----------

@dataclass
class _ProviderStats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_failure_at: float = 0.0
    total_latency_ms: float = 0.0


_lock = threading.Lock()
_stats: dict[str, _ProviderStats] = defaultdict(_ProviderStats)


# Skip a provider for this many seconds after this many consecutive
# failures. Conservative defaults: 3 failures → 60s cool-off.
_FAIL_THRESHOLD = 3
_COOL_OFF_SECONDS = 60


def _is_circuit_open(name: str) -> bool:
    s = _stats[name]
    if s.consecutive_failures < _FAIL_THRESHOLD:
        return False
    if time.time() - s.last_failure_at > _COOL_OFF_SECONDS:
        return False
    return True


def snapshot() -> dict:
    out_providers = []
    with _lock:
        for name, s in _stats.items():
            avg_latency_ms = (
                round(s.total_latency_ms / s.successes, 1)
                if s.successes else None
            )
            out_providers.append({
                "name": name,
                "attempts": s.attempts,
                "successes": s.successes,
                "failures": s.failures,
                "success_rate_pct": (
                    round(s.successes * 100 / s.attempts, 1)
                    if s.attempts else None
                ),
                "consecutive_failures": s.consecutive_failures,
                "circuit_open": _is_circuit_open(name),
                "avg_latency_ms": avg_latency_ms,
            })
    return {
        "providers": sorted(out_providers, key=lambda p: -p["attempts"]),
        "chain": list(fallback_chain()),
        "configured": configured_providers(),
    }


def reset_stats() -> None:
    """Used by tests + the admin "reset counters" button."""
    with _lock:
        _stats.clear()


# ---------- the router ----------

class RouterProvider:
    """A TalkingHeadProvider that walks the fallback chain on each
    render_clip call. The first provider whose env vars are set AND
    whose circuit-breaker is closed gets the attempt. On exception
    (provider down / quota / network), we record the failure and
    try the next.

    Caches the *successful* provider for the rest of THIS RouterProvider
    instance's life so repeated clips in the same render job don't
    re-walk the chain. (Different render jobs get a fresh instance
    via `get_router()`.)
    """

    name = "router"
    in_process = False  # whichever provider we route to handles its own

    def __init__(self, *, primary: str | None = None):
        self._primary = primary
        self._sticky: str | None = None

    def _ordered_candidates(self) -> list[str]:
        chain = list(fallback_chain())
        # Move the explicit primary to the front (if any) — it's the
        # user's subscription tier so it should win when healthy.
        if self._primary and self._primary in chain:
            chain = [self._primary] + [c for c in chain if c != self._primary]
        elif self._primary:
            chain = [self._primary] + chain
        # Then sticky (last success in this instance) — minimises
        # re-walking when both work.
        if self._sticky and self._sticky in chain and self._sticky != chain[0]:
            chain = [self._sticky] + [c for c in chain if c != self._sticky]
        return chain

    def render_clip(self, audio_path, output_path, *args, **kwargs):
        last_error: Exception | None = None
        for name in self._ordered_candidates():
            if not _configured(name):
                continue
            if _is_circuit_open(name):
                continue
            provider = _th._build_named_provider(name)  # noqa: SLF001
            with _lock:
                _stats[name].attempts += 1
            start = time.perf_counter()
            try:
                result = provider.render_clip(audio_path, output_path,
                                              *args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000.0
                with _lock:
                    s = _stats[name]
                    s.successes += 1
                    s.consecutive_failures = 0
                    s.total_latency_ms += latency_ms
                self._sticky = name
                return result
            except Exception as e:  # noqa: BLE001
                with _lock:
                    s = _stats[name]
                    s.failures += 1
                    s.consecutive_failures += 1
                    s.last_failure_at = time.time()
                last_error = e
                # Try next in chain
                continue
        # Every provider in the chain failed or was unavailable.
        raise RuntimeError(
            f"all avatar providers failed in the fallback chain; "
            f"last error: {last_error}",
        )


def get_router(*, primary: str | None = None) -> RouterProvider:
    """Construct a fresh router for one render job. Pass `primary`
    to honour the user's subscription tier."""
    return RouterProvider(primary=primary)
