"""Smoke for v2.7 — P1 NEP/NCF + P2 DIKSHA/NDEAR + Q4 customer success.
Run: PYTHONPATH=. python scripts/test_v2_7.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_7_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import customer_success, diksha, nep_alignment
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.7 smoke test")
    print("---------------")

    # --- P1 NEP + NCF alignment ---
    nep_alignment.migrate()
    nep_list = nep_alignment.list_nep()
    check(
        "P1 NEP catalog has >=10 competencies after seeding",
        len(nep_list) >= 10,
        f"got {len(nep_list)}",
    )
    cats = {c.category for c in nep_list}
    check(
        "P1 NEP categories include fln, 21cs, digital",
        {"fln", "21cs", "digital"}.issubset(cats),
    )
    # Idempotent re-migrate
    nep_alignment.migrate()
    check(
        "P1 re-migrate doesn't double-seed NEP",
        len(nep_alignment.list_nep()) == len(nep_list),
    )

    ncf_list = nep_alignment.list_ncf()
    check(
        "P1 NCF catalog has >=10 competencies",
        len(ncf_list) >= 10,
    )
    sec = nep_alignment.list_ncf(stage="secondary")
    check(
        "P1 list_ncf(stage='secondary') filters",
        all(c.stage == "secondary" for c in sec) and len(sec) >= 1,
    )

    # Bad stage
    try:
        nep_alignment.list_ncf(stage="bogus")
        check("P1 list_ncf rejects bad stage", False)
    except ValueError:
        check("P1 list_ncf rejects bad stage", True)

    # Score text
    lesson_text = (
        "In this lesson we use critical thinking to analyse Newton's "
        "first law of motion. We hypothesise that an object in motion "
        "stays in motion. The student will solve a problem about a "
        "ball on a frictionless surface and explain the result. "
        "We connect this to digital simulation tools."
    )
    res = nep_alignment.score_text(text=lesson_text, framework="nep")
    check(
        "P1 score_text(nep) returns matches + overall in [0,1]",
        0 <= res.overall_score <= 1
        and len(res.matches) >= 2,
        f"overall={res.overall_score} matches={len(res.matches)}",
    )
    check(
        "P1 critical_thinking + problem_solving among matches",
        any(m["competency_key"] == "21cs.critical_thinking"
            for m in res.matches)
        and any(m["competency_key"] == "21cs.problem_solving"
                for m in res.matches),
    )

    # NCF scoring with stage
    res_ncf = nep_alignment.score_text(
        text=lesson_text, framework="ncf", stage="secondary",
    )
    check(
        "P1 score_text(ncf, stage='secondary') restricts catalog",
        res_ncf.total_competencies == len(
            nep_alignment.list_ncf(stage="secondary"),
        ),
    )

    # Bad framework
    try:
        nep_alignment.score_text(text="x" * 25, framework="bogus")
        check("P1 score_text rejects bad framework", False)
    except ValueError:
        check("P1 score_text rejects bad framework", True)

    # Persist + retrieve
    nep_alignment.persist_lesson_alignment(
        lesson_id="lesson-A",
        framework="nep",
        matches=res.matches,
    )
    saved = nep_alignment.get_lesson_alignment(
        lesson_id="lesson-A", framework="nep",
    )
    check(
        "P1 persist + read alignment round-trips",
        len(saved) == len(res.matches),
    )

    # Re-persist replaces (no duplicates)
    nep_alignment.persist_lesson_alignment(
        lesson_id="lesson-A", framework="nep",
        matches=res.matches[:2],
    )
    saved2 = nep_alignment.get_lesson_alignment(
        lesson_id="lesson-A", framework="nep",
    )
    check(
        "P1 re-persist replaces (no growing duplicates)",
        len(saved2) == 2,
    )

    # Coverage summary across multiple lessons
    nep_alignment.persist_lesson_alignment(
        lesson_id="lesson-B", framework="nep",
        matches=[{"competency_key": "21cs.critical_thinking", "score": 0.5}],
    )
    cov = nep_alignment.coverage_summary(
        lesson_ids=["lesson-A", "lesson-B"], framework="nep",
    )
    check(
        "P1 coverage_summary aggregates across lessons",
        cov["total_lessons"] == 2
        and len(cov["by_competency"]) >= 1,
    )

    # --- P2 DIKSHA + NDEAR ---
    diksha.migrate()
    check(
        "P2 is_api_configured False without DIKSHA_API_KEY",
        not diksha.is_api_configured(),
    )

    # Manifest-paste import
    ref = diksha.import_from_manifest(
        diksha_id="do_31312345",
        title="NCERT Class 10 — Quadratic Equations",
        description="Standard NCERT lesson",
        board="cbse", grade="10",
        subject="mathematics", medium="en",
        content_type="video",
        content_url="https://diksha.gov.in/play/do_31312345.mp4",
        imported_by="admin-1",
    )
    check(
        "P2 import_from_manifest returns DikshaContentRef",
        ref.diksha_id == "do_31312345"
        and ref.content_type == "video",
    )

    # Idempotent re-import (updates metadata)
    ref2 = diksha.import_from_manifest(
        diksha_id="do_31312345",
        title="NCERT Class 10 — Quadratic (updated)",
        board="cbse", grade="10",
    )
    check(
        "P2 re-import updates same row (UNIQUE diksha_id)",
        ref2.id == ref.id
        and ref2.title.endswith("(updated)"),
    )

    # Bad content_type rejected
    try:
        diksha.import_from_manifest(
            diksha_id="do_bad", title="x", content_type="bogus",
        )
        check("P2 import_from_manifest rejects bad content_type", False)
    except ValueError:
        check("P2 import_from_manifest rejects bad content_type", True)

    # API path without key raises
    try:
        diksha.import_from_api(diksha_id="do_x")
        check("P2 import_from_api rejects no-key path", False)
    except ValueError:
        check("P2 import_from_api rejects no-key path", True)

    # Listing
    refs = diksha.list_refs(board="cbse", grade="10")
    check(
        "P2 list_refs with board+grade filter returns row",
        any(r.diksha_id == "do_31312345" for r in refs),
    )

    # NDEAR export
    manifest = diksha.build_ndear_manifest(
        lesson_id="lesson-X",
        title="Photosynthesis explained",
        description="A 5-min explainer",
        board="cbse", grade=8, subject="science",
        language="en",
        content_url="https://cdn.aipathshala.in/v/abc.mp4",
        duration_seconds=300,
    )
    check(
        "P2 build_ndear_manifest produces NDEAR-shaped dict",
        manifest["schema"] == "ndear/v1.0"
        and "streamingUrl" in manifest
        and manifest["language"] == ["en"]
        and "frameworks" in manifest,
    )

    rec = diksha.record_export(
        lesson_id="lesson-X", manifest=manifest,
        exported_by="admin-1",
    )
    check(
        "P2 record_export returns NDEARExport with sha + version",
        rec.lesson_id == "lesson-X"
        and len(rec.manifest_sha) == 32
        and rec.ndear_version in ("v1.0", "1.0"),
    )

    last = diksha.latest_export("lesson-X")
    check(
        "P2 latest_export returns the just-recorded one",
        last and last.id == rec.id,
    )
    check(
        "P2 latest_export returns None for unknown lesson",
        diksha.latest_export("never-exported") is None,
    )

    # Bad manifest rejected
    try:
        diksha.record_export(
            lesson_id="x", manifest="not-a-dict",  # type: ignore
        )
        check("P2 record_export rejects non-dict manifest", False)
    except ValueError:
        check("P2 record_export rejects non-dict manifest", True)

    # --- Q4 customer success ---
    customer_success.migrate()
    hs = customer_success.compute_health(org_id="org-1", period_days=30)
    check(
        "Q4 compute_health returns HealthScore in [0,100]",
        0 <= hs.score <= 100 and hs.band in (
            "healthy", "watch", "at_risk", "critical",
        ),
        f"score={hs.score} band={hs.band}",
    )
    check(
        "Q4 components dict has 4 keys",
        set(hs.components.keys()) == {
            "engagement", "payment", "support", "growth",
        },
    )

    # Bad period_days
    try:
        customer_success.compute_health(org_id="x", period_days=999)
        check("Q4 compute_health rejects period_days > 365", False)
    except ValueError:
        check("Q4 compute_health rejects period_days > 365", True)

    latest = customer_success.latest_health("org-1")
    check(
        "Q4 latest_health returns the just-computed snapshot",
        latest and abs(latest.score - hs.score) < 0.01,
    )

    # at_risk_orgs — use a high threshold so org-1's mid-range score
    # ('watch' band ~67) lands in the queue
    at_risk = customer_success.at_risk_orgs(threshold=80.0)
    check(
        "Q4 at_risk_orgs surfaces below-threshold orgs",
        any(h.org_id == "org-1" for h in at_risk),
    )

    # CS events
    e = customer_success.log_event(
        kind="alert", title="Engagement dropped 40% week over week",
        org_id="org-1", severity="warning",
        body="DAU/MAU went from 0.35 to 0.21.",
    )
    check(
        "Q4 log_event returns CSEvent",
        e.kind == "alert" and e.severity == "warning",
    )

    # Bad kind/severity
    try:
        customer_success.log_event(kind="bogus", title="x")
        check("Q4 log_event rejects bad kind", False)
    except ValueError:
        check("Q4 log_event rejects bad kind", True)
    try:
        customer_success.log_event(
            kind="alert", title="x", severity="ultra",
        )
        check("Q4 log_event rejects bad severity", False)
    except ValueError:
        check("Q4 log_event rejects bad severity", True)

    # Resolve
    check(
        "Q4 resolve_event → True first time",
        customer_success.resolve_event(e.id),
    )
    check(
        "Q4 resolve_event → False second time",
        not customer_success.resolve_event(e.id),
    )

    # Unresolved queue surfaces only unresolved alerts/escalations
    customer_success.log_event(
        kind="escalation", title="Critical payment issue",
        org_id="org-1", severity="critical",
    )
    queue = customer_success.unresolved_alerts()
    check(
        "Q4 unresolved_alerts returns the critical escalation first",
        queue and queue[0].severity == "critical",
    )

    # org-scoped events
    org_evs = customer_success.list_events_for_org(org_id="org-1")
    check(
        "Q4 list_events_for_org returns both events",
        len(org_evs) >= 2,
    )

    # Onboarding
    steps = customer_success.get_onboarding_steps()
    check(
        "Q4 get_onboarding_steps returns 7+ items in offset order",
        len(steps) >= 7
        and steps[0]["day_offset"] == 0
        and steps[-1]["day_offset"] >= 14,
    )

    onboard_evt = customer_success.emit_onboarding_step(
        org_id="org-1", step_key="welcome",
    )
    check(
        "Q4 emit_onboarding_step creates 'onboarding_step' event",
        onboard_evt.kind == "onboarding_step",
    )

    try:
        customer_success.emit_onboarding_step(
            org_id="x", step_key="never_real",
        )
        check("Q4 emit_onboarding_step rejects unknown step", False)
    except ValueError:
        check("Q4 emit_onboarding_step rejects unknown step", True)

    # Renewal pipeline
    r = customer_success.upsert_renewal(
        org_id="org-1", plan_tier="M3",
        current_period_end=time.time() + 60 * 86400,
    )
    check(
        "Q4 upsert_renewal returns RenewalEntry with derived churn_risk",
        r.org_id == "org-1" and r.churn_risk in (
            "low", "medium", "high",
        ),
    )

    # Re-upsert updates
    r2 = customer_success.upsert_renewal(
        org_id="org-1", plan_tier="M4",
        current_period_end=time.time() + 30 * 86400,
        churn_risk="high",
    )
    check(
        "Q4 re-upsert updates churn_risk + plan_tier",
        r2.plan_tier == "M4" and r2.churn_risk == "high",
    )

    # record action
    check(
        "Q4 record_renewal_action → True",
        customer_success.record_renewal_action(
            org_id="org-1", action_kind="email_sent",
        ),
    )

    # upcoming_renewals sorting
    customer_success.upsert_renewal(
        org_id="org-2", plan_tier="M2",
        current_period_end=time.time() + 45 * 86400,
        churn_risk="low",
    )
    upcoming = customer_success.upcoming_renewals(days_ahead=90)
    check(
        "Q4 upcoming_renewals sorts high-risk first",
        len(upcoming) >= 2
        and upcoming[0].churn_risk == "high",
    )

    # Bad churn_risk rejected
    try:
        customer_success.upsert_renewal(
            org_id="org-x", plan_tier="M3",
            current_period_end=time.time() + 30 * 86400,
            churn_risk="catastrophic",
        )
        check("Q4 upsert_renewal rejects bad churn_risk", False)
    except ValueError:
        check("Q4 upsert_renewal rejects bad churn_risk", True)

    # --- HTTP layer ---
    with TestClient(app) as c:
        r = c.get("/api/frameworks/nep")
        check(
            "GET /api/frameworks/nep returns 200 + rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 10,
        )

        r = c.get("/api/frameworks/ncf?stage=secondary")
        check(
            "GET /api/frameworks/ncf?stage=secondary filters",
            r.status_code == 200
            and all(c["stage"] == "secondary" for c in r.json()["rows"]),
        )

        r = c.get("/api/frameworks/ncf?stage=bogus")
        check(
            "GET /api/frameworks/ncf rejects bad stage (400)",
            r.status_code == 400,
        )

        # Reset rate-limit so we can hit scorer endpoint cleanly
        from padhai import rate_limit
        rate_limit.preview_scorer.reset("testclient")
        rate_limit.preview_scorer.reset("unknown")

        r = c.post(
            "/api/frameworks/score",
            data={"text": lesson_text, "framework": "nep",
                  "lesson_id": "http-lesson-1"},
        )
        check(
            "POST /api/frameworks/score returns 200 + overall_score",
            r.status_code == 200 and "overall_score" in r.json(),
        )

        r = c.get(
            "/api/frameworks/lessons/http-lesson-1/alignment",
        )
        check(
            "GET /api/frameworks/lessons/{id}/alignment returns rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        # DIKSHA
        r = c.get("/api/diksha/status")
        check(
            "GET /api/diksha/status returns api_configured + ndear_version",
            r.status_code == 200
            and r.json()["ndear_version"] == "1.0",
        )

        r = c.get("/api/diksha/refs?board=cbse&grade=10")
        check(
            "GET /api/diksha/refs returns rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        r = c.get("/api/diksha/refs/no-such-id")
        check(
            "GET /api/diksha/refs/{bogus} returns 404",
            r.status_code == 404,
        )

        r = c.get("/api/diksha/lessons/lesson-X/latest-export")
        check(
            "GET latest-export returns export when present",
            r.status_code == 200
            and r.json().get("export", {}).get("ndear_version")
                in ("v1.0", "1.0"),
        )

        # CS admin endpoints
        r = c.get("/api/admin/cs/onboarding/steps")
        check(
            "GET /api/admin/cs/onboarding/steps returns 200",
            r.status_code == 200 and len(r.json()["steps"]) >= 7,
        )

        r = c.get("/api/admin/cs/events")
        check(
            "GET /api/admin/cs/events returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get("/api/admin/cs/health/org-1")
        check(
            "GET /api/admin/cs/health/{org_id} returns health snapshot",
            r.status_code == 200
            and r.json()["health"]["band"] in (
                "healthy", "watch", "at_risk", "critical",
            ),
        )

        r = c.get("/api/admin/cs/renewals?days_ahead=90")
        check(
            "GET /api/admin/cs/renewals?days_ahead=90 returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get("/api/admin/cs/renewals?days_ahead=10000")
        check(
            "GET /api/admin/cs/renewals rejects days_ahead > 730",
            r.status_code == 400,
        )

        # Auth gate on import endpoints
        r = c.post(
            "/api/diksha/import",
            data={"diksha_id": "do_x", "title": "x"},
        )
        check(
            "POST /api/diksha/import requires auth (401)",
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
        "v2.7 brought route count above 300",
        len(app.routes) > 300,
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
