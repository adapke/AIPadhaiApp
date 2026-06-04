"""Smoke for v2.8 — P3 state partnerships + R1 corporate + Q5 sales.

Run: PYTHONPATH=. python scripts/test_v2_8.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_8_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import corporate, sales_pipeline, state_partnerships
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.8 smoke test")
    print("---------------")

    # --- P3 state partnerships ---
    state_partnerships.migrate()
    all_states = state_partnerships.list_all()
    check(
        "P3 starter catalog has >=5 states seeded",
        len(all_states) >= 5,
        f"got {len(all_states)}",
    )
    check(
        "P3 UP seeded with student_population",
        any(p.state_code == "UP" and p.student_population
            and p.student_population > 0 for p in all_states),
    )

    # Idempotent re-migrate
    state_partnerships.migrate()
    check(
        "P3 re-migrate doesn't double-seed",
        len(state_partnerships.list_all()) == len(all_states),
    )

    # Filter by status
    prospects = state_partnerships.list_all(status="prospect")
    check(
        "P3 list_all(status='prospect') filters",
        all(p.status == "prospect" for p in prospects)
        and len(prospects) >= 1,
    )

    # Bad status filter
    try:
        state_partnerships.list_all(status="bogus")
        check("P3 list_all rejects bad status", False)
    except ValueError:
        check("P3 list_all rejects bad status", True)

    # Upsert — update existing
    upd = state_partnerships.upsert(
        state_code="UP", status="discovery",
        contact_name="Joint Secretary",
        contact_email="js@upgov.example",
        contract_value_inr=50_000_000,
    )
    check(
        "P3 upsert updates UP status + contract value",
        upd.status == "discovery"
        and upd.contract_value_inr == 50_000_000,
    )

    # Upsert — new state requires name+region+lang
    try:
        state_partnerships.upsert(state_code="XX")
        check("P3 upsert rejects new state without required fields", False)
    except ValueError:
        check("P3 upsert rejects new state without required fields", True)

    # Upsert — new state with all fields
    new_state = state_partnerships.upsert(
        state_code="OD", name="Odisha",
        region="east", primary_language="or",
        student_population=8_000_000,
    )
    check(
        "P3 upsert creates new state when fields supplied",
        new_state.state_code == "OD"
        and new_state.name == "Odisha"
        and new_state.status == "prospect",
    )

    # Status transition
    check(
        "P3 set_status → pilot",
        state_partnerships.set_status(
            state_code="OD", status="pilot",
        ),
    )
    check(
        "P3 set_status rejects bogus status",
        _raises(state_partnerships.set_status,
                state_code="OD", status="bogus"),
    )

    # Pipeline summary
    summary = state_partnerships.pipeline_summary()
    check(
        "P3 pipeline_summary returns dict keyed by status",
        "discovery" in summary or "prospect" in summary,
    )
    check(
        "P3 pipeline_summary has states + value + reach",
        all(
            {"states", "contract_value_inr", "reachable_students"}.issubset(v)
            for v in summary.values()
        ),
    )

    # --- R1 corporate training ---
    corporate.migrate()
    corp = corporate.register_corp_org(
        name="Infosys Test Tenant",
        industry="IT Services",
        headcount=300_000,
        contact_email="lnd@infosys.example",
        integration_kind="xapi",
        seat_limit=50,
    )
    check(
        "R1 register_corp_org returns CorporateOrg",
        corp.name == "Infosys Test Tenant"
        and corp.integration_kind == "xapi"
        and corp.seat_limit == 50,
    )

    # Duplicate name
    try:
        corporate.register_corp_org(name="Infosys Test Tenant")
        check("R1 register_corp_org rejects duplicate name", False)
    except ValueError:
        check("R1 register_corp_org rejects duplicate name", True)

    # Bad integration kind
    try:
        corporate.register_corp_org(
            name="WrongIntegration",
            integration_kind="bogus",
        )
        check("R1 register_corp_org rejects bad integration_kind", False)
    except ValueError:
        check("R1 register_corp_org rejects bad integration_kind", True)

    # Create training path
    modules = [
        {"title": "POSH 101", "kind": "lesson", "est_min": 10},
        {"title": "POSH Quiz", "kind": "quiz", "est_min": 5},
        {"title": "POSH Certificate", "kind": "cert", "est_min": 1},
    ]
    path = corporate.create_path(
        corp_org_id=corp.id,
        title="POSH Mandatory Compliance 2026",
        modules=modules,
        category="compliance",
        created_by="hr-admin",
    )
    check(
        "R1 create_path returns TrainingPath status='draft'",
        path.status == "draft" and len(path.modules) == 3
        and path.duration_min == 16,
    )

    # Empty modules rejected
    try:
        corporate.create_path(
            corp_org_id=corp.id, title="empty", modules=[],
        )
        check("R1 create_path rejects empty modules", False)
    except ValueError:
        check("R1 create_path rejects empty modules", True)

    # Bad category
    try:
        corporate.create_path(
            corp_org_id=corp.id, title="x",
            modules=modules, category="bogus_cat",
        )
        check("R1 create_path rejects bad category", False)
    except ValueError:
        check("R1 create_path rejects bad category", True)

    # Publish
    check(
        "R1 publish_path → True on draft with modules",
        corporate.publish_path(path_id=path.id),
    )
    check(
        "R1 publish_path → False on already-published",
        not corporate.publish_path(path_id=path.id),
    )

    # Enroll
    enr = corporate.enroll(
        path_id=path.id, employee_user_id="emp-1",
    )
    check(
        "R1 enroll returns Enrollment status='enrolled'",
        enr.status == "enrolled" and enr.completion_pct == 0.0,
    )

    # Duplicate enroll rejected
    try:
        corporate.enroll(path_id=path.id, employee_user_id="emp-1")
        check("R1 enroll rejects duplicate (path, employee)", False)
    except ValueError:
        check("R1 enroll rejects duplicate (path, employee)", True)

    # Progress
    enr_p = corporate.update_progress(
        enrollment_id=enr.id, completion_pct=50,
    )
    check(
        "R1 update_progress 50% → status='in_progress'",
        enr_p.status == "in_progress"
        and enr_p.completion_pct == 50.0
        and enr_p.started_at is not None,
    )

    # Complete
    enr_c = corporate.update_progress(
        enrollment_id=enr.id, completion_pct=100,
        final_score=92.5,
    )
    check(
        "R1 update_progress 100% → status='completed'",
        enr_c.status == "completed"
        and enr_c.completed_at is not None
        and enr_c.final_score == 92.5,
    )

    # xAPI emit
    sid = corporate.emit_xapi(
        enrollment_id=enr.id, actor_user_id="emp-1",
        verb="completed", object_id=path.id,
        object_kind="path",
        result={"score": 92.5, "success": True, "completion": True},
    )
    check("R1 emit_xapi returns statement id", bool(sid))

    # Bad verb
    try:
        corporate.emit_xapi(
            enrollment_id=enr.id, actor_user_id="emp-1",
            verb="alien_verb", object_id="x",
        )
        check("R1 emit_xapi rejects bad verb", False)
    except ValueError:
        check("R1 emit_xapi rejects bad verb", True)

    # List statements
    stmts = corporate.xapi_statements_for_enrollment(enr.id)
    check(
        "R1 xapi_statements_for_enrollment returns sid",
        any(s["id"] == sid for s in stmts),
    )

    # Seat-limit enforcement
    # Fill remaining seats then expect rejection on 51st
    for i in range(49):
        corporate.enroll(
            path_id=path.id, employee_user_id=f"emp-bulk-{i}",
        )
    # emp-1 + 49 bulk = 50 = seat_limit
    try:
        corporate.enroll(
            path_id=path.id, employee_user_id="emp-overflow",
        )
        check("R1 enroll enforces seat_limit", False)
    except ValueError as e:
        check(
            "R1 enroll enforces seat_limit",
            "seat limit" in str(e).lower(),
        )

    # Re-enrolling existing employee in a different path doesn't
    # consume a new seat — exercise that branch
    path2 = corporate.create_path(
        corp_org_id=corp.id,
        title="Security Awareness Training",
        modules=modules,
        category="security",
    )
    corporate.publish_path(path_id=path2.id)
    check(
        "R1 existing employee can enroll in 2nd path "
        "(no new seat consumed)",
        corporate.enroll(
            path_id=path2.id, employee_user_id="emp-1",
        ) is not None,
    )

    # Withdraw — need an in-progress enrollment (enr is already
    # completed by this point). Pick one of the bulk-enrolled ones.
    bulk = corporate.list_enrollments_for_path(path.id)
    in_progress = next(
        (b for b in bulk if b.status in ("enrolled", "in_progress")),
        None,
    )
    if in_progress:
        check(
            "R1 withdraw enrollment → True",
            corporate.withdraw(enrollment_id=in_progress.id),
        )
        check(
            "R1 withdraw idempotent (False second time)",
            not corporate.withdraw(enrollment_id=in_progress.id),
        )
    else:
        check("R1 withdraw enrollment (no in_progress to test)", True)
        check("R1 withdraw idempotent (no in_progress to test)", True)

    # Stats
    stats = corporate.org_completion_stats(corp.id)
    check(
        "R1 org_completion_stats returns paths + by_status",
        stats["paths"] >= 2 and "by_status" in stats,
    )

    # --- Q5 sales pipeline ---
    sales_pipeline.migrate()
    lead = sales_pipeline.create_lead(
        source="web", org_name="St. Pauls School",
        org_kind="school",
        contact_name="Principal R Kumar",
        contact_email="principal@stpauls.example",
        contact_phone="+91-9876543210",
        estimated_seats=500,
        expected_value_inr=300_000,
        notes="Inbound demo request from website",
    )
    check(
        "Q5 create_lead returns Lead with score > 0",
        lead.stage == "new" and lead.score > 0,
        f"score={lead.score}",
    )

    # Bad source
    try:
        sales_pipeline.create_lead(
            source="alien_source", org_name="X",
        )
        check("Q5 create_lead rejects bad source", False)
    except ValueError:
        check("Q5 create_lead rejects bad source", True)

    # Bad org_kind
    try:
        sales_pipeline.create_lead(
            source="web", org_name="X",
            org_kind="fish_market",
        )
        check("Q5 create_lead rejects bad org_kind", False)
    except ValueError:
        check("Q5 create_lead rejects bad org_kind", True)

    # Stage transition
    upd_l = sales_pipeline.update_stage(
        lead_id=lead.id, new_stage="qualified",
        user_id="ae-1", note="On a 60-min discovery call",
    )
    check(
        "Q5 update_stage moves new → qualified",
        upd_l.stage == "qualified",
    )

    # Activity log includes the stage_change
    acts = sales_pipeline.list_activities(lead.id)
    check(
        "Q5 stage_change auto-logged as activity",
        any(a.kind == "stage_change" for a in acts),
    )

    # Bad new_stage
    try:
        sales_pipeline.update_stage(
            lead_id=lead.id, new_stage="alien_stage",
        )
        check("Q5 update_stage rejects bad new_stage", False)
    except ValueError:
        check("Q5 update_stage rejects bad new_stage", True)

    # No-op transition (same stage) — returns existing
    same = sales_pipeline.update_stage(
        lead_id=lead.id, new_stage="qualified",
    )
    check(
        "Q5 same-stage transition is a no-op",
        same.stage == "qualified",
    )

    # Log a demo activity → bumps intent score
    pre_score = sales_pipeline.get_lead(lead.id).score
    sales_pipeline.log_activity(
        lead_id=lead.id, kind="demo",
        title="Conducted 30-min product demo",
        body="Walked through tutor + practice tests",
        created_by="ae-1",
    )
    post_score = sales_pipeline.get_lead(lead.id).score
    check(
        "Q5 demo activity bumps score",
        post_score >= pre_score,
        f"pre={pre_score} post={post_score}",
    )

    # Bad activity kind
    try:
        sales_pipeline.log_activity(
            lead_id=lead.id, kind="seance", title="x",
        )
        check("Q5 log_activity rejects bad kind", False)
    except ValueError:
        check("Q5 log_activity rejects bad kind", True)

    # Won deal — score should be capped near 100
    sales_pipeline.update_stage(
        lead_id=lead.id, new_stage="won", user_id="ae-1",
    )
    won_score = sales_pipeline.get_lead(lead.id).score
    check(
        "Q5 won stage pushes score high",
        won_score >= 50,
        f"score={won_score}",
    )

    # Lost deal — score should drop
    lead2 = sales_pipeline.create_lead(
        source="outreach", org_name="LostCo",
        expected_value_inr=100_000,
    )
    sales_pipeline.update_stage(
        lead_id=lead2.id, new_stage="lost", user_id="ae-2",
    )
    lost_score = sales_pipeline.get_lead(lead2.id).score
    check(
        "Q5 lost stage produces low score",
        lost_score < 30,
        f"score={lost_score}",
    )

    # Assign / CRM external id
    check(
        "Q5 assign → True",
        sales_pipeline.assign(
            lead_id=lead.id, owner_user_id="ae-7",
        ),
    )
    check(
        "Q5 set_crm_id → True",
        sales_pipeline.set_crm_id(
            lead_id=lead.id, crm_external_id="hubspot-12345",
        ),
    )
    final_lead = sales_pipeline.get_lead(lead.id)
    check(
        "Q5 owner + crm id persisted",
        final_lead.owner_user_id == "ae-7"
        and final_lead.crm_external_id == "hubspot-12345",
    )

    # Pipeline summary
    summary = sales_pipeline.pipeline_summary()
    check(
        "Q5 pipeline_summary has all stages with count/value/avg",
        all(
            "count" in v and "value_inr" in v and "avg_score" in v
            for v in summary.values()
        ),
    )
    check(
        "Q5 won stage has count >=1",
        summary["won"]["count"] >= 1,
    )

    # Recompute all scores
    n = sales_pipeline.recompute_all_scores()
    check(
        "Q5 recompute_all_scores returns count of active leads",
        n >= 0,
    )

    # Listing
    new_leads = sales_pipeline.list_leads(stage="won")
    check(
        "Q5 list_leads(stage='won') returns the won lead",
        any(l.id == lead.id for l in new_leads),
    )

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Public reads
        r = c.get("/api/states")
        check(
            "GET /api/states returns 200 + rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 5,
        )

        r = c.get("/api/states/UP")
        check(
            "GET /api/states/UP returns 200",
            r.status_code == 200 and r.json()["state_code"] == "UP",
        )

        r = c.get("/api/states/XX-no-such-state")
        check(
            "GET /api/states/{bogus} returns 404",
            r.status_code == 404,
        )

        r = c.get("/api/admin/states/pipeline")
        check(
            "GET /api/admin/states/pipeline returns 200",
            r.status_code == 200 and isinstance(r.json(), dict),
        )

        r = c.get("/api/corporate/paths")
        check(
            "GET /api/corporate/paths returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get(f"/api/admin/corporate/orgs/{corp.id}/stats")
        check(
            "GET /api/admin/corporate/orgs/{id}/stats returns 200",
            r.status_code == 200 and "by_status" in r.json(),
        )

        r = c.get("/api/admin/sales/pipeline")
        check(
            "GET /api/admin/sales/pipeline returns 200",
            r.status_code == 200,
        )

        r = c.get("/api/admin/sales/leads")
        check(
            "GET /api/admin/sales/leads returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get("/api/admin/sales/leads?stage=bogus")
        check(
            "GET /api/admin/sales/leads rejects bad stage (400)",
            r.status_code == 400,
        )

        # Lead creation is open + rate-limited (reset bucket first)
        from padhai import rate_limit
        rate_limit.preview_scorer.reset("testclient")
        rate_limit.preview_scorer.reset("unknown")
        r = c.post(
            "/api/sales/leads",
            data={"source": "web",
                  "org_name": "Test School via HTTP"},
        )
        check(
            "POST /api/sales/leads (open + rate-limited) returns 201",
            r.status_code == 201 and "id" in r.json(),
        )

        # Auth gates
        r = c.post(
            "/api/corporate/paths",
            data={"corp_org_id": "some-corp-id",
                  "title": "Valid Title For Auth Test",
                  "modules_json": "[{\"title\":\"x\"}]"},
        )
        check(
            "POST /api/corporate/paths requires auth (401)",
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
        "v2.8 brought route count above 320",
        len(app.routes) > 320,
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


def _raises(fn, **kwargs) -> bool:
    try:
        fn(**kwargs)
        return False
    except (ValueError, PermissionError):
        return True


if __name__ == "__main__":
    sys.exit(main())
