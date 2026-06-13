"""prod-78 — Print the curator-stats JSON to stdout.

Used by `make stats` and by ops scripts that want to track curator
throughput without going through the admin HTTP endpoint.

Reads from whatever DB `padhai.db.sqlite_path()` resolves to (env
override: PADHAI_DB_PATH). No auth needed since it runs locally.

Usage:
    PYTHONPATH=. python scripts/print_curator_stats.py            # 30-day window
    PYTHONPATH=. python scripts/print_curator_stats.py --days 7
    PYTHONPATH=. python scripts/print_curator_stats.py --days 365 --pretty
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--days", type=int, default=30,
        help="window for *_recent counters (default: 30)",
    )
    p.add_argument(
        "--pretty", action="store_true",
        help="indent JSON for human reading",
    )
    args = p.parse_args()

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from padhai import concept_videos as cv

    data = cv.curator_stats(since_days=args.days)
    json.dump(
        data, sys.stdout,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
