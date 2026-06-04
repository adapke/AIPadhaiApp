"""Smoke for v2.1 — L1 tutor + L6 LLM observability + Q1 feature flags.
First v3-roadmap release.

Run: PYTHONPATH=. python scripts/test_v2_1.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_1_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import feature_flags, llm_obs, tutor
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.1 smoke test")
    print("---------------")

    # --- Q1 feature flags ---
    feature_flags.migrate()
    f = feature_flags.upsert(
        flag_key="tutor.enabled",
        description="L1 AI tutor rollout gate",
        enabled_default=False,
        rollout_pct=0,
        target_user_ids=["pilot-1", "pilot-2"],
    )
    check(
        "Q1 upsert returns Flag with rollout_pct=0",
        f.rollout_pct == 0 and not f.enabled_default,
    )
    check(
        "Q1 explicit target_user_ids hit returns True",
        feature_flags.is_enabled("tutor.enabled", user_id="pilot-1"),
    )
    check(
        "Q1 non-target user with rollout_pct=0 returns False",
        not feature_flags.is_enabled("tutor.enabled", user_id="random-user"),
    )

    # Roll out to 50% — verify deterministic split
    feature_flags.upsert(
        flag_key="tutor.enabled",
        rollout_pct=50,
    )
    on = sum(
        1 for i in range(200)
        if feature_flags.is_enabled("tutor.enabled", user_id=f"u-{i}")
    )
    check(
        "Q1 rollout_pct=50 puts ~50% of users in the cohort",
        80 < on < 120,
        f"on={on}/200",
    )
    # Stability — same user always lands same side
    same_user_first = feature_flags.is_enabled("tutor.enabled", user_id="u-42")
    same_user_again = feature_flags.is_enabled("tutor.enabled", user_id="u-42")
    check("Q1 evaluation is deterministic per (flag, user)",
          same_user_first == same_user_again)

    # Variants — A/B test split
    feature_flags.upsert(
        flag_key="tutor.style",
        rollout_pct=100,
        variants={"socratic": 50, "direct": 50},
    )
    a = sum(
        1 for i in range(200)
        if feature_flags.variant_for("tutor.style", user_id=f"u-{i}") == "socratic"
    )
    check(
        "Q1 A/B variants split ~50/50",
        80 < a < 120,
        f"socratic={a}/200",
    )

    # Unknown flag → off
    check(
        "Q1 unknown flag returns False",
        not feature_flags.is_enabled("bogus.flag", user_id="u-1"),
    )

    # resolve_all snapshot
    snap = feature_flags.resolve_all(user_id="u-7")
    check(
        "Q1 resolve_all includes both seeded flags",
        "tutor.enabled" in snap and "tutor.style" in snap,
    )

    # Invalid flag_key rejected
    try:
        feature_flags.upsert(flag_key="bad flag with spaces", enabled_default=True)
        check("Q1 upsert rejects bad flag_key", False)
    except ValueError:
        check("Q1 upsert rejects bad flag_key", True)

    # delete
    feature_flags.upsert(flag_key="will.delete", enabled_default=True)
    check("Q1 delete returns True", feature_flags.delete("will.delete"))
    check(
        "Q1 second delete returns False",
        not feature_flags.delete("will.delete"),
    )

    # --- L6 LLM observability ---
    llm_obs.migrate()
    # Cost estimation
    cost = llm_obs.estimate_cost_paise(
        model="claude-haiku-4-5",
        tokens_in=1000, tokens_out=500,
    )
    check(
        "L6 cost estimate haiku 1k+500 tokens is non-zero and reasonable",
        0 < cost < 100,
        f"cost={cost} paise",
    )
    # Caching discount only applies to input tokens — use a prompt
    # with much more input than output to see the headline savings.
    big_in = llm_obs.estimate_cost_paise(
        model="claude-haiku-4-5",
        tokens_in=50000, tokens_out=200, cached=False,
    )
    big_in_cached = llm_obs.estimate_cost_paise(
        model="claude-haiku-4-5",
        tokens_in=50000, tokens_out=200, cached=True,
    )
    check(
        "L6 cached path saves >70% on input-heavy prompts",
        big_in_cached < big_in * 0.30,
        f"cached={big_in_cached} vs uncached={big_in}",
    )
    unknown_cost = llm_obs.estimate_cost_paise(
        model="bogus-model-7", tokens_in=1000, tokens_out=500,
    )
    check("L6 unknown model defaults to 0 cost", unknown_cost == 0)

    # Record + aggregate
    call_id = llm_obs.record_call(
        module="tutor", prompt_version="v1",
        model="claude-haiku-4-5",
        tokens_in=200, tokens_out=100,
        latency_ms=850,
        user_id="metric-user",
    )
    check("L6 record_call returns id", bool(call_id))

    # Multiple calls for stats
    for _ in range(3):
        llm_obs.record_call(
            module="pedagogy", prompt_version="v2",
            model="claude-opus-4-7",
            tokens_in=5000, tokens_out=2000,
            latency_ms=3200,
        )
    stats = llm_obs.stats_for_period(hours=1.0)
    check(
        "L6 stats_for_period counts the 4 calls + non-zero cost",
        stats["total_calls"] >= 4 and stats["total_cost_inr"] > 0,
        f"calls={stats['total_calls']} cost=₹{stats['total_cost_inr']}",
    )
    check(
        "L6 by_module breakdown has tutor + pedagogy",
        "tutor" in stats["by_module"] and "pedagogy" in stats["by_module"],
    )
    check(
        "L6 by_model breakdown has haiku + opus",
        "claude-haiku-4-5" in stats["by_model"]
        and "claude-opus-4-7" in stats["by_model"],
    )

    # Flagging
    fid = llm_obs.flag_call(
        llm_call_id=call_id,
        reporter_user_id="metric-user",
        reason="The tutor said 2+2=5",
        severity="high",
    )
    check("L6 flag_call returns id", bool(fid))
    pending = llm_obs.pending_flags()
    check(
        "L6 pending_flags surfaces the new flag",
        any(f["id"] == fid for f in pending),
    )
    check(
        "L6 high-severity flag sorts to top",
        pending[0]["severity"] == "high",
    )
    check(
        "L6 review_flag → True first time",
        llm_obs.review_flag(flag_id=fid, status="confirmed", note="bug confirmed"),
    )
    try:
        llm_obs.flag_call(
            llm_call_id="x", reporter_user_id="x",
            reason="x", severity="impossible",
        )
        check("L6 flag_call rejects bad severity", False)
    except ValueError:
        check("L6 flag_call rejects bad severity", True)

    # --- L1 tutor ---
    tutor.migrate()
    check("L1 is_available reflects env (False without ANTHROPIC_API_KEY)",
          not tutor.is_available())

    s = tutor.start_session(user_id="stu-tutor")
    check(
        "L1 start_session returns Session",
        s.id and s.user_id == "stu-tutor" and s.ended_at is None,
    )

    # M1 tier → "upgrade" message; no Claude call
    reply = tutor.send_message(sid=s.id, user_text="Hello", user_tier="M1")
    check(
        "L1 M1 tier gets premium-feature message",
        reply.over_budget and "premium" in reply.reply.lower()
        and reply.tokens_in == 0,
    )

    # M2 with no ANTHROPIC_API_KEY → "not configured" canned reply
    reply2 = tutor.send_message(sid=s.id, user_text="Test", user_tier="M2")
    check(
        "L1 M2 without key gets 'not configured' canned reply",
        reply2.tokens_in == 0 and reply2.tokens_out == 0
        and "not configured" in reply2.reply.lower(),
    )

    # End session
    check("L1 end_session → True first time", tutor.end_session(sid=s.id))
    check("L1 end_session → False second time",
          not tutor.end_session(sid=s.id))

    # Re-sending into an ended session should raise
    try:
        tutor.send_message(sid=s.id, user_text="x", user_tier="M2")
        check("L1 send_message on ended session raises", False)
    except ValueError:
        check("L1 send_message on ended session raises", True)

    # Long memory
    tutor.remember(
        user_id="stu-tutor",
        memory_key="weak_topic",
        value="photosynthesis",
        confidence=0.85,
    )
    val = tutor.recall(user_id="stu-tutor", memory_key="weak_topic")
    check(
        "L1 remember/recall round-trips",
        val == "photosynthesis",
        f"got {val!r}",
    )
    full = tutor.recall(user_id="stu-tutor")
    check(
        "L1 recall() returns dict of all memories",
        isinstance(full, dict) and "weak_topic" in full,
    )

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Auth gates on /api/me/flags + tutor sessions
        r = c.get("/api/me/flags")
        check("/api/me/flags requires auth (401)", r.status_code == 401)
        r = c.post("/api/tutor/sessions")
        check("POST /api/tutor/sessions requires auth (401)",
              r.status_code == 401)

        # Public read endpoints
        r = c.get("/api/admin/llm/stats?hours=1")
        check(
            "GET /api/admin/llm/stats returns 200 + total_calls",
            r.status_code == 200 and "total_calls" in r.json(),
        )

        r = c.get("/api/admin/flags")
        check(
            "GET /api/admin/flags returns 200 + rows",
            r.status_code == 200 and "rows" in r.json(),
        )

        # Admin upsert (open for now per v2.1 scope)
        r = c.post(
            "/api/admin/flags",
            data={"flag_key": "via.http", "rollout_pct": "25",
                  "enabled_default": "false"},
        )
        check(
            "POST /api/admin/flags creates a flag",
            r.status_code == 200 and r.json().get("rollout_pct") == 25,
        )

        # Hours validation
        r = c.get("/api/admin/llm/stats?hours=10000")
        check(
            "GET /api/admin/llm/stats rejects oversized hours (400)",
            r.status_code == 400,
        )

        # Exposure stats endpoint
        r = c.get("/api/admin/flags/tutor.enabled/exposures?hours=1")
        check(
            "GET /api/admin/flags/{key}/exposures returns 200",
            r.status_code == 200 and "total" in r.json(),
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Route count grew with v2.1
    check(
        "v2.1 brought route count above 170",
        len(app.routes) > 170,
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
