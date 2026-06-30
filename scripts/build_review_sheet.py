#!/usr/bin/env python3
"""prod-205 — emit a native-speaker review sheet for the i18n catalogue.

Reads padhai/locales/*.json and writes docs/i18n_review.csv with one row per
key: the canonical English plus every locale's current (first-pass) string.
A native reviewer opens the CSV in Excel / Google Sheets, finds their language
column, and corrects the cells in place; we diff the returned file back into
build_locales.py / hi.json.

UTF-8 with BOM so Excel renders Indic scripts correctly. Reproducible — re-run
after every catalogue expansion.

Usage:
  python scripts/build_review_sheet.py
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOC = ROOT / "padhai" / "locales"
OUT = ROOT / "docs" / "i18n_review.csv"

# Column order: English first (source), then the 9 review languages.
LANGS = ["en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"]


def _load(code: str) -> dict:
    return json.loads((LOC / f"{code}.json").read_text(encoding="utf-8"))


def main() -> int:
    data = {code: _load(code) for code in LANGS}
    en = data["en"]
    keys = [k for k in en if not k.startswith("_meta")]

    # Header uses each locale's own native name for clarity.
    header = ["key"] + [
        f"{code} - {data[code].get('_meta_native', code)}" for code in LANGS
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(header)
        for k in keys:
            w.writerow([k] + [data[code].get(k, "") for code in LANGS])

    print(f"wrote {OUT.relative_to(ROOT)}  ({len(keys)} strings x {len(LANGS)} languages)")
    # Quick parity sanity — every locale should have every key.
    for code in LANGS:
        missing = [k for k in keys if not data[code].get(k)]
        if missing:
            print(f"  WARN {code}: {len(missing)} blank (e.g. {missing[:3]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
