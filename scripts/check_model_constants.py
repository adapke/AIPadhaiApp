#!/usr/bin/env python3
"""CI guard: every Claude model ID must come from `padhai/models.py`.

CLAUDE.md §14 bug #8 was the codebase carrying `claude-haiku-4-5`
(the pre-rename Haiku form) across multiple modules and silently
failing every Claude call. Polish-10 centralized the strings into
`padhai/models.py`. This script locks the gate so a future
contributor can't reintroduce a literal model id by mistake.

It fails (exit 1) if any source file outside the allowlist contains
a string literal matching `claude-<family>-<version>`. The allowlist
covers:

- `padhai/models.py`  — the source of truth (defaults for the env
  overrides live here as literal strings, which is the point).
- `padhai/llm_obs.py` — the pricing table is keyed by literal model
  id (input / output / cached variants). Different keys map to
  different per-token rates, so they MUST stay literal.

Also tolerated by design:
- `tests/` and `scripts/` may reference literals for ad-hoc QA.
- `*.md` files — documentation is allowed to mention the ids.
- `padhai/schema_v2.py` — model column comment, not real code.

Run via `make verify`. Standalone:
    python scripts/check_model_constants.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MODEL_PATTERN = re.compile(r'["\']claude-(haiku|sonnet|opus)-[\w\-]*["\']')

# Files where literal model ids ARE the contract.
ALLOWLIST = {
    Path("padhai/models.py"),
    Path("padhai/llm_obs.py"),
    Path("padhai/schema_v2.py"),  # SQL comment only
}

# Directory prefixes that are out of scope (tests + scripts + docs).
SKIP_PREFIXES = ("tests/", "scripts/", "docs/", "node_modules/", ".venv/")
SKIP_SUFFIXES = (".md", ".yml", ".yaml", ".json")


def scan(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        # Skip allowlist + scripts + tests + node_modules + .venv
        if rel in ALLOWLIST:
            continue
        if any(str(rel).replace("\\", "/").startswith(p) for p in SKIP_PREFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in MODEL_PATTERN.finditer(line):
                findings.append((rel, lineno, m.group(0)))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings = scan(root)
    if not findings:
        print(
            "[check_model_constants] OK — no literal Claude model "
            "ids outside padhai/models.py + padhai/llm_obs.py.",
        )
        return 0
    print(
        "[check_model_constants] FAIL — literal Claude model ids "
        "found outside the allowlist. Import the constant from "
        "padhai.models instead (HAIKU_MODEL / SONNET_MODEL / OPUS_MODEL).",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    for path, lineno, snippet in findings:
        print(f"  {path}:{lineno}: {snippet}", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        f"Total: {len(findings)} literal(s). See CLAUDE.md §11 + §14 #8.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
