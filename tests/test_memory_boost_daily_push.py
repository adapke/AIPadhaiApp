"""prod-147 — Tests for the daily Memory Boost push cron.

Covers:
  1. run() returns the expected summary shape on an empty DB.
  2. run() processes active enrollments + sends pushes via injector.
  3. run() respects --limit by capping candidate users.
  4. dry_run=True bypasses the push sender entirely.
  5. _build_push_body adapts copy to streak length (0, low, high).
  6. Users without PYQs in their pool → skipped_no_pack increments.
  7. Push sender returning skipped_opt_out=True increments correct
     counter, NOT `sent`.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _import_cron():
    """Import scripts/memory_boost_daily_push.py as a module."""
    spec = importlib.util.spec_from_file_location(
        "memory_boost_daily_push_mod",
        REPO_ROOT / "scripts" / "memory_boost_daily_push.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _isolated(monkeypatch, tmp_path):
    db_path = tmp_path / f"test_cron_{uuid.uuid4().hex[:6]}.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db_path))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib

    from padhai import db, memory_boost, question_bank
    importlib.reload(db)
    importlib.reload(question_bank)
    importlib.reload(memory_boost)
    question_bank.migrate()
    memory_boost.migrate()
    return db_path


def _seed_enrollment(db_path: Path, user_id: str, pack_code: str = "cbse10",
                     daily_minutes: int = 30, status: str = "active"):
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS exam_pack_enrollments (
            id TEXT PRIMARY KEY,
            pack_code TEXT,
            user_id TEXT,
            target_date REAL,
            daily_minutes INTEGER,
            status TEXT,
            enrolled_at REAL,
            completed_at REAL
        );
    """)
    conn.execute(
        "INSERT INTO exam_pack_enrollments "
        "(id, pack_code, user_id, target_date, daily_minutes, status, "
        " enrolled_at, completed_at) VALUES (?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, pack_code, user_id, None,
         daily_minutes, status, 1700000000.0, None),
    )
    conn.commit()
    conn.close()


def _seed_pyqs(n: int = 3):
    from padhai import question_bank
    for i in range(n):
        question_bank.upsert(
            board="cbse", grade=10, subject="science",
            chapter=f"Chapter {i+1}", year=2024, paper="main",
            question_text=f"Test question {i+1} {uuid.uuid4().hex[:6]}",
        )


# ---------- Tests ----------


def test_run_empty_db_returns_zero_summary(monkeypatch, tmp_path):
    """prod-147 — Empty DB → all-zero summary, no errors."""
    _isolated(monkeypatch, tmp_path)
    mod = _import_cron()
    result = mod.run(dry_run=True)
    assert result["processed"] == 0
    assert result["sent"] == 0
    assert result["errors"] == []


def test_run_with_active_enrollment_calls_push(monkeypatch, tmp_path):
    """prod-147 — Enrolled user gets the push sender called."""
    db_path = _isolated(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    _seed_enrollment(db_path, user_id="user-A", pack_code="cbse10")

    @dataclass
    class FakeResult:
        delivered: int = 1
        failed: int = 0
        skipped_opt_out: bool = False

    calls: list[dict] = []

    def _fake_send(*, user_id, category, title, body, payload, **_kw):
        calls.append({
            "user_id": user_id, "category": category,
            "title": title, "body": body, "payload": payload,
        })
        return FakeResult()

    mod = _import_cron()
    result = mod.run(dry_run=False, push_sender=_fake_send)
    assert result["processed"] == 1
    assert result["sent"] == 1
    assert len(calls) == 1
    assert calls[0]["user_id"] == "user-A"
    assert calls[0]["category"] == "streak"
    assert "Memory Boost" in calls[0]["title"] or "streak" in calls[0]["title"].lower() or "📚" in calls[0]["title"] or "🔥" in calls[0]["title"]


def test_run_dry_run_does_not_invoke_sender(monkeypatch, tmp_path):
    """prod-147 — dry_run=True bypasses the push sender."""
    db_path = _isolated(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    _seed_enrollment(db_path, user_id="user-A", pack_code="cbse10")

    sender_was_called = False

    def _fake_send(**_kw):
        nonlocal sender_was_called
        sender_was_called = True
        return None

    mod = _import_cron()
    result = mod.run(dry_run=True, push_sender=_fake_send)
    assert sender_was_called is False
    assert result["dry_run"] is True
    # Even dry_run counts as 'sent' for the summary so ops can see
    # what would have shipped.
    assert result["sent"] == 1


def test_run_limit_caps_candidates(monkeypatch, tmp_path):
    """prod-147 — --limit N processes only first N users."""
    db_path = _isolated(monkeypatch, tmp_path)
    _seed_pyqs(n=5)
    for i in range(5):
        _seed_enrollment(db_path, user_id=f"user-{i}", pack_code="cbse10")

    @dataclass
    class FakeResult:
        delivered: int = 1
        failed: int = 0
        skipped_opt_out: bool = False

    calls: list[str] = []

    def _fake_send(*, user_id, **_kw):
        calls.append(user_id)
        return FakeResult()

    mod = _import_cron()
    result = mod.run(dry_run=False, limit=2, push_sender=_fake_send)
    assert result["processed"] == 2
    assert len(calls) == 2


def test_skipped_opt_out_counter(monkeypatch, tmp_path):
    """prod-147 — User who opted out increments skipped_opt_out,
    not sent."""
    db_path = _isolated(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    _seed_enrollment(db_path, user_id="user-opted-out", pack_code="cbse10")

    @dataclass
    class FakeResult:
        delivered: int = 0
        failed: int = 0
        skipped_opt_out: bool = True

    def _fake_send(**_kw):
        return FakeResult()

    mod = _import_cron()
    result = mod.run(dry_run=False, push_sender=_fake_send)
    assert result["skipped_opt_out"] == 1
    assert result["sent"] == 0


def test_no_pyqs_skip_no_pack(monkeypatch, tmp_path):
    """prod-147 — Enrollment with empty PYQ pool → skipped_no_pack."""
    db_path = _isolated(monkeypatch, tmp_path)
    # No _seed_pyqs() this time
    _seed_enrollment(db_path, user_id="user-no-pool", pack_code="cbse10")

    def _fake_send(**_kw):
        raise AssertionError("should not be called when pack is empty")

    mod = _import_cron()
    result = mod.run(dry_run=False, push_sender=_fake_send)
    assert result["skipped_no_pack"] == 1
    assert result["sent"] == 0


def test_build_push_body_scales_with_streak():
    """prod-147 — Push copy adapts to streak length."""
    mod = _import_cron()
    title_0, _ = mod._build_push_body(pack_size=3, streak=0)
    title_3, _ = mod._build_push_body(pack_size=3, streak=3)
    title_30, _ = mod._build_push_body(pack_size=3, streak=30)
    # The three titles should differ — different tone per streak band
    assert title_0 != title_3
    assert title_3 != title_30
    # Streak 30 should mention 30 explicitly
    assert "30" in title_30


def test_inactive_enrollments_skipped(monkeypatch, tmp_path):
    """prod-147 — status != 'active' enrollments are NOT processed."""
    db_path = _isolated(monkeypatch, tmp_path)
    _seed_pyqs(n=3)
    _seed_enrollment(db_path, user_id="completed-user", status="completed")
    _seed_enrollment(db_path, user_id="active-user", status="active")

    @dataclass
    class FakeResult:
        delivered: int = 1
        failed: int = 0
        skipped_opt_out: bool = False

    calls: list[str] = []

    def _fake_send(*, user_id, **_kw):
        calls.append(user_id)
        return FakeResult()

    mod = _import_cron()
    result = mod.run(dry_run=False, push_sender=_fake_send)
    assert result["processed"] == 1
    assert "active-user" in calls
    assert "completed-user" not in calls
