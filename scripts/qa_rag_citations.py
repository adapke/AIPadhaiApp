"""QA harness — verifies RAG citation recording end-to-end.

What it covers:
  1. pedagogy._record_lesson_provenance writes an ai_answer_provenance
     row with surface='lesson' and the upload citation attached.
  2. tutor_grounding.send_grounded_message writes a provenance row with
     surface='tutor' and citations passed through.
  3. citations.grounding_rate aggregates correctly across both surfaces.

No ANTHROPIC_API_KEY needed — we don't call Claude. We drive the
storage-layer hooks directly because the network calls are already
covered by tutor.py / pedagogy.py logic. The QA hook is "does the
provenance get written when these helpers run."
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force a fresh QA DB so we count only this run's rows.
os.environ["PADHAI_DB_PATH"] = str(ROOT / "qa_citations.db")
qa_db = Path(os.environ["PADHAI_DB_PATH"])
if qa_db.exists():
    qa_db.unlink()

from padhai import citations, pedagogy, tutor_grounding  # noqa: E402

UID = f"qa-user-{uuid.uuid4().hex[:8]}"
SID = f"qa-session-{uuid.uuid4().hex[:8]}"


def test_lesson_provenance() -> None:
    lesson = pedagogy.Lesson(
        title="Photosynthesis basics",
        language_code="en",
        language_name="English",
        level="middle",
        scenes=[
            pedagogy.Scene(
                title="Sunlight + chlorophyll",
                narration="Plants use sunlight, water and CO2 to make glucose.",
                bullets=["Sunlight", "Chlorophyll", "Glucose"],
            ),
            pedagogy.Scene(
                title="Releases oxygen",
                narration="The byproduct of photosynthesis is oxygen, breathed by animals.",
                bullets=["O2 byproduct", "Atmospheric oxygen", "Animal respiration"],
            ),
        ],
        quiz=[],
    )
    pedagogy._record_lesson_provenance(
        lesson=lesson,
        user_id=UID,
        source_upload_id="upload-fake-123",
        source_page_number=12,
        board_hint="CBSE",
        level="middle",
    )
    answers = citations.list_user_answers(user_id=UID, surface="lesson")
    assert len(answers) == 1, f"expected 1 lesson provenance, got {len(answers)}"
    a = answers[0]
    assert a.surface == "lesson", a.surface
    assert a.grounded is True, "lesson should be grounded by the upload citation"
    assert len(a.citations) == 1, f"expected 1 citation, got {len(a.citations)}"
    c = a.citations[0]
    assert c.source_kind == "upload"
    assert c.source_id == "upload-fake-123"
    assert c.page_number == 12
    print(f"  [OK]lesson provenance OK (id={a.id[:8]}…, citations={len(a.citations)})")


def test_tutor_grounding() -> None:
    chunks = [
        {
            "source_kind": "upload",
            "source_id": "upload-fake-456",
            "page_number": 42,
            "section": "Chapter 4: Ecosystems",
            "citation_text": "An ecosystem is a community of living organisms interacting with the non-living environment.",
            "relevance": 0.91,
        },
    ]
    reply = tutor_grounding.send_grounded_message(
        session_id=SID,
        user_id=UID,
        question_text="What is an ecosystem?",
        answer_text="An ecosystem is a community of living + non-living things interacting.",
        retrieved_chunks=chunks,
        confidence=0.88,
        surface="tutor",
    )
    assert reply.grounded is True, "tutor reply should be grounded"
    assert reply.citation_count == 1, reply.citation_count
    answers = citations.list_user_answers(user_id=UID, surface="tutor")
    assert len(answers) == 1, f"expected 1 tutor provenance, got {len(answers)}"
    a = answers[0]
    assert a.citations[0].section == "Chapter 4: Ecosystems"
    print(f"  [OK]tutor provenance OK (id={a.id[:8]}…, citations={len(a.citations)})")


def test_grounding_rate() -> None:
    rate = citations.grounding_rate()
    assert rate["total_answers"] == 2, rate
    assert rate["grounded_answers"] == 2, rate
    assert rate["grounding_rate"] == 1.0, rate
    by = {s["surface"]: s for s in rate["by_surface"]}
    assert by["lesson"]["total"] == 1, by
    assert by["tutor"]["total"] == 1, by
    print(f"  [OK]grounding_rate aggregates: {rate}")


def test_strict_mode_refuses() -> None:
    # source_only mode + zero citations → NotGroundedError, no row
    before = len(citations.list_user_answers(user_id=UID))
    try:
        citations.record_answer(
            surface="lesson", user_id=UID,
            question_text="ungrounded question",
            answer_text="ungrounded answer",
            answer_mode="source_only",
        )
    except citations.NotGroundedError as e:
        print(f"  [OK]strict mode raised NotGroundedError: {e.reason}")
    else:
        raise AssertionError("expected NotGroundedError")
    after = len(citations.list_user_answers(user_id=UID))
    assert after == before, "strict-mode rejection should NOT write a row"


def main() -> int:
    print("QA: RAG citation recording")
    print("-" * 60)
    try:
        test_lesson_provenance()
        test_tutor_grounding()
        test_grounding_rate()
        test_strict_mode_refuses()
    except AssertionError as e:
        print(f"  [FAIL] {e}", file=sys.stderr)
        return 1
    print("-" * 60)
    print("ALL RAG citation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
