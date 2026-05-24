"""CLI for the pre-render manifest. Submits a batch of (image × language
× level) lessons to Claude's Batch API at 50% off, polls until done,
and writes results to the lesson cache.

Usage:
    # Submit + wait + cache in one shot:
    python -m scripts.prerender \\
        --images scans/ncert_class10_ch3 \\
        --languages en hi ta \\
        --levels middle secondary

    # Submit only, write the batch id to stdout for later polling:
    python -m scripts.prerender --images scans/ --submit-only

    # Resume an earlier batch:
    python -m scripts.prerender --images scans/ --resume <batch_id>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import LEVEL_GUIDANCE, SUPPORTED_LANGUAGES
from padhai.prerender import fetch_results, submit_batch, wait_for_batch


def discover_images(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    images: list[Path] = []
    for ext in ("*.png", "*.jpg", "*.jpeg"):
        images.extend(root.rglob(ext))
    return sorted(images)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="padhai-prerender",
        description="Pre-render the lesson cache for a directory of pages "
                    "via Claude's Batch API (50% off list price).",
    )
    parser.add_argument(
        "--images", type=Path, required=True,
        help="path to a directory of page images (recursively), or a single image",
    )
    parser.add_argument(
        "--languages", nargs="+", default=["en"],
        choices=sorted(SUPPORTED_LANGUAGES),
        help="narration languages to pre-render (default: en)",
    )
    parser.add_argument(
        "--levels", nargs="+", default=["middle"],
        choices=sorted(LEVEL_GUIDANCE),
        help="difficulty levels to pre-render (default: middle)",
    )
    parser.add_argument(
        "--cache-dir", type=Path,
        help="cache directory (default: $PADHAI_CACHE_DIR or ~/.padhai/cache)",
    )
    parser.add_argument(
        "--submit-only", action="store_true",
        help="submit the batch and exit; use --resume later to fetch results",
    )
    parser.add_argument(
        "--resume", metavar="BATCH_ID",
        help="skip submission, fetch results from an existing batch id",
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=60.0,
        help="polling interval while waiting (default: 60)",
    )
    args = parser.parse_args(argv)

    images = discover_images(args.images)
    if not images:
        print(f"error: no images found under {args.images}", file=sys.stderr)
        return 2

    print(f"pre-render: {len(images)} pages × {len(args.languages)} languages "
          f"× {len(args.levels)} levels = {len(images) * len(args.languages) * len(args.levels)} requests")
    print(f"  estimated cost @ ₹5/lesson list, 50% batch discount: "
          f"₹{len(images) * len(args.languages) * len(args.levels) * 2.5:.0f}")

    cache = Cache(root=args.cache_dir)

    if args.resume:
        batch_id = args.resume
        print(f"resuming batch {batch_id} (skipping submission)")
    else:
        print("submitting batch to Anthropic Batch API...")
        batch_id = submit_batch(
            image_paths=images,
            languages=args.languages,
            levels=args.levels,
        )
        print(f"  batch_id: {batch_id}")
        if args.submit_only:
            print("--submit-only set; resume with:")
            print(f"  python -m scripts.prerender --images {args.images} "
                  f"--languages {' '.join(args.languages)} "
                  f"--levels {' '.join(args.levels)} --resume {batch_id}")
            return 0

    def _progress(batch):
        rc = batch.request_counts
        print(f"  status={batch.processing_status} "
              f"processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}")

    print("waiting for batch to finish (may take up to 24h; usually <1h)...")
    wait_for_batch(batch_id, poll_seconds=args.poll_seconds, on_status=_progress)

    print("fetching results and populating cache...")
    n = fetch_results(batch_id, image_paths=images, cache=cache)
    print(f"cached {n} lessons")
    print(cache.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
