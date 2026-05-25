"""Rate limiter unit tests (no HTTP server needed)."""
from __future__ import annotations

import importlib
import sys


def _fresh_limiter():
    """Import rate_limit with a clean module state each call."""
    # Remove cached module so _buckets is reset between tests.
    for key in list(sys.modules.keys()):
        if "rate_limit" in key:
            del sys.modules[key]
    from padhai import rate_limit as rl
    return rl


class TestTryConsume:
    def test_allows_initial_requests(self):
        rl = _fresh_limiter()
        # Brand-new bucket: first request should succeed.
        assert rl.try_consume("1.2.3.4", cost=1) is True

    def test_exhausts_bucket(self):
        rl = _fresh_limiter()
        # Drain the bucket completely (capacity defaults to 60).
        capacity = 60
        for _ in range(capacity):
            rl.try_consume("10.0.0.1", cost=1)
        # Next request should be denied.
        assert rl.try_consume("10.0.0.1", cost=1) is False

    def test_different_ips_independent(self):
        rl = _fresh_limiter()
        capacity = 60
        for _ in range(capacity):
            rl.try_consume("192.168.1.1", cost=1)
        # A different IP should still have a full bucket.
        assert rl.try_consume("192.168.1.2", cost=1) is True

    def test_invalid_ip_fails_open(self):
        rl = _fresh_limiter()
        # Invalid IPs should not raise; they fail open (allow).
        result = rl.try_consume("not_an_ip", cost=1)
        assert result is True


class TestClientIpFromRequest:
    def test_extracts_cf_connecting_ip(self):
        rl = _fresh_limiter()

        class FakeRequest:
            headers = {"cf-connecting-ip": "1.2.3.4"}
            client = None

        ip = rl.client_ip_from_request(FakeRequest())
        assert ip == "1.2.3.4"

    def test_falls_back_to_client_host(self):
        rl = _fresh_limiter()

        class FakeClient:
            host = "5.6.7.8"

        class FakeRequest:
            headers = {}
            client = FakeClient()

        ip = rl.client_ip_from_request(FakeRequest())
        assert ip == "5.6.7.8"

    def test_returns_unknown_when_no_ip(self):
        rl = _fresh_limiter()

        class FakeRequest:
            headers = {}
            client = None

        ip = rl.client_ip_from_request(FakeRequest())
        assert ip == "unknown"
