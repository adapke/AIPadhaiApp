"""prod-179 — One-command content seeder for a fresh deploy.

A fresh production deploy starts with an EMPTY module database (the
SQLite file at PADHAI_DB_PATH on the persistent disk, or the module
tables in Postgres). None of the curated content — concept videos,
past-year questions, real-world examples — exists until it's seeded.

All of that content is reconstructible from files committed to this
repo:
  • PYQs            → data/pyq/*.json  (~2,478 questions)
  • concept videos  → scripts/build_concept_videos.py (22 seeds)
  • verified tier   → scripts/auto_curate_videos.py (promotes seeds)
  • real-world ex.   → scripts/seed_real_world_examples.py (48 examples)

This script runs each seeder in order, idempotently (re-running skips
existing rows), and prints a final count so you can confirm the deploy
is populated.

Usage (on the production instance, once, after first deploy):
    python -m scripts.seed_all_content
    python -m scripts.seed_all_content --skip-curate   # skip network calls

Each sub-seeder is run as a subprocess so its own CLI + idempotency
logic is reused unchanged. A failure in one step is reported but does
not abort the others (so a flaky network call during the curator step
doesn't block PYQ seeding).

Exit codes:
    0 — all steps succeeded
    1 — at least one step failed (details printed)
"""
from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(label: str, args: list[str]) -> tuple[bool, str]:
    """Run a sub-seeder; return (ok, tail-of-output)."""
    print(f"\n{'=' * 64}\n[seed-all] {label}\n{'=' * 64}")
    try:
        proc = subprocess.run(
            [sys.executable, *args],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"[seed-all] {label}: TIMEOUT after 900s")
        return False, "timeout"
    out = (proc.stdout or "") + (proc.stderr or "")
    # Print the last ~15 lines so the deploy log stays readable.
    tail = "\n".join(out.strip().splitlines()[-15:])
    print(tail)
    ok = proc.returncode == 0
    print(f"[seed-all] {label}: {'OK' if ok else f'FAILED (exit {proc.returncode})'}")
    return ok, tail


def _content_counts() -> dict:
    """Read final row counts from whichever DB the modules use."""
    sys.path.insert(0, str(REPO_ROOT))
    counts = {}
    try:
        from padhai import concept_videos as cv
        cv.migrate()
        st = cv.stats()
        counts["concept_videos"] = st.get("total", "?")
        counts["concept_videos_verified"] = (
            st.get("by_quality_tier", {}).get("verified", "?")
        )
    except Exception as e:
        counts["concept_videos"] = f"(err {type(e).__name__})"
    try:
        from padhai import question_bank as qb
        qb.migrate()
        # question_bank has no stats(); count via a cheap query helper.
        import sqlite3

        from padhai import db as _db
        conn = sqlite3.connect(str(_db.sqlite_path()))
        with contextlib.suppress(Exception):
            counts["question_bank"] = conn.execute(
                "SELECT COUNT(*) FROM question_bank"
            ).fetchone()[0]
        conn.close()
    except Exception as e:
        counts["question_bank"] = f"(err {type(e).__name__})"
    try:
        from padhai import concept_examples as ex
        ex.migrate()
        counts["concept_examples_approved"] = ex.stats().get("approved", "?")
    except Exception as e:
        counts["concept_examples_approved"] = f"(err {type(e).__name__})"
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-curate", action="store_true",
                    help="Skip the auto-curator step (no network calls).")
    ap.add_argument("--skip-pyq", action="store_true",
                    help="Skip PYQ import (already large; faster reseeds).")
    args = ap.parse_args()

    steps: list[tuple[str, list[str]]] = []
    if not args.skip_pyq:
        steps.append((
            "Import PYQs (data/pyq/*.json)",
            ["-m", "scripts.import_pyq", "data/pyq/*.json"],
        ))
    steps.append((
        "Seed concept videos",
        ["scripts/build_concept_videos.py"],
    ))
    if not args.skip_curate:
        steps.append((
            "Auto-curate videos (iframe + oembed health-check)",
            ["scripts/auto_curate_videos.py", "--sleep-ms", "150"],
        ))
    steps.append((
        "Seed real-world examples",
        ["scripts/seed_real_world_examples.py"],
    ))

    results = []
    for label, cmd in steps:
        ok, _ = _run(label, cmd)
        results.append((label, ok))

    print(f"\n{'=' * 64}\n[seed-all] SUMMARY\n{'=' * 64}")
    for label, ok in results:
        print(f"  {'[OK]  ' if ok else '[FAIL]'} {label}")

    print("\n[seed-all] final content counts:")
    for k, v in _content_counts().items():
        print(f"  {k:28s} = {v}")

    failed = [label for label, ok in results if not ok]
    if failed:
        print(f"\n[seed-all] {len(failed)} step(s) failed — review output above.")
        return 1
    print("\n[seed-all] all content seeded. Deploy is populated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
