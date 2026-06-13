"""prod-86/83 — Tests for the concept-video CSV import script.

Locks the contract:
  - Missing required header → exit 2
  - Bad path → exit 2
  - Valid CSV → exit 0, rows loaded, idempotent on re-import
  - --dry-run → exit 0, no DB writes
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "import_concept_videos.py"


def _run(args, env_extra=None):
    """Run the CSV-import script with PYTHONPATH set; return CompletedProcess."""
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PADHAI_SKIP_DOTENV"] = "1"
    env["PADHAI_JWT_SECRET"] = "test-secret-abcdef0123456789abcdef0123456789"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
        timeout=30,
    )


@pytest.fixture()
def csv_path(tmp_path):
    p = tmp_path / "videos.csv"
    p.write_text(
        "concept,source,source_url,title,channel,quality_tier\n"
        "TestA,youtube,https://www.youtube.com/watch?v=testA1234567,Title A,ChanA,channel_seed\n"
        "TestB,youtube,https://www.youtube.com/watch?v=testB1234567,Title B,ChanB,verified\n",
        encoding="utf-8",
    )
    return p


def test_csv_import_missing_file_exits_2(tmp_path):
    r = _run([str(tmp_path / "nope.csv")])
    assert r.returncode == 2
    assert "file not found" in r.stderr.lower()


def test_csv_import_missing_header_exits_2(tmp_path):
    p = tmp_path / "bad.csv"
    # Missing required `source_url`
    p.write_text(
        "concept,source,title\n"
        "TestA,youtube,Title\n",
        encoding="utf-8",
    )
    r = _run([str(p)])
    assert r.returncode == 2
    assert "missing required column" in r.stderr.lower()


def test_csv_import_dry_run_writes_nothing(csv_path, tmp_path):
    db = tmp_path / "dryrun.db"
    r = _run([str(csv_path), "--dry-run"], env_extra={"PADHAI_DB_PATH": str(db)})
    assert r.returncode == 0
    assert "dry-run" in r.stderr.lower()
    assert "parsed 2 row(s)" in r.stderr
    # No DB writes
    if db.exists():
        import sqlite3
        conn = sqlite3.connect(str(db))
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM concept_videos",
            ).fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        finally:
            conn.close()
        assert n == 0


def test_csv_import_real_load_then_idempotent(csv_path, tmp_path):
    db = tmp_path / "import.db"
    r1 = _run([str(csv_path)], env_extra={"PADHAI_DB_PATH": str(db)})
    assert r1.returncode == 0, f"first run failed: {r1.stderr}"
    assert "loaded=2" in r1.stderr

    # Re-run is idempotent — same row count, no errors.
    r2 = _run([str(csv_path)], env_extra={"PADHAI_DB_PATH": str(db)})
    assert r2.returncode == 0, f"rerun failed: {r2.stderr}"
    assert "loaded=2" in r2.stderr

    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        n = conn.execute("SELECT COUNT(*) FROM concept_videos").fetchone()[0]
    finally:
        conn.close()
    assert n == 2, f"expected 2 rows after idempotent re-import, got {n}"


def test_csv_import_with_default_tier_override(tmp_path):
    """prod-86 — --default-quality-tier fills the field when CSV doesn't."""
    p = tmp_path / "no_tier.csv"
    p.write_text(
        "concept,source,source_url,title\n"
        "NoTierConcept,youtube,https://www.youtube.com/watch?v=notierabcdef,t\n",
        encoding="utf-8",
    )
    db = tmp_path / "tier.db"
    r = _run(
        [str(p), "--default-quality-tier=channel_seed"],
        env_extra={"PADHAI_DB_PATH": str(db)},
    )
    assert r.returncode == 0, r.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    try:
        tier = conn.execute(
            "SELECT quality_tier FROM concept_videos WHERE concept='NoTierConcept'",
        ).fetchone()
    finally:
        conn.close()
    assert tier is not None
    assert tier[0] == "channel_seed"
