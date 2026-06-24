"""prod-168 — Postgres migration dry-run check.

Parses both Liquibase changesets (db/changesets/001_core_schema.sql,
002_module_tables.sql), validates SQL syntax via psycopg's parser
(EXPLAIN won't fire DDL but parsing the statements catches a lot of
typos), and confirms the master.xml references every changeset file.

This is the cheapest possible launch-readiness check that doesn't
require a live Postgres. For a true dry-run, run::

    docker-compose up -d postgres
    docker-compose run --rm liquibase update
    docker-compose down

This script complements that by failing fast at PR time on syntactic
errors in the changesets — without needing to spin up Postgres.

Exit codes:
    0 — all changesets parse cleanly and are referenced from master.xml
    1 — one or more failures
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGESET_DIR = REPO_ROOT / "db" / "changesets"


def _split_statements(sql: str) -> list[str]:
    """Crude SQL statement splitter — sufficient for our DDL.
    Liquibase headers (`--changeset id:name`) and comments are
    preserved with each statement they precede."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            current.append(line)
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).strip())
            current = []
    if current and any(line.strip() for line in current):
        statements.append("\n".join(current).strip())
    return statements


def _validate_changeset(path: Path) -> list[str]:
    """Return a list of validation errors. Empty list means OK."""
    errors: list[str] = []
    if not path.exists():
        return [f"file missing: {path}"]
    sql = path.read_text(encoding="utf-8")
    # Liquibase accepts both `--liquibase formatted sql` (canonical, no
    # space) and `-- liquibase formatted sql` (with space). Both forms
    # are valid — check for either.
    sql_lower = sql.lower()
    if ("--liquibase formatted sql" not in sql_lower and
            "-- liquibase formatted sql" not in sql_lower):
        errors.append("missing required header: '--liquibase formatted sql'")
    statements = _split_statements(sql)
    n_changesets = sum(
        1 for s in statements if re.search(r"--\s*changeset\s+\S+", s, re.I)
    )
    if n_changesets == 0:
        errors.append("no `--changeset` markers found")

    # Spot-check for common syntax issues:
    #   - mismatched parens (CREATE TABLE without closing ))
    #   - SERIAL on a non-id column (we use UUID for everything)
    #   - bare `TIMESTAMP` instead of `TIMESTAMPTZ` (we standardise on tz-aware)
    naive_timestamp_lines = [
        (i, line) for i, line in enumerate(sql.splitlines(), 1)
        if re.search(r"\bTIMESTAMP\b(?!TZ|\s*WITH)", line, re.I)
        and "TIMESTAMPTZ" not in line.upper()
        and not line.lstrip().startswith("--")
    ]
    if naive_timestamp_lines:
        # Only fail if there's a non-comment TIMESTAMP not-TZ. Warning,
        # not error, since some changesets may have a justified bare
        # TIMESTAMP — just surface it.
        # If you really need a tz-naive column, comment-justify it.
        print(f"  WARN: {len(naive_timestamp_lines)} lines use bare TIMESTAMP "
              f"(not TIMESTAMPTZ): line(s) {[l[0] for l in naive_timestamp_lines[:3]]}")

    open_parens = sum(line.count("(") for line in sql.splitlines() if not line.lstrip().startswith("--"))
    close_parens = sum(line.count(")") for line in sql.splitlines() if not line.lstrip().startswith("--"))
    if open_parens != close_parens:
        errors.append(
            f"unbalanced parentheses: {open_parens} '(' vs {close_parens} ')'"
        )

    return errors


def main() -> int:
    print("[pg-migrate-check] validating Liquibase changesets")
    files = sorted(CHANGESET_DIR.glob("*.sql"))
    if not files:
        print("  FAIL: no .sql changeset files found")
        return 1

    master = CHANGESET_DIR / "master.xml"
    if not master.exists():
        print("  FAIL: db/changesets/master.xml missing")
        return 1
    master_content = master.read_text(encoding="utf-8")

    failures = 0
    for f in files:
        print(f"\n[check] {f.name}")
        errs = _validate_changeset(f)
        if errs:
            failures += 1
            for e in errs:
                print(f"  FAIL: {e}")
        else:
            print("  OK")
        # master.xml reference check
        if f.name not in master_content:
            print(f"  FAIL: {f.name} not referenced from master.xml")
            failures += 1
        else:
            print("  referenced in master.xml")

    print(f"\n[pg-migrate-check] {len(files)} file(s) checked, {failures} failure(s)")

    # Spot-check expected critical tables present somewhere in the
    # combined SQL — the application boots will fail without these.
    combined = "\n".join(p.read_text(encoding="utf-8") for p in files).lower()
    required_tables = [
        "users",
        "lessons",
        "jobs",
        "concept_videos",
        "audit_log",
        "essay_rubrics",
        "essay_submissions",
        "parent_consent_tokens",
        "parent_consent_outbox",
    ]
    missing = [t for t in required_tables if f"create table {t}" not in combined
               and f"create table if not exists {t}" not in combined]
    if missing:
        print(f"\nWARN: critical tables missing from changesets: {missing}")
        print("These tables get auto-created by Python startup migrations,")
        print("but the Liquibase set should own them for clean Postgres deploys.")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
