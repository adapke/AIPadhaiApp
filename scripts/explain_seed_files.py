"""prod-195 — write AI answer-explanations back into the PYQ seed JSON.

Unlike `scripts/backfill_explanations.py` (which caches explanations into
the DB of a running/deployed server), this writes the `explanation` field
directly into the `data/pyq/*.json` source files — so the explanations
are version-controlled and re-imported into every environment on deploy,
exactly like the hand-curated SAT explanations.

Use it to ship explanations for the flagship Indian exams; the long tail
of files can be run later (same command, more globs). Idempotent: a
question that already has a non-empty `explanation` is skipped, so
re-running only fills gaps. Reformats touched files with json.dump
(indent=2) — the import re-reads regardless.

Usage:
    python scripts/explain_seed_files.py data/pyq/jee_main_2024_*.json
    python scripts/explain_seed_files.py --dry-run data/pyq/neet_2024_*.json
    python scripts/explain_seed_files.py "data/pyq/*.json"   # whole seed

Needs ANTHROPIC_API_KEY in env / .env. ~Rs 0.01 / question on Haiku.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="JSON file(s) or globs")
    ap.add_argument("--dry-run", action="store_true",
                    help="count gaps, write nothing, make no Claude calls")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))
    from padhai import explain

    files: list[str] = []
    for p in args.paths:
        matched = glob.glob(p)
        files.extend(matched or [p])

    total_added, total_fail = 0, 0
    for path in sorted(files):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [skip] {path}: {e}")
            continue
        default_subject = data.get("default_subject")
        added = 0
        for q in data.get("questions", []):
            if (q.get("explanation") or "").strip():
                continue
            if args.dry_run:
                added += 1
                continue
            try:
                text = explain.generate_explanation(
                    question_text=q.get("question_text", ""),
                    options=q.get("options"),
                    correct_answer=q.get("correct_answer"),
                    subject=q.get("subject") or default_subject,
                )
            except Exception as e:
                total_fail += 1
                print(f"  [fail] {os.path.basename(path)}: {str(e)[:70]}")
                continue
            if text:
                q["explanation"] = text
                added += 1
        if added and not args.dry_run:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        verb = "would add" if args.dry_run else "added"
        print(f"  {verb:>9} {added:3d}  {os.path.basename(path)}")
        total_added += added

    print("=" * 56)
    print(f"total {'would add' if args.dry_run else 'added'}: {total_added}"
          f"   failed: {total_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
