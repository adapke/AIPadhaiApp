"""prod-82 — Nightly iframe-health check for verified concept videos.

Walks every row in `concept_videos` with `quality_tier='verified'`,
runs `check_iframe_embed()`, and reports rows whose embed status has
changed (publisher restricted embedding, video deleted, etc.).

This is the audit-loop closer for the curator workflow:
  • prod-67 catches iframe-blocked URLs at curator time.
  • prod-82 catches the much more common case where a previously-OK
    URL silently breaks AFTER it was verified.

Outputs JSON to stdout (machine-readable for cron monitoring) and a
human summary to stderr. Optionally demotes broken rows back to
`channel_seed` so they reappear in the curator queue.

Cron template (paste into crontab -e):
    37 3 * * * cd /opt/padhai && \
        PADHAI_DB_PATH=/var/lib/padhai/jobs.db \
        /usr/bin/python3 scripts/check_verified_iframes.py \
        --auto-demote >> /var/log/padhai-iframe-check.log 2>&1

Usage:
    PYTHONPATH=. python scripts/check_verified_iframes.py
    PYTHONPATH=. python scripts/check_verified_iframes.py --auto-demote
    PYTHONPATH=. python scripts/check_verified_iframes.py --limit 10 --pretty
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--auto-demote", action="store_true",
        help=(
            "Automatically demote rows that now embed-fail to "
            "channel_seed (re-queues them for curator review). "
            "Default: report-only (read-only)."
        ),
    )
    p.add_argument(
        "--limit", type=int, default=0,
        help="Cap the number of rows checked (0 = no cap).",
    )
    p.add_argument(
        "--sleep-ms", type=int, default=200,
        help=(
            "Pause between HEAD requests to be polite to YouTube. "
            "Default 200ms. Set 0 for tests."
        ),
    )
    p.add_argument(
        "--pretty", action="store_true",
        help="Indent the JSON report.",
    )
    args = p.parse_args()

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    from padhai import concept_videos as cv

    # Use the existing curator-queue helper to walk verified rows.
    rows = cv.list_curator_queue(
        quality_tier="verified",
        limit=args.limit if args.limit else 10000,
    )
    started = time.time()
    print(
        f"[iframe-check] starting on {len(rows)} verified row(s) "
        f"(auto_demote={args.auto_demote})",
        file=sys.stderr,
    )

    report = {
        "checked": 0,
        "ok": 0,
        "blocked": 0,
        "inconclusive": 0,
        "demoted": 0,
        "started_at": started,
        "rows": [],
    }

    for i, row in enumerate(rows):
        # Check the EMBED URL (what's actually iframed), not the watch URL.
        # YouTube serves X-Frame-Options on /watch but not on /embed/, so
        # checking source_url would falsely flag every video.
        url_to_check = row.embed_url or row.source_url
        result = cv.check_iframe_embed(url_to_check, timeout_sec=4.0)
        report["checked"] += 1
        entry = {
            "id": row.id,
            "concept": row.concept,
            "channel": row.channel,
            "source_url": row.source_url,
            "embeddable": result.get("embeddable"),
            "reason": result.get("reason"),
            "status_code": result.get("status_code"),
            "x_frame_options": result.get("x_frame_options"),
            "csp_frame_ancestors": result.get("csp_frame_ancestors"),
        }
        if result.get("embeddable") is True:
            report["ok"] += 1
        elif result.get("embeddable") is False:
            report["blocked"] += 1
            entry["action"] = "would_demote"
            if args.auto_demote:
                cv.set_quality_tier(
                    row.id, "channel_seed",
                    curator_note=(
                        f"auto-demoted by nightly check (prod-82): "
                        f"{result.get('reason')}"
                    ),
                )
                entry["action"] = "demoted"
                report["demoted"] += 1
            report["rows"].append(entry)
            print(
                f"[iframe-check] BLOCKED: {row.concept!r} "
                f"({row.id[:8]}...) — {result.get('reason')}",
                file=sys.stderr,
            )
        else:
            report["inconclusive"] += 1
            entry["action"] = "skipped"
            report["rows"].append(entry)
        if args.sleep_ms > 0 and i < len(rows) - 1:
            time.sleep(args.sleep_ms / 1000.0)

    report["elapsed_sec"] = round(time.time() - started, 2)

    print(
        f"[iframe-check] done in {report['elapsed_sec']}s — "
        f"ok={report['ok']} blocked={report['blocked']} "
        f"inconclusive={report['inconclusive']} demoted={report['demoted']}",
        file=sys.stderr,
    )

    json.dump(
        report, sys.stdout,
        indent=2 if args.pretty else None,
        sort_keys=True,
    )
    sys.stdout.write("\n")

    # Exit code signals to cron that something needs attention.
    # 0 = all OK, 1 = at least one row blocked.
    return 1 if report["blocked"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
