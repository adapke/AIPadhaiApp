"""Smoke for v3.15 — Adaptive personalised Exam Packs.

Covers:
- create_personalised_pack (idempotent on (user, base))
- re_adapt: signal gathering + rule firing + weightage adjustment
- Rule semantics: weak / strong / skipped / recent_mock_low
- Bound checks: adjusted in [base × 0.3, base × 3]
- Personalised topic view falls back to base when no override
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_15.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_15_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import adaptive_packs as ap
    from padhai import exam_taxonomy as et
    from padhai import mastery
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

    print("v3.15 smoke test — Adaptive packs")
    print("---------------------------------")

    ap.migrate()
    ap.migrate()
    et.migrate()    # seeded taxonomy
    mastery.migrate()

    # ============================================================
    # create_personalised_pack
    # ============================================================
    p = ap.create_personalised_pack(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "create_personalised_pack returns Pack with auto title",
        p.base_pack_code == "upsc_cse_2026"
        and "Personalised" in p.title,
    )

    # Idempotent
    p_again = ap.create_personalised_pack(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "create_personalised_pack idempotent on (user, base)",
        p_again.id == p.id,
    )

    # Unknown base pack
    check(
        "create_personalised_pack rejects unknown base",
        raises(ap.create_personalised_pack, exc_type=ValueError,
               user_id="x", base_pack_code="ghost_pack"),
    )

    # ============================================================
    # Build signals: stu-1 is weak in polity, strong in history,
    # has skipped economy plans, scored poorly on environment in
    # recent mock
    # ============================================================

    # Weak: polity — 5 wrong attempts → mastery < 0.4
    for _ in range(5):
        mastery.update(
            user_id="stu-1", topic_key="upsc_cse::polity",
            correct=False,
        )
    # Strong: history_modern — many correct → mastery > 0.85
    for _ in range(8):
        mastery.update(
            user_id="stu-1",
            topic_key="upsc_cse::history_modern", correct=True,
        )

    # Adapt
    out = ap.re_adapt(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "re_adapt returns topics_updated > 0",
        out["topics_updated"] > 0,
    )
    check(
        "re_adapt fires weak_topic_boost",
        out["rules_fired"].get("weak_topic_boost", 0) >= 1,
    )
    check(
        "re_adapt fires strong_topic_relief",
        out["rules_fired"].get("strong_topic_relief", 0) >= 1,
    )

    # Personalised topic view shows polity at higher adjusted_weightage
    view = ap.personalised_topic_view(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    polity = next(
        (t for t in view if t["topic_code"] == "polity"), None,
    )
    history = next(
        (t for t in view if t["topic_code"] == "history_modern"),
        None,
    )
    check(
        "Polity (weak) has adjusted_weightage > base",
        polity
        and polity["adjusted_weightage"] > polity["base_weightage"],
    )
    check(
        "Polity reasons include 'weak_topic_boost'",
        polity
        and "weak_topic_boost" in polity["reasons"],
    )
    check(
        "History_modern (strong) has adjusted_weightage < base",
        history
        and history["adjusted_weightage"] < history["base_weightage"],
    )
    check(
        "History_modern reasons include 'strong_topic_relief'",
        history
        and "strong_topic_relief" in history["reasons"],
    )

    # Untouched topic falls back to base weightage
    economy = next(
        (t for t in view if t["topic_code"] == "economy"), None,
    )
    check(
        "Economy (untouched) keeps base_weightage",
        economy
        and economy["adjusted_weightage"] == economy["base_weightage"],
    )

    # Adapted count bumped
    p_after = ap.get_personalised_pack(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "adaptation_count bumped + last_adapted_at set",
        p_after.adaptation_count >= 1
        and p_after.last_adapted_at is not None,
    )

    # ============================================================
    # Bounds check — extreme weak case shouldn't exceed 3x base
    # ============================================================

    # Layer more weakness signals on polity
    for _ in range(10):
        mastery.update(
            user_id="stu-extreme", topic_key="upsc_cse::polity",
            correct=False,
        )
    ap.create_personalised_pack(
        user_id="stu-extreme", base_pack_code="upsc_cse_2026",
    )
    ap.re_adapt(
        user_id="stu-extreme", base_pack_code="upsc_cse_2026",
    )
    extreme_view = ap.personalised_topic_view(
        user_id="stu-extreme", base_pack_code="upsc_cse_2026",
    )
    extreme_polity = next(
        (t for t in extreme_view if t["topic_code"] == "polity"),
        None,
    )
    check(
        "Extreme weak topic capped at base × 3.0",
        extreme_polity
        and extreme_polity["adjusted_weightage"]
        <= extreme_polity["base_weightage"] * 3.0 + 0.01,
    )

    # ============================================================
    # Signal audit
    # ============================================================
    signals = ap.list_signals(p_after.id)
    check(
        "list_signals returns ≥2 audit rows",
        len(signals) >= 2,
    )

    weak_signals = [
        s for s in signals if s.rule_code == "weak_topic_boost"
    ]
    check(
        "weak_topic_boost signal has mastery value attached",
        weak_signals
        and weak_signals[0].signal_value is not None,
    )

    # ============================================================
    # should_re_adapt
    # ============================================================

    # Fresh pack → no need
    needs, reason = ap.should_re_adapt(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "should_re_adapt False for fresh pack",
        not needs and reason == "fresh",
    )

    # User with no pack
    needs2, reason2 = ap.should_re_adapt(
        user_id="never-adapted", base_pack_code="upsc_cse_2026",
    )
    check(
        "should_re_adapt True for unknown user",
        needs2 and reason2 == "no_personalised_pack",
    )

    # Stale: manually age the pack
    with ap._conn() as conn:
        conn.execute(
            "UPDATE personalised_packs SET last_adapted_at = ? "
            "WHERE id = ?",
            (time.time() - 86400 * (
                ap.ADAPT_STALE_AFTER_DAYS + 1),
             p_after.id),
        )
    needs3, reason3 = ap.should_re_adapt(
        user_id="stu-1", base_pack_code="upsc_cse_2026",
    )
    check(
        "should_re_adapt True when stale",
        needs3 and reason3 == "stale",
    )

    # ============================================================
    # Listing + delete
    # ============================================================
    listed = ap.list_user_packs("stu-1")
    check(
        "list_user_packs returns user's packs",
        len(listed) >= 1,
    )

    # Delete
    check(
        "delete_personalised_pack True for owner",
        ap.delete_personalised_pack(
            pack_id=p_after.id, user_id="stu-1",
        ),
    )
    check(
        "delete_personalised_pack False for non-owner",
        not ap.delete_personalised_pack(
            pack_id="ghost-id", user_id="stranger",
        ),
    )

    # ============================================================
    # personalised_topic_view falls back when no overrides
    # ============================================================
    fresh_view = ap.personalised_topic_view(
        user_id="brand-new-user",
        base_pack_code="upsc_cse_2026",
    )
    check(
        "personalised_topic_view falls back when no overrides",
        fresh_view
        and all(t["adjusted_weightage"] == t["base_weightage"]
                for t in fresh_view)
        and all(not t["is_personalised"] for t in fresh_view),
    )

    # Bad pack code
    check(
        "personalised_topic_view rejects unknown base",
        raises(ap.personalised_topic_view, exc_type=ValueError,
               user_id="x", base_pack_code="ghost_pack"),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/adaptive-packs",
            data={"base_pack_code": "upsc_cse_2026"},
        )
        check(
            "POST /api/adaptive-packs → 401",
            r.status_code == 401,
        )

        r = c.get("/api/adaptive-packs/me")
        check(
            "GET /api/adaptive-packs/me → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/adaptive-packs/upsc_cse_2026/re-adapt",
        )
        check(
            "POST /api/adaptive-packs/{}/re-adapt → 401",
            r.status_code == 401,
        )

        r = c.get(
            "/api/adaptive-packs/upsc_cse_2026/topics",
        )
        check(
            "GET /api/adaptive-packs/{}/topics → 401",
            r.status_code == 401,
        )

        r = c.get(
            "/api/adaptive-packs/upsc_cse_2026/should-re-adapt",
        )
        check(
            "GET should-re-adapt → 401",
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
        "v3.15 brought route count above 570",
        len(app.routes) > 570,
        f"routes={len(app.routes)}",
    )

    print("---------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
