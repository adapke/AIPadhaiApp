"""prod-86 — Bulk-import concept-video rows from CSV.

Curators (and ops) can add concept videos in bulk without writing
Python. Pairs with the existing `concept_videos.from_csv_row` +
`bulk_load` helpers — same idempotent upsert semantics, same natural
key (concept_norm + source_url + language).

CSV columns (header row required):
    concept,source,source_url,title,channel,duration_sec,language,
    board,grade_min,grade_max,subject,quality_tier,curator_note

Required: concept, source, source_url, title.
Optional: everything else. Integers (duration_sec / grade_min /
grade_max) accept empty strings (parsed as None).

Usage:
    PYTHONPATH=. python scripts/import_concept_videos.py PATH.csv
    PYTHONPATH=. python scripts/import_concept_videos.py PATH.csv --dry-run
    PYTHONPATH=. python scripts/import_concept_videos.py PATH.csv \\
        --default-quality-tier=channel_seed --default-language=en

Exit codes:
    0 — all rows loaded
    1 — at least one row failed to load (errors printed to stderr)
    2 — file not found / parse error
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("csv_path", help="Path to the CSV file")
    p.add_argument(
        "--dry-run", action="store_true",
        help="Parse + validate without touching the DB.",
    )
    p.add_argument(
        "--default-quality-tier", default=None,
        help=(
            "Fill quality_tier when missing in CSV. "
            "Default: schema default ('verified')."
        ),
    )
    p.add_argument(
        "--default-language", default=None,
        help="Fill language when missing in CSV. Default: 'en'.",
    )
    args = p.parse_args()

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        print(f"[import_cv] file not found: {csv_path}", file=sys.stderr)
        return 2

    from padhai import concept_videos as cv

    raw_rows: list[dict] = []
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                print("[import_cv] CSV has no header row", file=sys.stderr)
                return 2
            required = {"concept", "source", "source_url", "title"}
            missing = required - set(reader.fieldnames)
            if missing:
                print(
                    f"[import_cv] CSV missing required column(s): "
                    f"{sorted(missing)}",
                    file=sys.stderr,
                )
                return 2
            for row in reader:
                # Strip whitespace + apply defaults.
                cleaned = {k: (v or "").strip() for k, v in row.items()}
                if args.default_quality_tier and not cleaned.get("quality_tier"):
                    cleaned["quality_tier"] = args.default_quality_tier
                if args.default_language and not cleaned.get("language"):
                    cleaned["language"] = args.default_language
                # Drop empty optional fields so upsert defaults kick in.
                cleaned = {k: v for k, v in cleaned.items() if v != ""}
                raw_rows.append(cv.from_csv_row(cleaned))
    except (csv.Error, UnicodeDecodeError) as e:
        print(f"[import_cv] CSV parse error: {e}", file=sys.stderr)
        return 2

    print(
        f"[import_cv] parsed {len(raw_rows)} row(s) from {csv_path.name}",
        file=sys.stderr,
    )
    if args.dry_run:
        print("[import_cv] --dry-run: not writing to DB", file=sys.stderr)
        for i, r in enumerate(raw_rows[:5]):
            print(
                f"  preview row {i}: concept={r.get('concept')!r} "
                f"url={r.get('source_url')!r} tier={r.get('quality_tier')!r}",
                file=sys.stderr,
            )
        if len(raw_rows) > 5:
            print(f"  ... +{len(raw_rows) - 5} more rows", file=sys.stderr)
        return 0

    loaded, errors = cv.bulk_load(raw_rows)
    print(
        f"[import_cv] loaded={loaded} errors={len(errors)} "
        f"total_input={len(raw_rows)}",
        file=sys.stderr,
    )
    for err in errors:
        print(f"[import_cv] ERR: {err}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
