"""Smoke for v3.2 — Depth: mock engine + readiness + tutor grounding.

Covers:
- Mock paper authoring + attempt lifecycle (mock_engine.py)
- Exam Readiness Score aggregator (readiness.py) — 5-component blend
- Citation-aware tutor wrapper (tutor_grounding.py) — modes +
  cheat-guard + fallbacks

Run: PYTHONPATH=. python scripts/test_v3_2.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_2_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import mock_engine as me
    from padhai import readiness as rd
    from padhai import tutor_grounding as tg
    from padhai import citations as cit
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

    print("v3.2 smoke test — Depth")
    print("-----------------------")

    # ============================================================
    # Mock engine
    # ============================================================
    me.migrate()
    me.migrate()  # idempotent
    et.migrate()  # need pack catalog seeded
    cit.migrate()
    rd.migrate()
    tg.migrate()
    mastery.migrate()

    # Create paper — SSC CGL Tier 1 style
    paper = me.create_paper(
        title="SSC CGL Tier 1 — Practice Mock #1",
        pack_code="ssc_cgl_2026",
        exam_code="ssc_cgl",
        mode="full",
        total_time_min=60,
        negative_marking=0.25,
        sections=[
            {"code": "reasoning", "title": "Reasoning",
             "time_min": 15, "marks_per_q": 2},
            {"code": "gk", "title": "General Knowledge",
             "time_min": 15, "marks_per_q": 2},
            {"code": "quant", "title": "Quantitative Aptitude",
             "time_min": 15, "marks_per_q": 2},
            {"code": "english", "title": "English",
             "time_min": 15, "marks_per_q": 2},
        ],
    )
    check(
        "Mock create_paper returns Paper draft + 4 sections",
        paper.status == "draft" and len(paper.sections) == 4
        and paper.negative_marking == 0.25,
    )

    # Bad mode
    check(
        "Mock create_paper rejects bad mode",
        raises(me.create_paper, exc_type=ValueError,
               title="Bad Paper", mode="moon_mode",
               total_time_min=60,
               sections=[{"code": "s1", "marks_per_q": 1}]),
    )

    # Empty sections
    check(
        "Mock create_paper rejects empty sections",
        raises(me.create_paper, exc_type=ValueError,
               title="Empty Sections", total_time_min=60,
               sections=[]),
    )

    # Duplicate section codes
    check(
        "Mock create_paper rejects duplicate section codes",
        raises(me.create_paper, exc_type=ValueError,
               title="Dup Sections", total_time_min=60,
               sections=[{"code": "dup", "marks_per_q": 1},
                         {"code": "dup", "marks_per_q": 1}]),
    )

    # Add questions — 5 per section, 20 total
    pos = 1
    for sec in ["reasoning", "gk", "quant", "english"]:
        for q in range(5):
            me.add_question(
                paper_id=paper.id, position=pos,
                section_code=sec,
                question_id=f"q-{sec}-{q+1}",
                correct_answer=str(q + 1),    # answer is option index 1-5
                topic_code=f"{sec}_topic_{(q % 2) + 1}",
            )
            pos += 1

    paper_after = me.get_paper(paper.id)
    check(
        "Mock add_question bumps total_questions to 20",
        paper_after.total_questions == 20,
    )
    check(
        "Mock add_question bumps total_marks to 40",
        paper_after.total_marks == 40,
    )

    # Bad section code
    check(
        "Mock add_question rejects unknown section",
        raises(me.add_question, exc_type=ValueError,
               paper_id=paper.id, position=99,
               section_code="cosmic_section",
               question_id="q-x"),
    )

    # Duplicate position
    check(
        "Mock add_question rejects duplicate position",
        raises(me.add_question, exc_type=ValueError,
               paper_id=paper.id, position=1,
               section_code="reasoning",
               question_id="q-dup"),
    )

    # Can't start attempt on draft
    check(
        "Mock start_attempt refuses draft paper",
        raises(me.start_attempt, exc_type=ValueError,
               paper_id=paper.id, user_id="student-1"),
    )

    # Publish
    check(
        "Mock publish_paper returns True on draft + ≥5 q",
        me.publish_paper(paper.id),
    )

    # Can't add after publish
    check(
        "Mock add_question rejected on published paper",
        raises(me.add_question, exc_type=ValueError,
               paper_id=paper.id, position=99,
               section_code="reasoning", question_id="late"),
    )

    # Tiny paper can't publish
    tiny = me.create_paper(
        title="Tiny Mock", total_time_min=10,
        sections=[{"code": "x", "marks_per_q": 1}],
    )
    me.add_question(
        paper_id=tiny.id, position=1,
        section_code="x", question_id="qx",
    )
    check(
        "Mock publish refuses paper with <5 questions",
        raises(me.publish_paper, exc_type=ValueError,
               paper_id=tiny.id),
    )

    # Start attempt
    att = me.start_attempt(
        paper_id=paper.id, user_id="student-1",
    )
    check(
        "Mock start_attempt returns Attempt status='started'",
        att.status == "started"
        and att.max_score == 40,
    )

    # Respond to a few — answer correctly for reasoning (positions 1-5)
    for pos in range(1, 6):
        me.submit_response(
            attempt_id=att.id, position=pos,
            chosen_answer=str(pos % 5),    # correct: q-reasoning-1 → "1", but answer was pos-loop-i
            time_seconds=30,
        )
    # Actually answer correctly for positions 1-5: correct answer is
    # str(q+1) where q ranges 0-4 inside reasoning. q-reasoning-1's correct
    # is "1", q-reasoning-2's is "2", etc. So positions 1-5 should answer "1","2","3","4","5".
    for pos in range(1, 6):
        me.submit_response(
            attempt_id=att.id, position=pos,
            chosen_answer=str(pos),
            time_seconds=25,
        )

    # Half-wrong for GK (positions 6-10): answer odd positions correctly
    for pos in range(6, 11):
        me.submit_response(
            attempt_id=att.id, position=pos,
            chosen_answer=str(pos - 5) if pos % 2 else "wrong",
            time_seconds=40,
        )

    # Leave quant (11-15) unattempted
    # Mark some for review
    me.submit_response(
        attempt_id=att.id, position=11,
        chosen_answer=None, marked_review=True,
    )

    # All wrong for english (16-20)
    for pos in range(16, 21):
        me.submit_response(
            attempt_id=att.id, position=pos,
            chosen_answer="z",  # wrong
            time_seconds=35,
        )

    # Status moved to in_progress
    att_now = me.get_attempt(att.id)
    check(
        "Mock attempt moves to in_progress after responses",
        att_now.status == "in_progress",
    )

    # Submit
    graded = me.submit_attempt(att.id)
    check(
        "Mock submit_attempt returns submitted attempt",
        graded.status == "submitted"
        and graded.submitted_at is not None,
    )
    # Reasoning: 5 correct × 2 = 10
    # GK: pos 7+9 correct ("2","4"); pos 6,8,10 wrong → 2×2 - 3×0.5 = 2.5
    # Quant: 5 unattempted = 0
    # English: 5 wrong → -5×0.5 = -2.5
    # Totals: correct=7, wrong=8, unattempted=5, raw_score=10.0
    check(
        "Mock grading: 5 correct (reasoning) + 2 correct (gk) = 7 correct",
        graded.correct_count == 7,
    )
    check(
        "Mock grading: 3 wrong (gk) + 5 wrong (english) = 8 wrong",
        graded.wrong_count == 8,
    )
    check(
        "Mock grading: 5 unattempted (quant)",
        graded.unattempted_count == 5,
    )
    check(
        "Mock grading: raw_score = 10.0 (7×2 - 8×0.5)",
        abs(graded.raw_score - 10.0) < 0.01,
        f"got raw_score={graded.raw_score}",
    )

    # Re-submitting is idempotent
    graded2 = me.submit_attempt(att.id)
    check(
        "Mock submit_attempt idempotent on submitted",
        graded2.id == graded.id
        and graded2.status == "submitted",
    )

    # Percentile filled in (only 1 attempt so percentile = 50.0)
    att_with_pct = me.get_attempt(att.id)
    check(
        "Mock percentile computed (sole attempt → 50%)",
        att_with_pct.percentile == 50.0,
    )

    # Section scores: reasoning=10 (5 correct), gk=2.5 (2 correct - 3×0.5),
    # quant=0 (unattempted), english=-2.5 (5 wrong)
    check(
        "Mock section_scores: reasoning=10, gk=2.5, english=-2.5",
        graded.section_scores.get("reasoning") == 10.0
        and graded.section_scores.get("gk") == 2.5
        and graded.section_scores.get("english") == -2.5,
    )

    # Topic breakdown — verify aggregation per topic
    check(
        "Mock topic_breakdown contains topic counts",
        "reasoning_topic_1" in graded.topic_breakdown
        or "reasoning_topic_2" in graded.topic_breakdown,
    )

    # Attempt analysis
    analysis = me.attempt_analysis(att.id)
    check(
        "Mock attempt_analysis returns scores + accuracy",
        analysis["ready"]
        and analysis["accuracy"] > 0
        and "section_scores" in analysis
        and "avg_time_per_q" in analysis,
    )

    # Cohort stats
    cs = me.cohort_stats(paper.id)
    check(
        "Mock cohort_stats returns submissions + mean",
        cs["submissions"] == 1 and cs["mean"] is not None,
    )

    # Second attempt by another user → percentiles recompute
    att2 = me.start_attempt(paper_id=paper.id, user_id="student-2")
    # All correct for student-2
    for pos in range(1, 21):
        sec_idx = (pos - 1) // 5
        sec = ["reasoning", "gk", "quant", "english"][sec_idx]
        # q index within section: 0..4 → correct answer is index+1
        q_in_sec = (pos - 1) % 5
        me.submit_response(
            attempt_id=att2.id, position=pos,
            chosen_answer=str(q_in_sec + 1),
            time_seconds=20,
        )
    g2 = me.submit_attempt(att2.id)
    check(
        "Mock student-2 scores 40/40 (all correct)",
        g2.correct_count == 20 and g2.raw_score == 40.0,
    )

    # Now percentiles update — student-1 (12.5) is below student-2 (40)
    a1_now = me.get_attempt(att.id)
    a2_now = me.get_attempt(att2.id)
    check(
        "Mock percentile re-ranked: student-1 < student-2",
        a1_now.percentile < a2_now.percentile,
    )

    # ============================================================
    # Readiness
    # ============================================================

    # Seed mastery for student-2 so readiness has signal
    # UPSC topic keys per readiness convention: 'upsc_cse::polity', etc.
    for code in ["polity", "economy", "history_modern",
                 "environment", "science_tech"]:
        for _ in range(3):
            mastery.update(
                user_id="student-2",
                topic_key=f"upsc_cse::{code}",
                correct=True,
            )

    # Compute readiness for an empty pack
    r_upsc = rd.compute_readiness(
        user_id="student-2", pack_code="upsc_cse_2026",
    )
    check(
        "Readiness compute returns score 0..100",
        0 <= r_upsc.score <= 100,
    )
    check(
        "Readiness mastery component reflects practice",
        r_upsc.mastery_score > 0,
    )

    # Components stored
    check(
        "Readiness components dict has all 5 + weights",
        all(k in r_upsc.components
            for k in ["mastery", "mock", "coverage",
                      "consistency", "trust", "weights"]),
    )

    # Bad pack code
    check(
        "Readiness compute rejects unknown pack",
        raises(rd.compute_readiness, exc_type=ValueError,
               user_id="student-2", pack_code="ghost_pack"),
    )

    # Bad weights (don't sum to 1)
    check(
        "Readiness compute rejects bad weights",
        raises(rd.compute_readiness, exc_type=ValueError,
               user_id="student-2", pack_code="upsc_cse_2026",
               weights={"mastery": 0.5, "mock": 0.1,
                        "coverage": 0.1, "consistency": 0.1,
                        "trust": 0.05}),  # sums to 0.85
    )

    # Compute readiness for SSC pack — student-2 has 40/40 mock
    r_ssc = rd.compute_readiness(
        user_id="student-2", pack_code="ssc_cgl_2026",
    )
    check(
        "Readiness for SSC has mock component > 0 (has attempts)",
        r_ssc.mock_score > 0,
    )

    # Student-1 has lower mock score on SSC pack
    r_ssc_s1 = rd.compute_readiness(
        user_id="student-1", pack_code="ssc_cgl_2026",
    )
    check(
        "Readiness student-1 mock < student-2 (12.5 vs 40)",
        r_ssc_s1.mock_score < r_ssc.mock_score,
    )

    # Idempotent — second compute updates same row
    r_again = rd.compute_readiness(
        user_id="student-2", pack_code="ssc_cgl_2026",
    )
    check(
        "Readiness compute is idempotent (same id)",
        r_again.id == r_ssc.id,
    )

    # Get + auto-refresh on miss
    r_get = rd.get_readiness(
        user_id="student-fresh", pack_code="upsc_cse_2026",
        refresh_if_stale=True,
    )
    check(
        "Readiness get auto-creates row for new user",
        r_get is not None and r_get.user_id == "student-fresh",
    )

    # List for user
    rows = rd.list_user_readiness("student-2")
    check(
        "Readiness list_user_readiness returns multiple packs",
        len(rows) >= 2,
    )

    # Pack leaderboard
    lb = rd.pack_leaderboard("ssc_cgl_2026")
    check(
        "Readiness leaderboard returns sorted entries",
        len(lb) >= 2
        and lb[0]["score"] >= lb[-1]["score"],
    )

    # ============================================================
    # Tutor grounding
    # ============================================================

    # Default mode is general
    sm0 = tg.get_session_mode("sess-new")
    check(
        "Tutor grounding default mode is None (caller handles default)",
        sm0 is None,
    )

    # Set mode
    sm = tg.set_session_mode(
        session_id="sess-1", user_id="student-1",
        answer_mode="source_only",
    )
    check(
        "Tutor grounding set_session_mode persists 'source_only'",
        sm.answer_mode == "source_only",
    )

    # Bad mode
    check(
        "Tutor grounding rejects bad mode",
        raises(tg.set_session_mode, exc_type=ValueError,
               session_id="s-x", user_id="u",
               answer_mode="cosmic"),
    )

    # Re-set updates same row
    sm2 = tg.set_session_mode(
        session_id="sess-1", user_id="student-1",
        answer_mode="official",
    )
    check(
        "Tutor grounding re-set updates row",
        sm2.answer_mode == "official",
    )

    # send_grounded_message: source_only with no chunks → fallback
    reply = tg.send_grounded_message(
        session_id="sess-2", user_id="student-1",
        question_text="What is dharma?",
        answer_text="LLM-only answer text.",
    )
    # No session mode set for sess-2 → uses general by default
    check(
        "Tutor grounded general-mode no-chunks → grounded=False but not fallback",
        not reply.grounded and not reply.fallback,
    )

    # Now set strict mode + send without chunks
    tg.set_session_mode(
        session_id="sess-strict", user_id="student-1",
        answer_mode="source_only",
    )
    reply_s = tg.send_grounded_message(
        session_id="sess-strict", user_id="student-1",
        question_text="What is photosynthesis?",
        answer_text="LLM hallucination here.",
    )
    check(
        "Tutor strict mode no-chunks → fallback message",
        reply_s.fallback
        and reply_s.fallback_reason == "no_source_match"
        and reply_s.text == cit.NOT_FOUND_MESSAGE,
    )

    # Strict mode WITH chunks → real reply
    reply_grounded = tg.send_grounded_message(
        session_id="sess-strict", user_id="student-1",
        question_text="What is photosynthesis?",
        answer_text="Photosynthesis converts light to chemical energy.",
        retrieved_chunks=[
            {"source_kind": "upload",
             "source_id": "bio_class_10_ch6",
             "page_number": 12,
             "citation_text": (
                 "Photosynthesis is the process by which "
                 "plants synthesize food using sunlight."
             ),
             "relevance": 0.88},
        ],
    )
    check(
        "Tutor strict mode with chunks → grounded reply",
        reply_grounded.grounded and not reply_grounded.fallback
        and reply_grounded.citation_count == 1,
    )

    # Official mode requires official_doc citation
    tg.set_session_mode(
        session_id="sess-off", user_id="student-1",
        answer_mode="official",
    )
    reply_off_fail = tg.send_grounded_message(
        session_id="sess-off", user_id="student-1",
        question_text="Q?",
        answer_text="A.",
        retrieved_chunks=[
            {"source_kind": "upload",     # not official_doc
             "source_id": "x",
             "citation_text": "x"},
        ],
    )
    check(
        "Tutor official mode rejects non-official sources",
        reply_off_fail.fallback
        and reply_off_fail.fallback_reason == "no_official_source",
    )

    # Official + official_doc citation succeeds
    reply_off_ok = tg.send_grounded_message(
        session_id="sess-off", user_id="student-1",
        question_text="Q?",
        answer_text="A.",
        retrieved_chunks=[
            {"source_kind": "official_doc",
             "source_id": "ncert_class_10",
             "page_number": 42,
             "citation_text": "verbatim text"},
        ],
    )
    check(
        "Tutor official mode accepts official_doc citation",
        reply_off_ok.grounded and not reply_off_ok.fallback,
    )

    # Cheat guard: activate test, then try to ask anything
    tg.set_test_active(
        session_id="sess-strict", user_id="student-1", active=True,
    )
    reply_cheat = tg.send_grounded_message(
        session_id="sess-strict", user_id="student-1",
        question_text="What's the answer to question 5?",
        answer_text="Anything LLM said",
        retrieved_chunks=[
            {"source_kind": "official_doc",
             "source_id": "x", "citation_text": "x"},
        ],
    )
    check(
        "Tutor cheat-guard refuses while test active",
        reply_cheat.fallback
        and reply_cheat.fallback_reason == "cheat_guard"
        and reply_cheat.text == tg.CHEAT_GUARD_MESSAGE,
    )

    # Deactivate test
    tg.set_test_active(
        session_id="sess-strict", user_id="student-1", active=False,
    )

    # Fallback audit list
    fb = tg.user_recent_fallbacks("student-1")
    check(
        "Tutor user_recent_fallbacks shows recorded fallbacks",
        len(fb) >= 2,  # at least no_source_match + cheat_guard + no_official_source
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Public — list published mock papers
        r = c.get(f"/api/mock/papers?pack_code=ssc_cgl_2026")
        check(
            "GET /api/mock/papers filtered → 200 + ≥1 paper",
            r.status_code == 200
            and len(r.json()["papers"]) >= 1,
        )

        r = c.get(f"/api/mock/papers/{paper.id}")
        check(
            "GET /api/mock/papers/{id} → 200",
            r.status_code == 200
            and r.json()["title"].startswith("SSC CGL"),
        )

        r = c.get(f"/api/mock/papers/{paper.id}/cohort-stats")
        check(
            "GET /api/mock/papers/{id}/cohort-stats → 200",
            r.status_code == 200
            and r.json()["submissions"] == 2,
        )

        # Public pack leaderboard
        r = c.get("/api/exam-packs/ssc_cgl_2026/leaderboard")
        check(
            "GET /api/exam-packs/{}/leaderboard → 200",
            r.status_code == 200
            and len(r.json()["entries"]) >= 2,
        )

        # Auth gates — mock
        r = c.post(f"/api/mock/papers/{paper.id}/start")
        check(
            "POST /api/mock/papers/{}/start → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/admin/mock/papers",
            data={"title": "Auth Test Paper",
                  "sections_json": '[{"code":"s","marks_per_q":1}]',
                  "total_time_min": 60},
        )
        check(
            "POST /api/admin/mock/papers → 401",
            r.status_code == 401,
        )

        # Auth gates — readiness
        r = c.get("/api/readiness/me")
        check(
            "GET /api/readiness/me → 401",
            r.status_code == 401,
        )
        r = c.get("/api/readiness/me/upsc_cse_2026")
        check(
            "GET /api/readiness/me/{pack} → 401",
            r.status_code == 401,
        )

        # Auth gates — tutor grounding
        r = c.post(
            "/api/tutor/sessions/sess-1/mode",
            data={"answer_mode": "source_only"},
        )
        check(
            "POST /api/tutor/sessions/{}/mode → 401",
            r.status_code == 401,
        )
        r = c.get("/api/tutor/me/fallbacks")
        check(
            "GET /api/tutor/me/fallbacks → 401",
            r.status_code == 401,
        )

        # Form validation: mock paper too-short title
        r = c.post(
            "/api/admin/mock/papers",
            data={"title": "x",
                  "sections_json": "[]",
                  "total_time_min": 60},
        )
        check(
            "POST admin mock paper rejects short title (422)",
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
        "v3.2 brought route count above 440",
        len(app.routes) > 440,
        f"routes={len(app.routes)}",
    )

    print("-----------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
