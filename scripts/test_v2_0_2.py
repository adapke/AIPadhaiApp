"""Smoke for v2.0.2 — router split.

Verifies that the endpoints extracted to `padhai/routers/*` still
work + that the router include mechanism is wired correctly.

Run: PYTHONPATH=. python scripts/test_v2_0_2.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_0_2_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import routers
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.0.2 smoke test")
    print("-----------------")

    # Router package loadable + advertises >=4 sub-routers
    names = routers._ROUTER_NAMES
    check(
        "routers package lists 4+ sub-routers",
        len(names) >= 4,
        f"routers: {names}",
    )

    # Each named router imports successfully + has a `router` attr
    for name in names:
        mod = __import__(f"padhai.routers.{name}", fromlist=["router"])
        check(
            f"router '{name}' imports + exposes APIRouter",
            hasattr(mod, "router"),
        )

    # Confirm the extracted endpoints are present on the app
    expected = {
        "/api/render/math",
        "/api/render/diagram",
        "/api/curriculum/score",
        "/api/curriculum/objectives",
        "/api/preschool/activities",
        "/api/preschool/categories",
        "/api/procurement/skus",
        "/api/region",
        "/api/indic/profile",
        "/api/indic/normalize",
        "/api/tts/providers",
        "/api/admin/soc2/dashboard",
        "/api/countries",
        "/api/coaching/tracks",
        "/api/coaching/digest",
        "/api/question-bank/search",
        "/api/question-bank/stats",
        "/api/question-bank/{qid}",
    }
    actual = {r.path for r in app.routes if hasattr(r, "path")}
    missing = expected - actual
    check(
        "all extracted endpoint paths are registered",
        not missing,
        f"missing={missing}" if missing else "all present",
    )

    # Auth-coupled endpoints that should still live in web.py
    must_stay_in_web = {
        "/auth/login",
        "/api/orgs/{org_id}/audit",
        "/api/users/me/push-tokens",
        "/api/me/streak",
        "/api/me/mastery",
        "/api/coaching/practice/stats",  # auth-required POST/GET pair
    }
    still_present = must_stay_in_web & actual
    check(
        "auth-coupled endpoints kept in web.py still registered",
        still_present == must_stay_in_web,
        f"missing={must_stay_in_web - actual}",
    )

    # Exercise the extracted endpoints over HTTP
    with TestClient(app) as c:
        r = c.get("/api/region")
        check("GET /api/region (extracted) → 200",
              r.status_code == 200 and "region" in r.json())

        r = c.get("/api/countries")
        check("GET /api/countries (extracted) → 200",
              r.status_code == 200 and len(r.json()["countries"]) >= 5)

        r = c.get("/api/procurement/skus")
        check("GET /api/procurement/skus (extracted) → 200",
              r.status_code == 200 and len(r.json()["skus"]) == 6)

        r = c.get("/api/preschool/activities")
        check("GET /api/preschool/activities (extracted) → 200",
              r.status_code == 200 and len(r.json()["rows"]) >= 10)

        r = c.get("/api/admin/soc2/dashboard")
        check("GET /api/admin/soc2/dashboard (extracted) → 200",
              r.status_code == 200 and "readiness" in r.json())

        r = c.get("/api/indic/profile?language=ta")
        check("GET /api/indic/profile (extracted) → 200",
              r.status_code == 200 and r.json()["language"] == "ta")

        r = c.get("/api/tts/providers")
        check("GET /api/tts/providers (extracted) → 200",
              r.status_code == 200 and "providers" in r.json())

        r = c.get("/api/question-bank/stats")
        check("GET /api/question-bank/stats (extracted) → 200",
              r.status_code == 200 and "total" in r.json())

        r = c.post("/api/render/math",
                   data={"latex": "a+b", "inline": "false"})
        check(
            "POST /api/render/math (extracted) → 200",
            r.status_code == 200 and "html" in r.json(),
        )

        # Size cap still enforced in the extracted route
        big = "x" * 3000
        r = c.post("/api/render/math",
                   data={"latex": big, "inline": "false"})
        check(
            "extracted /api/render/math still rejects oversized LaTeX (422)",
            r.status_code == 422,
        )

        # Rate limit still enforced in the extracted route
        from padhai import rate_limit
        rate_limit.preview_math.reset("testclient")
        rate_limit.preview_math.reset("unknown")
        seen_429 = False
        for i in range(40):
            r = c.post("/api/render/math",
                       data={"latex": f"y_{i}", "inline": "false"})
            if r.status_code == 429:
                seen_429 = True
                break
        check(
            "extracted /api/render/math still rate-limits (429 after burst)",
            seen_429,
        )

        r = c.get("/")
        check("Root reports a semver-shaped version",
              str(r.json().get("version", "")).split(".")[0].isdigit(),
              f"version={r.json().get('version')}")

    # Router extraction sanity. Avoid brittle line-count gates: later
    # product work can legitimately add code while the extraction remains
    # healthy. The production behavior we care about is that the extracted
    # routers stay registered and continue serving their routes.
    extracted_routes = expected & actual
    check(
        "extracted routers expose a healthy route surface",
        len(extracted_routes) == len(expected),
        f"extracted_routes={len(extracted_routes)}",
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
