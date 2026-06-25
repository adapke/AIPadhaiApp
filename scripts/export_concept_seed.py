"""prod-184 — export the curated `verified` concept-video catalog from the
local DB into a repo data file so it SHIPS TO PRODUCTION.

The problem this solves: the SQLite module DB is gitignored, so the
verified concept videos curated over prod-14..167 live only on the dev
box. A fresh production deploy runs `scripts/build_concept_videos.py`,
whose inline CATALOG is mostly placeholder `@channel/search` URLs — so
prod's `/concept` surfaces ~1 working video instead of the ~50 that are
actually curated.

This script reads every `verified` row, re-checks each URL is still live
+ embeddable via YouTube oembed (drops any that died since curation),
and writes the survivors to `data/concept_videos_seed.json`.
`build_concept_videos.py` loads that file (prod-184), so the curated
catalog is now version-controlled and ships on every deploy.

Run after curating (e.g. after scripts/curate_real_videos.py):
    python scripts/export_concept_seed.py
"""
from __future__ import annotations

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

_OUT = os.path.join(_REPO, "data", "concept_videos_seed.json")

# Fields bulk_load()/upsert() consume. Mirrors the concept_videos schema
# minus the runtime-only columns (id, *_at, play_count, curator_note).
_FIELDS = [
    "concept", "source", "source_url", "title", "channel", "duration_sec",
    "language", "board", "grade_min", "grade_max", "subject", "quality_tier",
]


def _oembed_live(source_url: str) -> bool:
    import re
    m = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{6,})", source_url or "")
    if not m:
        return False
    url = (
        "https://www.youtube.com/oembed?url="
        f"https://www.youtube.com/watch?v={m.group(1)}&format=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return False
    except Exception:
        return False


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(_REPO, ".env"))

    import sqlite3

    from padhai import db as _db

    conn = sqlite3.connect(str(_db.sqlite_path()))
    conn.row_factory = sqlite3.Row
    cols = {c[1] for c in conn.execute("PRAGMA table_info(concept_videos)").fetchall()}
    select = ", ".join(f for f in _FIELDS if f in cols)
    rows = conn.execute(
        f"SELECT {select} FROM concept_videos WHERE quality_tier='verified' "
        "ORDER BY subject, concept",
    ).fetchall()
    conn.close()

    print(f"verified rows in DB: {len(rows)} — re-verifying via oembed…")
    kept, dropped = [], []
    for r in rows:
        d = dict(r)  # sqlite3.Row -> plain dict (column name -> value)
        if _oembed_live(d.get("source_url", "")):
            kept.append(d)
        else:
            dropped.append(d.get("concept", "?"))

    os.makedirs(os.path.dirname(_OUT), exist_ok=True)
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"  live + written: {len(kept)}")
    if dropped:
        print(f"  dropped (dead URL): {len(dropped)} -> {dropped}")
    print(f"  wrote {_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
