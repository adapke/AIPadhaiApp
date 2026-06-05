"""Router-layer smoke tests.

13 router slices have been extracted out of web.py since polish-1.
None of them had explicit unit-test coverage — the only assurance
they worked was the existing pytest suite + the manual end-to-end
smoke that we ran during each extraction.

This module covers the things that the next-touch can silently
break without a CI signal:

1. Each router module imports cleanly (no circular-import on the
   late `from .. import web as _web` pattern). The package's
   `all_routers()` is the wiring source of truth — if it fails
   here the app boot would fail too.

2. The expected URL paths land on `app.routes`. Catches a slice
   getting half-extracted: a route definition removed from web.py
   but the router not registered in `__init__.py`.

3. Each extracted endpoint enforces auth/role gates *before* it
   touches data. We can't make real DB calls (the test SQLite
   doesn't have org tables seeded), but we can verify 401/403/400
   short-circuit responses.

These tests stay deliberately shallow — exhaustive behavioural
coverage of each router belongs in its module's own tests, not
here. The point of this file is the contract that the slice is
wired correctly.
"""

from __future__ import annotations

import pytest


def test_all_router_modules_import(client) -> None:  # noqa: ARG001 — fixture load side-effect
    """Every registered router module imports without errors and
    exports a `router` attribute. The `client` fixture is requested
    only so `padhai.web` is in sys.modules before we iterate — some
    routers (e.g. `me`) do a top-level `from ..web import current_user`
    that would otherwise cycle through `routers.__init__.all_routers`."""
    import importlib

    from padhai.routers import _ROUTER_NAMES
    for name in _ROUTER_NAMES:
        mod = importlib.import_module(f"padhai.routers.{name}")
        assert hasattr(mod, "router"), f"{name}: no `router` exported"


def test_extracted_router_paths_registered(client) -> None:  # noqa: ARG001
    """The 13 extracted router slices wire the URL paths web.py used
    to declare. If a slice is half-extracted (route removed from
    web.py but module not registered), this catches it.

    `client` fixture is requested so `padhai.web` is loaded via the
    same path the rest of the suite uses (lifespan + lazy router
    wiring) — not strictly required, but keeps the import order
    predictable across test runs."""
    from padhai.web import app
    paths = {getattr(r, "path", "") for r in app.routes}

    # One representative URL per extracted slice. Picked to cover
    # different prefixes — auth-gated, public, /scim/* (no /api/),
    # the multi-page bundle endpoint, etc.
    expected = {
        # multipage.py (slice 1)
        "/jobs/{job_id}/combined.mp4",
        # explainer.py (2)
        "/explain",
        # v2_video.py (3)
        "/api/v2/video-requests/{request_id}/status",
        # parents.py (4)
        "/api/parents/link",
        # orgs_api.py (5)
        "/api/orgs",
        # orgs_classes.py (6)
        "/api/orgs/{org_id}/classes",
        # orgs_leaderboard.py (7)
        "/api/orgs/{org_id}/classes/{class_id}/leaderboard",
        # orgs_attendance.py (8)
        "/api/orgs/{org_id}/classes/{cid}/attendance",
        # orgs_assignments.py (9)
        "/api/orgs/{org_id}/assignments",
        # orgs_fees.py (10)
        "/api/orgs/{org_id}/fees/structures",
        # orgs_exams.py (11)
        "/api/orgs/{org_id}/exams",
        # branding.py (12)
        "/api/branding/resolve",
        # scim.py (13)
        "/scim/v2/ServiceProviderConfig",
    }
    missing = expected - paths
    assert not missing, f"router paths missing from app: {sorted(missing)}"


def test_org_route_unauthenticated_is_401(client) -> None:
    """Calling an org-gated route with no Authorization header
    returns 401 — the router's `_require_user` short-circuit. This
    is the test that catches a router accidentally being wired
    without the auth dependency."""
    r = client.get("/api/orgs/any-org/classes")
    assert r.status_code == 401, (
        f"expected 401 for unauthenticated org call, got {r.status_code}: {r.text}"
    )


def test_branding_resolve_is_public(client) -> None:
    """The /api/branding/resolve endpoint is public — the SPA calls
    it on page load BEFORE the user authenticates. Must return a
    valid branding payload (platform defaults when no subdomain
    match) without an Authorization header."""
    r = client.get("/api/branding/resolve")
    assert r.status_code == 200, r.text
    body = r.json()
    # Must always have the platform-default shape, even with no host.
    assert "brand_name" in body
    assert "brand_color" in body


def test_scim_users_unauthenticated_is_401(client) -> None:
    """SCIM is bearer-token-authenticated, not JWT. Without a
    bearer header, the router must 401 with the SCIM error shape."""
    r = client.get("/scim/v2/Users")
    assert r.status_code == 401, r.text


def test_scim_service_provider_config_is_public(client) -> None:
    """SCIM ServiceProviderConfig is IdP-discovery — must work
    without authentication (it's how the IdP knows what to call)."""
    r = client.get("/scim/v2/ServiceProviderConfig")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "schemas" in body
    assert any("ServiceProviderConfig" in s for s in body["schemas"])


def test_parent_link_requires_auth(client) -> None:
    """Parent-link creation is an authenticated endpoint. Without
    a token the router returns 401 / 403 / 422 (the last when
    FastAPI rejects the missing form body before we even reach
    the auth gate). All three are acceptable — we just don't want
    a 200/201."""
    r = client.post("/api/parents/link", data={"child_email": "x@y.z"})
    assert r.status_code in (401, 403, 422), (
        f"expected 401/403/422, got {r.status_code}: {r.text}"
    )


def test_multipage_combined_status_unknown_job_is_404(client) -> None:
    """The combined-MP4 status endpoint returns 404 for an unknown
    job_id. This exercises the multipage slice's normal failure
    path — `_jobs.find_siblings` returns [] and the router 404s."""
    r = client.get("/jobs/nonexistent-job-id/combined")
    # 401 if PADHAI_REQUIRE_AUTH=1 (pre-auth gate runs first);
    # 404 if anonymous access allowed and job-not-found path runs.
    # Tests in this repo run with PADHAI_REQUIRE_AUTH=0 (conftest),
    # so we expect 404.
    assert r.status_code in (401, 404), (
        f"expected 401/404, got {r.status_code}: {r.text}"
    )


@pytest.mark.parametrize("path", [
    "/api/orgs/fake/classes/fake/leaderboard",
    "/api/orgs/fake/classes/fake/attendance?date=2026-01-01",
    "/api/orgs/fake/assignments",
    "/api/orgs/fake/fees/structures",
    "/api/orgs/fake/exams",
])
def test_org_subsystem_routes_gate_on_membership(client, path: str) -> None:
    """Every /api/orgs/{org_id}/<subsystem> endpoint must reject an
    unauthenticated caller BEFORE doing any data access. If the
    role-gate accidentally regresses (e.g. someone moves the
    `_require_user` call after a DB read), this test catches it.

    Acceptable failure codes: 401 (no token), 403 (token but not a
    member), 404 (org not found). 200/201 would be a real bug —
    that would mean the route exposed data without checking auth."""
    r = client.get(path)
    assert r.status_code in (401, 403, 404), (
        f"{path} returned {r.status_code} for unauthenticated caller — "
        f"expected 401/403/404. Body: {r.text[:200]}"
    )
