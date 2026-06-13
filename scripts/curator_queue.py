"""prod-42 — Concept-video curator queue CLI.

Lists every concept-video row marked `channel_seed` and prints a
clickable YouTube search URL pre-filled with concept + channel, so a
curator can:

  1. Click the search link → find the actual video on the trusted channel.
  2. Copy the watch URL + title.
  3. Call the admin endpoint to update + verify in one shot:
        POST /api/admin/concept-videos/{id}/update
        Body: {"title": "...", "source_url": "...", "verify": true}

Usage:
    PYTHONPATH=. python scripts/curator_queue.py
    PYTHONPATH=. python scripts/curator_queue.py --tier ai_fallback
    PYTHONPATH=. python scripts/curator_queue.py --tier verified

For environments where stdout doesn't support unicode (Windows cp1252),
pass --ascii to drop the Devanagari rows from output.
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--tier", default="channel_seed",
        choices=["verified", "channel_seed", "ai_fallback"],
    )
    p.add_argument("--limit", type=int, default=200)
    p.add_argument(
        "--ascii", action="store_true",
        help="Skip rows containing non-ASCII (Devanagari, etc.) chars.",
    )
    args = p.parse_args()

    import contextlib
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from padhai import concept_videos as cv

    rows = cv.list_curator_queue(quality_tier=args.tier, limit=args.limit)
    if not rows:
        print(f"[curator] no rows in tier={args.tier!r}")
        return 0

    skipped = 0
    print(f"[curator] {len(rows)} row(s) in tier={args.tier!r}\n")
    for i, r in enumerate(rows, 1):
        line = (
            f"#{i}  id={r.id[:8]}...  concept={r.concept!r}  "
            f"channel={r.channel!r}  language={r.language!r}"
        )
        if args.ascii:
            try:
                line.encode("ascii")
            except UnicodeEncodeError:
                skipped += 1
                continue
        print(line)

        if r.title and not r.title.startswith("["):
            print(f"     current title: {r.title}")
        else:
            print(f"     current title (stub): {r.title}")

        if r.source_url:
            print(f"     current url:   {r.source_url}")

        q = r.concept
        if r.channel:
            q = f"{q} {r.channel}"
        search = (
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(q)
        )
        print(f"     SEARCH:        {search}")
        print()

    if skipped:
        print(f"[curator] skipped {skipped} non-ASCII row(s) (--ascii)")
    print(
        "[curator] To verify a row from a browser session "
        "(admin auth required):\n"
        '    curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \\\n'
        "         -H 'Content-Type: application/json' \\\n"
        "         -d '{\"title\":\"...\",\"source_url\":\"https://www.youtube.com/watch?v=XXX\",\"verify\":true}' \\\n"
        "         http://127.0.0.1:8000/api/admin/concept-videos/<id>/update",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
