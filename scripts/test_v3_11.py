"""Smoke for v3.11 — Marketplace quality controls.

Covers:
- Ratings (idempotent per (item, user); recompute triggers)
- Refunds (request → decide → quality penalty; SLA expiry sweep)
- Copyright claims (filed → severe auto-flips status, upheld
  removes item)
- Quality score composite: rating × 20 - refund_penalty -
  copyright_penalty + recency_boost
- Item status: auto under_review when quality drops below 40
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_11.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_11_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import marketplace_quality as mq
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

    print("v3.11 smoke test — Marketplace quality")
    print("--------------------------------------")

    mq.migrate()
    mq.migrate()

    # ============================================================
    # Ratings
    # ============================================================
    r1 = mq.rate(
        item_kind="course", item_id="course-1",
        user_id="stu-1", rating=5, review_text="Excellent!",
    )
    check(
        "rate returns Rating",
        r1.rating == 5 and r1.review_text == "Excellent!",
    )

    # Bad item_kind
    check(
        "rate rejects bad item_kind",
        raises(mq.rate, exc_type=ValueError,
               item_kind="alien", item_id="x",
               user_id="u", rating=5),
    )

    # Out-of-range rating
    check(
        "rate rejects rating > 5",
        raises(mq.rate, exc_type=ValueError,
               item_kind="course", item_id="x",
               user_id="u", rating=99),
    )

    # Idempotent — same user re-rating updates the row
    r1_again = mq.rate(
        item_kind="course", item_id="course-1",
        user_id="stu-1", rating=4,
        review_text="Actually it was just good",
    )
    check(
        "rate idempotent on (item, user) — same row id, new value",
        r1_again.id == r1.id and r1_again.rating == 4,
    )

    # Different users add ratings — composite forms
    mq.rate(
        item_kind="course", item_id="course-1",
        user_id="stu-2", rating=5,
    )
    mq.rate(
        item_kind="course", item_id="course-1",
        user_id="stu-3", rating=5,
    )

    # Quality score after 3 ratings
    q = mq.get_quality(item_kind="course", item_id="course-1")
    check(
        "Quality score has rating_count=3 + non-null rating_avg",
        q.rating_count == 3
        and q.rating_avg is not None,
    )
    # rating_avg ≈ (4 + 5 + 5)/3 = 4.67; score ≈ 4.67×20 = ~93.3
    # + recency_boost 5 = ~98 (capped at 100)
    check(
        "Quality score for high-rated item is high",
        q.score >= 90,
        f"got score={q.score}",
    )

    # Public rating list returns sorted-by-helpful
    rows = mq.list_ratings_for_item(
        item_kind="course", item_id="course-1",
    )
    check(
        "list_ratings_for_item returns 3 ratings",
        len(rows) == 3,
    )

    # Mark helpful
    check(
        "mark_review_helpful → True",
        mq.mark_review_helpful(rating_id=r1.id),
    )

    # ============================================================
    # Refunds
    # ============================================================
    rf = mq.request_refund(
        item_kind="course", item_id="course-1",
        purchase_id="purchase-abc", user_id="stu-4",
        amount_paise=199900,
        reason="Content did not match description",
    )
    check(
        "request_refund returns pending refund w/ SLA",
        rf.status == "pending"
        and rf.sla_due_at > rf.requested_at,
    )

    # Bad amount
    check(
        "request_refund rejects amount < 1",
        raises(mq.request_refund, exc_type=ValueError,
               item_kind="course", item_id="x",
               purchase_id="p", user_id="u",
               amount_paise=0, reason="x" * 10),
    )

    # Short reason
    check(
        "request_refund rejects short reason",
        raises(mq.request_refund, exc_type=ValueError,
               item_kind="course", item_id="x",
               purchase_id="p", user_id="u",
               amount_paise=100, reason="hi"),
    )

    # Decide approve
    decided = mq.decide_refund(
        refund_id=rf.id, action="approve",
        reviewer_user_id="admin-1",
        decision_reason="Verified the content mismatch",
    )
    check(
        "decide_refund approve → status='approved'",
        decided.status == "approved",
    )

    # Can't decide again
    check(
        "decide_refund on already-decided → ValueError",
        raises(mq.decide_refund, exc_type=ValueError,
               refund_id=rf.id, action="approve",
               reviewer_user_id="admin-1"),
    )

    # Refund counted in quality
    q_after_refund = mq.get_quality(
        item_kind="course", item_id="course-1",
    )
    check(
        "Quality refund_count updated to 1",
        q_after_refund.refund_count == 1,
    )

    # Bad action
    check(
        "decide_refund rejects bad action",
        raises(mq.decide_refund, exc_type=ValueError,
               refund_id="x", action="cosmic_action",
               reviewer_user_id="x"),
    )

    # SLA expiry sweep
    aged = mq.request_refund(
        item_kind="course", item_id="course-1",
        purchase_id="purchase-aged", user_id="stu-5",
        amount_paise=99900, reason="taking too long",
    )
    with mq._conn() as conn:
        conn.execute(
            "UPDATE market_refund_requests SET sla_due_at = ? "
            "WHERE id = ?",
            (time.time() - 60, aged.id),
        )
    n_expired = mq.expire_stale_refunds()
    check(
        "expire_stale_refunds returns count ≥1",
        n_expired >= 1,
    )
    aged_now = mq.get_refund(aged.id)
    check(
        "Aged refund flipped to 'auto_expired'",
        aged_now.status == "auto_expired",
    )

    # ============================================================
    # Copyright claims
    # ============================================================

    # Open a moderate plagiarism claim
    claim = mq.file_copyright_claim(
        item_kind="course", item_id="course-2",
        claimant_user_id="publisher-1",
        claimant_kind="publisher",
        claim_type="plagiarism",
        severity="moderate",
        evidence_text=(
            "Material on pages 12-30 is verbatim copy of "
            "our publication ISBN 978-xxx-xxxxx-x."
        ),
    )
    check(
        "file_copyright_claim returns open claim",
        claim.status == "open"
        and claim.claim_type == "plagiarism",
    )

    # Bad claim_type
    check(
        "file_copyright_claim rejects bad claim_type",
        raises(mq.file_copyright_claim, exc_type=ValueError,
               item_kind="course", item_id="x",
               claimant_user_id="u",
               claim_type="alien",
               evidence_text="some evidence here"),
    )

    # No evidence
    check(
        "file_copyright_claim rejects empty evidence",
        raises(mq.file_copyright_claim, exc_type=ValueError,
               item_kind="course", item_id="x",
               claimant_user_id="u",
               claim_type="plagiarism"),
    )

    # Severe claim auto-flips item to under_review
    severe = mq.file_copyright_claim(
        item_kind="course", item_id="course-3",
        claimant_user_id="board-1",
        claimant_kind="board",
        claim_type="verbatim_copy",
        severity="severe",
        evidence_text="Entire course is verbatim from CBSE PDFs.",
    )
    s3 = mq.get_item_status(
        item_kind="course", item_id="course-3",
    )
    check(
        "Severe claim → item auto-flipped to under_review",
        s3["status"] == "under_review",
    )

    # Decide uphold severe → removed
    upheld = mq.decide_claim(
        claim_id=severe.id, action="uphold",
        reviewer_user_id="admin-1",
        decision_reason="Confirmed verbatim copy",
    )
    check(
        "decide_claim uphold severe → status='upheld'",
        upheld.status == "upheld",
    )
    s3_after = mq.get_item_status(
        item_kind="course", item_id="course-3",
    )
    check(
        "Upheld severe claim → item removed",
        s3_after["status"] == "removed",
    )

    # Decide dismiss moderate
    dismissed = mq.decide_claim(
        claim_id=claim.id, action="dismiss",
        reviewer_user_id="admin-1",
        decision_reason="Insufficient evidence",
    )
    check(
        "decide_claim dismiss → status='dismissed'",
        dismissed.status == "dismissed",
    )

    # Bad action
    check(
        "decide_claim rejects bad action",
        raises(mq.decide_claim, exc_type=ValueError,
               claim_id=claim.id, action="moon",
               reviewer_user_id="x"),
    )

    # Can't decide already-decided
    check(
        "decide_claim on already-decided → ValueError",
        raises(mq.decide_claim, exc_type=ValueError,
               claim_id=claim.id, action="uphold",
               reviewer_user_id="x"),
    )

    # ============================================================
    # Quality score composite + auto-flip
    # ============================================================

    # Set up a low-rated item — should auto-flip to under_review
    for u in ["bad-1", "bad-2", "bad-3", "bad-4"]:
        mq.rate(
            item_kind="content_pack",
            item_id="bad-pack",
            user_id=u, rating=1,
        )

    bad_q = mq.get_quality(
        item_kind="content_pack", item_id="bad-pack",
    )
    check(
        "Bad-pack score is low",
        bad_q.score < 40,
        f"got score={bad_q.score}",
    )

    bad_status = mq.get_item_status(
        item_kind="content_pack", item_id="bad-pack",
    )
    check(
        "Bad-pack auto-flipped to under_review",
        bad_status["status"] == "under_review",
    )

    # Top items by quality
    top = mq.top_items_by_quality(
        item_kind="course", min_rating_count=3,
    )
    check(
        "top_items_by_quality returns course-1 (high score)",
        any(t.item_id == "course-1" for t in top),
    )

    # Admin override item status
    check(
        "set_item_status admin override",
        mq.set_item_status(
            item_kind="course", item_id="course-3",
            status="active",
            reason="Manual review found legitimate use",
            updated_by_user_id="admin-1",
        ),
    )
    restored = mq.get_item_status(
        item_kind="course", item_id="course-3",
    )
    check(
        "Manual restore worked",
        restored["status"] == "active",
    )

    # Default item status when never set
    fresh = mq.get_item_status(
        item_kind="tutor", item_id="never-set-tutor",
    )
    check(
        "Default item status is 'active'",
        fresh["status"] == "active",
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — quality + ratings + status
        r = c.get("/api/market/ratings/course/course-1")
        check(
            "GET /api/market/ratings/{}/{} → 200 public",
            r.status_code == 200,
        )

        r = c.get("/api/market/quality/course/course-1")
        check(
            "GET /api/market/quality/{}/{} → 200 public",
            r.status_code == 200
            and r.json()["quality"] is not None,
        )

        r = c.get("/api/market/quality/top?item_kind=course")
        check(
            "GET /api/market/quality/top → 200 public",
            r.status_code == 200,
        )

        r = c.get("/api/market/status/course/course-1")
        check(
            "GET /api/market/status/{}/{} → 200 public",
            r.status_code == 200,
        )

        # Auth gates
        r = c.post(
            "/api/market/ratings",
            data={"item_kind": "course", "item_id": "x",
                  "rating": 5},
        )
        check(
            "POST /api/market/ratings → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/market/refunds",
            data={"item_kind": "course", "item_id": "x",
                  "purchase_id": "p",
                  "amount_paise": 100,
                  "reason": "valid reason here"},
        )
        check(
            "POST /api/market/refunds → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/market/copyright-claims",
            data={"item_kind": "course", "item_id": "x",
                  "claim_type": "plagiarism",
                  "evidence_text": "evidence"},
        )
        check(
            "POST /api/market/copyright-claims → 401",
            r.status_code == 401,
        )

        # Admin gates
        r = c.get("/api/admin/market/refunds")
        check(
            "GET /api/admin/market/refunds → 401",
            r.status_code == 401,
        )

        r = c.get("/api/admin/market/copyright-claims")
        check(
            "GET /api/admin/market/copyright-claims → 401",
            r.status_code == 401,
        )

        # Form validation
        r = c.post(
            "/api/market/ratings",
            data={"item_kind": "course", "item_id": "x",
                  "rating": 99},
        )
        check(
            "POST /api/market/ratings rejects rating > 5 (422)",
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
        "v3.11 brought route count above 530",
        len(app.routes) > 530,
        f"routes={len(app.routes)}",
    )

    print("--------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
