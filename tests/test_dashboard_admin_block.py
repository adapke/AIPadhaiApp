"""prod-77 — Tests for the /api/me/dashboard admin block contract.

The /api/me/dashboard endpoint returns an additional `admin` block for
admin users only. Lock the contract:

  - Anonymous → 401 (auth required for the whole endpoint)
  - Non-admin signed-in user → no `admin` key in the response
  - Admin user → `admin.pending_curator_count` + `admin.curator_url`
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def dev_admin_client(tmp_path, monkeypatch):
    """Reload web.py with no DATABASE_URL so the dev-fallback in
    api_deps.require_admin_role treats any signed-in user as admin.
    Pin SQLite to tmp_path so tests don't touch the dev DB."""
    db = tmp_path / "dashboard_test.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import db as _db
    from padhai import web as _web
    importlib.reload(_db)
    importlib.reload(_auth)
    importlib.reload(_web)
    yield TestClient(_web.app)


def _signup(client: TestClient, email: str | None = None) -> str:
    """Sign up a user and return their bearer token. Skips if 503."""
    if email is None:
        email = f"dashtest+{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if r.status_code == 503:
        pytest.skip("auth not configured")
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


def test_dashboard_anonymous_returns_401(dev_admin_client):
    """prod-77 — anonymous callers get 401, not a silent empty block."""
    r = dev_admin_client.get("/api/me/dashboard")
    assert r.status_code in (401, 403), (
        f"anonymous should be blocked; got {r.status_code}: {r.text[:200]}"
    )


def test_dashboard_admin_block_present_for_admin_user(dev_admin_client):
    """prod-58/77 — admin user sees the admin block with curator count.
    Dev-fallback treats any signed-in user as admin since DATABASE_URL
    is unset in this fixture."""
    tok = _signup(dev_admin_client)
    r = dev_admin_client.get(
        "/api/me/dashboard",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert "admin" in d, (
        "admin block missing for dev-fallback admin user; "
        f"keys: {list(d.keys())}"
    )
    admin = d["admin"]
    # Contract
    assert "pending_curator_count" in admin
    assert "curator_url" in admin
    assert admin["curator_url"] == "/admin/concept-curator"
    assert isinstance(admin["pending_curator_count"], int)
    assert admin["pending_curator_count"] >= 0


def test_dashboard_admin_block_count_reflects_channel_seed(dev_admin_client):
    """prod-77 — pending_curator_count must equal the number of
    channel_seed rows in the catalog (the queue length)."""
    from padhai import concept_videos as cv

    # Seed 3 channel_seed, 1 verified
    cv.upsert(
        concept="X1", source="youtube",
        source_url="https://www.youtube.com/watch?v=xxxxxxxxxxx",
        title="x1", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="X2", source="youtube",
        source_url="https://www.youtube.com/watch?v=yyyyyyyyyyy",
        title="x2", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="X3", source="youtube",
        source_url="https://www.youtube.com/watch?v=zzzzzzzzzzz",
        title="x3", quality_tier="channel_seed",
    )
    cv.upsert(
        concept="V1", source="youtube",
        source_url="https://www.youtube.com/watch?v=vvvvvvvvvvv",
        title="v1", quality_tier="verified",
    )

    tok = _signup(dev_admin_client)
    r = dev_admin_client.get(
        "/api/me/dashboard",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200
    admin = r.json().get("admin") or {}
    assert admin.get("pending_curator_count") == 3, admin


def test_admin_block_returns_none_for_non_admin_unit(monkeypatch):
    """prod-77 — the _admin_block() helper must return None when the
    user is not an admin (so the dashboard handler omits it from the
    response). Unit-level test that doesn't need Postgres.
    """
    from fastapi import HTTPException

    from padhai.routers import dashboard

    def _fake_require_admin_role(user):  # noqa: ARG001
        raise HTTPException(403, "admin only")

    monkeypatch.setattr(
        "padhai.api_deps.require_admin_role",
        _fake_require_admin_role,
    )

    class FakeUser:
        id = "fake-user-id"
        email = "student@example.com"

    out = dashboard._admin_block(FakeUser())
    assert out is None, f"non-admin must get None, got {out!r}"


def test_admin_block_returns_dict_for_admin_unit(monkeypatch):
    """prod-77 — when the admin gate passes, _admin_block returns the
    contract dict with both required keys."""
    from padhai.routers import dashboard

    # No-op admin gate (always passes)
    monkeypatch.setattr(
        "padhai.api_deps.require_admin_role",
        lambda user: None,  # noqa: ARG005
    )
    # Stub the curator-queue helper so the test is hermetic.
    monkeypatch.setattr(
        "padhai.concept_videos.list_curator_queue",
        lambda **kw: ["row1", "row2"],  # noqa: ARG005
    )

    class FakeUser:
        id = "fake-admin-id"
        email = "admin@example.com"

    out = dashboard._admin_block(FakeUser())
    assert isinstance(out, dict)
    assert out["pending_curator_count"] == 2
    assert out["curator_url"] == "/admin/concept-curator"
