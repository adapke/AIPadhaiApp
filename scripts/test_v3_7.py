"""Smoke for v3.7 — Expert review workflow.

Covers:
- Expert apply / approve / status lifecycle
- Review request idempotency + queue listing
- Claim → decide (approve/correct/reject) with commission math
- Verification lookup on (target_kind, target_id)
- SLA expiry sweep
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_7.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_7_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import expert_review as ex
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

    print("v3.7 smoke test — Expert review workflow")
    print("----------------------------------------")

    ex.migrate()
    ex.migrate()  # idempotent

    # ============================================================
    # Expert lifecycle
    # ============================================================
    e1 = ex.apply_as_expert(
        user_id="expert-1", display_name="Prof. Iyer",
        subjects=["mathematics", "physics"],
        exam_codes=["jee_main", "neet_ug"],
        languages=["en", "ta"],
        credentials="20 yrs JEE Physics teacher",
        rate_per_review_paise=8000,
    )
    check(
        "apply_as_expert returns status='applied'",
        e1.status == "applied"
        and e1.rate_per_review_paise == 8000,
    )

    # Duplicate apply rejected
    check(
        "apply_as_expert rejects duplicate user_id",
        raises(ex.apply_as_expert, exc_type=ValueError,
               user_id="expert-1", display_name="dup",
               subjects=["x"]),
    )

    # Empty subjects rejected
    check(
        "apply_as_expert rejects empty subjects",
        raises(ex.apply_as_expert, exc_type=ValueError,
               user_id="expert-z", display_name="X",
               subjects=[]),
    )

    # Bad rate
    check(
        "apply_as_expert rejects rate < MIN",
        raises(ex.apply_as_expert, exc_type=ValueError,
               user_id="expert-y", display_name="X",
               subjects=["x"], rate_per_review_paise=10),
    )

    # Approve
    check(
        "approve_expert flips applied → active",
        ex.approve_expert("expert-1"),
    )
    e1_active = ex.get_expert("expert-1")
    check(
        "Expert status='active' + activated_at set",
        e1_active.status == "active"
        and e1_active.activated_at is not None,
    )

    # Approve idempotent
    check(
        "approve_expert no-op on already-active",
        not ex.approve_expert("expert-1"),
    )

    # Listing
    actives = ex.list_experts(status="active")
    check(
        "list_experts(status='active') returns the approved expert",
        any(e.user_id == "expert-1" for e in actives),
    )

    # Filter by subject substring
    math_experts = ex.list_experts(subject="mathematics")
    check(
        "list_experts filters by subject",
        any(e.user_id == "expert-1" for e in math_experts),
    )

    # Add a second expert (subject: history) for routing test
    e2 = ex.apply_as_expert(
        user_id="expert-2", display_name="Dr. Sharma",
        subjects=["history", "polity"],
    )
    ex.approve_expert("expert-2")

    # ============================================================
    # Review queue
    # ============================================================
    r1 = ex.request_review(
        target_kind="ai_answer", target_id="ans-1",
        requested_by="student-1",
        subject_hint="mathematics", priority=3,
    )
    check(
        "request_review returns pending review",
        r1.status == "pending" and r1.priority == 3,
    )

    # Idempotent on (target_kind, target_id) while pending
    r1_dup = ex.request_review(
        target_kind="ai_answer", target_id="ans-1",
        requested_by="student-2", subject_hint="x",
    )
    check(
        "request_review idempotent (same active row returned)",
        r1_dup.id == r1.id,
    )

    # Bad target_kind
    check(
        "request_review rejects bad target_kind",
        raises(ex.request_review, exc_type=ValueError,
               target_kind="alien", target_id="x"),
    )

    # Bad priority
    check(
        "request_review rejects priority > 10",
        raises(ex.request_review, exc_type=ValueError,
               target_kind="ai_answer", target_id="x",
               priority=99),
    )

    # Queue ordering — priority then requested_at
    r2 = ex.request_review(
        target_kind="ai_answer", target_id="ans-2",
        subject_hint="history", priority=1,
    )
    r3 = ex.request_review(
        target_kind="ai_answer", target_id="ans-3",
        subject_hint="mathematics", priority=7,
    )
    queue = ex.list_review_queue(status="pending")
    check(
        "Queue ordered by priority ASC",
        queue[0].priority <= queue[-1].priority,
    )

    # Bad status filter
    check(
        "list_review_queue rejects bad status",
        raises(ex.list_review_queue, exc_type=ValueError,
               status="moon_status"),
    )

    # Filter by subject
    math_queue = ex.list_review_queue(
        status="pending", subject="mathematics",
    )
    check(
        "Queue filter by subject works",
        all(r.subject_hint == "mathematics" for r in math_queue),
    )

    # ============================================================
    # Claim — subject routing + atomicity
    # ============================================================

    # Expert-1 (math) can claim math review
    claimed = ex.claim_review(
        review_id=r1.id, reviewer_user_id="expert-1",
    )
    check(
        "claim_review by qualified expert → in_review",
        claimed.status == "in_review"
        and claimed.reviewer_user_id == "expert-1",
    )

    # Expert-1 can't claim history review (subject mismatch)
    check(
        "claim_review refuses subject mismatch",
        raises(ex.claim_review, exc_type=PermissionError,
               review_id=r2.id, reviewer_user_id="expert-1"),
    )

    # Expert-2 (history) can claim
    claim_hist = ex.claim_review(
        review_id=r2.id, reviewer_user_id="expert-2",
    )
    check(
        "Expert-2 claims history review",
        claim_hist.reviewer_user_id == "expert-2",
    )

    # Second claim attempt on same review → race-lost
    check(
        "claim_review on already-claimed → ValueError",
        raises(ex.claim_review, exc_type=ValueError,
               review_id=r1.id, reviewer_user_id="expert-2"),
    )

    # Unregistered user can't claim
    check(
        "claim_review refuses non-expert",
        raises(ex.claim_review, exc_type=ValueError,
               review_id=r3.id, reviewer_user_id="random-user"),
    )

    # Paused expert can't claim
    ex.set_expert_status(user_id="expert-1", status="paused")
    check(
        "claim_review refuses paused expert",
        raises(ex.claim_review, exc_type=ValueError,
               review_id=r3.id, reviewer_user_id="expert-1"),
    )
    ex.set_expert_status(user_id="expert-1", status="active")

    # ============================================================
    # Decide — approve / correct / reject + commission math
    # ============================================================

    # Approve
    decided = ex.decide_review(
        review_id=r1.id, reviewer_user_id="expert-1",
        action="approve", reason="Looks correct",
    )
    check(
        "decide approve → status='approved'",
        decided.status == "approved",
    )
    check(
        "decide approve books full commission (8000)",
        decided.commission_paise == 8000,
    )

    # Expert earnings rolled up
    e1_after = ex.get_expert("expert-1")
    check(
        "Expert total_reviews bumped",
        e1_after.total_reviews == 1
        and e1_after.total_earned_paise == 8000,
    )

    # Verification row created
    check(
        "Verification row created for approve",
        ex.is_verified(target_kind="ai_answer", target_id="ans-1"),
    )

    verification = ex.get_verification(
        target_kind="ai_answer", target_id="ans-1",
    )
    check(
        "get_verification returns reviewer + review_id",
        verification is not None
        and verification["reviewer_user_id"] == "expert-1",
    )

    # Wrong reviewer can't decide
    ex.claim_review(
        review_id=r3.id, reviewer_user_id="expert-1",
    )
    check(
        "decide_review refuses non-claimer",
        raises(ex.decide_review, exc_type=PermissionError,
               review_id=r3.id, reviewer_user_id="expert-2",
               action="approve"),
    )

    # Action='correct' requires corrected_answer
    check(
        "decide correct without corrected_answer → ValueError",
        raises(ex.decide_review, exc_type=ValueError,
               review_id=r3.id, reviewer_user_id="expert-1",
               action="correct"),
    )

    # Correct OK
    cor = ex.decide_review(
        review_id=r3.id, reviewer_user_id="expert-1",
        action="correct",
        corrected_answer="The correct formula is E = mc².",
        reason="Original missed the squared term",
    )
    check(
        "decide correct → 'corrected' + corrected_answer stored",
        cor.status == "corrected"
        and "mc" in (cor.corrected_answer or ""),
    )
    check(
        "Correct also creates verification row",
        ex.is_verified(target_kind="ai_answer", target_id="ans-3"),
    )

    # Reject (half rate)
    ex.claim_review(
        review_id=r2.id, reviewer_user_id="expert-2",  # already claimed
    ) if False else None    # noop — r2 already in_review
    rej = ex.decide_review(
        review_id=r2.id, reviewer_user_id="expert-2",
        action="reject", reason="off-topic, low quality",
    )
    check(
        "decide reject → status='rejected'",
        rej.status == "rejected",
    )
    e2_default_rate = 5000
    check(
        "Reject books half rate (2500)",
        rej.commission_paise == int(e2_default_rate * 0.5),
    )

    # Reject does NOT create verification
    check(
        "Reject does NOT create verification row",
        not ex.is_verified(
            target_kind="ai_answer", target_id="ans-2",
        ),
    )

    # Bad action
    check(
        "decide_review rejects bad action",
        raises(ex.decide_review, exc_type=ValueError,
               review_id=r1.id, reviewer_user_id="expert-1",
               action="moon_action"),
    )

    # Can't decide on already-decided
    check(
        "decide_review on already-decided → ValueError",
        raises(ex.decide_review, exc_type=ValueError,
               review_id=r1.id, reviewer_user_id="expert-1",
               action="approve"),
    )

    # ============================================================
    # SLA expiry sweep
    # ============================================================
    aged = ex.request_review(
        target_kind="qb_question", target_id="qb-aged",
    )
    with ex._conn() as conn:
        conn.execute(
            "UPDATE expert_reviews SET sla_due_at = ? "
            "WHERE id = ?",
            (time.time() - 60, aged.id),
        )
    n = ex.expire_stale_reviews()
    check(
        "expire_stale_reviews moves aged pending → expired",
        n >= 1,
    )
    aged_now = ex.get_review(aged.id)
    check(
        "Aged review status='expired'",
        aged_now.status == "expired",
    )

    # ============================================================
    # Ratings + earnings summary + queue stats
    # ============================================================
    check(
        "rate_expert with 5 stars",
        ex.rate_expert(
            expert_user_id="expert-1", rating=5,
            rater_user_id="student-1",
        ),
    )
    # Out-of-range rating
    check(
        "rate_expert rejects rating > 5",
        raises(ex.rate_expert, exc_type=ValueError,
               expert_user_id="expert-1", rating=99,
               rater_user_id="x"),
    )

    earnings = ex.expert_earnings_summary("expert-1")
    check(
        "expert_earnings_summary has by_status + totals",
        "by_status" in earnings
        and earnings["total_reviews"] >= 2
        and earnings["rating_count"] == 1,
    )

    stats = ex.queue_stats()
    check(
        "queue_stats returns by_status + sla_breached_pending",
        "by_status" in stats
        and "sla_breached_pending" in stats,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — list experts
        r = c.get("/api/experts")
        check(
            "GET /api/experts → 200",
            r.status_code == 200
            and len(r.json()["experts"]) >= 1,
        )

        # Public — verification lookup
        r = c.get(
            "/api/verifications/ai_answer/ans-1",
        )
        check(
            "GET /api/verifications/{}/{} → 200 + verified=True",
            r.status_code == 200
            and r.json()["is_verified"] is True,
        )

        # Auth gates
        r = c.post(
            "/api/experts/apply",
            data={"display_name": "Valid Expert Name",
                  "subjects": "math"},
        )
        check(
            "POST /api/experts/apply → 401",
            r.status_code == 401,
        )

        r = c.get("/api/experts/me")
        check(
            "GET /api/experts/me → 401",
            r.status_code == 401,
        )

        r = c.get("/api/expert-reviews/queue")
        check(
            "GET /api/expert-reviews/queue → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/expert-reviews/request",
            data={"target_kind": "ai_answer",
                  "target_id": "x"},
        )
        check(
            "POST /api/expert-reviews/request → 401",
            r.status_code == 401,
        )

        # Admin gates
        r = c.post(
            "/api/admin/experts/expert-1/approve",
        )
        check(
            "POST /api/admin/experts/{}/approve → 401",
            r.status_code == 401,
        )

        r = c.get("/api/admin/expert-reviews/stats")
        check(
            "GET /api/admin/expert-reviews/stats → 401",
            r.status_code == 401,
        )

        # Form validation: rate too low → 422
        r = c.post(
            "/api/experts/apply",
            data={"display_name": "X", "subjects": "math",
                  "rate_per_review_paise": 10},
        )
        check(
            "POST /api/experts/apply rejects rate < 1000 (422)",
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
        "v3.7 brought route count above 480",
        len(app.routes) > 480,
        f"routes={len(app.routes)}",
    )

    print("----------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
