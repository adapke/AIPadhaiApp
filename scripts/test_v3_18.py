"""Smoke for v3.18 — Goal-led home UI.

Covers:
- GET / with Accept: text/html serves the new goal-led home
- /home alias serves the same HTML
- /landing serves the public landing
- /ui-legacy still serves the pre-v3.18 dashboard
- HTML contains the JS that fetches our backend APIs
- API client (no Accept header) still gets JSON at /

Run: PYTHONPATH=. python scripts/test_v3_18.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_18_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai.web import app
    from padhai import home_ui

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v3.18 smoke test — Goal-led home UI")
    print("-----------------------------------")

    # ============================================================
    # Module-level — HTML constants are non-empty + reasonable
    # ============================================================
    home = home_ui.get_home_html()
    landing = home_ui.get_landing_html()
    check(
        "home_ui.get_home_html() returns a substantial HTML doc",
        len(home) > 5000 and "<!doctype html>" in home.lower(),
    )
    check(
        "Home HTML references the navigation manifest endpoint",
        "/api/navigation/manifest" in home,
    )
    check(
        "Home HTML references the student-home dashboard endpoint",
        "/api/home/me/dashboard" in home,
    )
    check(
        "Home HTML contains the brand title 'PadhaiApp'",
        "PadhaiApp" in home,
    )
    check(
        "Home HTML has the mobile bottom-nav container",
        "mobile-bottom-nav" in home,
    )
    check(
        "Landing HTML has Sign in CTA",
        "Sign in" in landing,
    )

    # ============================================================
    # HTTP — / with Accept: text/html returns the new home
    # ============================================================
    with TestClient(app) as c:
        # Browsers send Accept: text/html
        r = c.get("/", headers={"Accept": "text/html"})
        check(
            "GET / with Accept: text/html → 200 + HTML",
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("text/html"),
        )
        check(
            "GET / HTML contains the goal-led headline placeholder",
            "Loading your study plan" in r.text
            or "Welcome" in r.text,
        )
        check(
            "GET / HTML references the dashboard API",
            "/api/home/me/dashboard" in r.text,
        )
        check(
            "GET / HTML references the navigation manifest API",
            "/api/navigation/manifest" in r.text,
        )

        # API client (no Accept header) still gets JSON
        r = c.get("/")
        check(
            "GET / without Accept → JSON (existing behaviour)",
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("application/json"),
        )
        check(
            "JSON root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

        # Explicit Accept: application/json forces JSON
        r = c.get(
            "/",
            headers={"Accept": "application/json"},
        )
        check(
            "GET / Accept: application/json → JSON",
            r.headers.get("content-type", "").startswith("application/json"),
        )

        # /home serves the HTML directly
        r = c.get("/home")
        check(
            "GET /home → 200 + HTML",
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("text/html")
            and "PadhaiApp" in r.text,
        )

        # /landing serves the public landing
        r = c.get("/landing")
        check(
            "GET /landing → 200 + HTML + Sign in CTA",
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("text/html")
            and "Sign in" in r.text,
        )

        # /ui serves the new home (same as /home)
        r = c.get("/ui")
        check(
            "GET /ui serves the new goal-led home",
            r.status_code == 200
            and "/api/home/me/dashboard" in r.text,
        )

        # /ui-legacy still works
        r = c.get("/ui-legacy")
        check(
            "GET /ui-legacy → 200 (kept for backwards compat)",
            r.status_code == 200
            and r.headers.get("content-type", "").startswith("text/html"),
        )

        # The navigation manifest API is reachable from anonymous
        # users (public) — required for the home page to render
        # without a login
        r = c.get("/api/navigation/manifest")
        check(
            "GET /api/navigation/manifest → 200 (public)",
            r.status_code == 200
            and "sections" in r.json(),
        )

        # The dashboard API is auth-gated; the JS in the home
        # handles 401 gracefully
        r = c.get("/api/home/me/dashboard")
        check(
            "GET /api/home/me/dashboard → 401 anonymous (handled in JS)",
            r.status_code == 401,
        )

    check(
        "v3.18 brought route count above 590",
        len(app.routes) > 590,
        f"routes={len(app.routes)}",
    )

    print("-----------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
