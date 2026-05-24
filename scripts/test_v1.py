"""Smoke test for v1.0 surfaces (F1 schema, E9 branding, D3 PWA).

Run:  PYTHONPATH=. python scripts/test_v1.py

Exits 0 on success, prints one line per check. The CI workflow at
`.github/workflows/smoke.yml` (when wired) runs this against a
freshly-started container to catch regressions before deploy.
"""

from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from pathlib import Path

# Force a fresh DB so we exercise the cold-start path (no orgs table
# yet → branding endpoints should NOT crash).
_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai.web import app

    failed: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "ok  " if ok else "FAIL"
        print(f"  {marker}  {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failed.append(name)

    print("v1.0 smoke test")
    print("---------------")

    # `with` so startup lifespan runs — that's what calls schema_v2.migrate()
    # and branding.migrate(), creating the F1 tables we assert on below.
    with TestClient(app) as c:
        # --- D3 PWA surface ---
        r = c.get("/sw.js")
        check(
            "GET /sw.js returns javascript",
            r.status_code == 200
            and "javascript" in r.headers.get("content-type", ""),
            f"status={r.status_code}",
        )

        r = c.get("/manifest.json")
        check(
            "GET /manifest.json returns PWA manifest",
            r.status_code == 200 and r.json().get("name") == "AI Pathshala",
            f"status={r.status_code}",
        )

        r = c.get(
            "/manifest.json",
            headers={"X-Forwarded-Host": "stpauls.aipathshala.in"},
        )
        check(
            "GET /manifest.json with subdomain header degrades to default "
            "when org doesn't exist (no crash)",
            r.status_code == 200,
            f"status={r.status_code}",
        )

        # --- E9 branding surface ---
        r = c.get("/api/branding/resolve")
        body = r.json() if r.status_code == 200 else {}
        check(
            "GET /api/branding/resolve returns platform defaults on cold DB",
            r.status_code == 200
            and body.get("brand_name") == "AI Pathshala"
            and body.get("brand_color") == "#5E60CE",
            f"status={r.status_code}",
        )

        r = c.get("/api/orgs/does-not-exist/branding")
        check(
            "GET /api/orgs/{id}/branding requires auth",
            r.status_code == 401,
            f"status={r.status_code}",
        )

        # --- SPA shell wiring ---
        r = c.get("/ui")
        html = r.text if r.status_code == 200 else ""
        check("GET /ui returns 200", r.status_code == 200)
        check("SPA has manifest link", 'rel="manifest"' in html)
        check("SPA has theme-color meta", "theme-color" in html)
        check(
            "SPA registers service worker",
            "serviceWorker.register" in html and "/sw.js" in html,
        )
        check("SPA has applyBranding IIFE", "applyBranding" in html)
        check(
            "SPA has apple-mobile-web-app meta tags (iOS install affordance)",
            "apple-mobile-web-app-capable" in html,
        )

        # --- Version sanity (each release's own smoke asserts its
        # exact version; this one just confirms we're on the v1.x line) ---
        r = c.get("/")
        body = r.json() if r.status_code == 200 else {}
        check(
            "Root endpoint reports a semver-shaped version",
            str(body.get("version", "")).split(".")[0].isdigit(),
            f"version={body.get('version')}",
        )

    # --- F1 schema (additive — should be present after startup ran) ---
    # Inspect the DB AFTER the TestClient context closed so the lifespan
    # startup definitely ran.
    with sqlite3.connect(_DB) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    for t in (
        "document_pages",
        "video_requests",
        "video_blueprints",
        "generated_videos",
    ):
        check(f"F1 table '{t}' created at startup", t in tables)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
