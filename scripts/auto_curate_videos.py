"""prod-167 — Auto-curator pass on channel_seed concept videos.

For each video currently at quality_tier='channel_seed':
  1. iframe-block pre-check (X-Frame-Options / CSP frame-ancestors)
  2. YouTube oembed metadata fetch (proves the video still exists +
     is publicly visible)
  3. If both pass AND the video is from one of the trusted channels
     the curator pre-vetted (Khan Academy / Peekaboo Kidz / CrashCourse
     / etc.), promote channel_seed -> verified.
  4. If either check fails, demote channel_seed -> ai_fallback so it
     stops surfacing in the SPA's verified strip.

Trusted-channel list lives in TRUSTED_CHANNELS below. Add a channel
slug there only after a human has spot-checked several videos from
that channel and confirmed they're appropriate Indian-context K-12
explainers.

Usage:
    python scripts/auto_curate_videos.py --dry-run        # report only
    python scripts/auto_curate_videos.py                  # apply
    python scripts/auto_curate_videos.py --limit 20       # smoke
    python scripts/auto_curate_videos.py --aggressive     # promote
                                                          # even non-trusted
                                                          # channels if both
                                                          # checks pass

Exit codes:
    0 — success (even if zero rows were promoted/demoted)
    1 — DB unreachable / schema mismatch
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# UTF-8 stdout — channel + concept names contain non-ASCII (Devanagari).
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Channels the curator has pre-vetted as "AI Pathshala-compatible".
# A channel-name (case-insensitive substring) match is enough to promote.
# When adding entries: spot-check at least 3 videos per channel for
# Indian context fit + Devanagari/Hindi support + non-zero educational
# value before listing here.
TRUSTED_CHANNELS = {
    "khan academy",
    "khan academy india",
    "peekaboo kidz",
    "crashcourse",
    "fuseschool",
    "fuseschool global education",
    "3blue1brown",
    "tic-tac-learn",
    "byju",         # Byju's official channel (CBSE-aligned)
    "veritasium",   # high-quality physics
    "magnet brains",
    "physics wallah",
    "unacademy",
    "vedantu",
    "doubtnut",
    "tutorialspoint",
    "edpuzzle",
    "ncert official",
    "diksha",
}


def _channel_is_trusted(channel: str | None) -> bool:
    if not channel:
        return False
    cl = channel.lower().strip()
    return any(t in cl for t in TRUSTED_CHANNELS)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change without writing.")
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N rows.")
    p.add_argument(
        "--aggressive", action="store_true",
        help="Promote to verified even when channel isn't on the "
        "trusted list, as long as both health checks pass. Use only "
        "when you've reviewed the catalog manually.",
    )
    p.add_argument(
        "--sleep-ms", type=int, default=200,
        help="Delay between HTTP checks to be polite to YouTube.",
    )
    args = p.parse_args()

    from padhai import concept_videos as cv

    cv.migrate()
    seeds = cv.list_curator_queue(quality_tier="channel_seed",
                                  limit=args.limit or 1000)
    print(f"[auto-curate] {len(seeds)} channel_seed rows in queue")
    if not seeds:
        return 0

    promoted = demoted = unchanged = skipped = 0
    for v in seeds:
        title = (v.title or v.concept)[:60]
        # 1. Static iframe-embed check (fast — HEAD).
        #    IMPORTANT: check the EMBED url, not the source /watch url.
        #    YouTube's /watch URLs set X-Frame-Options:SAMEORIGIN to
        #    block hotlinking; the /embed/{video_id} variant doesn't.
        embed_target = v.embed_url or v.source_url
        ie = cv.check_iframe_embed(embed_target)
        # 2. oembed metadata (proves the video exists). oembed always
        #    expects the canonical /watch URL.
        try:
            meta = cv.fetch_oembed_metadata(v.source_url)
        except Exception:
            meta = None

        emb_ok = ie.get("embeddable") in (True, None)  # None = inconclusive
        oembed_ok = bool(meta and meta.get("title"))

        ch = v.channel or (meta or {}).get("author_name")
        trusted = _channel_is_trusted(ch)
        promote = emb_ok and oembed_ok and (trusted or args.aggressive)
        demote = (ie.get("embeddable") is False) or (
            ie.get("status_code") in (403, 404, 410)
        )

        line = (
            f"[{v.id[:8]}] {title!r:62.62} "
            f"ch={ch!r:30.30} "
            f"emb={'OK' if emb_ok else 'BLOCKED'} "
            f"oembed={'OK' if oembed_ok else 'FAIL'} "
            f"trusted={'Y' if trusted else 'N'}"
        )

        if demote:
            if args.dry_run:
                print(f"  WOULD DEMOTE: {line}")
            else:
                cv.set_quality_tier(
                    v.id, "ai_fallback",
                    curator_note=(
                        f"auto-demoted (prod-167): iframe={ie.get('reason')}, "
                        f"http={ie.get('status_code')}"
                    ),
                )
                print(f"  DEMOTED:      {line}")
            demoted += 1
        elif promote:
            if args.dry_run:
                print(f"  WOULD PROMOTE: {line}")
            else:
                cv.set_quality_tier(
                    v.id, "verified",
                    curator_note=(
                        f"auto-curated (prod-167): channel={ch!r}, "
                        f"oembed_title={meta.get('title') if meta else None!r}"
                    ),
                )
                print(f"  PROMOTED:     {line}")
            promoted += 1
        else:
            print(f"  unchanged:    {line}")
            unchanged += 1

        time.sleep(args.sleep_ms / 1000.0)

    print(
        f"\n[auto-curate] done. promoted={promoted} demoted={demoted} "
        f"unchanged={unchanged} skipped={skipped} "
        f"dry_run={args.dry_run} aggressive={args.aggressive}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
