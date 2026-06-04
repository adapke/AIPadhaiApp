"""Smoke for v2.6 — O1 teacher publishing + O2 content market + N4 mentor.

Run: PYTHONPATH=. python scripts/test_v2_6.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_6_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import content_market, mentorship, teacher_publishing
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.6 smoke test")
    print("---------------")

    # --- O1 teacher publishing ---
    teacher_publishing.migrate()
    c = teacher_publishing.apply_as_creator(
        user_id="teach-1", display_name="Anjali UPSC",
        bio="UPSC GS-1 teacher",
    )
    check(
        "O1 apply_as_creator returns Creator status='pending'",
        c.status == "pending" and not c.verified,
    )

    # Duplicate apply
    try:
        teacher_publishing.apply_as_creator(
            user_id="teach-1", display_name="dup",
        )
        check("O1 apply_as_creator rejects duplicate user", False)
    except ValueError:
        check("O1 apply_as_creator rejects duplicate user", True)

    # Bad name
    try:
        teacher_publishing.apply_as_creator(
            user_id="teach-2", display_name="x",
        )
        check("O1 apply_as_creator rejects short name", False)
    except ValueError:
        check("O1 apply_as_creator rejects short name", True)

    # Series creation rejected while pending
    try:
        teacher_publishing.create_series(
            creator_user_id="teach-1",
            title="Modern History Crash Course",
            price_paise=49900,
        )
        check("O1 create_series rejects pending creator", False)
    except ValueError:
        check("O1 create_series rejects pending creator", True)

    # Approve + create series
    check(
        "O1 approve_creator → True",
        teacher_publishing.approve_creator(creator_id=c.id, verified=True),
    )
    s = teacher_publishing.create_series(
        creator_user_id="teach-1",
        title="Modern History Crash Course",
        price_paise=49900,
        exam="upsc_mains", subject="history", language="en",
    )
    check(
        "O1 create_series returns Series status='draft'",
        s.status == "draft" and s.price_paise == 49900,
    )

    # Price out of bounds
    try:
        teacher_publishing.create_series(
            creator_user_id="teach-1", title="Cheap series",
            price_paise=100,
        )
        check("O1 create_series rejects price below floor", False)
    except ValueError:
        check("O1 create_series rejects price below floor", True)

    # Can't publish empty series
    try:
        teacher_publishing.publish_series(
            series_id=s.id, creator_user_id="teach-1",
        )
        check("O1 publish_series rejects empty series", False)
    except ValueError:
        check("O1 publish_series rejects empty series", True)

    # Add lessons
    l1 = teacher_publishing.add_lesson(
        series_id=s.id, creator_user_id="teach-1",
        title="Lesson 1: Mughal Decline",
        duration_seconds=900, video_url="https://r2.example.com/l1.mp4",
        free_preview=True,
    )
    l2 = teacher_publishing.add_lesson(
        series_id=s.id, creator_user_id="teach-1",
        title="Lesson 2: Battle of Plassey",
        duration_seconds=1200,
    )
    check(
        "O1 add_lesson assigns positions 1, 2 sequentially",
        l1.position == 1 and l2.position == 2,
    )
    s_post = teacher_publishing.get_series(s.id)
    check(
        "O1 lesson_count + total_minutes rollups updated",
        s_post.lesson_count == 2
        and s_post.total_minutes == (900 // 60 + 1200 // 60),
    )

    # Non-creator can't add lesson
    try:
        teacher_publishing.add_lesson(
            series_id=s.id, creator_user_id="not-teach-1",
            title="hijack",
        )
        check("O1 add_lesson rejects non-owner", False)
    except PermissionError:
        check("O1 add_lesson rejects non-owner", True)

    # Publish
    check(
        "O1 publish_series → True on draft with lessons",
        teacher_publishing.publish_series(
            series_id=s.id, creator_user_id="teach-1",
        ),
    )
    s_pub = teacher_publishing.get_series(s.id)
    check(
        "O1 published series has published_at",
        s_pub.status == "published" and s_pub.published_at is not None,
    )

    # Storefront lists it
    front = teacher_publishing.list_storefront(exam="upsc_mains")
    check(
        "O1 storefront includes the published series",
        any(x.id == s.id for x in front),
    )

    # Purchase
    p = teacher_publishing.purchase(user_id="buyer-1", series_id=s.id)
    check(
        "O1 purchase returns Purchase with 70/30 split",
        p.price_paise == 49900
        and p.platform_fee_paise == int(49900 * 0.30)
        and p.creator_payout_paise == 49900 - int(49900 * 0.30),
    )

    # Creator can't buy own series
    try:
        teacher_publishing.purchase(user_id="teach-1", series_id=s.id)
        check("O1 purchase rejects creator buying own series", False)
    except ValueError:
        check("O1 purchase rejects creator buying own series", True)

    # Double-purchase rejected
    try:
        teacher_publishing.purchase(user_id="buyer-1", series_id=s.id)
        check("O1 purchase rejects duplicate by same user", False)
    except ValueError:
        check("O1 purchase rejects duplicate by same user", True)

    # has_access reflects purchase + ownership
    check(
        "O1 has_access True for buyer + creator",
        teacher_publishing.has_access(user_id="buyer-1", series_id=s.id)
        and teacher_publishing.has_access(user_id="teach-1", series_id=s.id),
    )
    check(
        "O1 has_access False for stranger",
        not teacher_publishing.has_access(
            user_id="random", series_id=s.id,
        ),
    )

    # Creator earnings rollup
    earn = teacher_publishing.creator_earnings("teach-1")
    check(
        "O1 creator_earnings shows 1 purchase + payout",
        earn["total_purchases"] == 1
        and earn["total_payout_paise"] == p.creator_payout_paise,
    )

    # --- O2 content marketplace ---
    content_market.migrate()
    pub = content_market.register_publisher(
        name="CBSE NCERT Board",
        kind="board",
        contact_email="cbse@example.org",
    )
    check(
        "O2 register_publisher returns Publisher status=unverified",
        pub.kind == "board" and not pub.verified,
    )

    # Duplicate publisher rejected
    try:
        content_market.register_publisher(
            name="CBSE NCERT Board", kind="board",
        )
        check("O2 register_publisher rejects duplicate", False)
    except ValueError:
        check("O2 register_publisher rejects duplicate", True)

    # Bad kind
    try:
        content_market.register_publisher(name="X", kind="bogus")
        check("O2 register_publisher rejects bad kind", False)
    except ValueError:
        check("O2 register_publisher rejects bad kind", True)

    # Unverified can't create packs
    try:
        content_market.create_pack(
            publisher_id=pub.id,
            title="Class 10 Math NCERT-aligned",
            price_inr_per_seat_year=300,
        )
        check("O2 create_pack rejects unverified publisher", False)
    except ValueError:
        check("O2 create_pack rejects unverified publisher", True)

    # Verify + create
    check(
        "O2 verify_publisher → True",
        content_market.verify_publisher(pub.id, verified=True),
    )
    pack = content_market.create_pack(
        publisher_id=pub.id,
        title="Class 10 Math NCERT-aligned",
        description="Full chapter coverage with worked examples",
        board="cbse", grade=10, subject="mathematics",
        chapter_count=15,
        price_inr_per_seat_year=300,
        language="en",
    )
    check(
        "O2 create_pack returns Pack status='draft'",
        pack.status == "draft" and pack.chapter_count == 15,
    )

    # Publish + subscribe
    check(
        "O2 publish_pack → True",
        content_market.publish_pack(
            pack_id=pack.id, publisher_id=pub.id,
        ),
    )

    sub = content_market.subscribe(
        org_id="org-school-1", pack_id=pack.id,
        seats=100, duration_days=365,
    )
    check(
        "O2 subscribe returns Subscription with platform fee + payout",
        sub.seats == 100
        and sub.total_paid_paise == 100 * 300 * 100   # 100 seats × ₹300 × 100 paise
        and sub.platform_fee_paise == int(sub.total_paid_paise * 0.10)
        and sub.publisher_payout_paise
            == sub.total_paid_paise - sub.platform_fee_paise,
    )

    # Re-subscribe expires old
    sub2 = content_market.subscribe(
        org_id="org-school-1", pack_id=pack.id, seats=150,
    )
    check(
        "O2 re-subscribe expires old + creates new",
        sub2.id != sub.id and sub2.status == "active",
    )

    # Pro-rated 30 days
    sub3 = content_market.subscribe(
        org_id="org-school-2", pack_id=pack.id,
        seats=10, duration_days=30,
    )
    expected_inr = round(10 * 300 * (30 / 365))
    check(
        "O2 30-day subscription is pro-rated",
        sub3.total_paid_paise == expected_inr * 100,
        f"got ₹{sub3.total_paid_paise/100} expected ₹{expected_inr}",
    )

    # Bad seats
    try:
        content_market.subscribe(
            org_id="org-x", pack_id=pack.id, seats=0,
        )
        check("O2 subscribe rejects seats < 1", False)
    except ValueError:
        check("O2 subscribe rejects seats < 1", True)

    # list + earnings
    org_subs = content_market.list_subscriptions_for_org("org-school-1")
    check(
        "O2 list_subscriptions_for_org returns 2 (active + expired)",
        len(org_subs) == 2,
    )

    pe = content_market.publisher_earnings(pub.id)
    check(
        "O2 publisher_earnings counts active subs only",
        pe["active_subscriptions"] == 2
        and pe["total_seats"] == 150 + 10,
    )

    # --- N4 mentor program ---
    mentorship.migrate()
    m = mentorship.apply(
        user_id="mentor-1",
        bio="UPSC 2023 (AIR 145), mentoring 1-on-1 for prelims",
        expertise_subjects=["polity", "history", "geography"],
        expertise_exams=["upsc"],
        available_hours_week=5,
        year_of_passing=2023,
        college="IIT-Delhi",
        languages=["en", "hi"],
    )
    check(
        "N4 apply returns MentorProfile status='applied'",
        m.status == "applied"
        and "polity" in m.expertise_subjects
        and m.year_of_passing == 2023,
    )

    # Duplicate apply rejected
    try:
        mentorship.apply(user_id="mentor-1")
        check("N4 apply rejects duplicate user", False)
    except ValueError:
        check("N4 apply rejects duplicate user", True)

    # Bad hours
    try:
        mentorship.apply(user_id="x", available_hours_week=99)
        check("N4 apply rejects available_hours_week > 40", False)
    except ValueError:
        check("N4 apply rejects available_hours_week > 40", True)

    # Bad year_of_passing
    try:
        mentorship.apply(user_id="y", year_of_passing=1950)
        check("N4 apply rejects year_of_passing < 2000", False)
    except ValueError:
        check("N4 apply rejects year_of_passing < 2000", True)

    # Promote to active
    check(
        "N4 set_mentor_status → active",
        mentorship.set_mentor_status(
            user_id="mentor-1", status="active",
        ),
    )

    # list_active_mentors filtering
    mentors = mentorship.list_active_mentors(subject="polity")
    check(
        "N4 list_active_mentors with subject filter surfaces mentor-1",
        any(p.user_id == "mentor-1" for p in mentors),
    )

    # Mentee can't request session with non-active mentor
    mentorship.apply(user_id="paused-mentor",
                     expertise_subjects=["bio"], expertise_exams=["neet"])
    try:
        mentorship.request_session(
            mentor_user_id="paused-mentor",
            mentee_user_id="aspirant-1",
            scheduled_at=time.time() + 3600,
        )
        check("N4 request_session rejects non-active mentor", False)
    except ValueError:
        check("N4 request_session rejects non-active mentor", True)

    # Self-pairing rejected
    try:
        mentorship.request_session(
            mentor_user_id="mentor-1",
            mentee_user_id="mentor-1",
            scheduled_at=time.time() + 3600,
        )
        check("N4 request_session rejects mentor==mentee", False)
    except ValueError:
        check("N4 request_session rejects mentor==mentee", True)

    # Successful request
    s_req = mentorship.request_session(
        mentor_user_id="mentor-1",
        mentee_user_id="aspirant-X",
        scheduled_at=time.time() + 3600,
        duration_min=60,
        topic="Polity prelims revision",
    )
    check(
        "N4 request_session returns Session status='scheduled'",
        s_req.status == "scheduled" and s_req.duration_min == 60,
    )

    # Wrong mentor can't complete
    try:
        mentorship.complete_session(
            session_id=s_req.id,
            mentor_user_id="some-other-mentor",
        )
        check("N4 complete_session rejects wrong mentor", False)
    except PermissionError:
        check("N4 complete_session rejects wrong mentor", True)

    # Complete + check hours rollup
    s_done = mentorship.complete_session(
        session_id=s_req.id, mentor_user_id="mentor-1",
        actual_duration_min=60, notes="Covered Fundamental Rights",
    )
    check(
        "N4 complete_session marks status=completed + duration logged",
        s_done.status == "completed" and s_done.actual_duration_min == 60,
    )

    profile_after = mentorship.get_profile("mentor-1")
    check(
        "N4 hours_logged bumped by 1.0 + free_months_earned=1",
        abs(profile_after.hours_logged - 1.0) < 0.01
        and profile_after.free_months_earned == 1,
        f"hours={profile_after.hours_logged} months={profile_after.free_months_earned}",
    )

    # Review by mentee
    rid = mentorship.review_session(
        session_id=s_req.id, reviewer_user_id="aspirant-X",
        rating=5, feedback="Cleared all doubts patiently.",
    )
    check("N4 review_session returns review_id", bool(rid))

    # Non-mentee can't review
    s_req2 = mentorship.request_session(
        mentor_user_id="mentor-1",
        mentee_user_id="aspirant-Y",
        scheduled_at=time.time() + 7200,
    )
    mentorship.complete_session(
        session_id=s_req2.id, mentor_user_id="mentor-1",
    )
    try:
        mentorship.review_session(
            session_id=s_req2.id, reviewer_user_id="random-user",
            rating=4,
        )
        check("N4 review_session rejects non-mentee", False)
    except PermissionError:
        check("N4 review_session rejects non-mentee", True)

    # Bad rating
    try:
        mentorship.review_session(
            session_id=s_req2.id, reviewer_user_id="aspirant-Y",
            rating=99,
        )
        check("N4 review_session rejects rating > 5", False)
    except ValueError:
        check("N4 review_session rejects rating > 5", True)

    # Duplicate review rejected
    mentorship.review_session(
        session_id=s_req2.id, reviewer_user_id="aspirant-Y",
        rating=4, feedback="great",
    )
    try:
        mentorship.review_session(
            session_id=s_req2.id, reviewer_user_id="aspirant-Y",
            rating=5,
        )
        check("N4 review_session rejects duplicate review", False)
    except ValueError:
        check("N4 review_session rejects duplicate review", True)

    # Mentor profile rating reflects average
    profile_rated = mentorship.get_profile("mentor-1")
    check(
        "N4 mentor profile rating_avg = (5+4)/2",
        abs(profile_rated.rating_avg - 4.5) < 0.01
        and profile_rated.rating_count == 2,
        f"avg={profile_rated.rating_avg} count={profile_rated.rating_count}",
    )

    reviews = mentorship.list_reviews(mentor_user_id="mentor-1")
    check(
        "N4 list_reviews returns 2 reviews",
        len(reviews) == 2,
    )

    # Cancel + no-show paths
    s_to_cancel = mentorship.request_session(
        mentor_user_id="mentor-1",
        mentee_user_id="aspirant-Z",
        scheduled_at=time.time() + 86400,
    )
    check(
        "N4 cancel_session by mentee → True",
        mentorship.cancel_session(
            session_id=s_to_cancel.id, user_id="aspirant-Z",
        ),
    )
    check(
        "N4 cancel_session by stranger → False",
        not mentorship.cancel_session(
            session_id=s_to_cancel.id, user_id="stranger",
        ),
    )

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Public reads
        r = c.get("/api/publishing/storefront?exam=upsc_mains")
        check(
            "GET /api/publishing/storefront returns 200 + rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        r = c.get("/api/content/publishers?kind=board")
        check(
            "GET /api/content/publishers?kind=board returns 200",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        r = c.get("/api/content/packs?board=cbse&grade=10")
        check(
            "GET /api/content/packs returns the published math pack",
            r.status_code == 200
            and any(p["title"].startswith("Class 10 Math")
                    for p in r.json()["rows"]),
        )

        r = c.get(f"/api/content/packs/{pack.id}")
        check(
            "GET /api/content/packs/{pid} returns 200",
            r.status_code == 200 and r.json()["chapter_count"] == 15,
        )

        r = c.get("/api/content/packs/no-such-id")
        check(
            "GET /api/content/packs/{bogus} returns 404",
            r.status_code == 404,
        )

        r = c.get("/api/mentors?subject=polity")
        check(
            "GET /api/mentors?subject=polity returns 200 + rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        r = c.get("/api/mentors/mentor-1/reviews")
        check(
            "GET /api/mentors/{uid}/reviews returns 200",
            r.status_code == 200 and len(r.json()["rows"]) >= 2,
        )

        # Auth-gated
        for path in (
            "/api/publishing/me/purchases",
            "/api/publishing/me/earnings",
            "/api/mentors/me",
        ):
            r = c.get(path)
            check(f"GET {path} → 401", r.status_code == 401)

        r = c.post(
            "/api/publishing/creators",
            data={"display_name": "Valid Name Here"},
        )
        check(
            "POST /api/publishing/creators requires auth (401)",
            r.status_code == 401,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Route count
    check(
        "v2.6 brought route count above 280",
        len(app.routes) > 280,
        f"routes={len(app.routes)}",
    )

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
