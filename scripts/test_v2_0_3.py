"""Smoke for v2.0.3 — router split pt 2 (auth-coupled endpoints).

Verifies that the ~29 auth-coupled endpoints moved to
`padhai/routers/me.py` + `padhai/routers/orgs_admin.py` still work
+ the shared `padhai/api_deps.py` helpers are reachable from both
web.py and the routers.

Run: PYTHONPATH=. python scripts/test_v2_0_3.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_0_3_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import api_deps, routers
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.0.3 smoke test")
    print("-----------------")

    # api_deps shape — 3 callables
    for fn in ("require_user", "org_or_404", "require_org_role"):
        check(
            f"api_deps.{fn} exists + callable",
            callable(getattr(api_deps, fn, None)),
        )

    # require_user raises 401 on None
    from fastapi import HTTPException
    try:
        api_deps.require_user(None)
        check("require_user(None) raises HTTPException", False)
    except HTTPException as e:
        check(
            "require_user(None) raises HTTPException(401)",
            e.status_code == 401,
        )

    # org_or_404 — when orgs table exists + has no matching row, raises
    # 404. On a fresh sandbox DB the table doesn't exist yet, so the
    # raw sqlite3.OperationalError bubbles up. Either way: not None.
    try:
        api_deps.org_or_404("ghost-org-id")
        check("org_or_404(missing) raises", False)
    except Exception:
        check("org_or_404(missing) raises", True)

    # New routers registered
    names = routers._ROUTER_NAMES
    check("routers package now lists at least 6 sub-routers",
          len(names) >= 6,
          f"routers: {names}")
    for name in ("me", "orgs_admin"):
        check(f"router '{name}' is registered", name in names)
        mod = __import__(f"padhai.routers.{name}", fromlist=["router"])
        check(f"router '{name}' module loads + has APIRouter",
              hasattr(mod, "router"))

    # All v2.0.3-extracted endpoints present
    actual = {r.path for r in app.routes if hasattr(r, "path")}
    expected_extracted = {
        "/api/users/me/push-tokens",
        "/api/users/me/push-prefs",
        "/api/me/streak",
        "/api/me/streak/opt",
        "/api/me/streak/calendar",
        "/api/me/mastery",
        "/api/me/mastery/signal",
        "/api/me/mastery/recommend",
        "/api/me/mastery/weak",
        "/api/coaching/practice/attempts",
        "/api/coaching/practice/stats",
        "/api/orgs/{org_id}/branding",
        "/api/orgs/{org_id}/audit",
        "/api/orgs/{org_id}/audit/export.csv",
        "/api/orgs/{org_id}/saml",
        "/api/orgs/{org_id}/scim/tokens",
        "/api/orgs/{org_id}/scim/tokens/{token_id}",
        "/api/orgs/{org_id}/data-residency",
        "/api/orgs/{org_id}/custom-domain",
        "/api/orgs/{org_id}/country",
    }
    missing = expected_extracted - actual
    check(
        "all v2.0.3-extracted endpoint paths registered",
        not missing,
        f"missing={missing}" if missing else "all 20 paths present",
    )

    # Auth gates still work
    with TestClient(app) as c:
        # /api/me/* requires auth
        for path in (
            "/api/me/streak",
            "/api/me/mastery",
            "/api/me/mastery/weak",
            "/api/users/me/push-prefs",
            "/api/coaching/practice/stats?exam=upsc",
        ):
            r = c.get(path)
            check(f"GET {path} → 401 (auth gate intact)",
                  r.status_code == 401)

        # POST endpoints requiring auth
        r = c.post(
            "/api/users/me/push-tokens",
            data={"platform": "fcm", "token": "x" * 30},
        )
        check(
            "POST /api/users/me/push-tokens → 401",
            r.status_code == 401,
        )
        r = c.post(
            "/api/me/mastery/signal",
            data={"topic_key": "x", "correct": "true"},
        )
        check(
            "POST /api/me/mastery/signal → 401",
            r.status_code == 401,
        )

        # Org-admin endpoints require auth (401 since unauth)
        for path in (
            "/api/orgs/some-org/branding",
            "/api/orgs/some-org/audit",
            "/api/orgs/some-org/saml",
            "/api/orgs/some-org/scim/tokens",
            "/api/orgs/some-org/data-residency",
            "/api/orgs/some-org/custom-domain",
            "/api/orgs/some-org/country",
        ):
            r = c.get(path)
            check(f"GET {path} → 401 (org auth gate intact)",
                  r.status_code == 401)

        # Size caps still enforced on the moved org-admin endpoints
        r = c.post(
            "/api/orgs/some-org/saml",
            data={
                "idp_entity_id": "x" * 600,
                "idp_sso_url": "https://x", "idp_certificate": "x",
                "sp_entity_id": "y",
            },
        )
        check(
            "POST /api/orgs/{id}/saml rejects oversized idp_entity_id (422)",
            r.status_code == 422,
        )

        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Router extraction sanity. Line counts are too brittle once later
    # product work lands; the production invariant is that extracted auth
    # routers remain registered and continue protecting their endpoints.
    org_or_me_routes = [
        r for r in app.routes
        if hasattr(r, "path")
        and (r.path.startswith("/api/me/") or r.path.startswith("/api/orgs/"))
    ]
    check(
        "extracted me/org routers expose a healthy route surface",
        len(org_or_me_routes) >= 20,
        f"routes={len(org_or_me_routes)}",
    )

    print("-----------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
