"""Smoke for v2.2 — L2 essay grader + L5 adaptive practice tests + Q2 cost opt.

Run: PYTHONPATH=. python scripts/test_v2_2.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v2_2_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import essay_grader, llm_cache, practice_test
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v2.2 smoke test")
    print("---------------")

    # --- Q2 LLM cost optimization ---
    desc = llm_cache.describe()
    check(
        "Q2 describe() returns dict with caching + batch flags",
        "caching_enabled" in desc and "batch_enabled" in desc,
    )
    check(
        "Q2 caching off by default",
        not llm_cache.is_caching_enabled(),
    )
    check(
        "Q2 batch off by default",
        not llm_cache.is_batch_enabled(),
    )

    # with_caching shape — caching OFF
    kwargs = llm_cache.with_caching(
        system_text="You are a tutor.", user_text="hi",
    )
    check(
        "Q2 with_caching off → string system + plain message",
        isinstance(kwargs["system"], str)
        and kwargs["messages"][0]["role"] == "user",
    )

    # Caching ON → system becomes a list of cache_control blocks
    os.environ["PADHAI_LLM_CACHE_ENABLED"] = "1"
    try:
        check("Q2 is_caching_enabled True after env set",
              llm_cache.is_caching_enabled())
        kwargs = llm_cache.with_caching(
            system_text="big system prompt", user_text="q",
        )
        check(
            "Q2 with_caching on → system is list with cache_control",
            isinstance(kwargs["system"], list)
            and kwargs["system"][0].get("cache_control")
                == {"type": "ephemeral"},
        )
    finally:
        os.environ.pop("PADHAI_LLM_CACHE_ENABLED", None)

    # Batch submit (dev path — writes JSONL outbox)
    sub = llm_cache.submit_batch([
        llm_cache.BatchRequest(
            custom_id="t1", model="claude-haiku-4-5",
            system="s", user_text="u",
            module="test",
        ),
    ])
    check(
        "Q2 submit_batch returns BatchSubmission with id + count",
        sub.batch_id.startswith("batch_") and sub.request_count == 1,
    )
    # Outbox file written
    outbox = Path(
        os.environ.get("PADHAI_LLM_BATCH_OUTBOX")
        or llm_cache.describe()["outbox_dir"]
    )
    out_path = outbox / f"{sub.batch_id}.jsonl"
    check("Q2 batch outbox JSONL exists", out_path.exists())

    # Poll a non-batch-enabled batch
    poll = llm_cache.poll_batch(sub.batch_id)
    check(
        "Q2 poll_batch returns pending when not configured",
        poll["status"] == "pending",
    )

    # Submit with zero requests → ValueError
    try:
        llm_cache.submit_batch([])
        check("Q2 submit_batch rejects empty list", False)
    except ValueError:
        check("Q2 submit_batch rejects empty list", True)

    # --- L2 essay grader ---
    essay_grader.migrate()
    r = essay_grader.upsert_rubric(
        exam="upsc_mains", paper="GS-1", topic="Modern History",
        criteria=[
            {"name": "Structure", "weight": 30,
             "description": "introduction conclusion paragraphs"},
            {"name": "Content", "weight": 50,
             "description": "accuracy facts dates names"},
            {"name": "Argument", "weight": 20,
             "description": "thesis evidence support reasoning"},
        ],
        max_marks=20,
    )
    check(
        "L2 upsert_rubric returns Rubric with 3 criteria",
        len(r.criteria) == 3 and r.max_marks == 20,
    )

    # Re-upsert same key → idempotent
    r2 = essay_grader.upsert_rubric(
        exam="upsc_mains", paper="GS-1", topic="Modern History",
        criteria=[{"name": "X", "weight": 100, "description": "x"}],
        max_marks=25,
    )
    check(
        "L2 re-upsert same key updates non-key fields",
        r2.id == r.id and r2.max_marks == 25,
    )

    # Invalid exam rejected
    try:
        essay_grader.upsert_rubric(
            exam="bogus", paper="x", topic=None,
            criteria=[{"name": "x", "weight": 1, "description": "x"}],
            max_marks=10,
        )
        check("L2 upsert_rubric rejects unknown exam", False)
    except ValueError:
        check("L2 upsert_rubric rejects unknown exam", True)

    # Submission too short rejected
    try:
        essay_grader.submit(
            user_id="u1", rubric_id=r.id, text="too short",
        )
        check("L2 submit rejects essays under 50 chars", False)
    except ValueError:
        check("L2 submit rejects essays under 50 chars", True)

    # Submit a real essay
    essay_text = (
        "The Indian independence movement was structured around mass "
        "civil disobedience driven by Gandhi. Key dates include 1919 "
        "and 1942 with the Quit India movement. The thesis here is "
        "that nonviolent resistance proved decisive evidence support "
        "reasoning structure introduction conclusion paragraphs."
    )
    s = essay_grader.submit(
        user_id="u1", rubric_id=r.id, text=essay_text,
    )
    check(
        "L2 submit returns Submission with text + ungraded fields",
        s.id and s.ai_score is None and not s.human_reviewed,
    )

    # Heuristic grader runs (no ANTHROPIC_API_KEY in this sandbox)
    g = essay_grader.grade(s.id)
    check(
        "L2 grade returns GradeResult with non-zero score",
        g.score > 0 and g.score <= r2.max_marks,
        f"score={g.score} max={r2.max_marks}",
    )
    check(
        "L2 grade method is 'heuristic' when no API key",
        g.method == "heuristic",
    )
    check(
        "L2 grade has by_criterion entries + suggestions",
        len(g.by_criterion) >= 1 and len(g.suggestions) >= 3,
    )

    # Persisted on submission
    s2 = essay_grader.get_submission(s.id)
    check(
        "L2 grade persisted to submission",
        s2.ai_score == g.score and s2.ai_feedback is not None,
    )

    # Human review
    check(
        "L2 record_human_review → True first time",
        essay_grader.record_human_review(
            submission_id=s.id, human_score=15.0,
            human_note="solid argument structure",
        ),
    )
    s3 = essay_grader.get_submission(s.id)
    check(
        "L2 human review persisted",
        s3.human_reviewed and s3.human_score == 15.0,
    )

    # Review queue
    q = essay_grader.review_queue()
    check(
        "L2 review_queue excludes already-reviewed",
        all(not r.human_reviewed for r in q),
    )

    # --- L5 practice tests ---
    practice_test.migrate()

    # Empty bank + no API key → placeholder fallback
    t = practice_test.generate(
        user_id="u-pract", exam="upsc_pre",
        subject="polity", target_minutes=10,
    )
    check(
        "L5 generate falls back to placeholders when bank+API empty",
        t.generation_method == "placeholder"
        and len(t.questions) >= 5,
        f"method={t.generation_method} count={len(t.questions)}",
    )

    # Seed the question bank, regenerate → method should flip
    from padhai import question_bank
    question_bank.migrate()
    for i in range(15):
        question_bank.upsert(
            board="cbse", grade=10, subject="polity",
            question_text=f"What is constitutional provision {i}?",
            options=[f"a) opt{i}", "b) other", "c) wrong", "d) none"],
            correct_answer="a", marks=1, difficulty="medium",
            topic_tags=["fundamental_rights"],
        )
    t2 = practice_test.generate(
        user_id="u-pract", exam="upsc_pre",
        subject="polity", target_minutes=10,
    )
    check(
        "L5 generate uses bank when populated",
        t2.generation_method in ("bank", "mixed", "synthetic"),
        f"method={t2.generation_method}",
    )

    # Bad exam rejected
    try:
        practice_test.generate(
            user_id="u-pract", exam="bogus_exam",
            subject="x", target_minutes=10,
        )
        check("L5 generate rejects unknown exam", False)
    except ValueError:
        check("L5 generate rejects unknown exam", True)

    # Bad difficulty mix rejected
    try:
        practice_test.generate(
            user_id="u-pract", exam="upsc_pre", subject="polity",
            difficulty_mix={"a": 0.5, "b": 0.4},   # doesn't sum to 1
        )
        check("L5 generate rejects mis-summing difficulty_mix", False)
    except ValueError:
        check("L5 generate rejects mis-summing difficulty_mix", True)

    # Start + submit round trip
    check("L5 start() returns True first time", practice_test.start(t2.id))
    check("L5 start() returns False second time",
          not practice_test.start(t2.id))

    # Answer the first half correctly
    answers = {}
    for i, q in enumerate(t2.questions):
        correct = (q.get("correct_answer") or "").strip().lower()
        if i < len(t2.questions) // 2 and correct:
            answers[q["id"]] = correct
        else:
            answers[q["id"]] = "z"  # intentionally wrong
    score = practice_test.submit(test_id=t2.id, answers=answers)
    check(
        "L5 submit returns score with total + max + per_question",
        "total" in score and "max" in score
        and isinstance(score["per_question"], list),
    )
    check(
        "L5 score total > 0 when half answered correctly",
        score["total"] > 0,
        f"total={score['total']} max={score['max']}",
    )

    # Already-submitted → ValueError
    try:
        practice_test.submit(test_id=t2.id, answers=answers)
        check("L5 second submit rejected", False)
    except ValueError:
        check("L5 second submit rejected", True)

    # --- HTTP layer ---
    with TestClient(app) as c:
        # All auth-gated
        for path, expect in [
            ("/api/essay/submissions", 401),
            ("/api/practice-tests", 401),
        ]:
            r = c.get(path)
            check(f"GET {path} → 401", r.status_code == expect)

        r = c.post(
            "/api/essay/submissions",
            data={"rubric_id": "x", "text": "y" * 80,
                  "grade_now": "false"},
        )
        check(
            "POST /api/essay/submissions requires auth (401)",
            r.status_code == 401,
        )

        # Public rubric catalog
        r = c.get("/api/essay/rubrics?exam=upsc_mains")
        check(
            "GET /api/essay/rubrics returns 200 + at least one rubric",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )

        # Cost-opt status endpoint
        r = c.get("/api/admin/llm/cost-opt")
        check(
            "GET /api/admin/llm/cost-opt returns 200 + flags",
            r.status_code == 200 and "caching_enabled" in r.json(),
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
        "v2.2 brought route count above 180",
        len(app.routes) > 180,
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
