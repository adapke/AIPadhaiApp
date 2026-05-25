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

import ipaddress
import threading
import time
from collections import defaultdict
from dataclasses import dataclass

_MAX_BUCKETS = 100_000  # hard cap to prevent memory exhaustion from spoofed keys


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
            if key not in self._buckets and len(self._buckets) >= _MAX_BUCKETS:
                # Bucket table full — fail open to avoid blocking legitimate
                # users, but log once so ops can notice and scale the limit.
                return True
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

# AI generation endpoints (lesson render, chat, explain). Keyed by
# user_id for authenticated users; client IP for anonymous. Generous
# burst for normal use; prevents runaway loops and accidental DDoS.
ai_generation = TokenBucket(capacity=10, rate_per_sec=0.1)       # 10 burst, then 1/10s
login = TokenBucket(capacity=5, rate_per_sec=0.05)               # 5 burst, then 1/20s — brute-force guard
file_upload = TokenBucket(capacity=20, rate_per_sec=0.5)         # 20 burst, then 1/2s


def _validate_ip(raw: str) -> str | None:
    """Return the IP string if it is a valid IPv4/IPv6 address, else None."""
    try:
        return str(ipaddress.ip_address(raw.strip()))
    except ValueError:
        return None


def client_ip_from_request(request) -> str:
    """Extract caller IP for rate-limit keying.

    Priority order:
      1. CF-Connecting-IP (set by Cloudflare — authenticated, not spoofable)
      2. X-Forwarded-For first hop, only when it parses as a valid IP
      3. TCP connection host (request.client.host)

    Never trusts arbitrary string values as keys — an invalid header
    falls through to the next source so attackers can't create unlimited
    bucket entries with synthetic keys.
    """
    try:
        headers = request.headers
        if headers:
            cf_ip = headers.get("cf-connecting-ip")
            if cf_ip and _validate_ip(cf_ip):
                return cf_ip.strip()
            xff = headers.get("x-forwarded-for")
            if xff:
                candidate = _validate_ip(xff.split(",")[0])
                if candidate:
                    return candidate
        if request.client:
            return request.client.host
    except Exception:  # noqa: BLE001
        pass
    return "unknown"
