"""prod-93 — Tests that lock the JSON output shape of ops CLIs.

Ops scripts that grep / jq / Splunk these outputs depend on stable
keys. If a field is renamed or dropped, this test catches it.

Covers:
  - scripts/print_curator_stats.py   (prod-78)
  - scripts/check_verified_iframes.py (prod-82)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATS_SCRIPT = REPO_ROOT / "scripts" / "print_curator_stats.py"
IFRAME_SCRIPT = REPO_ROOT / "scripts" / "check_verified_iframes.py"


def _run(script, *args, db_path=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PADHAI_SKIP_DOTENV"] = "1"
    env["PADHAI_JWT_SECRET"] = "test-secret-abcdef0123456789abcdef0123456789"
    if db_path:
        env["PADHAI_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, env=env,
        encoding="utf-8", errors="replace",
        timeout=60,
    )


def test_stats_cli_emits_json_with_contract_keys(tmp_path):
    """prod-78/93 — JSON keys are the contract; ops scripts grep them."""
    db = tmp_path / "stats.db"
    r = _run(STATS_SCRIPT, "--days", "7", db_path=db)
    assert r.returncode == 0, f"failed: {r.stderr}"
    data = json.loads(r.stdout)
    # Required keys (locked from prod-74's curator_stats helper):
    for key in (
        "total", "by_tier", "verified_recent", "updated_recent",
        "played_recent_total", "freshest_verified_iso",
        "oldest_verified_iso", "since_days",
    ):
        assert key in data, f"missing JSON key: {key}"
    # Empty DB → sensible defaults
    assert data["total"] == 0
    assert data["since_days"] == 7
    assert data["verified_recent"] == 0
    assert data["played_recent_total"] == 0


def test_stats_cli_pretty_flag_indents(tmp_path):
    db = tmp_path / "stats_pretty.db"
    r1 = _run(STATS_SCRIPT, "--days", "30", db_path=db)
    r2 = _run(STATS_SCRIPT, "--days", "30", "--pretty", db_path=db)
    assert r1.returncode == 0
    assert r2.returncode == 0
    # Pretty output is multi-line; compact is single line.
    assert "\n" in r2.stdout.strip(), "pretty should have newlines"
    assert r2.stdout.count("\n") > r1.stdout.count("\n")


def test_stats_cli_custom_days_flag(tmp_path):
    db = tmp_path / "stats_days.db"
    r = _run(STATS_SCRIPT, "--days", "365", db_path=db)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["since_days"] == 365


def test_iframe_check_cli_emits_report_json(tmp_path):
    """prod-82/93 — JSON keys are the contract for cron alerts."""
    db = tmp_path / "iframe.db"
    # Run on an empty DB — should report checked=0, exit 0
    r = _run(IFRAME_SCRIPT, "--sleep-ms", "0", db_path=db)
    assert r.returncode == 0, f"failed: {r.stderr}"
    data = json.loads(r.stdout)
    # Required keys
    for key in (
        "checked", "ok", "blocked", "inconclusive", "demoted",
        "started_at", "elapsed_sec", "rows",
    ):
        assert key in data, f"missing JSON key: {key}"
    # Empty DB → no rows checked
    assert data["checked"] == 0
    assert data["blocked"] == 0
    assert isinstance(data["rows"], list)
    assert data["rows"] == []


def test_iframe_check_cli_handles_limit_flag(tmp_path):
    """prod-82/93 — --limit caps how many rows we walk."""
    db = tmp_path / "iframe_limit.db"
    r = _run(IFRAME_SCRIPT, "--limit", "0", "--sleep-ms", "0", db_path=db)
    assert r.returncode == 0
    # Just verify it doesn't blow up on empty DB
    data = json.loads(r.stdout)
    assert data["checked"] == 0


def test_iframe_check_cli_exits_zero_on_empty_db(tmp_path):
    """prod-82/93 — exit code 0 when no blocked rows.
    (exit 1 means at least one verified row newly-blocked.)"""
    db = tmp_path / "iframe_exit.db"
    r = _run(IFRAME_SCRIPT, "--sleep-ms", "0", db_path=db)
    assert r.returncode == 0, (
        f"expected exit 0 for empty DB; got {r.returncode}. "
        f"stderr: {r.stderr}"
    )


def test_iframe_check_cli_summary_to_stderr(tmp_path):
    """prod-82/93 — JSON to stdout, summary to stderr (cron-friendly split)."""
    db = tmp_path / "iframe_streams.db"
    r = _run(IFRAME_SCRIPT, "--sleep-ms", "0", db_path=db)
    assert r.returncode == 0
    # stdout is JSON
    json.loads(r.stdout)
    # stderr has the iframe-check summary line
    assert "[iframe-check]" in r.stderr
    assert "ok=" in r.stderr
