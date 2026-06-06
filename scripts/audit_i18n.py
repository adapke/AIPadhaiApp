#!/usr/bin/env python3
"""Audit hardcoded English UI strings vs the i18n key set.

Surfaces the gap between what's in `padhai/locales/en.json` (39 keys)
and what's actually rendered in `_INDEX_HTML` / `HOME_HTML` /
`LANDING_HTML` (currently ~286 hardcoded English strings).

The goal is honest measurement, not magic translation: print a count,
the top-N offenders, and whether they're already covered by an i18n
key. Run before every release that claims "Hindi-ready" to make sure
the claim is true.

Usage:
    python scripts/audit_i18n.py           # human-readable report
    python scripts/audit_i18n.py --json    # machine-readable
    python scripts/audit_i18n.py --top 50  # show top 50 (default 30)

Exit code: always 0 (this is a measurement, not a gate). Use it in
CI to publish the gap as a metric; raise the gate via PR review.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML_FILES = (
    "padhai/home_ui.py",
    "padhai/web.py",
    "padhai/ui_pages.py",
)

# Hardcoded UI text shapes. Tuned to catch button/heading/label text;
# accept some false-positives from JS-string content.
_PAT_TAG_TEXT = re.compile(
    r'>([A-Z][a-zA-Z]+(?: [a-zA-Z]+){1,7})<'
)
_PAT_PLACEHOLDER = re.compile(
    r'placeholder=\"([A-Z][^\"]{3,80})\"'
)
_PAT_LABEL_VALUE = re.compile(
    r'value=\"([A-Z][a-zA-Z]+(?: [a-zA-Z]+){0,5})\"\s+'
    r'name=\"',
)

# Strings we don't want flagged — proper nouns, URLs, code identifiers.
_IGNORE_PREFIXES = ("http", "https", "www.", "/api/", "/lessons/")
_IGNORE_EXACT = frozenset({
    # Brand/proper nouns that don't translate
    "AI Pathshala", "PadhAI", "Spark",
    "Razorpay", "Anthropic", "Claude",
    "BYJU's", "Vedantu", "Unacademy",
    "GitHub", "CBSE", "ICSE", "IGCSE",
    "JEE", "NEET", "UPSC", "SSC",
})


def scan() -> dict[str, int]:
    """Return {string: occurrence_count} across the HTML-bearing
    modules."""
    found: dict[str, int] = {}
    for relpath in HTML_FILES:
        path = ROOT / relpath
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        for pat in (_PAT_TAG_TEXT, _PAT_PLACEHOLDER, _PAT_LABEL_VALUE):
            for m in pat.finditer(src):
                s = m.group(1).strip()
                if (
                    len(s) <= 5
                    or s in _IGNORE_EXACT
                    or any(s.startswith(p) for p in _IGNORE_PREFIXES)
                ):
                    continue
                found[s] = found.get(s, 0) + 1
    return found


def load_i18n_keys() -> set[str]:
    """English value set from the i18n catalogue — case-insensitive,
    so we can check if a hardcoded string is already there."""
    f = ROOT / "padhai" / "locales" / "en.json"
    if not f.is_file():
        return set()
    data = json.loads(f.read_text(encoding="utf-8"))
    out: set[str] = set()
    for v in data.values():
        if isinstance(v, str):
            out.add(v.strip().lower())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")
    ap.add_argument("--top", type=int, default=30,
                    help="how many top offenders to show (default 30)")
    args = ap.parse_args()

    found = scan()
    i18n_en_values = load_i18n_keys()

    # Bucket: covered (already in i18n) vs untranslated.
    covered: list[tuple[str, int]] = []
    untranslated: list[tuple[str, int]] = []
    for s, c in found.items():
        if s.strip().lower() in i18n_en_values:
            covered.append((s, c))
        else:
            untranslated.append((s, c))

    untranslated.sort(key=lambda x: -x[1])
    covered.sort(key=lambda x: -x[1])

    if args.json:
        print(json.dumps({
            "total_hardcoded": len(found),
            "covered_by_i18n": len(covered),
            "untranslated": len(untranslated),
            "coverage_pct": (
                round(100 * len(covered) / len(found), 1)
                if found else 100.0
            ),
            "top_untranslated": [
                {"text": s, "count": c}
                for s, c in untranslated[:args.top]
            ],
        }, indent=2, ensure_ascii=False))
        return 0

    print("=== i18n audit — hardcoded UI strings ===")
    print(f"Scanned: {', '.join(HTML_FILES)}")
    print(f"Total distinct hardcoded English strings : {len(found)}")
    print(f"  Already covered by an i18n key         : "
          f"{len(covered)} ({100*len(covered)/max(1,len(found)):.1f}%)")
    print(f"  Untranslated (need an i18n key + Hindi): "
          f"{len(untranslated)}")
    print()
    print(f"=== Top {args.top} untranslated (frequency-ordered) ===")
    for s, c in untranslated[:args.top]:
        print(f"  {c:>3}x  {s}")
    print()
    print(
        "Next step: catalog the high-frequency entries into "
        "padhai/locales/en.json + the per-locale files. Then wire "
        "the rendering site to call padhai.i18n.t(key, locale=...)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
