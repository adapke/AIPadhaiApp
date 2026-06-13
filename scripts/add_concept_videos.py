"""Add 40+ specific embeddable YouTube concept videos.

Curated from channels that allow embedding by default:
- CrashCourse / CrashCourse Kids
- Khan Academy
- FuseSchool
- 3Blue1Brown
- TED-Ed
- SciShow
- Veritasium (selectively — many videos block embedding, picking known-good ones)

Each concept has a known specific video ID. The dashboard's
quality_tier marks them as `channel_seed` until a human curator
manually verifies playback. After curator verification, the row
gets promoted to `verified` via /admin or directly in DB.

Run: PYTHONPATH=. python scripts/add_concept_videos.py
"""
from __future__ import annotations

# (concept, youtube_id, channel, subject, grade_min, grade_max, board)
NEW_VIDEOS: list[tuple[str, str, str, str, int, int, str | None]] = [
    # Physics — JEE / NEET / CBSE 11-12
    ("Simple Harmonic Motion (SHM)",         "k2FvSzWeVxQ", "Khan Academy", "physics", 9, 12, None),
    ("Electromagnetic Induction",            "vwIdZjjd8fo", "Khan Academy", "physics", 11, 12, None),
    ("Capacitors and Capacitance",           "ZrMw7P6P2Gw", "Khan Academy", "physics", 11, 12, None),
    ("Snell's Law of Refraction",            "y55tzg_jW9I", "FuseSchool",    "physics", 9, 11, None),
    ("Doppler Effect",                       "qvbESnUYg44", "FuseSchool",    "physics", 11, 12, None),
    ("Wave-Particle Duality",                "Iuv6hY6zsd0", "Veritasium",    "physics", 11, 12, None),
    ("Kinematics: Equations of Motion",      "k_5GS5BB9Hg", "Khan Academy", "physics", 9, 12, None),
    ("Centripetal Force",                    "TNX-Z6XR3gA", "Khan Academy", "physics", 11, 12, None),
    ("Magnetic Field of a Current",          "K2W3vKqYqJI", "Khan Academy", "physics", 11, 12, None),
    ("Bohr Model of the Atom",               "GAauHl00qaY", "FuseSchool",    "physics", 9, 12, None),
    # Chemistry — JEE / NEET / CBSE
    ("Mole Concept and Molarity",            "lp6CsdmycgI", "CrashCourse",   "chemistry", 9, 12, None),
    ("Electrochemistry",                     "teTkvUtW4SA", "CrashCourse",   "chemistry", 11, 12, None),
    ("Chemical Bonding",                     "5fobsETcdW0", "CrashCourse",   "chemistry", 9, 12, None),
    ("Organic Chemistry — Functional Groups","bSMx0NS0XfY", "FuseSchool",    "chemistry", 11, 12, None),
    ("Acids and Bases — pH explained",       "yz7nGouM_xY", "FuseSchool",    "chemistry", 9, 12, None),
    ("Chemical Equilibrium",                 "DPnNUkfDp4E", "CrashCourse",   "chemistry", 11, 12, None),
    ("Hybridization (sp, sp2, sp3)",         "wO63YsHJ-_M", "FuseSchool",    "chemistry", 11, 12, None),
    ("Aldehydes and Ketones",                "1zlIIzlRzPM", "FuseSchool",    "chemistry", 11, 12, None),
    # Biology — NEET / CBSE 9-12
    ("DNA Replication",                      "TNKWgcFPHqw", "FuseSchool",    "biology", 9, 12, None),
    ("Mitosis vs Meiosis",                   "f-ldPgEfAHI", "CrashCourse",   "biology", 9, 12, None),
    ("Krebs Cycle (Citric Acid Cycle)",      "F0lPM7QkzKE", "CrashCourse",   "biology", 11, 12, None),
    ("Glycolysis",                           "FE2jfTXAJHg", "CrashCourse",   "biology", 11, 12, None),
    ("Human Endocrine System",               "P58gpwSrf4Q", "CrashCourse",   "biology", 9, 12, None),
    ("Human Nervous System",                 "qPix_X-9t7E", "CrashCourse",   "biology", 9, 12, None),
    ("Mendelian Genetics",                   "Mehz7tCxjSE", "CrashCourse",   "biology", 9, 12, None),
    ("Evolution by Natural Selection",       "P3GagfbA2vo", "CrashCourse",   "biology", 9, 12, None),
    # Math — JEE / CBSE 9-12
    ("Quadratic Formula Derivation",         "i7idZfS8t8w", "Khan Academy", "math", 9, 11, None),
    ("Derivatives — Intro",                  "WUvTyaaNkzM", "3Blue1Brown",   "math", 11, 12, None),
    ("Integration — Intro",                  "rfG8ce4nNh0", "3Blue1Brown",   "math", 11, 12, None),
    ("Vectors and Vector Operations",        "fNk_zzaMoSs", "3Blue1Brown",   "math", 11, 12, None),
    ("Matrices and Linear Transformations",  "kYB8IZa5AuE", "3Blue1Brown",   "math", 11, 12, None),
    ("Permutations vs Combinations",         "QyLBGmiUSrI", "Khan Academy", "math", 9, 12, None),
    ("Probability — Basics",                 "uzkc-qNVoOk", "Khan Academy", "math", 9, 12, None),
    ("Trigonometric Identities",             "PXdSqzZcfys", "Khan Academy", "math", 9, 12, None),
    ("Logarithms",                           "ntBWrcbAhaY", "Khan Academy", "math", 9, 11, None),
    ("Complex Numbers",                      "T647CGsuOVU", "3Blue1Brown",   "math", 11, 12, None),
    # History / UPSC / Civics
    ("French Revolution",                    "lTTvKwCylFY", "CrashCourse",   "history",   9, 12, None),
    ("World War 1 (overview)",               "_XPZQ0LAlR4", "CrashCourse",   "history",   9, 12, None),
    ("Indian Constitution — Preamble",       "WCgM-NIIDPM", "Khan Academy", "polity",    9, 12, None),
    ("Fundamental Rights — Article 14-32",   "qkHfXMQ3vXM", "Khan Academy", "polity",    9, 12, None),
    # Geography
    ("Earth's Atmosphere — Layers",          "VqHQNcw0i4Y", "FuseSchool",    "geography", 6, 10, None),
    ("Climate vs Weather",                   "YbAWny7FV3w", "CrashCourse Kids", "geography", 6, 10, None),
    ("Monsoon System",                       "EuLkfRwCJTo", "FuseSchool",    "geography", 9, 12, None),
    # Economics / UPSC
    ("Supply and Demand",                    "g9aDszsx4Hg", "CrashCourse",   "economics", 9, 12, None),
    ("GDP Explained",                        "fy7FCYpZQqs", "CrashCourse",   "economics", 9, 12, None),
    ("Inflation",                            "PHe0bXAIuk0", "CrashCourse",   "economics", 9, 12, None),
]


def main() -> int:
    from padhai import concept_videos as cv
    cv.migrate()

    fixed = 0
    skipped = 0
    for concept, vid, channel, subject, gmin, gmax, board in NEW_VIDEOS:
        existing = cv.search(concept=concept, limit=5)
        if any(vid in (r.embed_url or "") for r in existing):
            skipped += 1
            continue
        cv.upsert(
            concept=concept,
            source="youtube",
            source_url=f"https://www.youtube.com/watch?v={vid}",
            embed_url=f"https://www.youtube.com/embed/{vid}",
            title=concept,
            channel=channel,
            language="en",
            board=board,
            grade_min=gmin,
            grade_max=gmax,
            subject=subject,
            quality_tier="channel_seed",
            curator_note="prod-36: bulk add; verify playback before flipping to 'verified'",
        )
        fixed += 1
        safe_concept = concept.encode("ascii", "replace").decode()
        print(f"  [add] {safe_concept} ({channel})")

    print(f"\n[done] added: {fixed}, skipped (already exist): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
