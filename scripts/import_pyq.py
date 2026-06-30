#!/usr/bin/env python3
"""PYQ ingest — load Previous Year Questions into the `question_bank` table.

JEE / NEET / UPSC students treat PYQs as table stakes. The
`question_bank.upsert()` API was always there but no batch loader
existed. This script bridges JSON / CSV files to it.

Input format (one JSON file per exam-year batch):

    {
      "source": "jee_main_2024_jan",      // free-form citation
      "default_board": "jee",
      "default_grade": 12,
      "default_subject": "mathematics",
      "default_year": 2024,
      "default_paper": "main",
      "questions": [
        {
          "question_text": "...",
          "options": ["...", "...", "...", "..."],   // null for free-form
          "correct_answer": "B",                       // letter or text
          "chapter": "Calculus",
          "topic_tags": ["limits", "continuity"],
          "difficulty": "medium",                      // easy | medium | hard
          "marks": 4
        },
        ...
      ]
    }

`default_*` fields are per-file defaults; each question can override.
Idempotent — re-running is safe (upsert keyed by natural fingerprint).

Usage:
    python scripts/import_pyq.py data/pyq/jee_main_2024_jan.json
    python scripts/import_pyq.py data/pyq/*.json
    python scripts/import_pyq.py --dry-run data/pyq/jee_main_2024_jan.json

Exit code: 0 on full success, 1 on any per-row error.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

# Make `padhai` importable when running directly from scripts/
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def load_file(path: Path) -> dict:
    """Load + validate one batch file. Raises ValueError on malformed."""
    if not path.is_file():
        raise ValueError(f"not a file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be an object")
    if "questions" not in data or not isinstance(data["questions"], list):
        raise ValueError(f"{path}: missing or invalid 'questions' array")
    return data


def merge_defaults(q: dict, defaults: dict) -> dict:
    """Per-question dict with file-level defaults applied. Question
    values win when both exist."""
    return {
        "board":          q.get("board",          defaults.get("default_board")),
        "grade":          q.get("grade",          defaults.get("default_grade")),
        "subject":        q.get("subject",        defaults.get("default_subject")),
        "chapter":        q.get("chapter",        defaults.get("default_chapter")),
        "year":           q.get("year",           defaults.get("default_year")),
        "paper":          q.get("paper",          defaults.get("default_paper")),
        "question_text":  q.get("question_text"),
        "options":        q.get("options"),
        "correct_answer": q.get("correct_answer"),
        "marks":          q.get("marks", 1),
        "difficulty":     q.get("difficulty"),
        "topic_tags":     q.get("topic_tags") or [],
        "source":         defaults.get("source"),
        "explanation":    q.get("explanation"),  # prod-193 — optional per-question
    }


def import_batch(
    data: dict, *, dry_run: bool = False,
) -> tuple[int, list[str]]:
    """Upsert each question. Returns (count_loaded, errors)."""
    from padhai import question_bank
    loaded = 0
    errors: list[str] = []
    for i, q in enumerate(data["questions"]):
        merged = merge_defaults(q, data)
        # Hard requirements: board, grade, subject, question_text.
        # Use `is None` so grade=0 (UPSC/CAT — not a school grade)
        # is treated as explicitly set, not missing.
        for required in ("board", "grade", "subject", "question_text"):
            val = merged.get(required)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"item {i}: missing required field {required!r}")
                break
        else:
            if dry_run:
                loaded += 1
                continue
            try:
                question_bank.upsert(**merged)
                loaded += 1
            except Exception as e:
                errors.append(f"item {i}: upsert failed: {e}")
    return loaded, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="JSON file(s) or globs")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate + count, do not write to DB")
    args = ap.parse_args()

    # Expand globs the shell didn't
    files: list[Path] = []
    for p in args.paths:
        matched = glob.glob(p)
        if matched:
            files.extend(Path(m) for m in matched)
        else:
            files.append(Path(p))

    if not files:
        print("[import_pyq] no input files matched", file=sys.stderr)
        return 1

    total_loaded = 0
    total_errors: list[str] = []
    for path in files:
        try:
            data = load_file(path)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"[import_pyq] FAIL  {path}: {e}", file=sys.stderr)
            total_errors.append(f"{path}: {e}")
            continue

        loaded, errors = import_batch(data, dry_run=args.dry_run)
        total_loaded += loaded
        for err in errors:
            total_errors.append(f"{path}: {err}")
        verb = "would-load" if args.dry_run else "loaded"
        n_q = len(data["questions"])
        print(
            f"[import_pyq] {verb} {loaded}/{n_q} "
            f"from {path} (source={data.get('source')!r})",
        )

    print(
        f"--- total {'would-load' if args.dry_run else 'loaded'}: "
        f"{total_loaded}; errors: {len(total_errors)}",
    )
    if total_errors:
        for err in total_errors[:10]:
            print(f"    ! {err}", file=sys.stderr)
        if len(total_errors) > 10:
            print(
                f"    ... and {len(total_errors) - 10} more errors",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
