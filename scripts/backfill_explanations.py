"""prod-194 — backfill AI answer-explanations onto the question bank.

Finds questions with no explanation, generates a concise worked-solution
via Claude (Haiku, ~Rs 0.01 each), and caches it into
`question_bank.explanation`. Curated explanations (e.g. the SAT seed) are
never touched — the worker only reads `list_without_explanation()`.

Usage:
    python scripts/backfill_explanations.py --limit 20
    python scripts/backfill_explanations.py --board cbse --limit 50
    python scripts/backfill_explanations.py --subject mathematics --limit 30
    python scripts/backfill_explanations.py --dry-run --limit 5   # no Claude calls
    python scripts/backfill_explanations.py --all                 # whole bank

Needs ANTHROPIC_API_KEY in env / .env. ~Rs 0.01 / question on Haiku; a
full ~2500-question backfill is ~Rs 25 and a few minutes. Idempotent and
resumable — re-running only picks up questions still missing an
explanation.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="max questions this run")
    ap.add_argument("--board", default=None, help="filter, e.g. cbse / jee / neet")
    ap.add_argument("--subject", default=None, help="filter, e.g. mathematics")
    ap.add_argument("--dry-run", action="store_true", help="list work, make no calls")
    ap.add_argument("--all", action="store_true", help="ignore --limit; whole bank")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))

    from padhai import explain
    from padhai import question_bank as qb

    limit = 2000 if args.all else args.limit
    todo = qb.list_without_explanation(
        limit=limit, board=args.board, subject=args.subject,
    )
    print(
        f"questions without explanation: {len(todo)} "
        f"(board={args.board}, subject={args.subject})"
    )
    if args.dry_run:
        for q in todo[:10]:
            print(f"  [would] {q.board}/{q.subject}: {q.question_text[:60]}")
        print(f"dry-run: would generate {len(todo)} explanation(s)")
        return 0

    done, failed = 0, 0
    for q in todo:
        try:
            text = explain.generate_explanation(
                question_text=q.question_text, options=q.options,
                correct_answer=q.correct_answer, subject=q.subject,
            )
            if text:
                qb.set_explanation(q.id, text)
                done += 1
                print(f"  [ok]   {q.board}/{q.subject}: {text[:58]}")
            else:
                failed += 1
                print(f"  [skip] {q.id}: empty explanation")
        except Exception as e:
            failed += 1
            print(f"  [fail] {q.id}: {str(e)[:80]}")

    print("=" * 60)
    print(f"explained: {done}   failed: {failed}")
    with contextlib.suppress(Exception):
        print("coverage:", qb.explanation_coverage_stats())
    return 0


if __name__ == "__main__":
    sys.exit(main())
