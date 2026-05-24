"""Smoke for v3.19 — Home UI UX fixes.

Covers:
- Sidebar reduced to section titles only (no per-feature clutter)
- Chips don't have anchor-href (so they don't navigate to JSON)
- Drawer markup present + Try-it button
- Landing carries inline auth form posting to /auth/login + /auth/signup
- PWA shell preserved
- All routes still resolve

Run: PYTHONPATH=. python scripts/test_v3_19.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_19_smoke.db"),
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

    print("v3.19 smoke test — Home UI UX fixes")
    print("-----------------------------------")

    # ============================================================
    # HOME_HTML
    # ============================================================
    home = home_ui.get_home_html()

    # Sidebar: no per-feature anchors — section buttons only
    check(
        "Sidebar uses <button> elements (not <a href=API>)",
        ".sidenav-item" in home and "renderSidebar" in home,
    )

    # Chips are <button>, NOT <a href=...> — that's the bug we fixed
    check(
        "Chips render as <button>, not <a href> to API URLs",
        "module-chip" in home
        and 'button class="module-chip"' in home,
    )
    check(
        "Chips do NOT navigate via href=/api/...",
        '<a class="module-chip" href="/api/' not in home,
    )

    # Drawer present
    check(
        "Drawer markup present",
        'drawer-backdrop' in home and 'openDrawer' in home,
    )
    check(
        "Drawer has Try-it button + close",
        'drawerTryBtn' in home and 'closeDrawer' in home,
    )

    # Drawer guards against unfilled path params
    check(
        "Drawer skips endpoints with {param} placeholders",
        'needs a parameter' in home,
    )

    # Section groups (chip grids per section)
    check(
        "Section groups rendered (one panel per §26 section)",
        'group-' in home and 'sectionGroups' in home,
    )

    # PWA shell preserved
    check(
        "Manifest link preserved",
        'rel="manifest"' in home,
    )
    check(
        "theme-color preserved",
        "theme-color" in home,
    )
    check(
        "Service worker registered",
        "serviceWorker.register" in home,
    )
    check(
        "applyBranding IIFE preserved",
        "applyBranding" in home,
    )
    check(
        "Apple meta tags preserved",
        "apple-mobile-web-app-capable" in home,
    )

    # Sign-in card for unauthed users
    check(
        "Home has 'Sign in' fallback card for unauthed users",
        "signinCard" in home and "Sign in" in home,
    )

    # ============================================================
    # LANDING_HTML
    # ============================================================
    landing = home_ui.get_landing_html()

    check(
        "Landing has Sign-in / Create-account tab UI",
        '"login"' in landing and '"signup"' in landing,
    )
    check(
        "Landing form posts to /auth/login + /auth/signup",
        "'/auth/' + mode" in landing,
    )
    check(
        "Landing redirects to /home on success",
        "location.href = '/home'" in landing,
    )
    check(
        "Landing has password input with min length",
        'type="password"' in landing
        and 'minlength="8"' in landing,
    )

    # ============================================================
    # HTTP — routes resolve
    # ============================================================
    with TestClient(app) as c:
        r = c.get("/", headers={"Accept": "text/html"})
        check(
            "GET / serves new home HTML",
            r.status_code == 200
            and "module-chip" in r.text
            and "drawer-backdrop" in r.text,
        )

        # Critical: the served HTML should NOT contain anchor
        # tags pointing at API JSON for module chips
        check(
            "Served / has no chip <a href='/api/...'>",
            '<a class="module-chip" href="/api/' not in r.text,
        )

        r = c.get("/landing")
        check(
            "GET /landing → 200 + has auth form",
            r.status_code == 200
            and 'name="email"' in r.text or "id=\"email\"" in r.text,
        )

        r = c.get("/home")
        check(
            "GET /home alias works",
            r.status_code == 200 and "drawer-backdrop" in r.text,
        )

        r = c.get("/ui-legacy")
        check(
            "GET /ui-legacy still works",
            r.status_code == 200,
        )

        # Auth endpoints — verify POST /auth/login + /auth/signup
        # are still routable (the form's targets)
        r = c.post(
            "/auth/login",
            data={"email": "", "password": ""},
        )
        check(
            "POST /auth/login route resolves (not 404)",
            r.status_code != 404,
            f"got {r.status_code}",
        )

        r = c.post(
            "/auth/signup",
            data={"email": "", "password": ""},
        )
        check(
            "POST /auth/signup route resolves (not 404)",
            r.status_code != 404,
            f"got {r.status_code}",
        )

        # Version
        r = c.get("/")
        check(
            "Root JSON reports 3.19.0",
            r.json().get("version") == "3.19.0",
            f"version={r.json().get('version')}",
        )

    paths = {r.path for r in app.routes if hasattr(r, "path")}
    expected_ui_paths = {"/", "/home", "/landing", "/ui", "/ui-legacy"}
    check(
        "v3.19 UI routes remain registered",
        expected_ui_paths.issubset(paths) and len(app.routes) >= 592,
        f"routes={len(app.routes)} missing={expected_ui_paths - paths}",
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
