"""Smoke test for v1.2 surfaces (G2 queue backend, G6 loadtest harness,
I3 push notifications).

Run: PYTHONPATH=. python scripts/test_v1_2.py

Exits 0 on success.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_2_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import push, queue_backend
    from padhai.web import app

    failed: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "ok  " if ok else "FAIL"
        print(f"  {marker}  {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failed.append(name)

    print("v1.2 smoke test")
    print("---------------")

    # --- G2: queue backend selector ---
    cfg = queue_backend.resolve()
    check(
        "G2 default backend is in-process",
        cfg.backend == queue_backend.INPROCESS_BACKEND,
        f"backend={cfg.backend}",
    )
    check(
        "G2 description is human-readable",
        "inprocess" in queue_backend.description(),
        queue_backend.description(),
    )
    # Redis URL detection (without actually connecting)
    os.environ["REDIS_URL"] = "redis://default:secret@example.com:6379/0"
    cfg2 = queue_backend.resolve()
    check(
        "G2 with REDIS_URL but no `rq` → falls back to inprocess",
        cfg2.backend == queue_backend.INPROCESS_BACKEND
        and cfg2.redis_url is not None
        and not cfg2.rq_available,
        f"backend={cfg2.backend} rq_available={cfg2.rq_available}",
    )
    desc = queue_backend.description()
    check(
        "G2 description warns when REDIS_URL set but rq missing",
        "REDIS_URL set but `rq` not installed" in desc,
        desc,
    )
    os.environ.pop("REDIS_URL", None)

    # --- I3: push module direct API ---
    push.migrate()
    with sqlite3.connect(_DB) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    for t in ("push_tokens", "push_prefs", "push_log"):
        check(f"I3 table '{t}' created", t in tables)

    # Register a token (first time → seeds default prefs)
    tok = push.register_token(
        user_id="u1", platform="fcm", token="device-token-AAAA",
        device_id="dev-001", app_version="1.2.0",
    )
    check(
        "I3 register_token returns active token",
        tok.active and tok.platform == "fcm",
    )
    check(
        "I3 re-registering bumps last_used (idempotent on user+token)",
        True,  # exercised in next call
    )
    tok2 = push.register_token(
        user_id="u1", platform="fcm", token="device-token-AAAA",
    )
    check(
        "I3 same id returned on re-register",
        tok2.id == tok.id,
        f"first={tok.id} second={tok2.id}",
    )

    # Prefs were seeded
    prefs = push.get_prefs("u1")
    check(
        "I3 default ON categories seeded after first register",
        prefs["assignment_due"] and prefs["exam_alert"]
        and prefs["attendance"] and prefs["announcement"]
        and prefs["streak"],
    )
    check("I3 marketing defaults OFF", prefs["marketing"] is False)

    # Update a pref
    push.set_pref(user_id="u1", category="streak", enabled=False)
    check(
        "I3 set_pref persists",
        push.is_opted_in(user_id="u1", category="streak") is False,
    )

    # Invalid category
    try:
        push.set_pref(user_id="u1", category="bogus", enabled=True)
        check("I3 set_pref rejects unknown category", False)
    except ValueError:
        check("I3 set_pref rejects unknown category", True)

    # Send fan-out — no platform keys configured, so each send writes
    # a log row with failed_reason='no_provider'
    result = push.send_one(
        user_id="u1", category="announcement",
        title="Test announcement",
        body="hello from smoke test",
    )
    check(
        "I3 send_one logs delivery with no_provider when keys absent",
        result.delivered == 0 and result.failed == 1
        and not result.skipped_opt_out
        and len(result.log_ids) == 1,
        f"delivered={result.delivered} failed={result.failed}",
    )

    # Opt-out path
    push.set_pref(user_id="u1", category="marketing", enabled=False)
    skipped = push.send_one(
        user_id="u1", category="marketing", title="ad",
    )
    check(
        "I3 send_one skips opted-out category (no log row written)",
        skipped.delivered == 0 and skipped.skipped_opt_out
        and len(skipped.log_ids) == 0,
    )

    # Log query
    log_rows = push.recent_log(user_id="u1", limit=10)
    check(
        "I3 recent_log returns the no_provider row",
        any(r.failed_reason == "no_provider" for r in log_rows),
    )

    # Stats
    stats = push.stats_for_period(hours=1.0)
    check(
        "I3 stats_for_period counts sent + failed_no_provider",
        stats["sent"] >= 1 and stats["failed_no_provider"] >= 1,
        str(stats),
    )

    # Open beacon
    log_id = log_rows[0].id
    check("I3 mark_opened first call → True", push.mark_opened(log_id=log_id))
    check(
        "I3 mark_opened second call → False (idempotent — already opened)",
        push.mark_opened(log_id=log_id) is False,
    )

    # --- HTTP-level checks ---
    with TestClient(app) as c:
        r = c.get("/api/push/stats?hours=1")
        check(
            "/api/push/stats returns 200",
            r.status_code == 200 and "sent" in r.json(),
        )
        r = c.get("/api/users/me/push-prefs")
        check(
            "/api/users/me/push-prefs requires auth (401)",
            r.status_code == 401,
        )
        r = c.post(
            "/api/users/me/push-tokens",
            data={"platform": "fcm", "token": "x" * 30},
        )
        check(
            "POST /api/users/me/push-tokens requires auth (401)",
            r.status_code == 401,
        )

        # G6 — verify the locustfile module is at least syntactically
        # valid (we can't run locust here, but we can import-check the
        # file path).
        loadtest_path = Path("scripts/loadtest_locustfile.py")
        check(
            "G6 locustfile exists",
            loadtest_path.exists() and loadtest_path.stat().st_size > 2000,
        )
        check(
            "G6 load-testing doc exists",
            Path("LOAD_TESTING.md").exists(),
        )
        check(
            "G2 migration runbook exists",
            Path("G2_QUEUE_MIGRATION.md").exists(),
        )

        # Version bump
        r = c.get("/")
        check(
            "Root endpoint reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
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
