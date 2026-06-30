"""prod-195/196 — write AI answer-explanations back into the PYQ seed JSON.

Unlike `scripts/backfill_explanations.py` (which caches explanations into
the DB of a running/deployed server), this writes the `explanation` field
directly into the `data/pyq/*.json` source files — so the explanations
are version-controlled and re-imported into every environment on deploy,
exactly like the hand-curated SAT explanations.

Idempotent: a question that already has a non-empty `explanation` is
skipped, so re-running only fills gaps (also makes it resumable after a
rate-limit blip). Each touched file is rewritten with json.dump(indent=2)
and the import re-reads it regardless of formatting.

Concurrency: `--workers N` generates a file's missing explanations in
parallel against one shared (thread-safe) Anthropic client — ~6 workers
turns a ~1-hour whole-bank sweep into ~10 minutes. Files are still
written one at a time, after their batch completes, so progress is saved
per file.

Usage:
    python scripts/explain_seed_files.py data/pyq/jee_main_2024_*.json
    python scripts/explain_seed_files.py --dry-run "data/pyq/*.json"
    python scripts/explain_seed_files.py --workers 6 "data/pyq/*.json"

Needs ANTHROPIC_API_KEY in env / .env. ~Rs 0.01 / question on Haiku.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
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
    ap.add_argument("--workers", type=int, default=6,
                    help="parallel generations per file (default 6)")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))
    from padhai import explain

    # One shared, thread-safe Anthropic client for the whole run (avoids
    # constructing thousands of clients). Best-effort — generate_explanation
    # falls back to its own if this is None.
    shared_client = None
    if not args.dry_run:
        with contextlib.suppress(Exception):
            from anthropic import Anthropic
            shared_client = Anthropic()

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
        questions = data.get("questions", [])
        missing = [
            (i, q) for i, q in enumerate(questions)
            if not (q.get("explanation") or "").strip()
        ]
        if args.dry_run:
            print(f"  would add {len(missing):3d}  {os.path.basename(path)}")
            total_added += len(missing)
            continue
        if not missing:
            continue

        def _gen(item, subj=default_subject):
            i, q = item
            try:
                text = explain.generate_explanation(
                    question_text=q.get("question_text", ""),
                    options=q.get("options"),
                    correct_answer=q.get("correct_answer"),
                    subject=q.get("subject") or subj,
                    client=shared_client,
                )
                return i, text, None
            except Exception as e:  # collect failures, don't abort the sweep
                return i, None, str(e)[:70]

        added, fail = 0, 0
        with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            for i, text, err in ex.map(_gen, missing):
                if text:
                    questions[i]["explanation"] = text
                    added += 1
                else:
                    fail += 1
                    if err:
                        print(f"  [fail] {os.path.basename(path)}[{i}]: {err}")
        if added:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
        print(f"  added {added:3d}  ({fail} failed)  {os.path.basename(path)}")
        total_added += added
        total_fail += fail

    print("=" * 56)
    print(f"total {'would add' if args.dry_run else 'added'}: {total_added}"
          f"   failed: {total_fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
