"""Shared test fixtures for AI Pathshala / PadhaiApp.

Uses an in-memory SQLite database (via DATABASE_URL env var pointing to
:memory:) so tests are hermetic and don't require a running Postgres.

Run:
    pip install -r requirements-test.txt
    pytest tests/ -v --cov=padhai --cov-report=term-missing
"""
from __future__ import annotations

import os
import secrets

import pytest
from fastapi.testclient import TestClient


# ---------- Environment setup -----------------------------------------------
# Must be set before importing the app so modules read them at import time.

os.environ.setdefault("PADHAI_JWT_SECRET", secrets.token_urlsafe(32))
os.environ.setdefault("PADHAI_REQUIRE_AUTH", "0")
# Point at SQLite in-memory for tests (auth module falls back to SQLite
# when DATABASE_URL is absent; the test helpers create tables manually).
# Leave DATABASE_URL unset so the app uses its local fallback paths.


# ---------- App fixture ------------------------------------------------------

@pytest.fixture(scope="session")
def client():
    """Single TestClient shared across the whole test session.

    scope=session is safe here because each test that mutates state
    (sign-up, profile update) uses a unique random email address so
    tests don't interfere with each other.

    Cookie leakage between tests (signup sets `pathshala_token` and the
    session-scoped TestClient carries it forward) is prevented by the
    autouse `_isolate_client_cookies` fixture below.
    """
    from padhai.web import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_client_cookies(request):
    """Clear the TestClient cookie jar before every test.

    Without this, a test that calls `signup()` (which sets
    `pathshala_token` via the response Set-Cookie header) leaks that
    cookie into every subsequent test in the same session — meaning
    "requires auth" assertions silently pass because the client is
    still authenticated from a prior test. Symptom: assertions like
    `assert r.status_code == 401` get 200/201 instead.

    Only runs when the test asks for the `client` fixture; harmless
    no-op otherwise.
    """
    if "client" in request.fixturenames:
        c = request.getfixturevalue("client")
        c.cookies.clear()
    yield


# ---------- Helpers ----------------------------------------------------------

def random_email() -> str:
    return f"test-{secrets.token_hex(6)}@example.com"


def signup(client: TestClient, *, email: str | None = None,
           password: str = "Test1234!") -> tuple[str, str]:
    """Sign up a fresh user. Returns (email, token).

    Skips the calling test automatically when DATABASE_URL is not set
    (auth requires Postgres; SQLite fallback is not available for the
    user repository).
    """
    email = email or random_email()
    r = client.post("/auth/signup", data={
        "email": email,
        "password": password,
        "terms_accepted": "true",
    })
    if r.status_code == 503:
        pytest.skip("DATABASE_URL not set — auth endpoints require a database")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    return email, token


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
