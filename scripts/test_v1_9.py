"""Smoke for v1.9 — K2 countries, K3 coaching.
Run: PYTHONPATH=. python scripts/test_v1_9.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_9_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import coaching, countries
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v1.9 smoke test")
    print("---------------")

    # K2 countries
    p_in = countries.profile_for("IN")
    check(
        "K2 India profile maps to razorpay + INR + Asia/Kolkata",
        p_in.payment_provider == "razorpay" and p_in.currency == "INR"
        and p_in.timezone == "Asia/Kolkata",
    )
    p_bd = countries.profile_for("BD")
    check(
        "K2 Bangladesh profile maps to bkash + BDT",
        p_bd.payment_provider == "bkash" and p_bd.currency == "BDT",
    )
    p_np = countries.profile_for("NP")
    check(
        "K2 Nepal profile maps to esewa + NPR",
        p_np.payment_provider == "esewa" and p_np.currency == "NPR",
    )
    p_lk = countries.profile_for("LK")
    check(
        "K2 Sri Lanka profile maps to payhere + LKR",
        p_lk.payment_provider == "payhere" and p_lk.currency == "LKR",
    )
    p_unknown = countries.profile_for("XX")
    check("K2 unknown country defaults to India profile", p_unknown.code == "IN")

    # K3 coaching
    coaching.migrate()
    t = coaching.create_track(
        exam="upsc", name="UPSC CSE 2026", target_year=2026,
        subjects=["Polity", "Economy", "Geography", "History"],
    )
    check(
        "K3 create_track UPSC returns track",
        t.exam == "upsc" and t.target_year == 2026 and len(t.subjects) == 4,
    )
    try:
        coaching.create_track(exam="bogus_exam", name="x")
        check("K3 create_track rejects unknown exam", False)
    except ValueError:
        check("K3 create_track rejects unknown exam", True)

    tracks = coaching.list_tracks(exam="upsc")
    check("K3 list_tracks(exam=upsc) returns 1", len(tracks) >= 1)

    # Record attempts → stats
    for i in range(5):
        coaching.record_attempt(
            user_id="aspirant-1", exam="upsc", subject="Polity",
            correct=(i % 2 == 0), time_seconds=45 + i,
        )
    for i in range(5):
        coaching.record_attempt(
            user_id="aspirant-1", exam="upsc", subject="Economy",
            correct=(i < 1), time_seconds=60,
        )
    stats = coaching.subject_stats(user_id="aspirant-1", exam="upsc")
    check(
        "K3 subject_stats shows Polity + Economy",
        "Polity" in stats and "Economy" in stats,
    )
    check(
        "K3 Polity accuracy ~0.6, Economy ~0.2",
        abs(stats["Polity"]["accuracy"] - 0.6) < 0.01
        and abs(stats["Economy"]["accuracy"] - 0.2) < 0.01,
        f"P={stats['Polity']['accuracy']} E={stats['Economy']['accuracy']}",
    )
    weak = coaching.weak_subjects(user_id="aspirant-1", exam="upsc")
    check(
        "K3 weak_subjects flags Economy (below 0.6 threshold)",
        "Economy" in weak and "Polity" not in weak,
        str(weak),
    )

    # Current affairs
    cid = coaching.add_current_affair(
        title="GST council meeting decisions",
        summary="The GST Council reduced rates on small items.",
        topic_tags=["economy", "polity"],
        relevance=8,
        exams=["upsc"],
    )
    check("K3 add_current_affair returns id", bool(cid))
    digest = coaching.daily_digest(exam="upsc", limit=10)
    check(
        "K3 daily_digest returns the new GST item",
        any("GST" in d["title"] for d in digest),
    )

    # HTTP layer
    with TestClient(app) as c:
        r = c.get("/api/countries")
        check(
            "GET /api/countries returns all 5 markets",
            r.status_code == 200 and len(r.json()["countries"]) >= 5,
        )

        r = c.get("/api/coaching/tracks?exam=upsc")
        check(
            "GET /api/coaching/tracks?exam=upsc returns rows",
            r.status_code == 200 and len(r.json()["tracks"]) >= 1,
        )

        r = c.get("/api/coaching/digest?exam=upsc")
        check(
            "GET /api/coaching/digest returns items",
            r.status_code == 200 and len(r.json()["items"]) >= 1,
        )

        r = c.get("/api/coaching/practice/stats?exam=upsc")
        check(
            "GET /api/coaching/practice/stats requires auth (401)",
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
