"""prod-185 — add BRAND-NEW verified concept videos to the catalog.

prod-184 brought the catalog to 69 verified by sourcing real URLs for the
genuinely-new channel_seed rows; that pool is now exhausted. To grow
toward 100+, this seeds concepts that weren't in the catalog at all —
high-value NEET/JEE/CBSE topics — as fresh `verified` rows.

Each entry's YouTube id was found via web search on a recognised
educational channel and is oembed-gated here (live + embeddable) before
insert; title + channel come from the oembed response so we store real
metadata. Idempotent via concept_videos.upsert() on the natural key.

After running, re-run scripts/export_concept_seed.py so the new rows land
in data/concept_videos_seed.json and ship to production.

Usage:
    python scripts/seed_new_concepts.py            # verify + insert
    python scripts/seed_new_concepts.py --dry-run  # verify only
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

# concept, subject, board, grade_min, grade_max, youtube_id.
# Brand-new concepts (not previously in the catalog), web-sourced from
# recognised educational channels; oembed-gated at runtime.
NEW_CONCEPTS = [
    ("Thermodynamics", "physics", "CBSE", 11, 12, "IECrvO3wjP0"),
    ("Electromagnetic Induction", "physics", "CBSE", 12, 12, "-l1rq5z62w0"),
    ("Simple Harmonic Motion", "physics", "CBSE", 11, 12, "jxstE6A_CYQ"),
    ("Work, Energy and Power", "physics", "CBSE", 11, 11, "qBtNdn7j5pg"),
    ("Refraction of Light", "physics", "CBSE", 10, 10, "19xlGU6QuEA"),
    ("Coulomb's Law", "physics", "CBSE", 12, 12, "mUQqzhOYnDU"),
    ("Electrochemistry", "chemistry", "CBSE", 12, 12, "PC1u_KkEEL4"),
    ("Redox Reactions", "chemistry", "CBSE", 9, 11, "lQ6FBA1HM3s"),
    # --- prod-186 batch: brand-new biology ---
    ("Mendel's Laws of Inheritance", "biology", "CBSE", 10, 12, "Y8oJaqZ0nN8"),
    ("DNA Structure", "biology", "CBSE", 11, 12, "s_aVwXtboTU"),
    ("Human Nervous System", "biology", "CBSE", 10, 11, "4zfKhChoIus"),
    ("Evolution by Natural Selection", "biology", "CBSE", 10, 12, "aTftyFboC_M"),
    ("Cell Organelles", "biology", "CBSE", 9, 11, "8IlzKri08kk"),
    ("Human Excretory System", "biology", "CBSE", 10, 10, "VAzAnGeszl8"),
    ("Transport in Plants", "biology", "CBSE", 10, 11, "jtuX7H05tmQ"),
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
    print(f"seeding {len(NEW_CONCEPTS)} brand-new concepts…")
    print("=" * 64)
    for concept, subject, board, gmin, gmax, vid in NEW_CONCEPTS:
        meta = _oembed(vid)
        if not meta:
            print(f"  [dead]  {concept[:30]:32} {vid} failed oembed")
            dead += 1
            continue
        label = f"{concept[:30]:32} [{meta['author'][:18]}] {meta['title'][:34]}"
        if args.dry_run:
            print(f"  [would] {label}")
            added += 1
            continue
        cv.upsert(
            concept=concept, source="youtube",
            source_url=f"https://www.youtube.com/watch?v={vid}",
            title=meta["title"], channel=meta["author"],
            subject=subject, board=board, grade_min=gmin, grade_max=gmax,
            quality_tier="verified", language="en",
            curator_note="prod-185: brand-new concept, oembed-verified",
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
