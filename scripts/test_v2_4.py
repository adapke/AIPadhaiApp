"""Smoke for v2.4 — L3 math vision + L4 mock interview + M3 mock test events.

Run: PYTHONPATH=. python scripts/test_v2_4.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_4_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import math_vision, mock_interview, mock_test_events
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.4 smoke test")
    print("---------------")

    # --- L3 math vision ---
    math_vision.migrate()
    check(
        "L3 is_vision_available False without ANTHROPIC_API_KEY",
        not math_vision.is_vision_available(),
    )
    sub = math_vision.submit(
        user_id="ms-user", image_url="https://r2.example.com/work.jpg",
        expected_language="en",
    )
    check(
        "L3 submit returns MathSubmission status=pending",
        sub.status == "pending" and sub.image_url.startswith("https://"),
    )
    # Too-long image_url rejected
    try:
        math_vision.submit(user_id="x", image_url="x" * 3000)
        check("L3 submit rejects oversized image_url", False)
    except ValueError:
        check("L3 submit rejects oversized image_url", True)

    # extract() without API key → errored status with 'no_provider'
    sub2 = math_vision.extract(submission_id=sub.id)
    check(
        "L3 extract without API key → status='errored' + no_provider",
        sub2.status == "errored" and sub2.error == "no_provider",
    )

    # Validate without steps → 'unknown'
    result = math_vision.validate(submission_id=sub.id)
    check(
        "L3 validate without extracted steps → 'unknown'",
        result.overall == "unknown" and result.first_wrong_step is None,
    )

    # Seed a valid submission manually for the syntactic validator
    import sqlite3
    sub3 = math_vision.submit(
        user_id="ms-user", image_url="https://r2.example.com/seq.jpg",
    )
    with sqlite3.connect(_DB) as conn:
        conn.execute(
            "UPDATE math_submissions SET "
            " extracted_latex = ?, confidence = 0.9, "
            " steps_json = ?, status = 'extracted', extracted_at = ? "
            "WHERE id = ?",
            (
                "x^2 + 2x + 1 = 0\n(x+1)^2 = 0\nx = -1",
                json.dumps([
                    "x^2 + 2x + 1 = 0",
                    "(x+1)^2 = 0",
                    "x = -1",
                ]),
                time.time(), sub3.id,
            ),
        )
    val_result = math_vision.validate(submission_id=sub3.id)
    check(
        "L3 validate runs over 3 steps with method=sympy or syntactic",
        val_result.method in ("sympy", "syntactic")
        and len(val_result.per_step) == 3,
        f"method={val_result.method} steps={len(val_result.per_step)}",
    )

    # latex normalization helpers
    norm = math_vision._latex_to_sympy_input(r"\frac{1}{2}+x^2")
    check(
        "L3 _latex_to_sympy_input rewrites fractions + powers",
        "(1)/(2)" in norm and "**2" in norm,
    )

    # --- L4 mock interview ---
    mock_interview.migrate()
    interview, opener = mock_interview.start(
        user_id="iv-user", track="upsc_personality",
    )
    check(
        "L4 start returns Interview + opener turn 0",
        interview.status == "in_progress"
        and opener.turn_index == 0
        and opener.question_text,
    )

    # Bad track rejected
    try:
        mock_interview.start(user_id="x", track="bogus")
        check("L4 start rejects unknown track", False)
    except ValueError:
        check("L4 start rejects unknown track", True)

    # Submit answer → heuristic feedback when no API key
    ans = mock_interview.submit_answer(
        interview_id=interview.id, turn_index=0,
        answer_text=(
            "I want to join the civil services because I believe in "
            "public service. I have specific examples from my work "
            "with rural NGOs that demonstrate this commitment."
        ),
    )
    check(
        "L4 submit_answer returns next_turn (interview not ended yet)",
        ans.next_turn is not None
        and ans.next_turn.turn_index == 1,
    )
    check(
        "L4 heuristic feedback has scores dict",
        "scores" in ans.feedback
        and "clarity" in ans.feedback["scores"],
    )
    check(
        "L4 feedback method is 'heuristic' without API key",
        ans.feedback.get("method") == "heuristic",
    )

    # Empty answer rejected
    try:
        mock_interview.submit_answer(
            interview_id=interview.id, turn_index=1,
            answer_text="",
        )
        check("L4 submit_answer rejects empty text", False)
    except ValueError:
        check("L4 submit_answer rejects empty text", True)

    # Double-answer rejected
    try:
        mock_interview.submit_answer(
            interview_id=interview.id, turn_index=0,
            answer_text="duplicate",
        )
        check("L4 cannot answer same turn twice", False)
    except ValueError:
        check("L4 cannot answer same turn twice", True)

    # End the interview
    ended = mock_interview.end(interview_id=interview.id)
    check(
        "L4 end returns Interview with status=ended + overall_score",
        ended.status == "ended" and ended.overall_score is not None,
    )
    check(
        "L4 end produces aggregated report",
        ended.feedback and "criteria_avg" in ended.feedback,
    )

    # Idempotent end
    ended2 = mock_interview.end(interview_id=interview.id)
    check(
        "L4 end is idempotent",
        ended2.status == "ended",
    )

    # Abandoned interview (no answers)
    abandoned, _opener = mock_interview.start(
        user_id="iv-user-2", track="generic",
    )
    abandoned = mock_interview.end(interview_id=abandoned.id)
    check(
        "L4 interview with no answers ends as 'abandoned'",
        abandoned.status == "abandoned",
    )

    # --- M3 mock test events ---
    mock_test_events.migrate()
    qset = [
        {"id": f"q-{i}",
         "question_text": f"Question {i}?",
         "options": [f"a) opt{i}", "b) other", "c) wrong", "d) none"],
         "correct_answer": "a", "marks": 1,
         "difficulty": "medium",
         "topic_tags": ["polity"]}
        for i in range(8)
    ]
    e = mock_test_events.schedule_event(
        title="UPSC Prelims Mock #1",
        exam="upsc_pre",
        scheduled_at=time.time() + 60,
        duration_min=30,
        question_set=qset,
        subject="polity",
        max_participants=500,
        created_by="admin-1",
    )
    check(
        "M3 schedule_event returns MockEvent with question_set persisted",
        e.title == "UPSC Prelims Mock #1"
        and len(e.question_set) == 8
        and e.status == "open_for_registration",
    )

    # Bad exam / empty qset rejected
    try:
        mock_test_events.schedule_event(
            title="bogus", exam="not_real",
            scheduled_at=time.time(), duration_min=10,
            question_set=qset,
        )
        check("M3 schedule_event rejects unknown exam", False)
    except ValueError:
        check("M3 schedule_event rejects unknown exam", True)
    try:
        mock_test_events.schedule_event(
            title="empty test", exam="upsc_pre",
            scheduled_at=time.time(), duration_min=10,
            question_set=[],
        )
        check("M3 schedule_event rejects empty question_set", False)
    except ValueError:
        check("M3 schedule_event rejects empty question_set", True)

    # Registration
    check(
        "M3 register first time → True",
        mock_test_events.register(event_id=e.id, user_id="stu-A"),
    )
    check(
        "M3 register second time → False (idempotent)",
        not mock_test_events.register(event_id=e.id, user_id="stu-A"),
    )
    mock_test_events.register(event_id=e.id, user_id="stu-B")
    mock_test_events.register(event_id=e.id, user_id="stu-C")
    check(
        "M3 registered_count returns 3",
        mock_test_events.registered_count(e.id) == 3,
    )

    # Start attempt
    a = mock_test_events.start_attempt(event_id=e.id, user_id="stu-A")
    check(
        "M3 start_attempt returns Attempt with started_at",
        a.started_at is not None and a.submitted_at is None,
    )
    # Idempotent re-start
    a2 = mock_test_events.start_attempt(event_id=e.id, user_id="stu-A")
    check(
        "M3 start_attempt idempotent",
        a2.id == a.id,
    )
    # Unregistered user rejected
    try:
        mock_test_events.start_attempt(event_id=e.id, user_id="stranger")
        check("M3 start_attempt requires registration", False)
    except ValueError:
        check("M3 start_attempt requires registration", True)

    # Submit attempt — A gets 5/8 right, B gets 8/8, C gets 2/8
    answers_a = {f"q-{i}": "a" if i < 5 else "b" for i in range(8)}
    answers_b = {f"q-{i}": "a" for i in range(8)}
    answers_c = {f"q-{i}": "a" if i < 2 else "c" for i in range(8)}
    mock_test_events.start_attempt(event_id=e.id, user_id="stu-B")
    mock_test_events.start_attempt(event_id=e.id, user_id="stu-C")
    att_a = mock_test_events.submit_attempt(
        event_id=e.id, user_id="stu-A", answers=answers_a,
    )
    att_b = mock_test_events.submit_attempt(
        event_id=e.id, user_id="stu-B", answers=answers_b,
    )
    att_c = mock_test_events.submit_attempt(
        event_id=e.id, user_id="stu-C", answers=answers_c,
    )
    check(
        "M3 submit_attempt scores correctly (A=5, B=8, C=2)",
        att_a.score == 5 and att_b.score == 8 and att_c.score == 2,
        f"A={att_a.score} B={att_b.score} C={att_c.score}",
    )

    # Leaderboard sorted by score desc; ranks 1..3
    lb = mock_test_events.leaderboard(e.id)
    check(
        "M3 leaderboard returns 3 rows sorted by score desc",
        len(lb) == 3
        and lb[0]["user_id"] == "stu-B" and lb[0]["rank"] == 1
        and lb[1]["user_id"] == "stu-A" and lb[1]["rank"] == 2
        and lb[2]["user_id"] == "stu-C" and lb[2]["rank"] == 3,
    )

    # Stats
    stats_e = mock_test_events.stats(e.id)
    check(
        "M3 stats reports registered=3 + submitted=3",
        stats_e["registered"] == 3 and stats_e["submitted"] == 3,
    )

    # Status lifecycle
    check(
        "M3 set_event_status → live",
        mock_test_events.set_event_status(
            event_id=e.id, status="live",
        ),
    )
    check(
        "M3 set_event_status → ended (sets ended_at)",
        mock_test_events.set_event_status(
            event_id=e.id, status="ended",
        ),
    )

    # Bad status rejected
    try:
        mock_test_events.set_event_status(event_id=e.id, status="bogus")
        check("M3 set_event_status rejects bogus", False)
    except ValueError:
        check("M3 set_event_status rejects bogus", True)

    # --- HTTP layer ---
    with TestClient(app) as c:
        # Auth gates
        for path in (
            "/api/math-vision",
            "/api/mock-interviews",
        ):
            r = c.get(path)
            check(f"GET {path} → 401", r.status_code == 401)

        # Public reads
        r = c.get("/api/mock-events/upcoming?exam=upsc_pre")
        check(
            "GET /api/mock-events/upcoming returns 200",
            r.status_code == 200 and "rows" in r.json(),
        )

        r = c.get(f"/api/mock-events/{e.id}/leaderboard")
        check(
            "GET /api/mock-events/{id}/leaderboard returns 200 + rows",
            r.status_code == 200 and len(r.json()["rows"]) == 3,
        )

        r = c.get(f"/api/mock-events/{e.id}/stats")
        check(
            "GET /api/mock-events/{id}/stats returns 200",
            r.status_code == 200 and r.json()["found"],
        )

        r = c.get("/api/mock-events/no-such-id/stats")
        check(
            "GET /api/mock-events/{bogus}/stats returns 404",
            r.status_code == 404,
        )

        # Math vision auth gate
        r = c.post(
            "/api/math-vision/submit",
            data={"image_url": "https://x.com/y.jpg",
                  "expected_language": "en"},
        )
        check(
            "POST /api/math-vision/submit requires auth (401)",
            r.status_code == 401,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Route count
    check(
        "v2.4 brought route count above 220",
        len(app.routes) > 220,
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
