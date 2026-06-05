"""QA — RAG citations cover essay, mock_interview, doubt surfaces."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["PADHAI_DB_PATH"] = str(ROOT / "qa_surfaces.db")
qa_db = Path(os.environ["PADHAI_DB_PATH"])
if qa_db.exists():
    qa_db.unlink()

# Make sure we don't accidentally use a real Anthropic key — we want
# the heuristic / no-key paths to run so we exercise those branches.
os.environ.pop("ANTHROPIC_API_KEY", None)

from padhai import citations, doubt_clearing, essay_grader, mock_interview

UID = f"qa-user-{uuid.uuid4().hex[:8]}"


def test_essay_grader_records_provenance():
    # Seed a rubric so we have something to grade against
    rubric = essay_grader.upsert_rubric(
        exam="cbse_class10_eng",
        paper="essay",
        topic="argumentative",
        criteria=[
            {"name": "Thesis", "weight": 4,
             "description": "Clear thesis statement defending a position"},
            {"name": "Evidence", "weight": 4,
             "description": "Concrete examples supporting the thesis"},
            {"name": "Style", "weight": 2,
             "description": "Coherent paragraph structure"},
        ],
        max_marks=10,
        created_by="qa",
    )
    sub = essay_grader.submit(
        user_id=UID, rubric_id=rubric.id,
        text=(
            "Reading is important. Books help students learn ideas they "
            "could not see otherwise. A reader builds vocabulary and "
            "imagination. Therefore, reading every day matters."
        ),
    )
    result = essay_grader.grade(sub.id)
    print(
        f"  [OK] essay graded (method={result.method}) "
        f"score={result.score}/{result.by_criterion and len(result.by_criterion)} criteria"
    )

    answers = citations.list_user_answers(user_id=UID, surface="essay")
    assert len(answers) == 1, f"expected 1 essay row, got {len(answers)}"
    a = answers[0]
    assert a.surface == "essay"
    assert a.grounded is False, "essay grader is general-mode, no citations"
    print(
        f"  [OK] essay provenance row id={a.id[:8]}... "
        f"reason={a.fallback_reason!r}"
    )


def test_mock_interview_records_provenance():
    interview, t0 = mock_interview.start(user_id=UID, track="upsc_personality")
    print(f"  [OK] interview started id={interview.id[:8]}... q={t0.question_text[:40]!r}")
    res = mock_interview.submit_answer(
        interview_id=interview.id,
        turn_index=0,
        answer_text=(
            "I want to be a civil servant because I want to serve "
            "rural India. My uncle is a BDO and I have seen the impact "
            "an honest officer can have on a village."
        ),
    )
    print(
        f"  [OK] turn answered, feedback method={res.feedback.get('method')}"
    )
    answers = citations.list_user_answers(user_id=UID, surface="mock_interview")
    assert len(answers) >= 1, f"expected ≥1 mock_interview row, got {len(answers)}"
    a = answers[0]
    assert a.surface == "mock_interview"
    print(
        f"  [OK] mock_interview provenance row id={a.id[:8]}... "
        f"reason={a.fallback_reason!r}"
    )


def test_doubt_records_provenance():
    # answer_via_ai_vision needs a doubt — pre-populate one
    d = doubt_clearing.submit(
        user_id=UID,
        question_text="What is the derivative of sin x?",
        image_url="https://example.com/snap.jpg",
    )
    print(f"  [OK] doubt submitted id={d.id[:8]}...")
    answered = doubt_clearing.answer_via_ai_vision(doubt_id=d.id)
    print(f"  [OK] doubt answered status={answered.status}")
    answers = citations.list_user_answers(user_id=UID, surface="doubt")
    assert len(answers) == 1, f"expected 1 doubt row, got {len(answers)}"
    a = answers[0]
    assert a.surface == "doubt"
    # image_url present → citations should have 1 entry (the upload)
    assert len(a.citations) == 1, f"expected 1 citation, got {len(a.citations)}"
    assert a.citations[0].source_kind == "upload"
    assert a.grounded is True, "doubt with image should be grounded"
    print(
        f"  [OK] doubt provenance row id={a.id[:8]}... "
        f"grounded={a.grounded} citation_count={len(a.citations)}"
    )


def test_grounding_rate_covers_all():
    rate = citations.grounding_rate()
    surfaces = {s["surface"] for s in rate["by_surface"]}
    print(f"  grounding rate snapshot: total={rate['total_answers']} "
          f"surfaces={sorted(surfaces)}")
    expected = {"essay", "mock_interview", "doubt"}
    missing = expected - surfaces
    assert not missing, f"missing surfaces in rollup: {missing}"
    print("  [OK] grounding_rate reports all 3 new surfaces")


def main():
    print("QA: RAG citations on essay/mock_interview/doubt")
    print("-" * 60)
    try:
        test_essay_grader_records_provenance()
        test_mock_interview_records_provenance()
        test_doubt_records_provenance()
        test_grounding_rate_covers_all()
    except AssertionError as e:
        print(f"  [FAIL] {e}", file=sys.stderr)
        return 1
    print("-" * 60)
    print("ALL RAG-surface checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
