"""Simple in-process rate limiter.

Token-bucket per (key, route) — typically key=client_ip. No external
deps. When we add Redis (G2 cutover), this gets swapped for a Redis-
backed limiter so multi-replica deploys share state — but the
interface here stays the same.

Use case: defend unauthenticated public preview endpoints
(`/api/render/math`, `/api/render/diagram`, `/api/curriculum/score`)
against bots + accidental loops. Not for auth-gated endpoints —
those should rely on per-user quotas elsewhere.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class TokenBucket:
    """Per-key rate limiter. `capacity` tokens max; refills at `rate`
    tokens/sec. Each `try_consume()` call costs 1 token by default.

    Thread-safe (web tier serves concurrent requests).
    """

    def __init__(self, *, capacity: float, rate_per_sec: float):
        self._capacity = capacity
        self._rate = rate_per_sec
        self._buckets: dict[str, _Bucket] = defaultdict(
            lambda: _Bucket(tokens=capacity, last_refill=time.monotonic()),
        )
        self._lock = threading.Lock()

    def try_consume(self, key: str, *, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            b = self._buckets[key]
            elapsed = now - b.last_refill
            b.tokens = min(self._capacity, b.tokens + elapsed * self._rate)
            b.last_refill = now
            if b.tokens >= cost:
                b.tokens -= cost
                return True
            return False

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


# Named buckets for the public preview endpoints. Tuned for human-
# scale interactive use; bots get 429-ed quickly.
preview_math = TokenBucket(capacity=30, rate_per_sec=1.0)        # 30 burst, then 1/s
preview_diagram = TokenBucket(capacity=20, rate_per_sec=0.5)     # 20 burst, then 1/2s
preview_scorer = TokenBucket(capacity=15, rate_per_sec=0.25)     # 15 burst, then 1/4s


def client_ip_from_request(request) -> str:
    """Extract caller IP for keying. Same logic as
    `audit.actor_from_request` — respects X-Forwarded-For when behind
    Cloudflare."""
    try:
        headers = request.headers
        xff = headers.get("x-forwarded-for") if headers else None
        if xff:
            return xff.split(",")[0].strip()
        if request.client:
            return request.client.host
    except Exception:  # noqa: BLE001
        pass
    return "unknown"
