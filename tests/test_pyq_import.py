"""prod-4 — PYQ ingest pipeline regression.

Locks the contract between `data/pyq/*.json` seed files and the
`scripts/import_pyq.py` loader. If a future PR breaks the JSON
schema, the upsert call signature, or the idempotency property, the
test fails before CI lets it land.

The test uses a temp SQLite path so it never touches the dev DB.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Seed dataset is the source of truth for "what we promise the PYQ
# tab will show". If items are intentionally removed (e.g. a
# transcription error), update the floor — never lower it casually.
SEED_DIR = REPO_ROOT / "data" / "pyq"
SEED_FILES = (
    "jee_main_2024_mathematics.json",
    "jee_main_2024_physics.json",
    "jee_main_2024_chemistry.json",
)
EXPECTED_PER_FILE = 20
EXPECTED_TOTAL = EXPECTED_PER_FILE * len(SEED_FILES)


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Pin question_bank to a throwaway SQLite under tmp_path."""
    db = tmp_path / "pyq_test.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    # Re-import db so it picks up the new env var
    import importlib

    from padhai import db as _db
    from padhai import question_bank as qb
    importlib.reload(_db)
    importlib.reload(qb)
    yield db


def test_seed_files_present():
    """The seed JSON files are checked into the repo, so the
    pipeline can be exercised offline without a separate dataset
    download."""
    for name in SEED_FILES:
        path = SEED_DIR / name
        assert path.is_file(), f"missing seed file: {path}"


def test_import_pipeline_loads_seed_files(temp_db):  # noqa: ARG001 (fixture used for side-effect)
    """End-to-end: load all three JEE 2024 seed files, confirm
    every row lands under the natural key."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    total = 0
    for name in SEED_FILES:
        data = load_file(SEED_DIR / name)
        loaded, errors = import_batch(data, dry_run=False)
        assert not errors, f"{name}: errors={errors}"
        assert loaded == EXPECTED_PER_FILE, (
            f"{name}: expected {EXPECTED_PER_FILE}, got {loaded}"
        )
        total += loaded

    stats = question_bank.stats()
    assert stats["total"] == EXPECTED_TOTAL, stats
    assert stats["by_board"] == {"jee": EXPECTED_TOTAL}, stats
    assert stats["by_subject"] == {
        "mathematics": EXPECTED_PER_FILE,
        "physics":     EXPECTED_PER_FILE,
        "chemistry":   EXPECTED_PER_FILE,
    }, stats


def test_import_is_idempotent(temp_db):  # noqa: ARG001 (fixture used for side-effect)
    """Replaying the same file must not double-insert. The natural
    key is (board, grade, subject, year, paper, question_text)."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    data = load_file(SEED_DIR / SEED_FILES[0])
    import_batch(data, dry_run=False)
    first = question_bank.stats()["total"]
    import_batch(data, dry_run=False)
    second = question_bank.stats()["total"]
    assert first == second == EXPECTED_PER_FILE, (
        f"expected stable count {EXPECTED_PER_FILE}; "
        f"got {first} then {second}"
    )


def test_imported_questions_have_required_fields(temp_db):  # noqa: ARG001 (fixture used for side-effect)
    """Every imported question needs at minimum: board/grade/subject/
    question_text/correct_answer. UI surfaces will silently break if
    a field comes through as None."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    data = load_file(SEED_DIR / SEED_FILES[0])
    import_batch(data, dry_run=False)

    rows = question_bank.search(board="jee", subject="mathematics", limit=50)
    assert len(rows) == EXPECTED_PER_FILE
    for q in rows:
        assert q.board == "jee"
        assert q.grade == 12
        assert q.subject == "mathematics"
        assert q.year == 2024
        assert q.paper == "main"
        assert q.question_text, q
        assert q.correct_answer in {"A", "B", "C", "D"}, q
        assert q.options and len(q.options) == 4, q
        assert q.difficulty in {"easy", "medium", "hard"}, q
        assert q.marks == 4, q


# ---------- prod-12: full seed across boards/exams/mediums ----------

# Boards we promise have seed coverage at prod-12. If a future PR
# accidentally deletes one of these board's last file, the test
# fails with a clear pointer.
EXPECTED_BOARDS = frozenset({
    "jee", "neet", "upsc", "cat",  # national exams
    "cbse", "icse",                # national boards
    "state_mh", "state_tn", "state_ka",
    "state_ap_tg", "state_gj", "state_wb", "state_up",  # state boards
})

# Hindi-medium subjects — proves the pipeline supports multi-medium
# (same board, same exam, different rendering medium).
HINDI_MEDIUM_SUBJECTS = frozenset({"mathematics_hindi", "science_hindi"})

# Floor for total question count across the full seed. Updated at
# prod-12. Future sprints expanding the seed should raise this.
MIN_TOTAL_QUESTIONS = 220


def test_full_seed_loads_without_errors(temp_db):  # noqa: ARG001
    """prod-12 — every JSON file under data/pyq/ must import cleanly.
    Catches malformed JSON, missing required fields, schema drift."""
    from scripts.import_pyq import import_batch, load_file

    total_errors = []
    for path in sorted(SEED_DIR.glob("*.json")):
        data = load_file(path)
        _, errors = import_batch(data, dry_run=False)
        if errors:
            total_errors.extend(f"{path.name}: {e}" for e in errors)
    assert not total_errors, f"import errors: {total_errors[:5]}"


def test_full_seed_has_minimum_question_count(temp_db):  # noqa: ARG001
    """prod-12 — guard against silent seed-file deletion."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    for path in sorted(SEED_DIR.glob("*.json")):
        import_batch(load_file(path), dry_run=False)

    stats = question_bank.stats()
    assert stats["total"] >= MIN_TOTAL_QUESTIONS, (
        f"total questions dropped: expected >={MIN_TOTAL_QUESTIONS}, "
        f"got {stats['total']}. Did someone delete a seed file?"
    )


def test_full_seed_covers_all_promised_boards(temp_db):  # noqa: ARG001
    """prod-12 — every board in EXPECTED_BOARDS must have at least
    one question. Surfaces the case where the last seed file for
    a state board got removed."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    for path in sorted(SEED_DIR.glob("*.json")):
        import_batch(load_file(path), dry_run=False)

    stats = question_bank.stats()
    missing = EXPECTED_BOARDS - set(stats["by_board"])
    assert not missing, (
        f"boards missing seed coverage: {sorted(missing)}. "
        f"Currently have: {sorted(stats['by_board'])}"
    )


def test_full_seed_includes_hindi_medium(temp_db):  # noqa: ARG001
    """prod-12 — Hindi-medium variants prove the pipeline supports
    multi-medium. Don't let a future cleanup pass delete them."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    for path in sorted(SEED_DIR.glob("*.json")):
        import_batch(load_file(path), dry_run=False)

    stats = question_bank.stats()
    subjects = set(stats["by_subject"])
    missing = HINDI_MEDIUM_SUBJECTS - subjects
    assert not missing, f"Hindi-medium subjects missing: {sorted(missing)}"


def test_post_school_exams_use_grade_zero(temp_db):  # noqa: ARG001
    """prod-12 — UPSC, CAT, SSC etc are post-school exams; the
    schema reserves grade=0 as a sentinel for those. Catches a
    regression where the import validator rejects falsy grade values."""
    from padhai import question_bank
    from scripts.import_pyq import import_batch, load_file

    for path in sorted(SEED_DIR.glob("*.json")):
        import_batch(load_file(path), dry_run=False)

    upsc = question_bank.search(board="upsc", limit=200)
    cat = question_bank.search(board="cat", limit=200)
    assert all(q.grade == 0 for q in upsc), (
        f"UPSC questions must have grade=0; got {[q.grade for q in upsc[:3]]}"
    )
    assert all(q.grade == 0 for q in cat), (
        f"CAT questions must have grade=0; got {[q.grade for q in cat[:3]]}"
    )
