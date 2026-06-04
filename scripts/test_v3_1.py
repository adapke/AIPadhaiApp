"""Smoke for v3.1 — Trust & Accuracy Foundation.

Covers:
- AI citations module (citations.py) — provenance + grounding rate
- Exam taxonomy + Exam Packs (exam_taxonomy.py) — seeded catalog +
  5 deep packs from review §Phase 2
- AI accuracy benchmark (accuracy_bench.py) — datasets, items,
  runs, judges
- api_deps.require_owner helper

Run: PYTHONPATH=. python scripts/test_v3_1.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_1_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi import HTTPException
    from fastapi.testclient import TestClient

    from padhai import accuracy_bench as ab
    from padhai import api_deps
    from padhai import citations as cit
    from padhai import exam_taxonomy as et
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

    print("v3.1 smoke test — Trust & Accuracy Foundation")
    print("---------------------------------------------")

    # ============================================================
    # api_deps.require_owner
    # ============================================================
    api_deps.register_owner_resolver(
        "fakeres", lambda rid: "owner-1" if rid == "r-1" else None,
    )

    class _User:
        def __init__(self, uid): self.id = uid

    # No user → 401
    try:
        api_deps.require_owner(
            resource_type="fakeres", resource_id="r-1", user=None,
        )
        check("require_owner None user → 401", False)
    except HTTPException as e:
        check("require_owner None user → 401", e.status_code == 401)

    # Unknown resource type → 500
    try:
        api_deps.require_owner(
            resource_type="ghost_type", resource_id="x",
            user=_User("u"),
        )
        check("require_owner unknown type → 500", False)
    except HTTPException as e:
        check("require_owner unknown type → 500", e.status_code == 500)

    # Missing resource → 404
    try:
        api_deps.require_owner(
            resource_type="fakeres", resource_id="missing",
            user=_User("u"),
        )
        check("require_owner missing → 404", False)
    except HTTPException as e:
        check("require_owner missing → 404", e.status_code == 404)

    # Wrong owner → 403
    try:
        api_deps.require_owner(
            resource_type="fakeres", resource_id="r-1",
            user=_User("not-owner"),
        )
        check("require_owner wrong owner → 403", False)
    except HTTPException as e:
        check("require_owner wrong owner → 403", e.status_code == 403)

    # Right owner → no raise
    try:
        api_deps.require_owner(
            resource_type="fakeres", resource_id="r-1",
            user=_User("owner-1"),
        )
        check("require_owner right owner → no raise", True)
    except HTTPException:
        check("require_owner right owner → no raise", False)

    # ============================================================
    # Citations
    # ============================================================
    cit.migrate()
    cit.migrate()  # idempotent

    # Ungrounded general-mode answer is allowed
    p1 = cit.record_answer(
        surface="tutor", user_id="u1",
        question_text="What is photosynthesis?",
        answer_text=(
            "Photosynthesis is the process by which plants make food."
        ),
        answer_mode="general",
        confidence=0.85,
    )
    check(
        "Citations ungrounded general-mode answer recorded",
        p1.id is not None and not p1.grounded
        and p1.answer_mode == "general",
    )

    # Strict source_only mode REFUSES ungrounded answer
    check(
        "Citations source_only refuses ungrounded → NotGroundedError",
        raises(cit.record_answer,
               exc_type=cit.NotGroundedError,
               surface="tutor", user_id="u1",
               question_text="Q?", answer_text="A.",
               answer_mode="source_only"),
    )

    # Grounded answer with citations
    p2 = cit.record_answer(
        surface="lesson", user_id="u1",
        question_text="Explain Article 21 of the Constitution.",
        answer_text=(
            "Article 21 protects life and personal liberty. "
            "Includes right to privacy, dignity, speedy trial."
        ),
        answer_mode="official",
        confidence=0.91,
        citations=[
            {"source_kind": "official_doc",
             "source_id": "polity_pack_v2",
             "page_number": 42,
             "section": "Fundamental Rights",
             "citation_text": (
                 "Article 21 guarantees no person shall be deprived "
                 "of his life or personal liberty."
             ),
             "relevance": 0.95},
            {"source_kind": "question_bank",
             "source_id": "qb-upsc-2018-gs2-q15",
             "page_number": None,
             "citation_text": "PYQ 2018 GS-II — Article 21 expansion",
             "relevance": 0.78},
        ],
    )
    check(
        "Citations grounded answer recorded with 2 citations",
        p2.grounded and len(p2.citations) == 2,
    )
    check(
        "Citation positions are 1-indexed in order",
        p2.citations[0].position == 1
        and p2.citations[1].position == 2,
    )

    # Bad surface
    check(
        "Citations rejects bad surface",
        raises(cit.record_answer, exc_type=ValueError,
               surface="alien", question_text="Q?",
               answer_text="A."),
    )

    # Bad source_kind in citation
    check(
        "Citations rejects bad citation.source_kind",
        raises(cit.record_answer, exc_type=ValueError,
               surface="tutor", question_text="Q?",
               answer_text="A.",
               citations=[{"source_kind": "moon_rock",
                           "source_id": "x",
                           "citation_text": "x"}]),
    )

    # Bad confidence
    check(
        "Citations rejects confidence > 1",
        raises(cit.record_answer, exc_type=ValueError,
               surface="tutor", question_text="Q?",
               answer_text="A.", confidence=1.5),
    )

    # List user's answers
    rows = cit.list_user_answers(user_id="u1")
    check(
        "Citations list_user_answers returns ≥2 rows",
        len(rows) >= 2,
    )

    # Grounded-only filter
    grounded = cit.list_user_answers(user_id="u1", grounded_only=True)
    check(
        "Citations grounded_only filter works",
        all(r.grounded for r in grounded) and len(grounded) >= 1,
    )

    # By-surface filter
    by_surf = cit.list_user_answers(user_id="u1", surface="lesson")
    check(
        "Citations surface filter works",
        all(r.surface == "lesson" for r in by_surf),
    )

    # Source impact
    src_cites = cit.list_citations_for_source(
        source_kind="official_doc", source_id="polity_pack_v2",
    )
    check(
        "Citations source impact returns the polity_pack_v2 cite",
        len(src_cites) >= 1
        and src_cites[0].source_id == "polity_pack_v2",
    )

    # Headline grounding rate
    rate = cit.grounding_rate()
    check(
        "Citations grounding_rate returns total + rate",
        "total_answers" in rate
        and "grounding_rate" in rate
        and rate["total_answers"] >= 2,
    )
    check(
        "Citations grounding_rate has by_surface breakdown",
        "by_surface" in rate and len(rate["by_surface"]) >= 2,
    )

    # ============================================================
    # Exam taxonomy + Exam Packs
    # ============================================================
    et.migrate()
    et.migrate()  # idempotent — no double seed

    segs = et.list_segments()
    check(
        "Taxonomy seeds ≥8 segments (kinder → research)",
        len(segs) >= 8,
    )
    check(
        "Taxonomy 'school' + 'govt' segments seeded",
        any(s.code == "school" for s in segs)
        and any(s.code == "govt" for s in segs),
    )

    bodies = et.list_bodies(country="IN")
    check(
        "Taxonomy seeds Indian exam bodies (≥10)",
        len(bodies) >= 10,
    )
    check(
        "Taxonomy CBSE + UPSC + SSC + NTA bodies present",
        all(c in {b.code for b in bodies}
            for c in ["cbse", "upsc", "ssc", "nta"]),
    )

    exams = et.list_exams()
    check(
        "Taxonomy seeds ≥15 exams",
        len(exams) >= 15,
    )

    # The 5 deep exam packs
    packs = et.list_packs()
    check(
        "Taxonomy seeds ≥5 Exam Packs (review §Phase 2)",
        len(packs) >= 5,
    )
    pack_codes = {p.code for p in packs}
    expected_packs = {
        "cbse_class_10_2026", "cbse_class_12_2026",
        "upsc_cse_2026", "ssc_cgl_2026", "ibps_po_2026",
    }
    check(
        "Taxonomy 5 deep packs all seeded by code",
        expected_packs.issubset(pack_codes),
    )

    # UPSC pack — verify pattern + cutoff fields populated
    upsc = et.get_pack("upsc_cse_2026")
    check(
        "UPSC pack has pattern_summary + cutoff_summary",
        upsc is not None
        and upsc.pattern_summary
        and upsc.cutoff_summary,
    )

    # Re-migrate doesn't double-seed
    before = len(et.list_exams())
    et.migrate()
    after = len(et.list_exams())
    check(
        "Taxonomy migrate is idempotent (exam count unchanged)",
        before == after,
    )

    # Topic tree — UPSC has Polity at high weightage
    upsc_topics = et.list_topics("upsc_cse", depth=0)
    check(
        "UPSC topic tree seeded with chapters",
        len(upsc_topics) >= 8,
    )
    check(
        "UPSC Polity chapter has weightage_pct = 16",
        any(t.code == "polity" and t.weightage_pct == 16.0
            for t in upsc_topics),
    )

    # Filter exams by segment
    govt_exams = et.list_exams(segment_code="govt")
    check(
        "Taxonomy filter by segment='govt' works",
        all(e.segment_code == "govt" for e in govt_exams)
        and len(govt_exams) >= 5,
    )
    check(
        "Taxonomy bad segment_code raises",
        raises(et.list_exams, exc_type=ValueError,
               segment_code="moon"),
    )

    # Add a custom topic
    nt = et.add_topic(
        exam_code="cbse_class_10", code="custom_topic_test",
        title="Custom Test Chapter", depth=0,
        weightage_pct=5.5,
    )
    check(
        "Topic add returns Topic with id",
        nt.id is not None and nt.weightage_pct == 5.5,
    )

    # Duplicate code
    check(
        "Topic add rejects duplicate code",
        raises(et.add_topic, exc_type=ValueError,
               exam_code="cbse_class_10",
               code="custom_topic_test", title="dup"),
    )

    # Custom pack
    cp = et.create_pack(
        code="custom_pack_test", exam_code="cbse_class_10",
        title="Custom Test Pack — CBSE 10",
        year=2027, estimated_hours=300,
    )
    check(
        "Pack create returns ExamPack with status='active'",
        cp.status == "active",
    )

    # Pack code collision
    check(
        "Pack create rejects duplicate code",
        raises(et.create_pack, exc_type=ValueError,
               code="custom_pack_test", exam_code="cbse_class_10",
               title="dup", year=2027),
    )

    # Bad exam_code
    check(
        "Pack create rejects unknown exam_code",
        raises(et.create_pack, exc_type=ValueError,
               code="x_pack", exam_code="ghost_exam",
               title="x_pack title"),
    )

    # Enrollment lifecycle
    enr = et.enroll(
        pack_code="upsc_cse_2026", user_id="student-1",
        daily_minutes=180,
    )
    check(
        "Pack enroll returns active enrollment",
        enr.status == "active" and enr.daily_minutes == 180,
    )

    # Duplicate enroll
    check(
        "Pack enroll rejects duplicate (pack, user)",
        raises(et.enroll, exc_type=ValueError,
               pack_code="upsc_cse_2026", user_id="student-1"),
    )

    # Bad daily_minutes
    check(
        "Pack enroll rejects daily_minutes > 720",
        raises(et.enroll, exc_type=ValueError,
               pack_code="upsc_cse_2026",
               user_id="student-overflow",
               daily_minutes=9999),
    )

    # Pack stats
    stats = et.pack_stats("upsc_cse_2026")
    check(
        "Pack stats returns total + by_status + active_rate",
        stats["total_enrollments"] >= 1
        and "by_status" in stats
        and "active_rate" in stats,
    )

    # Set enrollment status
    check(
        "Pack enrollment status → paused",
        et.set_enrollment_status(
            enrollment_id=enr.id, status="paused",
        ),
    )

    # Bad status
    check(
        "Pack enrollment rejects bad status",
        raises(et.set_enrollment_status, exc_type=ValueError,
               enrollment_id=enr.id, status="alien"),
    )

    # ============================================================
    # Accuracy benchmark
    # ============================================================
    ab.migrate()
    ab.migrate()  # idempotent

    ds = ab.create_dataset(
        code="ncert_class_10_math_v1",
        title="NCERT Class 10 Math — answer correctness v1",
        domain="school", task_kind="answer_correctness",
        description="Chapter 1-4 of NCERT Class 10 Math",
        reviewed_by="expert-reviewer-1,expert-reviewer-2",
    )
    check(
        "Bench create_dataset returns Dataset status='draft'",
        ds.status == "draft" and ds.item_count == 0,
    )

    # Bad code
    check(
        "Bench rejects bad code (uppercase)",
        raises(ab.create_dataset, exc_type=ValueError,
               code="BAD_CODE", title="x",
               domain="school", task_kind="answer_correctness"),
    )

    # Bad task_kind
    check(
        "Bench rejects bad task_kind",
        raises(ab.create_dataset, exc_type=ValueError,
               code="bad_task", title="x" * 5,
               domain="school", task_kind="alien_task"),
    )

    # Duplicate (code, version)
    check(
        "Bench rejects duplicate (code, version)",
        raises(ab.create_dataset, exc_type=ValueError,
               code="ncert_class_10_math_v1",
               title="dup", domain="school",
               task_kind="answer_correctness"),
    )

    # Versioning works
    ds_v2 = ab.create_dataset(
        code="ncert_class_10_math_v1",
        title="NCERT Class 10 Math v2",
        domain="school", task_kind="answer_correctness",
        version=2,
    )
    check(
        "Bench (code, version=2) creates new row",
        ds_v2.id != ds.id,
    )

    # Add items
    for i in range(1, 12):
        ab.add_item(
            dataset_id=ds.id,
            prompt=f"What is {i} + {i}?",
            expected={"answer": str(i + i)},
            difficulty="easy",
            tags={"chapter": "arithmetic"},
        )

    # Bad difficulty
    check(
        "Bench add_item rejects bad difficulty",
        raises(ab.add_item, exc_type=ValueError,
               dataset_id=ds.id, prompt="x", expected={"a": 1},
               difficulty="cosmic"),
    )

    # Bad weight
    check(
        "Bench add_item rejects weight > 10",
        raises(ab.add_item, exc_type=ValueError,
               dataset_id=ds.id, prompt="x",
               expected={"a": 1}, weight=99),
    )

    # Can't publish dataset with <10 items
    tiny = ab.create_dataset(
        code="tiny_ds", title="Too small", domain="school",
        task_kind="answer_correctness",
    )
    for i in range(3):
        ab.add_item(
            dataset_id=tiny.id, prompt=f"q{i}",
            expected={"answer": str(i)},
        )
    check(
        "Bench publish_dataset refuses <10 items",
        raises(ab.publish_dataset, exc_type=ValueError,
               dataset_id=tiny.id),
    )

    # Publish (11 items added above)
    check(
        "Bench publish_dataset succeeds with ≥10 items",
        ab.publish_dataset(ds.id),
    )

    # Can't add items to published dataset
    check(
        "Bench add_item refused on published dataset",
        raises(ab.add_item, exc_type=ValueError,
               dataset_id=ds.id, prompt="late",
               expected={"answer": "x"}),
    )

    # Run a benchmark — perfect runner
    def perfect_runner(prompt: str) -> dict:
        # Extract '1 + 1' style from prompt and answer correctly
        import re
        m = re.search(r"(\d+) \+ (\d+)", prompt)
        if m:
            return {"answer": str(int(m.group(1)) + int(m.group(2)))}
        return {"answer": "0"}

    run = ab.run_benchmark(
        dataset_id=ds.id, target="tutor_v1",
        judge="exact_match",
        runner=perfect_runner,
        model_version="claude-sonnet-4-6",
    )
    check(
        "Bench run with perfect runner → 11/11 pass",
        run.pass_count == 11 and run.fail_count == 0
        and run.mean_score == 1.0,
    )

    # Run with imperfect runner
    def half_runner(prompt: str) -> dict:
        # Right half the time
        import re
        m = re.search(r"(\d+) \+ (\d+)", prompt)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            return {
                "answer": str(a + b) if a % 2 == 0 else str(a + b + 1),
            }
        return {"answer": "0"}

    run2 = ab.run_benchmark(
        dataset_id=ds.id, target="tutor_v1_buggy",
        judge="exact_match",
        runner=half_runner,
    )
    check(
        "Bench run with imperfect runner has mixed pass/fail",
        run2.pass_count > 0 and run2.fail_count > 0,
    )

    # Crashing runner — items get skipped
    def crash_runner(prompt: str) -> dict:
        raise RuntimeError("simulated model timeout")

    run3 = ab.run_benchmark(
        dataset_id=ds.id, target="tutor_v1_crash",
        judge="exact_match", runner=crash_runner,
    )
    check(
        "Bench run with crashing runner skips all items",
        run3.skipped_count == 11
        and run3.pass_count == 0,
    )

    # Citation_check judge
    ds_cit = ab.create_dataset(
        code="upsc_polity_citation_v1",
        title="UPSC Polity Citation Check v1",
        domain="upsc", task_kind="citation_correctness",
    )
    for i in range(11):
        ab.add_item(
            dataset_id=ds_cit.id,
            prompt=f"Cite Article {21 + i}",
            expected={
                "citations": [
                    {"source_id": "polity_pack",
                     "page_number": 40 + i},
                ],
            },
        )
    ab.publish_dataset(ds_cit.id)

    def half_citation_runner(prompt: str) -> dict:
        # Cites the right doc but wrong page for even items
        import re
        m = re.search(r"Article (\d+)", prompt)
        if m:
            article = int(m.group(1))
            page = 40 + (article - 21)
            if article % 2 == 0:
                page += 5  # wrong
            return {
                "answer": "...",
                "citations": [
                    {"source_id": "polity_pack",
                     "page_number": page},
                ],
            }
        return {"answer": "", "citations": []}

    run_cit = ab.run_benchmark(
        dataset_id=ds_cit.id, target="tutor_v1",
        judge="citation_check",
        runner=half_citation_runner,
    )
    check(
        "Bench citation_check judge produces mixed scoring",
        run_cit.pass_count > 0 and run_cit.fail_count > 0,
    )

    # Trust dashboard
    td = ab.trust_dashboard()
    check(
        "Bench trust_dashboard returns targets",
        "targets" in td and len(td["targets"]) >= 2,
    )
    check(
        "Bench trust_dashboard reports PASS_THRESHOLD",
        td["pass_threshold"] == 0.70,
    )
    tutor_v1 = next(
        (t for t in td["targets"] if t["target"] == "tutor_v1"),
        None,
    )
    check(
        "Bench tutor_v1 has runs + mean_score",
        tutor_v1 is not None
        and tutor_v1["runs"] >= 1
        and tutor_v1["mean_score"] > 0,
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — exam taxonomy + packs
        r = c.get("/api/exam-taxonomy/segments")
        check(
            "GET /api/exam-taxonomy/segments → 200 + ≥8",
            r.status_code == 200
            and len(r.json()["segments"]) >= 8,
        )

        r = c.get("/api/exam-taxonomy/bodies?country=IN")
        check(
            "GET /api/exam-taxonomy/bodies → 200",
            r.status_code == 200
            and len(r.json()["bodies"]) >= 10,
        )

        r = c.get("/api/exam-taxonomy/exams?segment_code=govt")
        check(
            "GET /api/exam-taxonomy/exams filtered → 200",
            r.status_code == 200,
        )

        r = c.get(
            "/api/exam-taxonomy/exams?segment_code=bogus_seg",
        )
        check(
            "GET exams with bad segment → 400",
            r.status_code == 400,
        )

        r = c.get("/api/exam-taxonomy/exams/upsc_cse")
        check(
            "GET /api/exam-taxonomy/exams/upsc_cse → 200",
            r.status_code == 200
            and r.json()["short_title"] == "UPSC CSE",
        )

        r = c.get("/api/exam-taxonomy/exams/upsc_cse/topics")
        check(
            "GET UPSC topics → 200 + ≥8 chapters",
            r.status_code == 200
            and len(r.json()["topics"]) >= 8,
        )

        r = c.get("/api/exam-packs")
        check(
            "GET /api/exam-packs → 200 + ≥5 packs",
            r.status_code == 200
            and len(r.json()["packs"]) >= 5,
        )

        r = c.get("/api/exam-packs/upsc_cse_2026")
        check(
            "GET /api/exam-packs/upsc_cse_2026 → 200",
            r.status_code == 200
            and r.json()["exam_code"] == "upsc_cse",
        )

        r = c.get("/api/exam-packs/ghost_pack_id")
        check(
            "GET unknown pack → 404",
            r.status_code == 404,
        )

        # Auth gates — exam-pack enroll
        r = c.post(
            "/api/exam-packs/upsc_cse_2026/enroll",
            data={"daily_minutes": 90},
        )
        check(
            "POST /api/exam-packs/{}/enroll → 401 (auth gate)",
            r.status_code == 401,
        )

        # Auth gates — citations
        r = c.get("/api/citations/me")
        check(
            "GET /api/citations/me → 401 (auth gate)",
            r.status_code == 401,
        )

        # Auth gates — bench admin
        r = c.get("/api/admin/citations/grounding-rate")
        check(
            "GET /api/admin/citations/grounding-rate → 401",
            r.status_code == 401,
        )
        r = c.get("/api/admin/bench/trust-dashboard")
        check(
            "GET /api/admin/bench/trust-dashboard → 401",
            r.status_code == 401,
        )
        r = c.post(
            "/api/admin/bench/datasets",
            data={"code": "test_ds_v1",
                  "title": "Test Dataset",
                  "domain": "school",
                  "task_kind": "answer_correctness"},
        )
        check(
            "POST /api/admin/bench/datasets → 401",
            r.status_code == 401,
        )

        # Auth gates — admin taxonomy
        r = c.post(
            "/api/admin/exam-taxonomy/exams/cbse_class_10/topics",
            data={"code": "new_topic", "title": "New Topic Test"},
        )
        check(
            "POST admin topic → 401 (auth gate)",
            r.status_code == 401,
        )

        # Form validation: short title on pack create
        r = c.post(
            "/api/admin/exam-packs",
            data={"code": "x", "exam_code": "cbse_class_10",
                  "title": "x"},  # title too short
        )
        check(
            "POST admin pack with short title → 422",
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
        "v3.1 brought route count above 420",
        len(app.routes) > 420,
        f"routes={len(app.routes)}",
    )

    print("---------------------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
