"""Smoke for v3.0 — R2 university partners + R4 affiliates + P4 DigiLocker.

Run: PYTHONPATH=. python scripts/test_v3_0.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_0_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import university_partners as upart
    from padhai import affiliates as aff
    from padhai import digilocker as dl
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    def raises(fn, **kwargs):
        try:
            fn(**kwargs); return False
        except (ValueError, PermissionError):
            return True

    print("v3.0 smoke test")
    print("---------------")

    # ============================================================
    # R2 — university partners
    # ============================================================
    upart.migrate()
    upart.migrate()  # idempotent

    p1 = upart.register_partner(
        name="NPTEL Test Cell", kind="nptel",
        integration_kind="lti13",
        contact_email="lti@nptel.example",
        contracted_students=500_000,
        contract_value_inr=10_000_000,
    )
    check(
        "R2 register_partner returns Partner status='prospect'",
        p1.status == "prospect"
        and p1.integration_kind == "lti13"
        and p1.contracted_students == 500_000,
    )

    # Duplicate name
    check(
        "R2 register_partner rejects duplicate name",
        raises(upart.register_partner,
               name="NPTEL Test Cell", kind="nptel",
               integration_kind="lti13"),
    )

    # Bad kind / integration
    check(
        "R2 register_partner rejects bad kind",
        raises(upart.register_partner,
               name="X1", kind="moon",
               integration_kind="lti13"),
    )
    check(
        "R2 register_partner rejects bad integration_kind",
        raises(upart.register_partner,
               name="X2", kind="nptel",
               integration_kind="quantum"),
    )

    # Bad revenue share
    check(
        "R2 register_partner rejects revenue_share out of range",
        raises(upart.register_partner,
               name="X3", kind="university",
               integration_kind="rest_api",
               revenue_share_pct=0.95),
    )

    # Status transitions
    check(
        "R2 set_partner_status → contracted",
        upart.set_partner_status(
            partner_id=p1.id, status="contracted",
        ),
    )
    p1c = upart.get_partner(p1.id)
    check(
        "R2 contracted sets contracted_at",
        p1c.status == "contracted" and p1c.contracted_at is not None,
    )

    # Can't create course until partner contracted/live
    p_prospect = upart.register_partner(
        name="Prospect U", kind="university",
        integration_kind="rest_api",
    )
    check(
        "R2 create_course rejected on prospect partner",
        raises(upart.create_course,
               partner_id=p_prospect.id, course_code="X-101",
               title="Quantum Mechanics Intro"),
    )

    # LTI config
    check(
        "R2 set_lti_config persists on lti13 partner",
        upart.set_lti_config(
            partner_id=p1.id,
            lti_client_id="nptel-client-xyz",
            lti_deployment_id="nptel-dep-001",
        ),
    )

    # LTI cfg refused on non-LTI partner
    p_rest = upart.register_partner(
        name="REST Uni", kind="university",
        integration_kind="rest_api",
    )
    upart.set_partner_status(partner_id=p_rest.id, status="contracted")
    check(
        "R2 set_lti_config refused on non-lti13 partner",
        not upart.set_lti_config(
            partner_id=p_rest.id,
            lti_client_id="x", lti_deployment_id="y",
        ),
    )

    # Create course
    c1 = upart.create_course(
        partner_id=p1.id, course_code="cs-101",
        title="Intro to Algorithms (NPTEL)",
        duration_weeks=12, credits=4,
    )
    check(
        "R2 create_course returns Course status='draft'",
        c1.status == "draft"
        and c1.course_code == "CS-101",  # uppercased
    )

    # Duplicate course_code
    check(
        "R2 create_course rejects duplicate (partner, code)",
        raises(upart.create_course,
               partner_id=p1.id, course_code="cs-101",
               title="Same Code Again"),
    )

    # Bad duration / credits
    check(
        "R2 create_course rejects duration_weeks out of [1,104]",
        raises(upart.create_course,
               partner_id=p1.id, course_code="C-X",
               title="Bad Duration", duration_weeks=999),
    )
    check(
        "R2 create_course rejects credits > 20",
        raises(upart.create_course,
               partner_id=p1.id, course_code="C-Y",
               title="Bad Credits", credits=99),
    )

    # Course can't enroll until published
    check(
        "R2 enroll rejected on draft course",
        raises(upart.enroll_student,
               course_id=c1.id, partner_student_id="ns-1"),
    )

    # Publish
    check(
        "R2 publish_course returns True on draft course",
        upart.publish_course(c1.id),
    )
    check(
        "R2 publish_course no-op on already published",
        not upart.publish_course(c1.id),
    )

    # Enroll
    e1 = upart.enroll_student(
        course_id=c1.id, partner_student_id="ns-1",
        our_user_id="our-1",
    )
    check(
        "R2 enroll_student returns Enrollment status='enrolled'",
        e1.status == "enrolled" and e1.completion_pct == 0.0,
    )

    # Duplicate enrollment
    check(
        "R2 enroll rejects duplicate (course, partner_student)",
        raises(upart.enroll_student,
               course_id=c1.id, partner_student_id="ns-1"),
    )

    # Progress 50%
    e_mid = upart.update_progress(
        enrollment_id=e1.id, completion_pct=50,
    )
    check(
        "R2 update_progress 50% → 'in_progress' + started_at",
        e_mid.status == "in_progress"
        and e_mid.started_at is not None,
    )

    # Complete
    e_done = upart.update_progress(
        enrollment_id=e1.id, completion_pct=100,
        final_score=87.5,
    )
    check(
        "R2 update_progress 100% → 'completed'",
        e_done.status == "completed"
        and e_done.completed_at is not None
        and e_done.final_score == 87.5,
    )

    # Course completion_count bumped
    c1_after = upart.get_course(c1.id)
    check(
        "R2 course completion_count bumped",
        c1_after.completion_count == 1,
    )

    # Can't update completed enrollment
    check(
        "R2 update_progress rejected on completed enrollment",
        raises(upart.update_progress,
               enrollment_id=e1.id, completion_pct=50),
    )

    # Withdraw — fresh enrollment
    e2 = upart.enroll_student(
        course_id=c1.id, partner_student_id="ns-2",
    )
    check(
        "R2 withdraw returns True on enrolled",
        upart.withdraw(e2.id),
    )
    check(
        "R2 withdraw idempotent (False second time)",
        not upart.withdraw(e2.id),
    )

    # List enrollments
    enrs = upart.list_enrollments_for_course(c1.id)
    check(
        "R2 list_enrollments_for_course returns ≥2",
        len(enrs) >= 2,
    )

    # Bad status filter
    check(
        "R2 list_enrollments_for_course rejects bad status",
        raises(upart.list_enrollments_for_course,
               course_id=c1.id, status="bogus"),
    )

    # Stats
    stats = upart.partner_stats(p1.id)
    check(
        "R2 partner_stats returns course + enrollment counts",
        stats["course_count"] >= 1
        and stats["total_enrollments"] >= 2
        and stats["total_completions"] >= 1,
    )

    # Archive course
    check(
        "R2 archive_course returns True",
        upart.archive_course(c1.id),
    )

    # Partner listing + filters
    contracted = upart.list_partners(status="contracted")
    check(
        "R2 list_partners(status='contracted') filters",
        all(p.status == "contracted" for p in contracted),
    )
    check(
        "R2 list_partners rejects bad status",
        raises(upart.list_partners, status="alien"),
    )

    # ============================================================
    # R4 — affiliates
    # ============================================================
    aff.migrate()
    aff.migrate()  # idempotent

    a1 = aff.register_affiliate(
        user_id="affiliate-user-1",
        code="creator_alice",
        display_name="Alice Creator",
        kind="creator",
        email="alice@example.com",
    )
    check(
        "R4 register_affiliate returns 'active' affiliate w/ 10% pct",
        a1.status == "active"
        and a1.commission_pct == 0.10
        and a1.code == "creator_alice",
    )

    # Code is normalized to lowercase
    check(
        "R4 affiliate code lowercased on insert",
        a1.code == a1.code.lower(),
    )

    # Bad code format
    check(
        "R4 register rejects code w/ uppercase letters",
        raises(aff.register_affiliate,
               user_id="u-x", code="BAD_CODE",
               display_name="X", kind="creator"),
    )
    check(
        "R4 register rejects short code",
        raises(aff.register_affiliate,
               user_id="u-x2", code="ab",
               display_name="X", kind="creator"),
    )

    # Bad kind
    check(
        "R4 register rejects bad kind",
        raises(aff.register_affiliate,
               user_id="u-x3", code="okcode",
               display_name="X", kind="alien"),
    )

    # Duplicate user_id
    check(
        "R4 register rejects duplicate user_id",
        raises(aff.register_affiliate,
               user_id="affiliate-user-1", code="alice2",
               display_name="A", kind="creator"),
    )

    # Duplicate code
    check(
        "R4 register rejects duplicate code (other user)",
        raises(aff.register_affiliate,
               user_id="u-other", code="creator_alice",
               display_name="A", kind="creator"),
    )

    # Out-of-range commission
    check(
        "R4 register rejects commission_pct > MAX",
        raises(aff.register_affiliate,
               user_id="u-greedy", code="greedy_x",
               display_name="X", kind="creator",
               commission_pct=0.99),
    )

    # Track visit
    vid = aff.record_visit(
        code="creator_alice", landing_path="/courses/cbse-10-math",
        utm_source="instagram", utm_medium="reel",
        utm_campaign="diwali-2026",
    )
    check("R4 record_visit returns visit_id", bool(vid))

    # Visit count bumped
    a1_v = aff.get_affiliate_by_code("creator_alice")
    check("R4 total_clicks bumped to 1", a1_v.total_clicks == 1)

    # Unknown code → returns None silently
    check(
        "R4 record_visit returns None for unknown code",
        aff.record_visit(code="ghost_code") is None,
    )

    # Suspended affiliate doesn't track
    aff.set_affiliate_status(code="creator_alice", status="paused")
    check(
        "R4 paused affiliate visit returns None",
        aff.record_visit(code="creator_alice") is None,
    )
    aff.set_affiliate_status(code="creator_alice", status="active")

    # Attribute user
    attr = aff.attribute_user(
        user_id="referred-user-1",
        affiliate_code="creator_alice",
        landing_visit_id=vid,
    )
    check(
        "R4 attribute_user returns Attribution + 12-month window",
        attr is not None
        and attr.affiliate_code == "creator_alice"
        and (attr.commission_until - attr.attributed_at) > 364 * 86400,
    )

    # Conversion bumped
    a1_a = aff.get_affiliate_by_code("creator_alice")
    check(
        "R4 total_conversions bumped to 1",
        a1_a.total_conversions == 1,
    )

    # Idempotent — first-touch attribution wins
    aff.register_affiliate(
        user_id="other-aff-user", code="rival_bob",
        display_name="Bob", kind="influencer",
    )
    attr2 = aff.attribute_user(
        user_id="referred-user-1", affiliate_code="rival_bob",
    )
    check(
        "R4 second attribution doesn't rebind (first-touch sticky)",
        attr2 is not None
        and attr2.affiliate_code == "creator_alice",
    )

    # Book commission
    ev = aff.book_commission(
        user_id="referred-user-1", invoice_id="inv-1",
        invoice_paise=50000,  # ₹500
    )
    check(
        "R4 book_commission computes 10% (= 5000 paise)",
        ev is not None and ev.commission_paise == 5000,
    )

    # Earnings rolled up
    a1_e = aff.get_affiliate_by_code("creator_alice")
    check(
        "R4 total_earned_paise updated",
        a1_e.total_earned_paise == 5000,
    )

    # Idempotent commission on same invoice
    ev2 = aff.book_commission(
        user_id="referred-user-1", invoice_id="inv-1",
        invoice_paise=50000,
    )
    check(
        "R4 book_commission idempotent on (affiliate, invoice)",
        ev2 is not None and ev2.id == ev.id,
    )
    a1_e2 = aff.get_affiliate_by_code("creator_alice")
    check(
        "R4 idempotent rebook doesn't double-bump earnings",
        a1_e2.total_earned_paise == 5000,
    )

    # User without attribution → no commission
    nope = aff.book_commission(
        user_id="not-referred", invoice_id="inv-x",
        invoice_paise=50000,
    )
    check(
        "R4 book_commission returns None for non-attributed user",
        nope is None,
    )

    # Mark commission paid
    check(
        "R4 mark_commission_paid → True",
        aff.mark_commission_paid(ev.id),
    )
    check(
        "R4 mark_commission_paid idempotent (False second time)",
        not aff.mark_commission_paid(ev.id),
    )

    # Earnings summary
    es = aff.affiliate_earnings("creator_alice")
    check(
        "R4 affiliate_earnings has clicks + conversions + paid/pending",
        {"total_clicks", "total_conversions", "paid_paise",
         "pending_paise", "conversion_rate"}.issubset(es),
    )

    # Program summary
    summary = aff.program_summary()
    check(
        "R4 program_summary has active_affiliates + total_clicks",
        "active_affiliates" in summary
        and summary["active_affiliates"] >= 2,
    )

    # ============================================================
    # P4 — DigiLocker
    # ============================================================
    dl.migrate()
    dl.migrate()  # idempotent

    # Doc-type catalog seeded
    dts = dl.list_doc_types()
    check(
        "P4 migrate seeds doc-type catalog (≥4 types)",
        len(dts) >= 4,
    )
    check(
        "P4 course_completion type present + enabled",
        any(dt.code == "course_completion" and dt.enabled
            for dt in dts),
    )

    # Aadhaar hash
    h = dl.hash_aadhaar("123456789012")
    h2 = dl.hash_aadhaar("123456789012")
    check(
        "P4 hash_aadhaar is deterministic + same input → same hash",
        h == h2 and len(h) == 64,
    )

    # Bad aadhaar
    check(
        "P4 hash_aadhaar rejects non-12-digit input",
        raises(dl.hash_aadhaar, raw="123"),
    )
    check(
        "P4 hash_aadhaar rejects non-numeric input",
        raises(dl.hash_aadhaar, raw="12345678901a"),
    )

    # Doc body hash
    body = {"course": "Algebra 10", "score": 92.5,
            "student_name": "X"}
    bh = dl.hash_doc_body(body)
    bh2 = dl.hash_doc_body({"score": 92.5, "course": "Algebra 10",
                            "student_name": "X"})
    check(
        "P4 hash_doc_body canonical (order-independent)",
        bh == bh2 and len(bh) == 64,
    )

    # Org issuer registration
    o1 = dl.register_org_issuer(
        org_id="org-padhai-main",
        issuer_name="AI Pathshala Education Pvt Ltd",
        issuer_id="ISSUER_PADHAI_001",
        api_key="secret-key-here",
        callback_url="https://padhai.app/dl-callback",
    )
    check(
        "P4 register_org_issuer returns sandbox issuer",
        o1.status == "sandbox" and o1.issuer_id == "ISSUER_PADHAI_001",
    )

    # Duplicate
    check(
        "P4 register_org_issuer rejects duplicate org_id",
        raises(dl.register_org_issuer,
               org_id="org-padhai-main",
               issuer_name="dup", issuer_id="ISSUER_PADHAI_002"),
    )

    # Activate sandbox → live
    check(
        "P4 activate_org_issuer flips sandbox → live",
        dl.activate_org_issuer(org_id="org-padhai-main"),
    )
    o1_live = dl.get_org_issuer("org-padhai-main")
    check(
        "P4 org issuer status='live' + activated_at set",
        o1_live.status == "live" and o1_live.activated_at is not None,
    )

    # Activate already-live = no-op
    check(
        "P4 activate_org_issuer no-op on already-live",
        not dl.activate_org_issuer(org_id="org-padhai-main"),
    )

    # No consent → enqueue refused
    check(
        "P4 enqueue_issuance refused w/o consent",
        raises(dl.enqueue_issuance,
               org_id="org-padhai-main", user_id="student-1",
               doc_type_code="course_completion",
               doc_title="Algebra Completion",
               payload={"course": "x"}),
    )

    # Record consent
    c = dl.record_consent(
        user_id="student-1",
        aadhaar_raw="987654321098",
        consent_purposes=["course_completion", "exam_certificate"],
        consent_text=(
            "I authorise AI Pathshala to issue my course completion "
            "and exam certificates into my DigiLocker account."
        ),
    )
    check(
        "P4 record_consent persists with consented_at",
        c.consented_at > 0 and c.revoked_at is None,
    )

    # Idempotent consent — re-recording updates the row
    c2 = dl.record_consent(
        user_id="student-1",
        aadhaar_raw="987654321098",
        consent_purposes=["course_completion"],
        consent_text=("I authorise AI Pathshala to issue "
                      "course completion certificates only."),
    )
    check(
        "P4 record_consent is idempotent (same row id)",
        c2.id == c.id,
    )

    # Bad consent — empty purposes
    check(
        "P4 record_consent rejects empty purposes",
        raises(dl.record_consent,
               user_id="student-x",
               aadhaar_raw="987654321098",
               consent_purposes=[],
               consent_text="A consent statement of decent length."),
    )

    # Bad consent — unknown purpose
    check(
        "P4 record_consent rejects unknown purpose",
        raises(dl.record_consent,
               user_id="student-x",
               aadhaar_raw="987654321098",
               consent_purposes=["alien_cert"],
               consent_text="A consent statement of decent length."),
    )

    # Bad consent — short consent_text
    check(
        "P4 record_consent rejects too-short consent_text",
        raises(dl.record_consent,
               user_id="student-y",
               aadhaar_raw="987654321098",
               consent_purposes=["course_completion"],
               consent_text="short"),
    )

    # has_active_consent
    check(
        "P4 has_active_consent True for consented purpose",
        dl.has_active_consent(
            user_id="student-1", doc_type_code="course_completion",
        ),
    )
    check(
        "P4 has_active_consent False for unconsented purpose",
        not dl.has_active_consent(
            user_id="student-1", doc_type_code="exam_certificate",
        ),
    )

    # Enqueue issuance
    i1 = dl.enqueue_issuance(
        org_id="org-padhai-main", user_id="student-1",
        doc_type_code="course_completion",
        doc_title="Course Completion Certificate (Algebra 10)",
        payload={
            "course": "Algebra Grade 10",
            "score": 92.5, "completion_date": "2026-04-15",
            "student_name": "Aanya Sharma",
        },
    )
    check(
        "P4 enqueue_issuance creates 'pending' issuance",
        i1.status == "pending" and len(i1.body_sha256) == 64,
    )

    # Re-enqueue same body → idempotent
    i1_again = dl.enqueue_issuance(
        org_id="org-padhai-main", user_id="student-1",
        doc_type_code="course_completion",
        doc_title="Course Completion Certificate (Algebra 10)",
        payload={
            "course": "Algebra Grade 10",
            "score": 92.5, "completion_date": "2026-04-15",
            "student_name": "Aanya Sharma",
        },
    )
    check(
        "P4 enqueue_issuance idempotent on same body",
        i1_again.id == i1.id,
    )

    # Worker pulls pending
    pending = dl.list_pending_issuances()
    check(
        "P4 list_pending_issuances returns the pending row",
        any(p.id == i1.id for p in pending),
    )

    # mark_queued
    check("P4 mark_queued → True", dl.mark_queued(i1.id))
    check(
        "P4 mark_queued idempotent (False second time)",
        not dl.mark_queued(i1.id),
    )

    # mark_issued
    check(
        "P4 mark_issued sets digilocker_uri",
        dl.mark_issued(
            issuance_id=i1.id,
            digilocker_uri="https://api.digilocker.gov.in/uri/abc-123",
        ),
    )
    i1_done = dl.get_issuance(i1.id)
    check(
        "P4 issuance status='issued' after callback",
        i1_done.status == "issued"
        and i1_done.digilocker_uri.endswith("abc-123")
        and i1_done.issued_at is not None,
    )

    # Revoke issuance
    check(
        "P4 revoke_issuance flips issued → revoked",
        dl.revoke_issuance(issuance_id=i1.id),
    )

    # User-facing list
    mine = dl.list_user_issuances("student-1")
    check(
        "P4 list_user_issuances returns the user's docs",
        len(mine) >= 1,
    )

    # Stats
    s = dl.issuance_stats(org_id="org-padhai-main")
    check(
        "P4 issuance_stats has by_status + by_doc_type",
        "by_status" in s and "by_doc_type" in s,
    )

    # Revoke consent → future enqueue refused
    check("P4 revoke_consent True", dl.revoke_consent("student-1"))
    check(
        "P4 enqueue refused after consent revoked",
        raises(dl.enqueue_issuance,
               org_id="org-padhai-main", user_id="student-1",
               doc_type_code="course_completion",
               doc_title="Another Cert",
               payload={"x": 1}),
    )

    # mark_failed
    # First need another pending issuance to fail. Re-consent.
    dl.record_consent(
        user_id="student-2",
        aadhaar_raw="555444333222",
        consent_purposes=["course_completion"],
        consent_text=(
            "I consent to course completion certificate issuance."
        ),
    )
    i_fail = dl.enqueue_issuance(
        org_id="org-padhai-main", user_id="student-2",
        doc_type_code="course_completion",
        doc_title="Failure Test Cert",
        payload={"x": "y"},
    )
    check(
        "P4 mark_failed sets status='failed'",
        dl.mark_failed(
            issuance_id=i_fail.id,
            reason="DigiLocker rejected — schema mismatch",
        ),
    )

    # Set org status → paused → enqueue refused
    dl.set_org_status(org_id="org-padhai-main", status="paused")
    check(
        "P4 enqueue refused when org paused",
        raises(dl.enqueue_issuance,
               org_id="org-padhai-main", user_id="student-2",
               doc_type_code="course_completion",
               doc_title="Should Fail",
               payload={"x": 1}),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — university courses + doc-types
        r = c.get("/api/university/courses")
        check(
            "GET /api/university/courses → 200 + courses key",
            r.status_code == 200 and "courses" in r.json(),
        )

        r = c.get("/api/digilocker/doc-types")
        check(
            "GET /api/digilocker/doc-types → 200 + ≥4 types",
            r.status_code == 200
            and len(r.json()["doc_types"]) >= 4,
        )

        # Public — affiliate visit tracking (open, returns ok always)
        r = c.post(
            "/api/affiliates/track-visit",
            data={"code": "creator_alice",
                  "landing_path": "/"},
        )
        check(
            "POST /api/affiliates/track-visit → 200 (open)",
            r.status_code == 200 and "visit_id" in r.json(),
        )

        # Open enroll endpoint — bad partner data → 400
        r = c.post(
            "/api/university/courses/ghost-course-id/enroll",
            data={"partner_student_id": "x"},
        )
        check(
            "POST /api/university/courses/{ghost}/enroll → 400",
            r.status_code == 400,
        )

        # Auth gates — R2 admin
        r = c.get("/api/admin/university/partners")
        check(
            "GET /api/admin/university/partners → 401",
            r.status_code == 401,
        )
        r = c.post(
            "/api/admin/university/partners",
            data={"name": "NewPartner Co",
                  "kind": "university",
                  "integration_kind": "rest_api"},
        )
        check(
            "POST /api/admin/university/partners → 401",
            r.status_code == 401,
        )

        # Auth gates — R4
        r = c.post(
            "/api/affiliates/register",
            data={"code": "validcode",
                  "display_name": "Valid Display Name",
                  "kind": "creator"},
        )
        check(
            "POST /api/affiliates/register → 401 (auth gate)",
            r.status_code == 401,
        )

        r = c.get("/api/admin/affiliates")
        check(
            "GET /api/admin/affiliates → 401",
            r.status_code == 401,
        )

        # Auth gates — P4
        r = c.post(
            "/api/digilocker/consent",
            data={"aadhaar": "111122223333",
                  "consent_purposes": "course_completion",
                  "consent_text": (
                      "I consent to course completion certificates."
                  )},
        )
        check(
            "POST /api/digilocker/consent → 401 (auth gate)",
            r.status_code == 401,
        )

        r = c.get("/api/digilocker/issuances/me")
        check(
            "GET /api/digilocker/issuances/me → 401",
            r.status_code == 401,
        )

        # Form validation — R2 partner with too-low revenue_share
        r = c.post(
            "/api/admin/university/partners",
            data={"name": "PartnerX",
                  "kind": "nptel", "integration_kind": "lti13",
                  "revenue_share_pct": 0.01},
        )
        check(
            "POST partners rejects revenue_share < 0.10 (422)",
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
        "v3.0 brought route count above 395",
        len(app.routes) > 395,
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
