"""prod-6 — Sentry integration end-to-end regression tests.

Locks the gates around the Sentry init path so a future PR can't
silently break it:

  * init_sentry returns False when SENTRY_DSN isn't set (so dev
    startups never try to import sentry_sdk).
  * _maybe_capture_exception is a no-op before init (no crash on
    early exception paths).
  * /__sentry_test exists, raises in non-prod, and is gated in prod.
  * The before_send hook drops noisy 4xx events.

Real DSN ping isn't tested here — that requires a live Sentry
account and is part of the post-deploy validation in
PRODUCTION_CHECKLIST.md.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def fresh_obs(monkeypatch):
    """Reload observability so the module-level _sentry_initialised
    flag starts at False for each test."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PADHAI_SENTRY_TEST_TOKEN", raising=False)
    from padhai import observability
    importlib.reload(observability)
    return observability


def test_init_sentry_returns_false_without_dsn(fresh_obs):
    assert fresh_obs.init_sentry() is False
    assert fresh_obs._sentry_initialised is False


def test_capture_exception_is_noop_before_init(fresh_obs):
    """The middleware calls this on every exception. It must NEVER
    raise when Sentry isn't configured — otherwise a 500 in dev
    becomes a 500 inside a 500 handler."""
    # Should not raise
    fresh_obs._maybe_capture_exception(RuntimeError("dev path"))


def test_install_does_not_crash_without_sentry(fresh_obs):
    """install() must succeed on a vanilla dev box where SENTRY_DSN
    isn't set and sentry-sdk may not even be installed."""
    app = FastAPI()
    fresh_obs.install(app)
    # /__sentry_test was registered even when Sentry isn't initialised
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert "/__sentry_test" in paths


def test_sentry_test_route_raises_in_non_production(fresh_obs):
    """In dev / staging the endpoint fires the test exception
    unconditionally — devs need to be able to verify wiring without
    threading a token through curl."""
    app = FastAPI()
    fresh_obs.install(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/__sentry_test")
    assert resp.status_code == 500, resp.text


def test_sentry_test_route_404s_in_production_without_token(
    fresh_obs, monkeypatch,
):
    """Without PADHAI_SENTRY_TEST_TOKEN set, the prod endpoint must
    return 404 — never 500. Otherwise it's a DoS vector (Sentry
    quota burn) for anonymous traffic."""
    monkeypatch.setenv("APP_ENV", "production")
    app = FastAPI()
    fresh_obs.install(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/__sentry_test")
    assert resp.status_code == 404


def test_sentry_test_route_404s_in_production_with_wrong_token(
    fresh_obs, monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PADHAI_SENTRY_TEST_TOKEN", "real-token-xyz")
    app = FastAPI()
    fresh_obs.install(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/__sentry_test",
        headers={"X-Sentry-Test-Token": "wrong-token"},
    )
    assert resp.status_code == 404


def test_sentry_test_route_fires_in_production_with_correct_token(
    fresh_obs, monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PADHAI_SENTRY_TEST_TOKEN", "real-token-xyz")
    app = FastAPI()
    fresh_obs.install(app)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get(
        "/__sentry_test",
        headers={"X-Sentry-Test-Token": "real-token-xyz"},
    )
    assert resp.status_code == 500


def test_before_send_drops_default_noise_statuses(fresh_obs):
    """The before_send hook drops 401 / 403 / 404 / 405 / 422 / 429
    by default — those are normal user paths, not real errors."""
    hook = fresh_obs._build_before_send()
    for sc in (401, 403, 404, 405, 422, 429):
        evt = {"tags": {"status_code": str(sc)}}
        assert hook(evt, None) is None, f"should drop {sc}"


def test_before_send_keeps_5xx(fresh_obs):
    hook = fresh_obs._build_before_send()
    evt = {"tags": {"status_code": "500"}}
    assert hook(evt, None) is evt


def test_before_send_keeps_events_without_status(fresh_obs):
    """Background workers / startup exceptions don't have a status
    tag. Never drop those."""
    hook = fresh_obs._build_before_send()
    assert hook({}, None) == {}
    assert hook({"tags": {}}, None) == {"tags": {}}


def test_before_send_respects_env_override(fresh_obs, monkeypatch):
    """Ops can tune the drop list via SENTRY_DROP_STATUSES."""
    monkeypatch.setenv("SENTRY_DROP_STATUSES", "418")
    hook = fresh_obs._build_before_send()
    assert hook({"tags": {"status_code": "418"}}, None) is None
    # 404 is no longer dropped because the override replaces the list
    assert hook({"tags": {"status_code": "404"}}, None) is not None
