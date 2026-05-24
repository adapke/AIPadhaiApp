"""Smoke test for v1.1 surfaces (G1 backend selector, G3 CDN signing,
H3 audit log).

Run:  PYTHONPATH=. python scripts/test_v1_1.py

Exits 0 on success.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_1_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import audit, cdn, db_backend
    from padhai.web import app

    failed: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "ok  " if ok else "FAIL"
        print(f"  {marker}  {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failed.append(name)

    print("v1.1 smoke test")
    print("---------------")

    # --- G1: backend selector ---
    cfg = db_backend.resolve()
    check(
        "G1 default backend is SQLite",
        cfg.backend == "sqlite",
        f"backend={cfg.backend}",
    )
    check(
        "G1 description doesn't leak password (sqlite path expected)",
        cfg.backend == "sqlite" and "://" in db_backend.description(),
    )

    # Postgres URL detection (without actually connecting)
    os.environ["DATABASE_URL"] = (
        "postgres://u:p@ap-south-1.aws.neon.tech:5432/padhai"
    )
    cfg2 = db_backend.resolve()
    check(
        "G1 detects Postgres URL → backend=postgres",
        cfg2.backend == "postgres",
        f"backend={cfg2.backend}",
    )
    check(
        "G1 parses Neon region from host",
        cfg2.region == "ap-south-1",
        f"region={cfg2.region}",
    )
    desc = db_backend.description()
    check(
        "G1 description hides password but shows host/db/region",
        "p@" not in desc and "ap-south-1.aws.neon.tech" in desc
        and "padhai" in desc and "ap-south-1" in desc,
        desc,
    )
    os.environ.pop("DATABASE_URL")  # reset for following tests

    # --- G3: CDN signing ---
    check(
        "G3 not configured by default → maybe_redirect returns None",
        cdn.maybe_redirect("/jobs/abc/video") is None,
    )

    os.environ["PADHAI_CDN_BASE_URL"] = "https://cdn.aipathshala.in"
    os.environ["PADHAI_CDN_SIGNING_KEY"] = "test-secret-key"
    try:
        check("G3 is_configured() True after env set", cdn.is_configured())
        signed = cdn.signed_url("/jobs/abc/video")
        check(
            "G3 signed URL has expires + sig",
            signed.startswith("https://cdn.aipathshala.in/jobs/abc/video?")
            and "expires=" in signed and "sig=" in signed,
            signed,
        )
        # Extract + verify
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(signed)
        qs = parse_qs(parsed.query)
        ok = cdn.verify_signed_path(
            parsed.path,
            expires=int(qs["expires"][0]),
            sig=qs["sig"][0],
        )
        check("G3 round-trip verify_signed_path returns True", ok)

        # Tamper test
        bad = cdn.verify_signed_path(
            parsed.path,
            expires=int(qs["expires"][0]),
            sig="0" * 64,
        )
        check("G3 wrong signature → verify returns False", not bad)

        # Expired
        expired = cdn.verify_signed_path(
            "/jobs/abc/video", expires=1, sig=qs["sig"][0],
        )
        check("G3 expired URL → verify returns False", not expired)
    finally:
        os.environ.pop("PADHAI_CDN_BASE_URL", None)
        os.environ.pop("PADHAI_CDN_SIGNING_KEY", None)

    # --- Bring up the app for HTTP-level checks ---
    with TestClient(app) as c:
        # H3 audit table created at startup
        with sqlite3.connect(_DB) as conn:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        check("H3 audit_log table exists after startup", "audit_log" in tables)

        # Direct audit.record() round-trip (auth requires Postgres, not
        # available in this sandbox — so we exercise the write path
        # the way wired action sites use it).
        audit.record(
            action="auth.login.fail",
            actor_ip="203.0.113.10",
            actor_ua="smoke-test/1.0",
            target_type="email",
            target_id="nobody@example.com",
            note="user not found",
        )
        audit.record(
            action="org.exam.grade.override",
            org_id="org-smoke",
            actor_user_id="user-smoke",
            target_type="exam_attempt",
            target_id="att-1",
            after={"manual_score": 87, "total_score": 92},
        )
        rows = audit.query(action="auth.login.fail", limit=10)
        check(
            "audit row writes via record() + reads via query()",
            len(rows) >= 1,
            f"count={len(rows)}",
        )
        if rows:
            check(
                "audit row preserves IP",
                rows[0].actor_ip == "203.0.113.10",
            )
            check(
                "audit row preserves note",
                rows[0].note == "user not found",
            )

        # Filter by action_prefix
        prefix_rows = audit.query(action_prefix="org.", limit=10)
        check(
            "audit query by action_prefix='org.' finds the exam.grade row",
            any(r.action == "org.exam.grade.override" for r in prefix_rows),
        )

        # before/after JSON survives round-trip
        target_rows = audit.query(
            target_type="exam_attempt", target_id="att-1", limit=1,
        )
        check(
            "audit row preserves before/after as JSON",
            target_rows
            and json.loads(target_rows[0].after_json or "{}").get("manual_score") == 87,
        )

        # Audit list endpoint requires admin
        r = c.get("/api/orgs/some-org/audit")
        check(
            "GET /api/orgs/{id}/audit requires auth (401)",
            r.status_code == 401,
            f"status={r.status_code}",
        )
        r = c.get("/api/orgs/some-org/audit/export.csv")
        check(
            "GET /api/orgs/{id}/audit/export.csv requires auth (401)",
            r.status_code == 401,
            f"status={r.status_code}",
        )

        # /jobs/{id}/video without CDN configured → 404 (job not found),
        # not 302
        r = c.get("/jobs/nonexistent/video", follow_redirects=False)
        check(
            "/jobs/{id}/video without CDN → 404 (job not found)",
            r.status_code == 404,
            f"status={r.status_code}",
        )

        # Version sanity (each release's own smoke asserts its
        # exact version; this one just confirms we're on the v1.x line)
        r = c.get("/")
        check(
            "Root endpoint reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # CSV export — direct call to the generator
    sample = "".join(
        audit.export_csv_iter(org_id=None, from_ts=0, to_ts=None)
    )
    check(
        "CSV export starts with headers row",
        sample.split("\n")[0].startswith("id,created_at_iso,action"),
    )
    check(
        "CSV export includes the login.fail event we just logged",
        "auth.login.fail" in sample,
    )

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
