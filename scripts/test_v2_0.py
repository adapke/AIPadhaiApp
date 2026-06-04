"""Smoke for v2.0 — G5 (DR docs), J5 mastery, K4 preschool, H7 procurement.
Run: PYTHONPATH=. python scripts/test_v2_0.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_0_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import mastery, preschool, procurement
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.0 smoke test")
    print("---------------")

    # G5 + H7 — docs exist
    check("G5 DR runbook exists", Path("G5_DISASTER_RECOVERY.md").exists())
    check("H7 GeM procurement doc exists", Path("H7_GEM_PROCUREMENT.md").exists())

    # J5 mastery
    mastery.migrate()
    # First signal — correct → mastery = 1.0
    m = mastery.update(user_id="stu-1", topic_key="photosynthesis", correct=True)
    check(
        "J5 first correct signal → mastery 1.0",
        m.mastery == 1.0 and m.attempts == 1 and m.correct == 1,
    )
    # Second signal — incorrect → EWMA reduces
    m = mastery.update(user_id="stu-1", topic_key="photosynthesis", correct=False)
    check(
        "J5 second incorrect signal → mastery decreases",
        m.mastery < 1.0 and m.attempts == 2,
        f"mastery={m.mastery:.3f}",
    )
    # Several incorrect → should drift below 0.4
    for _ in range(5):
        mastery.update(user_id="stu-1", topic_key="photosynthesis", correct=False)
    m_final = mastery.get(user_id="stu-1", topic_key="photosynthesis")
    check(
        "J5 sustained incorrect signals push mastery below LOW threshold",
        m_final.mastery < mastery.LOW_THRESHOLD,
        f"final={m_final.mastery:.3f}",
    )
    # Recommendation reflects it
    rec = mastery.recommend_difficulty(
        user_id="stu-1", topic_key="photosynthesis",
    )
    check("J5 recommend_difficulty → 'easier'", rec == "easier")

    # Strong topic
    for _ in range(8):
        mastery.update(user_id="stu-1", topic_key="quadratic_eq", correct=True)
    rec2 = mastery.recommend_difficulty(
        user_id="stu-1", topic_key="quadratic_eq",
    )
    check("J5 sustained correct → 'advanced'", rec2 == "advanced")

    # Unknown topic → standard
    rec3 = mastery.recommend_difficulty(
        user_id="stu-1", topic_key="never_seen",
    )
    check("J5 unknown topic → 'standard'", rec3 == "standard")

    # Weak / strong lists
    weak = mastery.weak_topics(user_id="stu-1")
    strong = mastery.strong_topics(user_id="stu-1")
    check(
        "J5 weak_topics surfaces photosynthesis",
        any(m.topic_key == "photosynthesis" for m in weak),
    )
    check(
        "J5 strong_topics surfaces quadratic_eq",
        any(m.topic_key == "quadratic_eq" for m in strong),
    )

    # K4 preschool
    preschool.migrate()
    activities = preschool.list_activities()
    check(
        "K4 starter catalog has at least 10 activities",
        len(activities) >= 10,
        f"count={len(activities)}",
    )
    # Idempotent re-migrate (seed dedup)
    preschool.migrate()
    activities2 = preschool.list_activities()
    check(
        "K4 migrate idempotent — count unchanged after re-run",
        len(activities2) == len(activities),
    )
    hi = preschool.list_activities(language="hi")
    check(
        "K4 Hindi filter returns Hindi activities",
        len(hi) >= 2 and all(a.language == "hi" for a in hi),
    )
    cats = preschool.categories()
    check(
        "K4 categories has 5+ activity types",
        len(cats) >= 5,
        str(cats),
    )

    # H7 procurement
    skus = procurement.list_skus()
    check(
        "H7 SKU catalog has 6 entries",
        len(skus) == 6,
        f"count={len(skus)}",
    )
    school = procurement.get_sku("padhai-school-basic")
    check(
        "H7 school basic priced at ₹600/user/year",
        school.price_inr_per_user_year == 600,
    )
    govt = procurement.get_sku("padhai-govt-state")
    check(
        "H7 govt-state shows custom pricing",
        govt.price_inr_per_user_year == 0,
    )
    check(
        "H7 get_sku returns None for unknown",
        procurement.get_sku("padhai-bogus") is None,
    )

    # HTTP layer
    with TestClient(app) as c:
        r = c.get("/api/preschool/activities")
        check(
            "GET /api/preschool/activities returns 200",
            r.status_code == 200 and len(r.json()["rows"]) >= 10,
        )
        r = c.get("/api/preschool/categories")
        check(
            "GET /api/preschool/categories returns 200",
            r.status_code == 200 and "phonics" in r.json(),
        )
        r = c.get("/api/procurement/skus")
        check(
            "GET /api/procurement/skus returns 6 SKUs",
            r.status_code == 200 and len(r.json()["skus"]) == 6,
        )
        r = c.get("/api/procurement/skus/padhai-coaching-jee")
        check(
            "GET /api/procurement/skus/{code} returns SKU",
            r.status_code == 200 and r.json()["code"] == "padhai-coaching-jee",
        )
        r = c.get("/api/procurement/skus/bogus")
        check(
            "GET /api/procurement/skus/bogus returns 404",
            r.status_code == 404,
        )
        r = c.get("/api/me/mastery")
        check(
            "GET /api/me/mastery requires auth (401)",
            r.status_code == 401,
        )
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
        )

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed: print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
