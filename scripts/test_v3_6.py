"""Smoke for v3.6 — Parent + Teacher dashboards.

Covers:
- Parent dashboard: links resolved, child summaries composed
- Teacher dashboard: org + class scoping, class-wide weak-topic
  rollup, single-student detail
- Access control: parents only see their children; teachers only
  see their org
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_6.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_6_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import daily_plan as dp
    from padhai import dashboards as dash
    from padhai import exam_taxonomy as et
    from padhai import mastery, orgs, parents
    from padhai import mock_engine as me
    from padhai import moderation_queue as mq
    from padhai import readiness as rd
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    def raises(fn, exc_type=Exception, **kwargs):
        try:
            fn(**kwargs); return False
        except exc_type:
            return True

    print("v3.6 smoke test — Parent + Teacher dashboards")
    print("---------------------------------------------")

    # Migrate the surface we need.
    # parents.py + orgs.py bootstrap their schema on first _conn().
    mastery.migrate()
    et.migrate()
    rd.migrate()
    me.migrate()
    dp.migrate()
    mq.migrate()
    dash.migrate()    # no-op

    # ============================================================
    # Set up family + activity
    # ============================================================

    # Parent + 2 children
    PARENT = "parent-1"
    CHILD_A = "child-a"
    CHILD_B = "child-b"
    OUTSIDER = "outsider-1"   # no link

    # Link parent → child-a (verified)
    link_a, token_a = parents.invite(
        parent_user_id=PARENT, child_user_id=CHILD_A,
        initiated_by="parent", relation="father",
    )
    parents.verify(
        token=token_a, acting_user_id=CHILD_A,
        acting_ip="127.0.0.1",
    )

    # Link parent → child-b (verified)
    link_b, token_b = parents.invite(
        parent_user_id=PARENT, child_user_id=CHILD_B,
        initiated_by="parent", relation="father",
    )
    parents.verify(
        token=token_b, acting_user_id=CHILD_B,
        acting_ip="127.0.0.1",
    )

    # Child-A enrolls in UPSC pack + does some activity
    et.enroll(
        pack_code="upsc_cse_2026", user_id=CHILD_A,
        daily_minutes=180,
    )
    for tc in ["polity", "economy", "history_modern"]:
        for ok in [True, True, False]:
            mastery.update(
                user_id=CHILD_A,
                topic_key=f"upsc_cse::{tc}", correct=ok,
            )
    rd.compute_readiness(user_id=CHILD_A, pack_code="upsc_cse_2026")
    dp.generate_plan(user_id=CHILD_A, pack_code="upsc_cse_2026")

    # Child-B enrolls in CBSE pack
    et.enroll(
        pack_code="cbse_class_10_2026", user_id=CHILD_B,
        daily_minutes=90,
    )
    rd.compute_readiness(
        user_id=CHILD_B, pack_code="cbse_class_10_2026",
    )

    # ============================================================
    # Parent dashboard
    # ============================================================

    parent_dash = dash.parent_dashboard(PARENT)
    check(
        "Parent dashboard returns parent_user_id + children + computed_at",
        parent_dash["parent_user_id"] == PARENT
        and "children" in parent_dash
        and "computed_at" in parent_dash,
    )
    check(
        "Parent dashboard returns both verified children",
        len(parent_dash["children"]) == 2,
    )

    # Find child-a summary
    child_a_block = next(
        (c for c in parent_dash["children"]
         if c["child_user_id"] == CHILD_A),
        None,
    )
    check(
        "Child-A block has summary keys",
        child_a_block and {
            "exam_packs", "mastery", "mock_attempts",
            "daily_plan", "streak", "trust", "recent_fallbacks",
        }.issubset(child_a_block["summary"]),
    )

    # Child-A exam pack appears with readiness score
    packs = child_a_block["summary"]["exam_packs"]
    check(
        "Child-A's exam pack is in summary",
        any(p["pack_code"] == "upsc_cse_2026" for p in packs),
    )
    pack_a = next(
        p for p in packs if p["pack_code"] == "upsc_cse_2026"
    )
    check(
        "Child-A pack has readiness score 0..100",
        pack_a["readiness"] is not None
        and 0 <= pack_a["readiness"]["score"] <= 100,
    )

    # Child-A has daily plan stats (at least 1 day)
    check(
        "Child-A daily plan stats > 0 days",
        child_a_block["summary"]["daily_plan"]["days"] >= 1,
    )

    # Parent with no children
    empty_dash = dash.parent_dashboard("parent-with-nothing")
    check(
        "Parent with no links → empty children list",
        empty_dash["children"] == [],
    )

    # Outsider trying to see another's children — they get their
    # own dashboard, which is empty
    outsider_dash = dash.parent_dashboard(OUTSIDER)
    check(
        "Outsider sees only their own (empty) dashboard",
        outsider_dash["children"] == [],
    )

    # Revoked link doesn't appear
    parents.revoke(link_id=link_b.id, acting_user_id=PARENT)
    after_revoke = dash.parent_dashboard(PARENT)
    check(
        "Revoked link drops child from parent dashboard",
        len(after_revoke["children"]) == 1
        and after_revoke["children"][0]["child_user_id"] == CHILD_A,
    )

    # ============================================================
    # Teacher dashboard
    # ============================================================

    # Create org + teacher + 3 students (one with mastery, one with mocks)
    org = orgs.create_org(
        name="Test School", kind="school",
        owner_user_id="principal-1",
    )
    org_class = orgs.add_class(
        org_id=org.id, name="Class 10A",
    )
    # Make the school principal the teacher (they're admin already
    # by virtue of create_org), and also explicitly invite a
    # dedicated teacher.
    TEACHER = "teacher-1"
    orgs.add_member(
        org_id=org.id, user_id=TEACHER,
        role="teacher", class_id=org_class.id,
        display_name="Ms. Sharma",
    )

    STU_1 = "stu-1"
    STU_2 = "stu-2"
    STU_3 = "stu-3"
    for sid, name in [(STU_1, "Aanya"), (STU_2, "Ravi"), (STU_3, "Maya")]:
        orgs.add_member(
            org_id=org.id, user_id=sid, role="student",
            class_id=org_class.id, display_name=name,
        )

    # Give STU_1 some practice on CBSE math chapters
    for tc, n_correct, n_wrong in [
        ("real_numbers", 2, 1),
        ("polynomials", 3, 0),
        ("triangles", 0, 3),       # weak
    ]:
        for _ in range(n_correct):
            mastery.update(
                user_id=STU_1,
                topic_key=f"cbse_class_10::{tc}", correct=True,
            )
        for _ in range(n_wrong):
            mastery.update(
                user_id=STU_1,
                topic_key=f"cbse_class_10::{tc}", correct=False,
            )
    et.enroll(
        pack_code="cbse_class_10_2026", user_id=STU_1,
        daily_minutes=60,
    )
    rd.compute_readiness(
        user_id=STU_1, pack_code="cbse_class_10_2026",
    )

    # STU_2: enrol + take a mock
    et.enroll(
        pack_code="cbse_class_10_2026", user_id=STU_2,
        daily_minutes=60,
    )
    # Create a quick mock paper + attempt
    paper = me.create_paper(
        title="CBSE 10 Math Mini Mock",
        pack_code="cbse_class_10_2026",
        exam_code="cbse_class_10",
        total_time_min=30,
        sections=[
            {"code": "math", "title": "Math",
             "marks_per_q": 1},
        ],
    )
    for i in range(1, 6):
        me.add_question(
            paper_id=paper.id, position=i,
            section_code="math", question_id=f"q-m-{i}",
            correct_answer=str(i),
        )
    me.publish_paper(paper.id)
    att = me.start_attempt(paper_id=paper.id, user_id=STU_2)
    for i in range(1, 6):
        me.submit_response(
            attempt_id=att.id, position=i,
            chosen_answer=str(i),    # all correct
            time_seconds=20,
        )
    me.submit_attempt(att.id)
    rd.compute_readiness(
        user_id=STU_2, pack_code="cbse_class_10_2026",
    )

    # STU_3: no activity at all — surfaces 'least active'

    # Teacher dashboard at org level
    org_dash = dash.teacher_dashboard(
        teacher_user_id=TEACHER, org_id=org.id,
    )
    check(
        "Teacher org dashboard returns students count + summary",
        org_dash["students"] == 3
        and "summary" in org_dash
        and "students_detail" in org_dash,
    )

    # Class-scoped should match (one class in this org)
    class_dash = dash.teacher_dashboard(
        teacher_user_id=TEACHER, org_id=org.id,
        class_id=org_class.id,
    )
    check(
        "Teacher class dashboard returns same 3 students",
        class_dash["students"] == 3
        and class_dash["class_id"] == org_class.id,
    )

    # Class-scoped to non-existent class → 0 students
    empty_class = dash.teacher_dashboard(
        teacher_user_id=TEACHER, org_id=org.id,
        class_id="ghost-class",
    )
    check(
        "Teacher dashboard with bad class_id → 0 students",
        empty_class["students"] == 0,
    )

    # Class aggregate has avg_readiness
    summary = class_dash["summary"]
    check(
        "Class aggregate has avg_readiness + avg_mock_percentile",
        "avg_readiness" in summary
        and "avg_mock_percentile" in summary,
    )

    # Triangles should surface as a class weak topic
    weak_topics = [
        w["topic_code"]
        for w in summary.get("class_weak_topics", [])
    ]
    check(
        "Class weak topics surface triangles (STU_1 is weak)",
        "triangles" in weak_topics,
    )

    # Student detail (in-org)
    detail = dash.teacher_student_detail(
        teacher_user_id=TEACHER, org_id=org.id,
        student_user_id=STU_1,
    )
    check(
        "Teacher student detail returns the student's data",
        detail["student_user_id"] == STU_1
        and "detail" in detail,
    )

    # Student detail (NOT in org) → PermissionError
    check(
        "Teacher student detail refuses non-org student",
        raises(
            dash.teacher_student_detail,
            exc_type=PermissionError,
            teacher_user_id=TEACHER, org_id=org.id,
            student_user_id="stranger-1",
        ),
    )

    # Moderation flags on class members — none initially
    check(
        "No moderation flags raised → 0",
        org_dash["moderation_flags"]["total_open_flags"] == 0,
    )

    # Flag a class member's content; should surface in dashboard
    mq.scan(
        content_kind="forum_post", content_id="post-classmate",
        body="spam_link_xyz buy_followers obvious spam",
        author_user_id=STU_1,
    )
    org_dash2 = dash.teacher_dashboard(
        teacher_user_id=TEACHER, org_id=org.id,
    )
    # Note: the scan creates an auto_resolved item, not 'open' —
    # so total_open_flags may still be 0. Add an open one:
    mq.scan(
        content_kind="forum_post", content_id="post-fp",
        body="check out spam_link_xyz only one hit",
        author_user_id=STU_2,
    )
    org_dash3 = dash.teacher_dashboard(
        teacher_user_id=TEACHER, org_id=org.id,
    )
    check(
        "Class moderation flag count includes class members",
        org_dash3["moderation_flags"]["total_open_flags"] >= 1,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.get("/api/parent/me/dashboard")
        check(
            "GET /api/parent/me/dashboard → 401",
            r.status_code == 401,
        )

        r = c.get(f"/api/parent/me/children/{CHILD_A}")
        check(
            "GET /api/parent/me/children/{id} → 401",
            r.status_code == 401,
        )

        r = c.get(f"/api/teacher/orgs/{org.id}/dashboard")
        check(
            "GET /api/teacher/orgs/{}/dashboard → 401",
            r.status_code == 401,
        )

        r = c.get(
            f"/api/teacher/orgs/{org.id}/students/{STU_1}",
        )
        check(
            "GET /api/teacher/orgs/{}/students/{} → 401",
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
        "v3.6 brought route count above 469",
        len(app.routes) > 469,
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
