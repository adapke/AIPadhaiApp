"""One-off scanner for the prod-15 fix — find JS strings inside
Python triple-quoted blocks that use `\\'` (Python-escape for `'`)
inside single-quote-delimited JS strings. Python emits a literal
apostrophe → JS parser sees the string end early → SyntaxError.

Run: python scripts/_scan_js_apostrophes.py
"""
import re
import sys
from pathlib import Path

target = Path("padhai/ui_pages.py")
src = target.read_text(encoding="utf-8")

# Pattern: single-quoted JS string fragment that contains `\'`. Looking
# specifically for `\'s` (possessive) which is the most common form.
pattern = re.compile(r"'[^'\n]{0,300}\\'[a-z]")
hits = []
for m in pattern.finditer(src):
    line = src[: m.start()].count("\n") + 1
    snippet = m.group(0)
    if len(snippet) > 120:
        snippet = "..." + snippet[-120:]
    hits.append((line, snippet))

print(f"suspicious patterns: {len(hits)}")
for ln, sn in hits:
    print(f"  line {ln}: {sn}")

sys.exit(0)
