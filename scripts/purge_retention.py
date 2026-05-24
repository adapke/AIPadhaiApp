"""Periodic source-file retention purge.

Wire this into a Render cron job (one line in render.yaml) or run
locally:

    python -m scripts.purge_retention
    python -m scripts.purge_retention --dry-run

Idempotent + safe to run on multiple workers concurrently — the
`UPDATE ... SET purged_at = ?` claim acts as the lock.

Output is a single JSON line so it's easy to grep / pipe through
to a log shipper (Datadog, Grafana Loki, Sentry breadcrumbs)."""

from __future__ import annotations

import argparse
import json
import sys

from padhai import retention


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be deleted; do not actually delete.",
    )
    args = parser.parse_args()

    if args.dry_run:
        due = retention.list_due(limit=10_000)
        print(json.dumps({
            "dry_run": True,
            "would_delete": len(due),
            "would_free_bytes": sum(d["size_bytes"] or 0 for d in due),
            "sample": due[:5],
            "stats": retention.stats(),
        }, indent=2))
        return 0

    result = retention.purge_due()
    print(json.dumps({
        "scanned": result.scanned,
        "deleted": result.deleted,
        "skipped_keep_forever": result.skipped_keep_forever,
        "skipped_missing_file": result.skipped_missing_file,
        "bytes_freed": result.bytes_freed,
        "stats_after": retention.stats(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
