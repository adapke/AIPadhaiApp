"""prod-184 — promote channel_seed concept videos to `verified` by
attaching REAL, oembed-verified YouTube URLs.

Background: the channel_seed catalog rows shipped with placeholder /
non-specific URLs (fabricated IDs that 404, bare @channel/search links,
`sample0000x`). The auto-curator (prod-167) correctly refused to promote
them because they fail the YouTube oembed liveness check. This script
attaches a real video URL — sourced by web-searching the concept on
trusted educational channels — and promotes the row only after the
oembed check passes (live + embeddable). Same safety bar the existing
44 verified rows cleared.

Each candidate below was found via web search (real result URLs, not
guessed IDs) on a recognised educational channel (Khan Academy, Amoeba
Sisters, CrashCourse, FuseSchool, Physics Wallah, Infinity Learn, …).
The oembed call is the gate: a dead / private / non-embeddable ID is
skipped, never promoted. update_video(auto_fetch_oembed=True) pulls the
canonical title + channel from YouTube so we store the real metadata.

Idempotent: already-`verified` rows aren't touched (we only walk
channel_seed). Re-running re-verifies and re-promotes any that were
added since.

Usage:
    python scripts/curate_real_videos.py            # verify + promote
    python scripts/curate_real_videos.py --dry-run  # verify only, no writes
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

# Concept substring (matched case-insensitively against the row's
# `concept`) -> real YouTube video id. One real video per concept,
# sourced from a trusted educational channel via web search.
CANDIDATES: list[tuple[str, str]] = [
    ("Photosynthesis", "CMiPYHNNg28"),            # Amoeba Sisters
    ("Krebs Cycle", "juM2ROSLWfw"),               # Khan Academy
    ("Pythagorean", "AA6RfgP-AHU"),               # Khan Academy
    ("Mole Concept", "MfadB5RYDWY"),              # PW Class 11
    ("Newton's Second Law", "0efXaBr_JcU"),       # FuseSchool
    ("Newton's Third Law", "TVAxASr0iUY"),        # Infinity Learn
    ("Chemical Bonding", "JXvE9IhO1EU"),          # chemistry
    ("Periodic Table", "0RRVV4Diomg"),            # CrashCourse Chemistry #4
    ("Mitosis", "f-ldPgEfAHI"),                   # Amoeba Sisters
    ("Human Heart", "Gx5jpXOo0go"),               # GCSE/NEET biology
    ("Quadratic Equations", "IWigvJcCAJ0"),       # Khan Academy
    ("Gravity", "AoTNK9FK470"),                   # Khan Academy
    ("Friction", "lGbNg7KJKlM"),                  # physics
    ("Light Reflection", "31tdBD4Yw0o"),          # Class 10 physics
    ("Fundamental Rights", "IeTCLuG3iy4"),        # UPSC/CBSE Polity
    ("Trigonometry Basics", "PUB0TaZ7bhA"),       # trigonometry
    ("Probability", "uzkc-qNVoOk"),               # Khan Academy
    # --- prod-184 batch 2: remaining genuinely-new concepts ---
    ("pH explained", "75j1b1l6PWU"),              # Khan Academy — pH scale
    ("Acid Bases and Salts", "5bSXK0QttdY"),      # Physics Wallah (Class 10)
    ("Aldehydes", "-fBPX-4kFlw"),                 # CrashCourse Organic #27
    ("Chemical Equilibrium", "Nfa-B2Bcm8E"),      # Physics Wallah
    ("Hybridization", "pdJeQUd2g_4"),             # Organic Chemistry Tutor
    ("Bohr Model", "S1LDJUu4nko"),                # Infinity Learn NEET
    ("Doppler", "Ur3F-JLdq_Q"),                   # Doppler visual
    ("Kinematics", "24x235Nputs"),                # Organic Chemistry Tutor
    ("Magnetic Field", "UJy0xirX1ME"),            # BYJU'S
    ("Digestive System", "wIN_OwWT2Kk"),          # Khan Academy (Class 10)
    ("Endocrine", "-AsNt_hro2A"),                 # endocrine overview
    ("Atmosphere", "cogpvf8lMXg"),                # atmosphere layers
    ("Monsoon", "Fo8nlearLZQ"),                   # Indian Monsoon Class 9 NCERT
    ("GDP", "Wy4TGV-tPd8"),                        # GDP explained
    ("Supply and Demand", "2bc4rwjdfNg"),         # supply & demand
    ("Preamble", "8ePMJe_4XFg"),                  # StudyIQ Polity
    ("Permutations", "Zryrco_IrsA"),              # Khan Academy combinations
    ("Trigonometric Identities", "HY5Zxkrj35Y"),  # Class 10 trig identities
]


def _oembed(video_id: str) -> dict | None:
    """Return {title, author} if the video is live + embeddable, else None."""
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

    import sqlite3

    from padhai import concept_videos as cv
    from padhai import db as _db

    conn = sqlite3.connect(str(_db.sqlite_path()))
    conn.row_factory = sqlite3.Row
    seed_rows = conn.execute(
        "SELECT id, concept, concept_norm FROM concept_videos "
        "WHERE quality_tier='channel_seed'",
    ).fetchall()
    # Concepts that already have a verified row — promoting a channel_seed
    # duplicate of these would create a redundant verified row and can
    # collide on the UNIQUE(concept_norm, source_url, language) index when
    # the canonical URL is already in use. Only promote genuinely-NEW
    # concepts (no existing verified row).
    verified_norms = {
        r["concept_norm"] for r in conn.execute(
            "SELECT DISTINCT concept_norm FROM concept_videos "
            "WHERE quality_tier='verified'",
        ).fetchall()
    }
    conn.close()

    import sqlite3 as _sqlite3
    promoted, skipped_dead, no_match, skipped_dup = 0, 0, 0, 0
    used_concepts: set[str] = set()

    print(f"channel_seed rows: {len(seed_rows)}  |  candidates: {len(CANDIDATES)}")
    print("=" * 64)

    for key, vid in CANDIDATES:
        # First channel_seed row whose concept contains the key and that
        # we haven't already promoted this run.
        row = next(
            (r for r in seed_rows
             if key.lower() in (r["concept"] or "").lower()
             and r["concept"] not in used_concepts),
            None,
        )
        if row is None:
            print(f"  [no-match] {key!r} — no channel_seed row")
            no_match += 1
            continue
        if row["concept_norm"] in verified_norms:
            print(f"  [dup-skip] {key!r} — concept already verified")
            skipped_dup += 1
            continue
        meta = _oembed(vid)
        if not meta:
            print(f"  [dead]     {key!r} — {vid} failed oembed; skipped")
            skipped_dead += 1
            continue
        used_concepts.add(row["concept"])
        label = f"{row['concept'][:34]:36} [{meta['author'][:18]}] {meta['title'][:40]}"
        if args.dry_run:
            print(f"  [would]    {label}")
            promoted += 1
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        try:
            cv.update_video(
                row["id"], source_url=url, auto_fetch_oembed=True,
                curator_note="prod-184: real URL, oembed-verified",
            )
            cv.set_quality_tier(
                row["id"], "verified",
                curator_note="prod-184 auto-curated (oembed live + embeddable)",
            )
        except _sqlite3.IntegrityError as e:
            print(f"  [conflict] {key!r} — {e}; skipped")
            skipped_dup += 1
            continue
        print(f"  [promoted] {label}")
        promoted += 1

    print("=" * 64)
    verb = "would promote" if args.dry_run else "promoted"
    print(f"{verb}: {promoted}   dup-skipped: {skipped_dup}   "
          f"dead-skipped: {skipped_dead}   no-match: {no_match}")
    try:
        st = cv.stats()
        print(f"catalog now: {st}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
