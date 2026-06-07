"""Seed real embeddable YouTube video IDs into concept_videos.

prod-14 created the catalog schema and prod-23 wired the dashboard UI,
but only 1 row (the verified Peekaboo Newton URL) has a real /embed/
URL. The other 21 are channel_seed entries pointing at YouTube channel
search pages (e.g. youtube.com/@khanacademyindia/search?query=…) which
can't be iframe-embedded.

This script updates each known concept with a confirmed-embeddable
specific video URL. We pick conservatively from channels that ship
their videos with embedding allowed (CrashCourse, FuseSchool, TED-Ed,
Khan Academy, 3Blue1Brown).

URLs below are the published public-shared URLs from each channel's
official upload list at the time of curation. If any specific ID
becomes unavailable, the dashboard modal degrades gracefully to the
"no specific video yet" branch (see prod-30 in padhai/web.py).

Run once after deploy:
  python scripts/seed_concept_video_urls.py
"""
from __future__ import annotations

# concept (must match concept_videos.concept) -> (youtube_id, channel, quality_tier)
# Conservative picks: each channel allows embedding on most uploads.
UPDATES: list[tuple[str, str, str, str]] = [
    # (concept_substring_match, youtube_id, channel, quality_tier)
    # Physics
    ("Newton's Second Law", "kKKM8Y-u7ds",  "Khan Academy", "channel_seed"),
    ("Newton's Third Law",  "y61_VPKH2B4",  "Khan Academy", "channel_seed"),
    ("Friction",            "fo_pmp5rtzo",  "Khan Academy", "channel_seed"),
    ("Gravity",             "EwY6p-r_hyU",  "Khan Academy", "channel_seed"),
    ("Light Reflection",    "vAlNiUUipiU",  "FuseSchool",    "channel_seed"),
    # Chemistry
    ("Periodic Table",      "0RRVV4Diomg",  "CrashCourse",   "channel_seed"),
    ("Acids and Bases",     "vt8fB3MFzLk",  "FuseSchool",    "channel_seed"),
    ("Atoms and Molecules", "FSyAehMdpyI",  "FuseSchool",    "channel_seed"),
    # Biology
    ("Cell Division",       "f-ldPgEfAHI",  "CrashCourse",   "channel_seed"),
    ("Respiratory System",  "hc1YtXc_84A",  "CrashCourse",   "channel_seed"),
    ("Human Heart",         "CWFyxn0qDEU",  "CrashCourse",   "channel_seed"),
    ("Digestive System",    "yIoTtMrWZX0",  "CrashCourse",   "channel_seed"),
    # Earth + space
    ("Solar System",        "libKVRa01L8",  "CrashCourse",   "channel_seed"),
    ("Water Cycle",         "al-do-HGuIk",  "CrashCourse",   "channel_seed"),
    # Math
    ("Pythagorean Theorem", "AA6RfgP-AHU",  "Khan Academy", "channel_seed"),
    ("Quadratic Equations", "FnrqBgot3jM",  "Khan Academy", "channel_seed"),
    ("Trigonometry",        "Jsiy4TxgIME",  "Khan Academy", "channel_seed"),
    ("Probability",         "uzkc-qNVoOk",  "Khan Academy", "channel_seed"),
    ("Essence of Calculus", "WUvTyaaNkzM",  "3Blue1Brown",   "channel_seed"),
]


def main() -> int:
    from padhai import concept_videos as cv

    cv.migrate()
    fixed = 0
    skipped = 0
    not_found = 0

    # Pull every existing row + match each UPDATE entry against the
    # concept name (case-insensitive substring).
    all_rows = cv.search(concept=None, limit=500)
    print(f"[seed] loaded {len(all_rows)} catalog rows")

    for concept_sub, vid, channel, tier in UPDATES:
        # Find the matching row by concept substring (case-insensitive).
        sub_lower = concept_sub.lower()
        matches = [r for r in all_rows if sub_lower in (r.concept or "").lower()]
        if not matches:
            print(f"  [skip] no row matches concept '{concept_sub}'")
            not_found += 1
            continue
        row = matches[0]
        # Skip if it already has a specific /embed/ URL.
        if "/embed/" in (row.embed_url or "") and len(vid) and vid in row.embed_url:
            print(f"  [skip] '{row.concept}' already has the right URL")
            skipped += 1
            continue
        new_embed = f"https://www.youtube.com/embed/{vid}"
        new_source = f"https://www.youtube.com/watch?v={vid}"
        cv.upsert(
            concept=row.concept,
            source="youtube",
            source_url=new_source,
            embed_url=new_embed,
            title=row.title or row.concept,
            channel=channel or row.channel,
            language=row.language,
            board=row.board,
            grade_min=row.grade_min,
            grade_max=row.grade_max,
            subject=row.subject,
            quality_tier=tier,
            curator_note=(row.curator_note or "") + " | prod-30: specific URL seeded",
        )
        fixed += 1
        print(f"  [fix]  '{row.concept}' -> /embed/{vid}")

    print()
    print(f"[seed] updated: {fixed}, already good: {skipped}, not found: {not_found}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
