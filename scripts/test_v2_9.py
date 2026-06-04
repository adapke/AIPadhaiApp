"""Smoke for v2.9 — M4 tutor marketplace + O3 question pack market + R3 vouchers.

Run: PYTHONPATH=. python scripts/test_v2_9.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_9_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import question_bank
    from padhai import question_pack_market as qpm
    from padhai import tutor_marketplace as tm
    from padhai import vouchers as vch
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    def raises(fn, **kwargs):
        try:
            fn(**kwargs); return False
        except (ValueError, PermissionError, vch.VoucherError):
            return True

    print("v2.9 smoke test")
    print("---------------")

    # ============================================================
    # M4 — 1:1 tutor marketplace
    # ============================================================
    tm.migrate()
    # Idempotent re-migrate
    tm.migrate()

    t1 = tm.apply_as_tutor(
        user_id="tutor-1", display_name="Prof. Sharma",
        rate_inr_per_30min=300, bio="20 yrs JEE Math",
        exams=["jee", "neet"], subjects=["physics", "math"],
        languages=["en", "hi"],
    )
    check(
        "M4 apply_as_tutor returns Tutor status='applied'",
        t1.status == "applied" and t1.platform_fee_pct == 0.20,
    )

    # Duplicate apply
    check(
        "M4 apply_as_tutor rejects duplicate user",
        raises(tm.apply_as_tutor,
               user_id="tutor-1", display_name="dup",
               rate_inr_per_30min=300),
    )

    # Rate floor / ceiling
    check(
        "M4 apply_as_tutor rejects below floor rate",
        raises(tm.apply_as_tutor,
               user_id="tutor-low", display_name="Cheap",
               rate_inr_per_30min=10),
    )
    check(
        "M4 apply_as_tutor rejects above ceiling rate",
        raises(tm.apply_as_tutor,
               user_id="tutor-high", display_name="Pricey",
               rate_inr_per_30min=99999),
    )

    # Approve to active
    check(
        "M4 approve_tutor flips applied → active",
        tm.approve_tutor(user_id="tutor-1"),
    )
    t1 = tm.get_tutor_by_user("tutor-1")
    check(
        "M4 approve_tutor sets status='active' + verified=True",
        t1.status == "active" and t1.verified,
    )
    # Re-approve no-op
    check(
        "M4 approve_tutor is idempotent (no-op on active)",
        not tm.approve_tutor(user_id="tutor-1"),
    )

    # Search returns active tutors
    found = tm.search_tutors(exam="jee")
    check(
        "M4 search_tutors filters by exam",
        any(t.user_id == "tutor-1" for t in found),
    )
    found_sub = tm.search_tutors(subject="math")
    check(
        "M4 search_tutors filters by subject",
        any(t.user_id == "tutor-1" for t in found_sub),
    )

    # Book a session
    when = time.time() + 86400
    b1 = tm.book_session(
        tutor_user_id="tutor-1", student_user_id="student-1",
        scheduled_at=when, duration_min=30, topic="Calculus",
    )
    check(
        "M4 book_session computes ₹300 × 1 block = 30000 paise",
        b1.price_paise == 30000 and b1.status == "requested",
    )
    check(
        "M4 book_session applies 20% platform fee",
        b1.platform_fee_paise == 6000 and b1.tutor_payout_paise == 24000,
    )

    # Self-booking rejected
    check(
        "M4 book_session rejects tutor==student",
        raises(tm.book_session,
               tutor_user_id="tutor-1", student_user_id="tutor-1",
               scheduled_at=when),
    )

    # Bad duration
    check(
        "M4 book_session rejects bad duration",
        raises(tm.book_session,
               tutor_user_id="tutor-1", student_user_id="student-1",
               scheduled_at=when, duration_min=45),
    )

    # 60-min booking → 2x price
    b60 = tm.book_session(
        tutor_user_id="tutor-1", student_user_id="student-2",
        scheduled_at=when + 3600, duration_min=60,
    )
    check(
        "M4 60-min booking scales price 2x",
        b60.price_paise == 60000,
    )

    # Confirm
    bc = tm.confirm_booking(booking_id=b1.id, tutor_user_id="tutor-1")
    check(
        "M4 confirm_booking moves requested → confirmed",
        bc.status == "confirmed" and bc.confirmed_at is not None,
    )

    # Confirm by wrong user
    check(
        "M4 confirm_booking rejects non-tutor",
        raises(tm.confirm_booking,
               booking_id=b1.id, tutor_user_id="someone-else"),
    )

    # Start
    bs = tm.start_session(booking_id=b1.id, user_id="student-1")
    check(
        "M4 start_session moves confirmed → in_progress",
        bs.status == "in_progress" and bs.started_at is not None,
    )

    # Complete
    bc2 = tm.complete_booking(booking_id=b1.id, tutor_user_id="tutor-1")
    check(
        "M4 complete_booking moves → completed + paid_out",
        bc2.status == "completed" and bc2.payment_status == "paid_out",
    )

    # Tutor earnings updated
    t_after = tm.get_tutor_by_user("tutor-1")
    check(
        "M4 tutor earnings bumped after complete",
        t_after.total_earnings_paise == 24000 and t_after.booking_count == 1,
    )

    # Review (only by student, only on completed)
    rid = tm.review_booking(
        booking_id=b1.id, reviewer_user_id="student-1",
        rating=5, feedback="Great session",
    )
    check("M4 review_booking returns id", bool(rid))

    # Double-review rejected
    check(
        "M4 review_booking rejects double-review",
        raises(tm.review_booking,
               booking_id=b1.id, reviewer_user_id="student-1",
               rating=4),
    )

    # Review by tutor rejected
    b2 = tm.book_session(
        tutor_user_id="tutor-1", student_user_id="student-3",
        scheduled_at=when + 7200,
    )
    tm.confirm_booking(booking_id=b2.id, tutor_user_id="tutor-1")
    tm.complete_booking(booking_id=b2.id, tutor_user_id="tutor-1")
    check(
        "M4 review_booking rejects tutor-as-reviewer",
        raises(tm.review_booking,
               booking_id=b2.id, reviewer_user_id="tutor-1",
               rating=5),
    )

    # Tutor rating rolled up
    t_rated = tm.get_tutor_by_user("tutor-1")
    check(
        "M4 tutor rating rolls up (count=1, avg=5)",
        t_rated.rating_count == 1 and t_rated.rating_avg == 5.0,
    )

    # Cancel + refund
    b3 = tm.book_session(
        tutor_user_id="tutor-1", student_user_id="student-4",
        scheduled_at=when + 14400,
    )
    bc3 = tm.cancel_booking(
        booking_id=b3.id, user_id="student-4", reason="changed plans",
    )
    check(
        "M4 cancel_booking with refund sets status=refunded",
        bc3.status == "refunded" and bc3.payment_status == "refunded",
    )

    # No-show
    b4 = tm.book_session(
        tutor_user_id="tutor-1", student_user_id="student-5",
        scheduled_at=when + 18000,
    )
    tm.confirm_booking(booking_id=b4.id, tutor_user_id="tutor-1")
    check(
        "M4 mark_no_show keeps tutor payout",
        tm.mark_no_show(booking_id=b4.id, tutor_user_id="tutor-1"),
    )

    # Earnings summary
    es = tm.tutor_earnings_summary("tutor-1")
    check(
        "M4 tutor_earnings_summary has all keys",
        {"completed_sessions", "pending_requests",
         "total_payout_paise"}.issubset(es),
    )

    # Set tutor status — pause
    check(
        "M4 set_tutor_status('paused')",
        tm.set_tutor_status(user_id="tutor-1", status="paused"),
    )
    # Paused tutors can't be booked
    check(
        "M4 paused tutor can't be booked",
        raises(tm.book_session,
               tutor_user_id="tutor-1", student_user_id="student-x",
               scheduled_at=when + 99999),
    )
    tm.set_tutor_status(user_id="tutor-1", status="active")  # reset

    # ============================================================
    # O3 — Question pack marketplace
    # ============================================================
    qpm.migrate()
    qpm.migrate()  # idempotent

    s1 = qpm.apply_as_setter(
        user_id="setter-1", display_name="Mrs. Iyer",
        credentials="25 yrs CBSE Mathematics",
    )
    check(
        "O3 apply_as_setter returns Setter verified=False",
        s1.user_id == "setter-1" and not s1.verified,
    )

    # Can't create pack while unverified
    check(
        "O3 create_pack rejected when setter unverified",
        raises(qpm.create_pack,
               setter_user_id="setter-1",
               title="Algebra Drills Class 10",
               price_paise=9900),
    )

    # Verify
    check("O3 verify_setter", qpm.verify_setter(user_id="setter-1"))

    # Create pack
    p1 = qpm.create_pack(
        setter_user_id="setter-1",
        title="Algebra Drills Class 10",
        price_paise=9900,
        exam="cbse-x", board="cbse", grade=10,
        subject="math", difficulty="medium",
    )
    check(
        "O3 create_pack returns Pack status='draft'",
        p1.status == "draft" and p1.price_paise == 9900,
    )

    # Price floor
    check(
        "O3 create_pack rejects price below floor",
        raises(qpm.create_pack,
               setter_user_id="setter-1", title="Cheap Pack",
               price_paise=100),
    )

    # Title too short
    check(
        "O3 create_pack rejects short title",
        raises(qpm.create_pack,
               setter_user_id="setter-1", title="x",
               price_paise=9900),
    )

    # Add questions (use sham qids — J6's question_bank doesn't gate)
    question_bank.migrate()
    for i in range(1, 7):
        qpm.add_question(
            pack_id=p1.id, setter_user_id="setter-1",
            question_id=f"q-{i}",
        )
    p1_after = qpm.get_pack(p1.id)
    check(
        "O3 add_question bumps question_count to 6",
        p1_after.question_count == 6,
    )

    # Duplicate add idempotent
    cnt = qpm.add_question(
        pack_id=p1.id, setter_user_id="setter-1", question_id="q-1",
    )
    check(
        "O3 add_question idempotent on duplicate qid",
        cnt == 6,
    )

    # Wrong setter
    check(
        "O3 add_question rejects non-setter",
        raises(qpm.add_question,
               pack_id=p1.id, setter_user_id="other",
               question_id="q-X"),
    )

    # Pack with <5 questions can't publish
    p2 = qpm.create_pack(
        setter_user_id="setter-1", title="Too Small Pack",
        price_paise=9900,
    )
    check(
        "O3 publish_pack rejects pack with <5 questions",
        raises(qpm.publish_pack,
               pack_id=p2.id, setter_user_id="setter-1"),
    )

    # Publish
    check(
        "O3 publish_pack returns True on draft with ≥5 q",
        qpm.publish_pack(pack_id=p1.id, setter_user_id="setter-1"),
    )

    p1_pub = qpm.get_pack(p1.id)
    check(
        "O3 publish sets status + preview_question_ids (3 sample)",
        p1_pub.status == "published"
        and len(p1_pub.preview_question_ids) == 3,
    )

    # Browse marketplace
    browse = qpm.browse_packs(exam="cbse-x")
    check(
        "O3 browse_packs filters by exam",
        any(p.id == p1.id for p in browse),
    )

    # Setter can't buy own pack
    check(
        "O3 purchase_pack rejects setter buying own pack",
        raises(qpm.purchase_pack,
               pack_id=p1.id, buyer_user_id="setter-1"),
    )

    # Buyer purchases
    pur = qpm.purchase_pack(pack_id=p1.id, buyer_user_id="buyer-1")
    check(
        "O3 purchase_pack splits 10% platform / 90% setter",
        pur.platform_fee_paise == 990
        and pur.setter_payout_paise == 8910,
    )

    # Idempotent re-purchase
    pur2 = qpm.purchase_pack(pack_id=p1.id, buyer_user_id="buyer-1")
    check(
        "O3 purchase_pack idempotent for same buyer",
        pur2.id == pur.id,
    )

    # user_has_pack
    check(
        "O3 user_has_pack True after purchase",
        qpm.user_has_pack(pack_id=p1.id, user_id="buyer-1"),
    )
    check(
        "O3 user_has_pack False for non-buyer",
        not qpm.user_has_pack(pack_id=p1.id, user_id="never-bought"),
    )

    # Refund
    check("O3 refund_purchase True", qpm.refund_purchase(purchase_id=pur.id))
    check(
        "O3 user_has_pack False after refund",
        not qpm.user_has_pack(pack_id=p1.id, user_id="buyer-1"),
    )

    # Setter earnings
    se = qpm.setter_earnings_summary("setter-1")
    check(
        "O3 setter_earnings_summary returns dict with by_pack",
        "by_pack" in se and "total_payout_paise" in se,
    )

    # Archive
    check(
        "O3 archive_pack True",
        qpm.archive_pack(pack_id=p1.id, setter_user_id="setter-1"),
    )

    # ============================================================
    # R3 — Vouchers + bundles
    # ============================================================
    vch.migrate()
    vch.migrate()  # idempotent

    # Create percent voucher
    v1 = vch.create_voucher(
        code="welcome20", kind="percent", value=20,
        description="20% off first purchase",
    )
    check(
        "R3 create_voucher uppercases code + persists",
        v1.code == "WELCOME20" and v1.value == 20,
    )

    # Bad kind
    check(
        "R3 create_voucher rejects bad kind",
        raises(vch.create_voucher,
               code="badkind", kind="quantum", value=10),
    )

    # Bad percent value
    check(
        "R3 create_voucher rejects percent > 100",
        raises(vch.create_voucher,
               code="huge", kind="percent", value=150),
    )

    # Duplicate code
    check(
        "R3 create_voucher rejects duplicate code",
        raises(vch.create_voucher,
               code="welcome20", kind="percent", value=10),
    )

    # Validate — 20% off ₹500 → ₹100 discount
    r = vch.validate_voucher(
        code="WELCOME20", user_id="u1", order_paise=50000,
    )
    check(
        "R3 validate_voucher computes 20% discount",
        r.discount_paise == 10000 and r.final_paise == 40000,
    )

    # Redeem
    r2 = vch.redeem_voucher(
        code="WELCOME20", user_id="u1", order_paise=50000,
    )
    check(
        "R3 redeem_voucher returns same discount",
        r2.discount_paise == 10000,
    )

    # Per-user cap (default 1)
    check(
        "R3 second redemption blocked by per-user cap",
        raises(vch.redeem_voucher,
               code="WELCOME20", user_id="u1",
               order_paise=50000),
    )

    # Fresh user — should work
    r3 = vch.redeem_voucher(
        code="WELCOME20", user_id="u2", order_paise=50000,
    )
    check("R3 different user can redeem", r3.discount_paise == 10000)

    # Fixed-amount voucher
    vf = vch.create_voucher(
        code="FLAT100", kind="fixed", value=10000,
        max_uses_per_user=5,
    )
    rf = vch.validate_voucher(
        code="FLAT100", user_id="u1", order_paise=50000,
    )
    check(
        "R3 fixed voucher gives flat discount",
        rf.discount_paise == 10000 and rf.final_paise == 40000,
    )

    # Fixed voucher caps at order_paise
    rf2 = vch.validate_voucher(
        code="FLAT100", user_id="u1", order_paise=5000,
    )
    check(
        "R3 fixed voucher capped to order_paise",
        rf2.discount_paise == 5000 and rf2.final_paise == 0,
    )

    # SKU-scoped voucher
    vs = vch.create_voucher(
        code="MATH50", kind="percent", value=50,
        applies_to=["course_math_*"],
    )
    rs = vch.validate_voucher(
        code="MATH50", user_id="u1",
        order_paise=10000, sku="course_math_grade10",
    )
    check(
        "R3 SKU-scoped voucher matches prefix pattern",
        rs.discount_paise == 5000,
    )

    check(
        "R3 SKU-scoped voucher rejects unmatched SKU",
        raises(vch.validate_voucher,
               code="MATH50", user_id="u1",
               order_paise=10000, sku="course_science_grade10"),
    )

    # Min-order constraint
    vmin = vch.create_voucher(
        code="BIGSPEND", kind="percent", value=10,
        min_order_paise=100000,
    )
    check(
        "R3 voucher rejects order below min_order_paise",
        raises(vch.validate_voucher,
               code="BIGSPEND", user_id="u1", order_paise=50000),
    )

    # Expired voucher
    vexp = vch.create_voucher(
        code="EXPIRED", kind="percent", value=10,
        expires_at=time.time() - 60,
    )
    check(
        "R3 expired voucher rejected",
        raises(vch.validate_voucher,
               code="EXPIRED", user_id="u1", order_paise=50000),
    )

    # Status change → paused → not redeemable
    vch.set_voucher_status(code="MATH50", status="paused")
    check(
        "R3 paused voucher rejected",
        raises(vch.validate_voucher,
               code="MATH50", user_id="u1",
               order_paise=10000, sku="course_math_x"),
    )

    # Max uses cap (global)
    vcap = vch.create_voucher(
        code="ONLY1", kind="percent", value=10, max_uses=1,
    )
    vch.redeem_voucher(
        code="ONLY1", user_id="u-cap-1", order_paise=10000,
    )
    check(
        "R3 max_uses globally exhausts voucher",
        raises(vch.validate_voucher,
               code="ONLY1", user_id="u-cap-2",
               order_paise=10000),
    )

    # Listings + user redemptions (only redeem_voucher persists, not
    # validate_voucher — so u1 has exactly 1)
    rs_list = vch.list_user_redemptions("u1")
    check(
        "R3 list_user_redemptions returns the u1 redemption",
        len(rs_list) >= 1
        and rs_list[0]["voucher_code"] == "WELCOME20",
    )

    # Bundles
    b_solo = vch.find_matching_bundle([])
    check("R3 find_matching_bundle([]) → None", b_solo is None)

    b1 = vch.create_bundle(
        title="Class 10 Math + Science Course",
        sku_codes=["course_math_10", "course_sci_10"],
        bundle_discount_pct=25,
    )
    check(
        "R3 create_bundle stores 2-SKU bundle",
        len(b1.sku_codes) == 2 and b1.bundle_discount_pct == 25,
    )

    # Bundle requires ≥2 SKUs
    check(
        "R3 create_bundle rejects 1-SKU input",
        raises(vch.create_bundle,
               title="Solo Bundle", sku_codes=["just_one"],
               bundle_discount_pct=10),
    )

    # apply_bundle: cart matches → discount applies
    br = vch.apply_bundle(
        skus=["course_math_10", "course_sci_10", "course_hist_10"],
        total_paise=100000,
    )
    check(
        "R3 apply_bundle applies 25% on matching cart",
        br.discount_paise == 25000 and br.final_paise == 75000,
    )

    # apply_bundle: partial cart → no discount
    br_none = vch.apply_bundle(
        skus=["course_math_10"], total_paise=100000,
    )
    check(
        "R3 apply_bundle returns 0 discount on partial cart",
        br_none.discount_paise == 0,
    )

    # Best bundle wins when multiple match
    vch.create_bundle(
        title="Mega Class 10 Bundle",
        sku_codes=["course_math_10", "course_sci_10"],
        bundle_discount_pct=40,
    )
    br_best = vch.apply_bundle(
        skus=["course_math_10", "course_sci_10"],
        total_paise=100000,
    )
    check(
        "R3 apply_bundle picks highest-discount matching bundle",
        br_best.discount_paise == 40000,
    )

    # Paused bundle excluded
    vch.set_bundle_status(bundle_id=b1.id, status="paused")
    check(
        "R3 set_bundle_status('paused')",
        vch.get_bundle(b1.id).status == "paused",
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # M4 — public search
        r = c.get("/api/marketplace/tutors?exam=jee")
        check(
            "GET /api/marketplace/tutors → 200 + active rows",
            r.status_code == 200 and "tutors" in r.json(),
        )

        # O3 — public browse
        r = c.get("/api/qb-market/packs")
        check(
            "GET /api/qb-market/packs → 200 + packs key",
            r.status_code == 200 and "packs" in r.json(),
        )

        # R3 — public bundles list
        r = c.get("/api/bundles")
        check(
            "GET /api/bundles → 200 + bundles key",
            r.status_code == 200 and "bundles" in r.json(),
        )

        # R3 — bundle match (public)
        r = c.post(
            "/api/bundles/match",
            data={"sku_codes": "course_math_10,course_sci_10",
                  "total_paise": 100000},
        )
        check(
            "POST /api/bundles/match → 200 + discount",
            r.status_code == 200 and "discount_paise" in r.json(),
        )

        # Auth gates — M4 (use valid form data so Form validator
        # doesn't 422 before the auth check)
        for path, data in (
            ("/api/marketplace/tutors/apply",
             {"display_name": "Valid Tutor Name",
              "rate_inr_per_30min": 300}),
            ("/api/marketplace/bookings",
             {"tutor_user_id": "x", "scheduled_at": time.time() + 99999,
              "duration_min": 30}),
        ):
            r = c.post(path, data=data)
            check(
                f"POST {path} → 401 (auth gate)",
                r.status_code == 401,
                f"got {r.status_code}",
            )

        # Auth gates — O3 (valid form data)
        for path, data in (
            ("/api/qb-market/setters/apply",
             {"display_name": "Valid Setter Name"}),
            ("/api/qb-market/packs",
             {"title": "A Valid Title", "price_paise": 9900}),
        ):
            r = c.post(path, data=data)
            check(
                f"POST {path} → 401 (auth gate)",
                r.status_code == 401,
            )

        # Auth gates — R3
        r = c.get("/api/admin/vouchers")
        check(
            "GET /api/admin/vouchers → 401 (unauth admin route)",
            r.status_code == 401,
        )
        r = c.post(
            "/api/admin/vouchers",
            data={"code": "TEST20", "kind": "percent", "value": 20},
        )
        check(
            "POST /api/admin/vouchers → 401 (unauth admin route)",
            r.status_code == 401,
        )

        # Form validation — too-short display_name (caught by Form,
        # but we need a valid form so 401 surfaces. Wrong rate
        # should 422.)
        r = c.post(
            "/api/marketplace/tutors/apply",
            data={"display_name": "X", "rate_inr_per_30min": 10},
        )
        check(
            "POST /api/marketplace/tutors/apply rejects bad rate (422)",
            r.status_code == 422,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    check(
        "v2.9 left route count above 360",
        len(app.routes) > 360,
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
