"""prod-192 — seed SAT (US Digital SAT) concept videos into the catalog.

Part of the SAT exam section. Adds real, recognised SAT-prep YouTube
videos as `verified` concept_videos rows with board="SAT". Each id is
oembed-gated here (live + embeddable) before insert; title + channel
come from the oembed response so we store real metadata. Idempotent via
concept_videos.upsert() on the natural key.

The /sat hub page embeds a curated subset of these by id directly;
seeding them here additionally surfaces them in the concept catalog and
ships them via data/concept_videos_seed.json.

After running, re-run scripts/export_concept_seed.py so the new rows land
in data/concept_videos_seed.json and ship to production.

Usage:
    python scripts/seed_sat_videos.py            # verify + insert
    python scripts/seed_sat_videos.py --dry-run  # verify only
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# concept, subject, grade_min, grade_max, youtube_id.
# All board="SAT". Web-sourced from recognised SAT-prep channels;
# oembed-gated at runtime. Subjects align with practice_test SAT
# subjects (sat_math / sat_reading_writing) plus sat_overview.
SAT_VIDEOS = [
    # --- Overview / format / scoring ---
    ("SAT — Digital Format, Structure & Scoring", "sat_overview", 11, 12, "aNjoBgqrKvE"),
    ("SAT — Khan Academy Official Prep Overview", "sat_overview", 11, 12, "cSD0qVybO9s"),
    # --- Math ---
    ("SAT Math — Full Review", "sat_math", 11, 12, "ty7B8VyCnFY"),
    ("SAT Math — Strategies for an 800", "sat_math", 11, 12, "Dp291sIwtYk"),
    ("SAT Math — Geometry and Trigonometry", "sat_math", 11, 12, "Vwtux_sW9Zs"),
    ("SAT Math — Linear Equations (Algebra)", "sat_math", 11, 12, "wBA0TpNy0Wo"),
    ("SAT Math — Systems of Linear Equations", "sat_math", 11, 12, "iYs57D4Ko0s"),
    ("SAT Math — Desmos Calculator Guide", "sat_math", 11, 12, "-pGNBb8M3LQ"),
    ("SAT Math — Desmos on the Digital SAT", "sat_math", 11, 12, "Vfi0f5_5PUg"),
    # --- Reading & Writing ---
    ("SAT Reading & Writing — Study Guide", "sat_reading_writing", 11, 12, "1EHXD2eVKzA"),
    ("SAT Reading & Writing — Tips to Break 1500", "sat_reading_writing", 11, 12, "FS_nvzsoIyE"),
    ("SAT Reading & Writing — Question Types & Strategies", "sat_reading_writing", 11, 12, "8PrSFbEJXvY"),
    ("SAT Grammar — Every Rule in 15 Minutes", "sat_reading_writing", 11, 12, "NLz8CRdMvuI"),
    ("SAT Grammar — 5 Hacks for a Top English Score", "sat_reading_writing", 11, 12, "4jgnbFnXiYs"),
]


def _oembed(video_id: str) -> dict | None:
    url = (
        "https://www.youtube.com/oembed?url="
        f"https://www.youtube.com/watch?v={video_id}&format=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
            return {"title": d.get("title", ""), "author": d.get("author_name", "")}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))

    from padhai import concept_videos as cv

    added, dead = 0, 0
    print(f"seeding {len(SAT_VIDEOS)} SAT concept videos…")
    print("=" * 64)
    for concept, subject, gmin, gmax, vid in SAT_VIDEOS:
        meta = _oembed(vid)
        if not meta:
            print(f"  [dead]  {concept[:34]:36} {vid} failed oembed")
            dead += 1
            continue
        label = f"{concept[:34]:36} [{meta['author'][:16]}] {meta['title'][:30]}"
        if args.dry_run:
            print(f"  [would] {label}")
            added += 1
            continue
        cv.upsert(
            concept=concept, source="youtube",
            source_url=f"https://www.youtube.com/watch?v={vid}",
            title=meta["title"], channel=meta["author"],
            subject=subject, board="SAT", grade_min=gmin, grade_max=gmax,
            quality_tier="verified", language="en",
            curator_note="prod-192: SAT exam section, oembed-verified",
        )
        print(f"  [added] {label}")
        added += 1

    print("=" * 64)
    print(f"{'would add' if args.dry_run else 'added'}: {added}   dead-skipped: {dead}")
    with contextlib.suppress(Exception):
        st = cv.stats()
        print(f"catalog now: {st.get('by_quality_tier')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
