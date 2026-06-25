#!/usr/bin/env python3
"""prod-14 — Seed the concept-video catalog with curated entries.

Three quality tiers, all marked honestly:

  * verified       — URL was tested + plays back. The Peekaboo Kidz
                     Newton's First Law URL the user shared sits here.
  * channel_seed   — Channel is trusted (Khan, Peekaboo, FuseSchool,
                     CrashCourse, 3Blue1Brown) but the specific URL
                     needs a curator to spot-check before going live.
                     The seed lists the CHANNEL's search URL so the
                     curator can find the right video.
  * ai_fallback    — No curated content yet; SPA falls back to
                     /explain/video for these concepts.

A startup with 1 person doing curation can move a row from
`channel_seed` → `verified` in ~30 seconds (open URL, confirm it
plays the right concept, update via admin endpoint).

Usage:
  python scripts/build_concept_videos.py            # write all rows
  python scripts/build_concept_videos.py --check    # validate without writing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# =============================================================================
# CURATED CATALOG
# =============================================================================
# Each entry is a dict ready to pass to concept_videos.upsert().
# Honest quality_tier values reflect what I can actually verify:
#   - 'verified' (1 row): the Peekaboo Newton's First Law URL the
#     user directly shared and I confirmed via WebFetch.
#   - 'channel_seed' (rest): trusted channels (Peekaboo Kidz,
#     Khan Academy India, CrashCourse, FuseSchool, 3Blue1Brown,
#     Magnet Brains) but the specific video URL needs the curator's
#     30-second spot-check before flipping to 'verified'. The seed
#     URL points at the channel's search results for the concept so
#     the curator can find the right video quickly.

CATALOG: list[dict] = [
    # ------- VERIFIED (1) ----------------------------------------
    # The user shared this URL directly; confirmed it's the Peekaboo
    # Newton's First Law explainer via WebFetch metadata.
    {
        "concept": "Newton's First Law of Motion",
        "source": "youtube",
        "source_url": "https://www.youtube.com/watch?v=adLj6kygwds",
        "title": "What Is Newton's First Law Of Motion? — Dr. Binocs Show",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "physics",
        "grade_min": 6, "grade_max": 10,
        "quality_tier": "verified",
        "curator_note": "User-shared URL; Dr.Binocs cartoon style; ~3-5 min runtime; "
                        "anthropomorphises inertia as a wooden villain.",
    },
    # The 4 entries below: I had specific video IDs in mind from
    # training memory, but downgraded to channel_seed to be honest —
    # I can't actually verify those IDs are live without fetching
    # each URL. Curator confirms the URL before flipping to verified.
    {
        "concept": "Photosynthesis",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@khanacademyindia/search?query=photosynthesis",
        "title": "[Curator: find Khan India Photosynthesis video]",
        "channel": "Khan Academy India",
        "language": "en",
        "subject": "biology",
        "grade_min": 6, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Essence of Calculus",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@3blue1brown/search?query=essence+of+calculus",
        "title": "[Curator: find 3Blue1Brown 'essence of calculus' chapter 1]",
        "channel": "3Blue1Brown",
        "language": "en",
        "subject": "mathematics",
        "grade_min": 11, "grade_max": 12,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Water Cycle",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@crashcourse/search?query=water+cycle",
        "title": "[Curator: find Crash Course Geography Water Cycle video]",
        "channel": "CrashCourse",
        "language": "en",
        "subject": "geography",
        "grade_min": 6, "grade_max": 12,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Digestive System",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=digestive+system",
        "title": "[Curator: find Peekaboo Digestive System video]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "biology",
        "grade_min": 5, "grade_max": 9,
        "quality_tier": "channel_seed",
    },

    # ------- CHANNEL_SEED (rest) ---------------------------------
    # For each, source_url points at a search-the-channel URL. The
    # curator opens it, picks the best video, updates source_url to
    # the specific watch URL, and flips quality_tier to 'verified'.

    # ----- Physics: foundational, Peekaboo / Khan style -----
    {
        "concept": "Newton's Second Law of Motion",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=newton+second+law",
        "title": "[Curator: find Peekaboo's Newton's Second Law explainer]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "physics",
        "grade_min": 6, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Newton's Third Law of Motion",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=newton+third+law",
        "title": "[Curator: find Peekaboo's Newton's Third Law explainer]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "physics",
        "grade_min": 6, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Gravity",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=gravity",
        "title": "[Curator: find Peekaboo's Gravity explainer]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "physics",
        "grade_min": 5, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Friction",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=friction",
        "title": "[Curator: find Peekaboo's Friction explainer]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "physics",
        "grade_min": 5, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Light Reflection",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@khanacademyindia/search?query=reflection+light",
        "title": "[Curator: find Khan India's Light Reflection video]",
        "channel": "Khan Academy India",
        "language": "en",
        "subject": "physics",
        "grade_min": 8, "grade_max": 10,
        "quality_tier": "channel_seed",
    },

    # ----- Biology -----
    {
        "concept": "Human Heart",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=heart",
        "title": "[Curator: find Peekaboo's Human Heart explainer]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "biology",
        "grade_min": 5, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Respiratory System",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=respiratory+system",
        "title": "[Curator: find Peekaboo's Respiratory System video]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "biology",
        "grade_min": 5, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Cell Division",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@FuseSchool/search?query=cell+division+mitosis",
        "title": "[Curator: find FuseSchool's Mitosis / Cell Division video]",
        "channel": "FuseSchool",
        "language": "en",
        "subject": "biology",
        "grade_min": 9, "grade_max": 12,
        "quality_tier": "channel_seed",
    },

    # ----- Chemistry -----
    {
        "concept": "Atoms and Molecules",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=atoms+molecules",
        "title": "[Curator: find Peekaboo's Atoms & Molecules video]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "chemistry",
        "grade_min": 6, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Periodic Table",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@crashcourse/search?query=periodic+table",
        "title": "[Curator: find Crash Course Chemistry Periodic Table video]",
        "channel": "CrashCourse",
        "language": "en",
        "subject": "chemistry",
        "grade_min": 9, "grade_max": 12,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Acids and Bases",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@FuseSchool/search?query=acids+bases",
        "title": "[Curator: find FuseSchool Acids & Bases]",
        "channel": "FuseSchool",
        "language": "en",
        "subject": "chemistry",
        "grade_min": 8, "grade_max": 12,
        "quality_tier": "channel_seed",
    },

    # ----- Mathematics -----
    {
        "concept": "Pythagorean Theorem",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@khanacademyindia/search?query=pythagoras",
        "title": "[Curator: find Khan India Pythagoras Theorem video]",
        "channel": "Khan Academy India",
        "language": "en",
        "subject": "mathematics",
        "grade_min": 8, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Quadratic Equations",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@khanacademyindia/search?query=quadratic+equation",
        "title": "[Curator: find Khan India Quadratic Equations video]",
        "channel": "Khan Academy India",
        "language": "en",
        "subject": "mathematics",
        "grade_min": 9, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Trigonometry Basics",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@3blue1brown/search?query=trigonometry",
        "title": "[Curator: find 3Blue1Brown trigonometry video]",
        "channel": "3Blue1Brown",
        "language": "en",
        "subject": "mathematics",
        "grade_min": 10, "grade_max": 12,
        "quality_tier": "channel_seed",
    },
    {
        "concept": "Probability",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@khanacademyindia/search?query=probability",
        "title": "[Curator: find Khan India Probability intro]",
        "channel": "Khan Academy India",
        "language": "en",
        "subject": "mathematics",
        "grade_min": 9, "grade_max": 12,
        "quality_tier": "channel_seed",
    },

    # ----- Geography / Social Science -----
    {
        "concept": "Solar System",
        "source": "youtube",
        "source_url": "https://www.youtube.com/@PeekabooKidz/search?query=solar+system",
        "title": "[Curator: find Peekaboo Solar System video]",
        "channel": "Peekaboo Kidz",
        "language": "en",
        "subject": "geography",
        "grade_min": 5, "grade_max": 10,
        "quality_tier": "channel_seed",
    },
]

# Hindi-medium starter row to prove the multi-language pipeline works
# end-to-end. Real Hindi-curated content lives on channels like
# "Magnet Brains", "Physics Wallah", "Vedantu JEE Hindi" — populated
# in subsequent sprints.
CATALOG.append({
    "concept": "न्यूटन का गति का पहला नियम",
    "source": "youtube",
    "source_url": "https://www.youtube.com/@MagnetBrainsEducation/search?query=न्यूटन+का+पहला+नियम",
    "title": "[क्यूरेटर: Magnet Brains का Newton's First Law (Hindi) वीडियो खोजें]",
    "channel": "Magnet Brains",
    "language": "hi",
    "subject": "physics",
    "grade_min": 9, "grade_max": 11,
    "quality_tier": "channel_seed",
})


# =============================================================================


def _load_verified_seed() -> list[dict]:
    """prod-184 — the curated `verified` catalog, exported from the dev
    DB by scripts/export_concept_seed.py (each URL oembed-reverified).
    This is what makes the real catalog ship to production; the inline
    CATALOG above is mostly placeholder channel_seed rows. Returns [] if
    the file isn't present (older checkouts)."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "concept_videos_seed.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate without writing")
    args = ap.parse_args()

    from padhai import concept_videos as cv

    verified_seed = _load_verified_seed()

    if args.check:
        # Dry-run — just count
        verified = sum(1 for r in CATALOG if r.get("quality_tier") == "verified")
        seeded = sum(1 for r in CATALOG if r.get("quality_tier") == "channel_seed")
        print(f"  inline CATALOG rows: {len(CATALOG)}")
        print(f"    verified:     {verified}")
        print(f"    channel_seed: {seeded}")
        print(f"  data/concept_videos_seed.json verified rows: {len(verified_seed)}")
        return 0

    print(f"[seed] {len(CATALOG)} inline rows + {len(verified_seed)} "
          "verified-seed rows…")
    loaded, errors = cv.bulk_load(CATALOG + verified_seed)
    if errors:
        for e in errors:
            print(f"  ! {e}", file=sys.stderr)
        return 1

    stats = cv.stats()
    print(f"  loaded: {loaded}")
    print(f"  catalog total: {stats['total']}")
    print(f"  by quality:    {stats['by_quality_tier']}")
    print(f"  by subject:    {stats['by_subject']}")
    print(f"  by language:   {stats['by_language']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
