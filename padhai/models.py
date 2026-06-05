"""Central registry of Claude model IDs.

Every Claude-using module imports model constants from here. When a
model is renamed (like the 2025-10 Haiku rename that invalidated the
bare `claude-haiku-4-5` form), this file is the only place that
changes — 13+ scattered call sites used to drift independently and
introduce bug #8 (CLAUDE.md §14).

CLAUDE.md §11 documents which surface uses which tier.

## Constants

- `HAIKU_MODEL`  — cheap + fast. Tutor, doubt-vision, mock interview,
                   practice tests, upload chat/quiz/summary, recap +
                   explainer + flashcards.
- `SONNET_MODEL` — balanced. Essay grader, doubt-vision (also used in
                   some lesson surfaces).
- `OPUS_MODEL`   — strongest. Full lesson generation (pedagogy.py),
                   math-vision OCR.

## Env overrides

The full-tier overrides apply to every surface using that tier:
    PADHAI_HAIKU_MODEL  → HAIKU_MODEL
    PADHAI_SONNET_MODEL → SONNET_MODEL
    PADHAI_OPUS_MODEL   → OPUS_MODEL

Surface-specific overrides still work — modules call
`os.environ.get("PADHAI_<SURFACE>_MODEL", <TIER>_MODEL)` so an op can
override a single endpoint without touching the others.

## Cache-suffixed variants

`llm_obs.py` carries a pricing table keyed by model id; it has both
the bare-name and `-cached` rows because the cost-per-call differs
when the system prompt hits the Anthropic prompt cache. Those keys
are NOT exported here — pricing lookup is `llm_obs.record_call`'s
job, and it does its own resolution.
"""

from __future__ import annotations

import os

HAIKU_MODEL: str = os.environ.get(
    "PADHAI_HAIKU_MODEL", "claude-haiku-4-5-20251001",
)
SONNET_MODEL: str = os.environ.get(
    "PADHAI_SONNET_MODEL", "claude-sonnet-4-6",
)
OPUS_MODEL: str = os.environ.get(
    "PADHAI_OPUS_MODEL", "claude-opus-4-7",
)

# Sanity: never expose a bare "claude-haiku-4-5" — that form was
# invalidated by the 2025-10 model rename. Anything ending with just
# the family + version (no date suffix) for Haiku is the broken form.
assert HAIKU_MODEL.startswith("claude-haiku-"), HAIKU_MODEL
assert HAIKU_MODEL != "claude-haiku-4-5", (
    f"{HAIKU_MODEL!r} is the pre-rename form — set PADHAI_HAIKU_MODEL "
    f"to claude-haiku-4-5-20251001 or later. See CLAUDE.md §14 bug #8."
)
