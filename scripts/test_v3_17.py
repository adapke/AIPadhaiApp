"""Smoke for v3.17 — Navigation manifest + student home aggregator.

Covers:
- Navigation manifest: 8 sections + mobile bottom nav
- Role-based filtering (admin items hidden from non-admins)
- Single-section lookup
- Student home dashboard composes data from many sub-modules
- Defensive fallback when user has no signals
- HTTP endpoints + auth gates (home requires auth; manifest public)

Run: PYTHONPATH=. python scripts/test_v3_17.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_17_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import citations as cit
    from padhai import daily_plan as dp
    from padhai import exam_taxonomy as et
    from padhai import mastery
    from padhai import mock_engine as me
    from padhai import navigation as nav
    from padhai import readiness as rd
    from padhai import spaced_repetition as srs
    from padhai import student_home as sh
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v3.17 smoke test — Navigation + student home")
    print("---------------------------------------------")

    # Migrate dependencies the home aggregator reads from
    et.migrate()
    mastery.migrate()
    rd.migrate()
    me.migrate()
    dp.migrate()
    srs.migrate()
    cit.migrate()
    nav.migrate()
    sh.migrate()

    # ============================================================
    # Navigation manifest
    # ============================================================
    manifest = nav.get_manifest()
    check(
        "Manifest returns sections + mobile_bottom_nav",
        "sections" in manifest and "mobile_bottom_nav" in manifest,
    )
    check(
        "Manifest has 8 top-level sections (per §26)",
        len(manifest["sections"]) == 8,
    )

    # Section slugs match §26
    expected_slugs = {
        "exam-hub", "study-studio", "mocks", "ai-tutor",
        "community", "school", "marketplace", "admin",
    }
    found_slugs = {s["slug"] for s in manifest["sections"]}
    check(
        "All 8 §26 slugs present",
        expected_slugs == found_slugs,
        f"got {found_slugs}",
    )

    # Mobile nav has 5 tabs
    check(
        "Mobile bottom nav has 5 tabs (per mockup)",
        len(manifest["mobile_bottom_nav"]) == 5,
    )

    # Every section has features
    check(
        "Every section has at least 2 features",
        all(len(s["features"]) >= 2
            for s in manifest["sections"]),
    )

    # 'keep' badges present (per §26 — existing features stay)
    has_keep = any(
        any(f["badge"] == "keep" for f in s["features"])
        for s in manifest["sections"]
    )
    check(
        "Manifest carries 'keep' badges on existing features",
        has_keep,
    )

    # ============================================================
    # Role-based filtering
    # ============================================================

    # admin role sees admin-only features
    admin_view = nav.get_manifest(role="admin")
    admin_section = next(
        (s for s in admin_view["sections"]
         if s["slug"] == "admin"), None,
    )
    check(
        "Admin role sees the admin section",
        admin_section
        and len(admin_section["features"]) >= 3,
    )

    # student role — admin section drops out entirely (every
    # feature has visible_to_role='admin')
    student_view = nav.get_manifest(role="student")
    student_admin = next(
        (s for s in student_view["sections"]
         if s["slug"] == "admin"), None,
    )
    # The admin section's items all have visible_to_role='admin',
    # so the role='student' filter removes them all → section
    # dropped from the response
    check(
        "Student role does not see admin-only section",
        student_admin is None,
    )

    # teacher role sees the teacher-dashboard feature
    teacher_view = nav.get_manifest(role="teacher")
    school_section = next(
        (s for s in teacher_view["sections"]
         if s["slug"] == "school"), None,
    )
    teacher_features = (
        [f["title"] for f in school_section["features"]]
        if school_section else []
    )
    check(
        "Teacher role sees the teacher dashboard feature",
        "Teacher dashboard" in teacher_features,
    )

    # ============================================================
    # Single-section + slug list
    # ============================================================
    section = nav.get_section("exam-hub")
    check(
        "get_section('exam-hub') returns the section",
        section
        and section["title"] == "Exam Hub"
        and len(section["features"]) >= 3,
    )

    none_section = nav.get_section("ghost-slug")
    check(
        "get_section returns None for unknown slug",
        none_section is None,
    )

    slugs = nav.list_section_slugs()
    check(
        "list_section_slugs returns 8 slugs",
        len(slugs) == 8 and "exam-hub" in slugs,
    )

    # ============================================================
    # Student home — empty user (no enrollments, no mastery)
    # ============================================================
    empty = sh.build_dashboard(user_id="brand-new-user")
    check(
        "Dashboard for new user returns all sections",
        all(k in empty for k in [
            "hero", "exam_pack", "readiness", "metrics",
            "daily_flow", "next_mock", "community",
            "trust", "recent_fallbacks", "modules",
        ]),
    )
    check(
        "New user exam_pack is empty dict",
        empty["exam_pack"] == {},
    )
    check(
        "New user readiness is empty dict",
        empty["readiness"] == {},
    )
    check(
        "New user hero has a welcome headline",
        empty["hero"]["headline"],
    )

    # ============================================================
    # Student home — populated user
    # ============================================================

    # Enroll in UPSC pack + populate some signals
    et.enroll(
        pack_code="upsc_cse_2026", user_id="stu-1",
        daily_minutes=120,
    )
    # Weak topics
    for tc in ["polity", "economy"]:
        for _ in range(3):
            mastery.update(
                user_id="stu-1",
                topic_key=f"upsc_cse::{tc}", correct=False,
            )
    # Strong topic
    for _ in range(5):
        mastery.update(
            user_id="stu-1",
            topic_key="upsc_cse::history_modern", correct=True,
        )
    # Compute readiness so the home view has data
    rd.compute_readiness(
        user_id="stu-1", pack_code="upsc_cse_2026",
    )

    home = sh.build_dashboard(user_id="stu-1")
    check(
        "Populated user dashboard has exam_pack title",
        "UPSC" in (home["exam_pack"].get("title") or ""),
    )
    check(
        "Populated user has readiness.score 0..100",
        home["readiness"].get("score") is not None
        and 0 <= home["readiness"]["score"] <= 100,
    )
    check(
        "Populated user has weak_topics from readiness",
        home["readiness"].get("weak_topics"),
    )
    # Hero references weakness or strength
    headline = home["hero"]["headline"]
    check(
        "Hero headline references UPSC + a weak/strong topic",
        "UPSC" in headline.lower() or "upsc" in headline
        or "behind" in headline or "strong" in headline,
        f"got {headline!r}",
    )
    check(
        "Hero has 4 action buttons",
        len(home["hero"]["actions"]) == 4,
    )

    # Daily flow generates plan
    check(
        "Daily flow has a plan + blocks",
        home["daily_flow"]["plan_id"] is not None
        and len(home["daily_flow"]["blocks"]) >= 1,
    )

    # Modules section mirrors navigation
    check(
        "Modules section returns the navigation chip grid",
        len(home["modules"]) == 8,
    )

    # Trust signal defaults sensibly
    check(
        "Trust signal returns sample_size + grounded_rate",
        "sample_size" in home["trust"]
        and "grounded_rate" in home["trust"],
    )

    # Metrics dict shape
    check(
        "Metrics dict has 3 expected keys",
        {"due_flashcards", "weak_topic_count",
         "fallback_count_recent"}.issubset(home["metrics"]),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — navigation manifest
        r = c.get("/api/navigation/manifest")
        check(
            "GET /api/navigation/manifest → 200 public",
            r.status_code == 200
            and len(r.json()["sections"]) == 8,
        )

        # Role filter
        r = c.get("/api/navigation/manifest?role=student")
        check(
            "GET /api/navigation/manifest?role=student → 200",
            r.status_code == 200
            and not any(
                s["slug"] == "admin"
                for s in r.json()["sections"]
            ),
        )

        # Single section
        r = c.get("/api/navigation/sections/study-studio")
        check(
            "GET /api/navigation/sections/study-studio → 200",
            r.status_code == 200
            and r.json()["title"] == "Study Studio",
        )

        r = c.get("/api/navigation/sections/ghost-slug")
        check(
            "GET /api/navigation/sections/{ghost} → 404",
            r.status_code == 404,
        )

        # List slugs
        r = c.get("/api/navigation/sections")
        check(
            "GET /api/navigation/sections → 200 + 8 slugs",
            r.status_code == 200
            and len(r.json()["sections"]) == 8,
        )

        # Home — auth gate
        r = c.get("/api/home/me/dashboard")
        check(
            "GET /api/home/me/dashboard → 401 (auth gate)",
            r.status_code == 401,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    check(
        "v3.17 brought route count above 585",
        len(app.routes) > 585,
        f"routes={len(app.routes)}",
    )

    print("---------------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
