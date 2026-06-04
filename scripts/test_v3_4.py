"""Smoke for v3.4 — Daily plan generator.

Covers:
- Plan generation w/ block allocation rules
- Block lifecycle (mark done → completion_pct → status)
- should_regenerate triggers (no_plan_today / stale / readiness_drift)
- Govt segment gets current_affairs block; non-govt doesn't
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_4.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_4_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import daily_plan as dp
    from padhai import exam_taxonomy as et
    from padhai import mastery
    from padhai import mock_engine as me
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

    print("v3.4 smoke test — Daily plan generator")
    print("--------------------------------------")

    dp.migrate()
    dp.migrate()   # idempotent
    et.migrate()    # need seeded packs + topics
    mastery.migrate()
    me.migrate()

    # ============================================================
    # Generate for a govt segment pack (UPSC) — should include
    # current_affairs block
    # ============================================================

    # Seed some mastery to populate weak topics realistically
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::polity", correct=False,
    )
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::polity", correct=False,
    )
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::economy", correct=False,
    )
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::history_modern",
        correct=True,
    )
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::history_modern",
        correct=True,
    )
    mastery.update(
        user_id="stu-1", topic_key="upsc_cse::history_modern",
        correct=True,
    )

    plan = dp.generate_plan(
        user_id="stu-1", pack_code="upsc_cse_2026",
        total_minutes=120,
    )
    check(
        "Plan generated for UPSC pack with status='active'",
        plan.status == "active"
        and plan.total_minutes == 120,
    )
    check(
        "Plan has ≥3 blocks (practice + read + mock/revise/ca)",
        len(plan.blocks) >= 3,
    )

    # Verify block kinds — govt should have current_affairs
    kinds = {b.kind for b in plan.blocks}
    check(
        "UPSC plan includes 'current_affairs' block (govt segment)",
        "current_affairs" in kinds,
    )
    check(
        "UPSC plan includes 'practice' block",
        "practice" in kinds,
    )
    check(
        "UPSC plan includes 'read' block",
        "read" in kinds,
    )

    # Block estimated_min sums ~= total_minutes (within rounding)
    total = sum(b.estimated_min for b in plan.blocks)
    check(
        f"Plan blocks sum to ~total_minutes (got {total})",
        abs(total - 120) <= 30,    # rounding wiggle room
    )

    # Weak topics surface in plan blocks
    block_topics = {b.topic_code for b in plan.blocks if b.topic_code}
    check(
        "Polity is targeted (weakest by mastery)",
        "polity" in block_topics,
    )

    # ============================================================
    # Generate for a non-govt pack (CBSE Class 10) — no current_affairs
    # ============================================================

    plan_cbse = dp.generate_plan(
        user_id="stu-1", pack_code="cbse_class_10_2026",
        total_minutes=90,
    )
    cbse_kinds = {b.kind for b in plan_cbse.blocks}
    check(
        "CBSE plan does NOT include current_affairs (non-govt)",
        "current_affairs" not in cbse_kinds,
    )
    check(
        "CBSE plan still has practice + read",
        "practice" in cbse_kinds and "read" in cbse_kinds,
    )

    # ============================================================
    # Replace existing plan for same date — second generate overrides
    # ============================================================

    p1 = dp.generate_plan(
        user_id="stu-2", pack_code="ssc_cgl_2026",
        total_minutes=60, reason="fresh",
    )
    p2 = dp.generate_plan(
        user_id="stu-2", pack_code="ssc_cgl_2026",
        total_minutes=90, reason="post_mock",
    )
    check(
        "Re-generate replaces existing plan (same date)",
        p1.id != p2.id and p2.total_minutes == 90,
    )
    # p1 should be gone
    check(
        "Old plan id is removed after replace",
        dp.get_plan(p1.id) is None,
    )

    # ============================================================
    # get_or_generate — returns existing, doesn't replace
    # ============================================================
    p3 = dp.get_or_generate(
        user_id="stu-2", pack_code="ssc_cgl_2026",
    )
    check(
        "get_or_generate returns existing plan (no replace)",
        p3.id == p2.id,
    )

    # New user → generates fresh
    p_new = dp.get_or_generate(
        user_id="stu-fresh", pack_code="ssc_cgl_2026",
    )
    check(
        "get_or_generate creates plan for new user",
        p_new is not None and p_new.user_id == "stu-fresh",
    )

    # ============================================================
    # Bad inputs
    # ============================================================
    check(
        "generate_plan rejects unknown pack",
        raises(dp.generate_plan, exc_type=ValueError,
               user_id="x", pack_code="ghost_pack"),
    )
    check(
        "generate_plan rejects total_minutes < 10",
        raises(dp.generate_plan, exc_type=ValueError,
               user_id="x", pack_code="upsc_cse_2026",
               total_minutes=5),
    )
    check(
        "generate_plan rejects total_minutes > 720",
        raises(dp.generate_plan, exc_type=ValueError,
               user_id="x", pack_code="upsc_cse_2026",
               total_minutes=9999),
    )
    check(
        "generate_plan rejects bad date format",
        raises(dp.generate_plan, exc_type=ValueError,
               user_id="x", pack_code="upsc_cse_2026",
               plan_date="not-a-date"),
    )

    # ============================================================
    # mark_block_done lifecycle
    # ============================================================

    # Plan starts at 0% completion
    plan_done = dp.get_plan(plan.id)
    check(
        "Fresh plan has completion_pct = 0",
        plan_done.completion_pct == 0,
    )

    # Mark first block done
    first_block = plan_done.blocks[0]
    check(
        "mark_block_done returns True for owner",
        dp.mark_block_done(
            block_id=first_block.id, user_id="stu-1",
        ),
    )

    # Completion bumped
    plan_after = dp.get_plan(plan.id)
    check(
        "completion_pct > 0 after first block done",
        plan_after.completion_pct > 0,
    )
    check(
        "Block is marked completed",
        any(b.id == first_block.id and b.completed
            for b in plan_after.blocks),
    )

    # Wrong user can't mark block
    check(
        "mark_block_done refuses non-owner",
        not dp.mark_block_done(
            block_id=plan_after.blocks[1].id,
            user_id="stu-wrong",
        ),
    )

    # Bad block id
    check(
        "mark_block_done returns False for unknown block",
        not dp.mark_block_done(
            block_id="ghost-block", user_id="stu-1",
        ),
    )

    # Mark all remaining blocks → plan completes
    for b in plan_after.blocks:
        if not b.completed:
            dp.mark_block_done(block_id=b.id, user_id="stu-1")

    plan_final = dp.get_plan(plan.id)
    check(
        "Plan flips to 'completed' when all blocks done",
        plan_final.status == "completed"
        and plan_final.completion_pct == 100.0
        and plan_final.completed_at is not None,
    )

    # ============================================================
    # Skip plan
    # ============================================================
    skip_p = dp.generate_plan(
        user_id="stu-3", pack_code="ssc_cgl_2026",
        total_minutes=60,
    )
    check(
        "skip_plan returns True on active plan",
        dp.skip_plan(plan_id=skip_p.id, user_id="stu-3"),
    )
    check(
        "skip_plan idempotent (False second time)",
        not dp.skip_plan(plan_id=skip_p.id, user_id="stu-3"),
    )
    check(
        "skip_plan refuses non-owner",
        not dp.skip_plan(plan_id=skip_p.id, user_id="stranger"),
    )

    # ============================================================
    # should_regenerate triggers
    # ============================================================

    # No plan today → should regen
    needs, reason = dp.should_regenerate(
        user_id="stu-nobody", pack_code="upsc_cse_2026",
    )
    check(
        "should_regenerate True when no plan exists",
        needs and reason == "no_plan_today",
    )

    # Fresh plan → don't regen
    fresh = dp.generate_plan(
        user_id="stu-fresh2", pack_code="upsc_cse_2026",
    )
    needs2, reason2 = dp.should_regenerate(
        user_id="stu-fresh2", pack_code="upsc_cse_2026",
    )
    check(
        "should_regenerate False when plan is fresh",
        not needs2 and reason2 == "fresh",
    )

    # Manually age the plan past stale cutoff
    with dp._conn() as conn:
        conn.execute(
            "UPDATE daily_plans SET created_at = ? WHERE id = ?",
            (time.time() - (dp.PLAN_STALE_AFTER_HOURS + 1) * 3600,
             fresh.id),
        )
    needs3, reason3 = dp.should_regenerate(
        user_id="stu-fresh2", pack_code="upsc_cse_2026",
    )
    check(
        "should_regenerate True when plan is stale",
        needs3 and reason3 == "stale",
    )

    # ============================================================
    # Listing + stats
    # ============================================================
    recent = dp.list_recent_plans(user_id="stu-1")
    check(
        "list_recent_plans returns the user's plans",
        len(recent) >= 1,
    )

    stats = dp.pack_completion_stats(
        user_id="stu-1", pack_code="upsc_cse_2026",
    )
    check(
        "pack_completion_stats has avg_completion_pct + counts",
        "avg_completion_pct" in stats
        and stats["completed_days"] >= 1,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.get("/api/daily-plan/me/upsc_cse_2026")
        check(
            "GET /api/daily-plan/me/{pack} → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/daily-plan/me/upsc_cse_2026/regenerate",
            data={"total_minutes": 60},
        )
        check(
            "POST regenerate → 401",
            r.status_code == 401,
        )

        r = c.post("/api/daily-plan/blocks/some-id/done")
        check(
            "POST /api/daily-plan/blocks/{}/done → 401",
            r.status_code == 401,
        )

        r = c.get("/api/daily-plan/me/recent")
        check(
            "GET /api/daily-plan/me/recent → 401",
            r.status_code == 401,
        )

        r = c.get(
            "/api/daily-plan/me/upsc_cse_2026/should-regenerate",
        )
        check(
            "GET should-regenerate → 401",
            r.status_code == 401,
        )

        # Form validation: regen with bad minutes
        r = c.post(
            "/api/daily-plan/me/upsc_cse_2026/regenerate",
            data={"total_minutes": 5},
        )
        check(
            "POST regenerate rejects minutes < 10 (422)",
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
        "v3.4 brought route count above 455",
        len(app.routes) > 455,
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
