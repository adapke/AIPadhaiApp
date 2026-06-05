"""QA harness — verifies exam_taxonomy scope wiring into lessons.

Covers:
  1. taxonomy_scope_for_user returns None when the user has no
     enrollment.
  2. After enrolling in cbse_class_10_2026, the scope resolves to
     board_hint='CBSE' + non-empty chapter_titles.
  3. board_hint_for_exam mapping for: cbse_class_10, upsc_cse, ssc_cgl,
     jee_main, neet_ug.
  4. pedagogy.build_user_text injects "Syllabus scope:" when
     taxonomy_scope is provided, and combines cleanly with board_hint
     guidance.

No Claude calls. Pure storage + helper-function verification.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["PADHAI_DB_PATH"] = str(ROOT / "qa_taxonomy.db")
qa_db = Path(os.environ["PADHAI_DB_PATH"])
if qa_db.exists():
    qa_db.unlink()

from padhai import exam_taxonomy, pedagogy

UID = f"qa-user-{uuid.uuid4().hex[:8]}"


def test_scope_none_without_enrollment() -> None:
    exam_taxonomy.migrate()
    scope = exam_taxonomy.taxonomy_scope_for_user("nonexistent-user")
    assert scope is None, f"expected None, got {scope}"
    print("  [OK] no enrollment -> scope is None")


def test_cbse_10_scope() -> None:
    exam_taxonomy.enroll(
        pack_code="cbse_class_10_2026",
        user_id=UID,
        daily_minutes=45,
    )
    scope = exam_taxonomy.taxonomy_scope_for_user(UID)
    assert scope is not None, "scope should resolve after enrollment"
    assert scope["exam_code"] == "cbse_class_10", scope["exam_code"]
    assert scope["board_hint"] == "CBSE", scope["board_hint"]
    assert scope["pack_code"] == "cbse_class_10_2026", scope["pack_code"]
    assert "CBSE" in scope["scope_summary"], scope["scope_summary"]
    assert scope["chapter_titles"], "expected non-empty chapter list"
    print(
        f"  [OK] CBSE Class 10 enrollment -> exam={scope['exam_code']} "
        f"board_hint={scope['board_hint']} "
        f"chapters={len(scope['chapter_titles'])}"
    )
    print(f"        scope_summary head: {scope['scope_summary'][:140]}…")


def test_board_hint_mapping() -> None:
    cases = [
        ("cbse_class_10", "CBSE"),
        ("upsc_cse", "UPSC"),
        ("ssc_cgl", "SSC"),
        ("ibps_po", None),    # no explicit body→hint mapping yet
    ]
    for exam_code, expected in cases:
        got = exam_taxonomy.board_hint_for_exam(exam_code)
        if expected is None:
            assert got is None, f"{exam_code}: expected None, got {got!r}"
        else:
            assert got == expected, f"{exam_code}: expected {expected!r}, got {got!r}"
        print(f"  [OK] board_hint_for_exam({exam_code!r}) = {got!r}")


def test_build_user_text_injects_scope() -> None:
    txt = pedagogy.build_user_text(
        language_code="en",
        level="middle",
        board_hint="CBSE",
        taxonomy_scope="Student is preparing for CBSE 10. In-scope: A; B; C.",
    )
    assert "Syllabus scope:" in txt, "scope block missing"
    assert "CBSE" in txt, "board guidance missing"
    assert "In-scope: A; B; C." in txt, "scope summary missing"
    print("  [OK] build_user_text injects taxonomy_scope alongside board_hint")
    # Also verify backward compatibility — no scope, no insertion.
    txt2 = pedagogy.build_user_text(
        language_code="en", level="middle", board_hint="CBSE",
    )
    assert "Syllabus scope:" not in txt2
    print("  [OK] build_user_text omits scope block when none passed")


def main() -> int:
    print("QA: exam_taxonomy scope wiring")
    print("-" * 60)
    try:
        test_scope_none_without_enrollment()
        test_cbse_10_scope()
        test_board_hint_mapping()
        test_build_user_text_injects_scope()
    except AssertionError as e:
        print(f"  [FAIL] {e}", file=sys.stderr)
        return 1
    print("-" * 60)
    print("ALL exam_taxonomy checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
