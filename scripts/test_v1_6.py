"""Smoke for v1.6 — H5 custom domains, H6 SOC 2 dashboard, G4 region.
Run: PYTHONPATH=. python scripts/test_v1_6.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_6_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import custom_domains, region, soc2
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v1.6 smoke test")
    print("---------------")

    # H5
    s = custom_domains.get_status("missing-org")
    check("H5 unknown org → no domain", s.custom_domain is None and not s.verified)
    try:
        custom_domains.set_domain(org_id="x", domain="bad domain")
        check("H5 rejects invalid domain", False)
    except ValueError:
        check("H5 rejects invalid domain", True)

    check(
        "H5 resolve_by_domain returns None for unknown host",
        custom_domains.resolve_by_domain("nowhere.example.com") is None,
    )

    # H6
    score = soc2.readiness_score()
    check(
        "H6 readiness_score returns floats in [0,1]",
        0 <= score["overall_readiness_pct"] <= 1,
        f"score={score['overall_readiness_pct']:.2f}",
    )
    check(
        "H6 estimated_days_to_type_1 is positive",
        score["estimated_days_to_type_1"] > 0,
    )
    metrics = soc2.gather_metrics()
    check(
        "H6 gather_metrics returns at least 5 entries",
        len(metrics) >= 5,
        f"count={len(metrics)}",
    )
    pols = soc2.policies()
    check(
        "H6 policies include access_control + encryption + bcp_dr",
        {p["id"] for p in pols} >= {"access_control", "encryption", "bcp_dr"},
    )

    # G4
    info = region.current()
    check(
        "G4 default region 'dev'",
        info.region == "dev" and info.label == "local dev",
    )
    os.environ["PADHAI_REGION"] = "singapore"
    info2 = region.current()
    check(
        "G4 PADHAI_REGION=singapore picked up",
        info2.region == "singapore" and not info2.is_primary,
    )
    os.environ["PADHAI_REGION_PROMOTED"] = "1"
    info3 = region.current()
    check(
        "G4 PROMOTED=1 → is_primary True",
        info3.is_primary,
    )
    os.environ.pop("PADHAI_REGION", None)
    os.environ.pop("PADHAI_REGION_PROMOTED", None)

    # HTTP
    with TestClient(app) as c:
        r = c.get("/api/admin/soc2/dashboard")
        check(
            "GET /api/admin/soc2/dashboard returns 200 with readiness",
            r.status_code == 200 and "readiness" in r.json()
            and "metrics" in r.json() and "policies" in r.json(),
        )
        r = c.get("/api/region")
        check(
            "GET /api/region returns 200",
            r.status_code == 200 and "region" in r.json(),
        )
        r = c.get("/api/orgs/some-org/custom-domain")
        check(
            "GET /api/orgs/{id}/custom-domain requires auth (401)",
            r.status_code == 401,
        )
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed: print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
