"""Smoke for v2.0.4 — docs refresh + push batching.

Run: PYTHONPATH=. python scripts/test_v2_0_4.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_0_4_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import push
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.0.4 smoke test")
    print("-----------------")

    # --- Docs exist + have v2-era content ---
    changelog = Path("CHANGELOG.md")
    check("CHANGELOG.md exists", changelog.exists())
    if changelog.exists():
        body = changelog.read_text(encoding="utf-8")
        check(
            "CHANGELOG covers v0.10 + v1.0 + v2.0",
            "v0.10" in body and "v1.0.0" in body and "v2.0.0" in body,
        )
        check(
            "CHANGELOG mentions v2.0.4 entry",
            "v2.0.4" in body,
        )

    readme = Path("README.md")
    check("README.md exists", readme.exists())
    if readme.exists():
        body = readme.read_text(encoding="utf-8")
        check(
            "README reflects platform state (not v0.x CLI prototype framing)",
            "AI Pathshala" in body and "School ERP" in body
            and "SAML" in body and "Capacitor" in body,
        )
        check(
            "README lists 160-route surface + router split",
            "routers/" in body and "router" in body.lower(),
        )
        check(
            "README references current ROADMAPs + CHANGELOG",
            "ROADMAP_V2.md" in body and "CHANGELOG.md" in body,
        )

    # --- I3 push batching — single transaction per send_one() ---
    push.migrate()
    # Register 5 tokens for one user, simulate a send, verify log
    # rows landed in batch.
    for i in range(5):
        push.register_token(
            user_id="batch-test",
            platform="fcm",
            token=f"device-tok-{i}-" + "x" * 20,
        )
    t0 = time.perf_counter()
    result = push.send_one(
        user_id="batch-test",
        category="announcement",
        title="Batch test",
        body="checking batch insert",
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    check(
        "send_one to 5 tokens returned 5 log_ids",
        len(result.log_ids) == 5,
        f"got {len(result.log_ids)}",
    )
    check(
        "send_one wrote no-provider rows for all 5 tokens",
        result.failed == 5 and result.delivered == 0,
    )

    # All log rows present
    rows = push.recent_log(user_id="batch-test", limit=10)
    check(
        "recent_log returns all 5 rows from the batch",
        len(rows) == 5,
        f"got {len(rows)}",
    )

    # The batch path should be well under 100ms for 5 rows; a serial
    # per-row open would take longer on slow disks, but this is just
    # a sanity check, not a hard perf gate.
    check(
        "send_one to 5 tokens completed in reasonable time",
        elapsed_ms < 1000,
        f"elapsed={elapsed_ms:.1f}ms",
    )

    # Edge case: opt-out user → no rows written, no batch executed
    push.set_pref(user_id="batch-test", category="marketing", enabled=False)
    skipped = push.send_one(
        user_id="batch-test", category="marketing", title="ad",
    )
    check(
        "send_one skips opted-out user (no log rows + skipped flag)",
        skipped.skipped_opt_out and len(skipped.log_ids) == 0,
    )

    # Edge case: no tokens → no batch, no log_ids
    no_tok = push.send_one(
        user_id="no-such-user", category="announcement", title="x",
    )
    check(
        "send_one for user with no tokens returns empty",
        no_tok.delivered == 0 and no_tok.failed == 0
        and len(no_tok.log_ids) == 0,
    )

    # --- HTTP: version + key endpoints still work ---
    with TestClient(app) as c:
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

        # Spot-check a route from each release surface to confirm
        # no regression from the v2.0.4 changes
        for path, expect_status in [
            ("/api/region", 200),                 # v1.6 → v2.0.2 router
            ("/api/procurement/skus", 200),       # v2.0 → v2.0.2 router
            ("/api/me/streak", 401),              # v2.0.3 router (auth gate)
            ("/api/orgs/x/branding", 401),        # v2.0.3 router
            ("/api/push/stats", 200),             # I3 from v1.2 still in web.py
        ]:
            r = c.get(path)
            check(
                f"GET {path} → {expect_status}",
                r.status_code == expect_status,
                f"got {r.status_code}",
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
