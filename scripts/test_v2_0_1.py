"""Smoke for v2.0.1 hardening — rate limits, size caps, streaks
idempotency, deactivate_member helper, custom-domain TLD validation.

Run: PYTHONPATH=. python scripts/test_v2_0_1.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_0_1_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import custom_domains, orgs, rate_limit, streaks
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.0.1 smoke test")
    print("-----------------")

    # --- Rate limiter unit ---
    rl = rate_limit.TokenBucket(capacity=3, rate_per_sec=10.0)
    check("RL fresh bucket allows first consume", rl.try_consume("x"))
    rl.try_consume("x"); rl.try_consume("x")
    check("RL 4th consume blocked when burst=3", not rl.try_consume("x"))
    # Refill: at 10/s, 0.3s should give ~3 tokens
    time.sleep(0.4)
    check("RL refills over time", rl.try_consume("x"))
    check("RL distinct keys have independent buckets", rl.try_consume("y"))

    # --- Streaks idempotency (the bug) ---
    streaks.migrate()
    # Simulate a user with 6-day streak going into day 7
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    import sqlite3
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_streaks "
            "(user_id, current_streak, longest_streak, last_active_date, "
            " xp_total, level, opted_in, updated_at) "
            "VALUES ('streaker', 6, 6, ?, 60, 1, 1, ?)",
            (yesterday, time.time()),
        )

    # First action today → streak should advance to 7 + grant the
    # streak_7 bonus exactly once.
    s = streaks.award(user_id="streaker", kind="lesson_done")
    check(
        "streaks day-7 transition advances streak to 7",
        s.current_streak == 7,
        f"current={s.current_streak}",
    )
    # XP: 60 + 10 (lesson_done) + 50 (streak_7) = 120
    check(
        "streaks day-7 grants the streak_7 bonus once",
        s.xp_total == 120,
        f"xp={s.xp_total}",
    )

    # Second action same day → streak unchanged, NO additional bonus
    s2 = streaks.award(user_id="streaker", kind="lesson_done")
    check(
        "streaks subsequent same-day actions do NOT re-grant bonus",
        s2.current_streak == 7 and s2.xp_total == 130,
        f"xp={s2.xp_total} (expected 130 = 120 + 10)",
    )

    # --- Custom domain TLD validation ---
    custom_domains.migrate()
    for bad in (
        "localhost",
        "aipathshala.in",
        "app.aipathshala.in",
        "test.local",
        "myapp.test",
        "192.168.1.1",
        "",
        "   ",
        "no-dot",
    ):
        try:
            custom_domains.set_domain(org_id="x", domain=bad)
            check(f"custom_domain rejects {bad!r}", False)
        except ValueError:
            check(f"custom_domain rejects {bad!r}", True)
    # A real-looking domain should NOT be rejected on validation
    # (the org-id won't exist so the underlying UPDATE is a no-op,
    # but the validation passes — that's what we're testing).
    try:
        custom_domains.set_domain(org_id="x", domain="learn.stpauls.edu.in")
        check("custom_domain accepts valid school domain", True)
    except ValueError as e:
        check(f"custom_domain accepts valid school domain ({e})", False)

    # --- orgs.deactivate_member helper ---
    check(
        "orgs.deactivate_member is callable",
        callable(getattr(orgs, "deactivate_member", None)),
    )
    # On a non-existent member, returns False (idempotent).
    check(
        "orgs.deactivate_member on missing → False",
        orgs.deactivate_member(org_id="ghost", member_id="ghost") is False,
    )

    # --- HTTP rate limits + size caps ---
    with TestClient(app) as c:
        # Math: 2001-char latex → 422 (FastAPI validation)
        big = "x" * 2001
        r = c.post("/api/render/math", data={"latex": big, "inline": "false"})
        check(
            "POST /api/render/math rejects oversized LaTeX (422)",
            r.status_code == 422,
            f"status={r.status_code}",
        )

        # Diagram: oversized spec → 422
        big_spec = "{" + ("a" * 9000) + "}"
        r = c.post(
            "/api/render/diagram",
            data={"spec_lang": "svg_spec", "spec": big_spec,
                  "alt_text": "x", "diagram_type": "cycle",
                  "width": "400", "height": "300"},
        )
        check(
            "POST /api/render/diagram rejects oversized spec (422)",
            r.status_code == 422,
            f"status={r.status_code}",
        )

        # Diagram: out-of-range width → 422
        r = c.post(
            "/api/render/diagram",
            data={"spec_lang": "svg_spec", "spec": "{}",
                  "alt_text": "x", "diagram_type": "cycle",
                  "width": "10", "height": "300"},
        )
        check(
            "POST /api/render/diagram rejects tiny width (422)",
            r.status_code == 422,
        )

        # Math: legitimate request still works
        r = c.post(
            "/api/render/math",
            data={"latex": "x^2 + 1", "inline": "false"},
        )
        check(
            "POST /api/render/math accepts normal LaTeX",
            r.status_code == 200,
            f"status={r.status_code}",
        )

        # Hammer the math endpoint to verify 429 kicks in. After the
        # 30-token burst is exhausted, the 31st request returns 429.
        # (Use a distinct LaTeX to avoid any caching.)
        rate_limit.preview_math.reset("testclient")
        rate_limit.preview_math.reset("unknown")
        passed = 0
        rate_limited = False
        for i in range(40):
            r = c.post(
                "/api/render/math",
                data={"latex": f"x_{i}", "inline": "false"},
            )
            if r.status_code == 429:
                rate_limited = True
                break
            if r.status_code == 200:
                passed += 1
        check(
            "POST /api/render/math 429s after burst exhausted",
            rate_limited and passed >= 25,
            f"passed={passed} 429={rate_limited}",
        )

        # Curriculum scorer size cap
        r = c.post(
            "/api/curriculum/score",
            data={
                "lesson_text": "x" * 25000,
                "board": "cbse", "grade": "8", "subject": "science",
            },
        )
        check(
            "POST /api/curriculum/score rejects oversized lesson_text (422)",
            r.status_code == 422,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # --- requirements files exist ---
    check(
        "requirements-optional.txt exists",
        Path("requirements-optional.txt").exists(),
    )
    check(
        ".github/workflows/smoke.yml exists",
        Path(".github/workflows/smoke.yml").exists(),
    )

    print("-----------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed: print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
