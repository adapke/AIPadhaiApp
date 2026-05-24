"""J1 — Math equation rendering.

Today Claude emits formulas as plain English ("x equals negative b plus
or minus the square root of b squared minus four a c, all over two a").
TTS-friendly but ugly on the slide. v1.5 adds a parallel `math_blocks`
field on Scene — Claude emits LaTeX → we render to SVG via KaTeX SSR
→ embeds in the slide. The `alt_text` field keeps the spoken form for
narration.

Two render paths:

  HTML output:  inline KaTeX SVG (server-side rendered via a
                small subprocess shelling out to a KaTeX bundle)
  Slide raster: bundled into the PNG via the same code path
                as text rendering — we pre-render the SVG to PNG
                and composite

For environments where the KaTeX bundle isn't installed (most current
dev sandboxes), we fall back to "$$expr$$"-wrapped text that's still
human-readable. The full SVG path turns on when `KATEX_BIN` is set.

Schema: Scene gets math_blocks: list[MathBlock] (already supported
via Scene's extra fields from v0.7). This module just adds the helpers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class MathBlock:
    latex: str
    alt_text: str            # spoken form for TTS
    inline: bool = False     # inline vs display (block) math
    position: str = "auto"   # 'auto' | 'left' | 'right' | 'center'


def is_katex_available() -> bool:
    """KaTeX SSR availability gate. `KATEX_BIN` points at a tiny
    Node.js script that takes LaTeX on stdin and emits SVG on stdout."""
    return bool(os.environ.get("KATEX_BIN")) and os.path.exists(
        os.environ.get("KATEX_BIN", ""),
    )


def render_to_svg(latex: str, *, inline: bool = False) -> str:
    """Render a LaTeX string to inline SVG. Raises RuntimeError if
    KaTeX isn't available — caller should fall back to a text repr."""
    if not is_katex_available():
        raise RuntimeError(
            "KaTeX not configured — set KATEX_BIN to the SSR script path"
        )
    binp = os.environ.get("KATEX_BIN", "")
    try:
        # KaTeX SSR script reads {"latex": "...", "displayMode": bool}
        # on stdin and writes SVG to stdout. Tiny — ~30 lines of Node.
        proc = subprocess.run(
            [binp],
            input=json.dumps({"latex": latex, "displayMode": not inline}),
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        return proc.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"KaTeX render failed: {e.stderr}") from e


def render_to_html(latex: str, *, inline: bool = False,
                   alt_text: str | None = None) -> str:
    """SVG-when-available, spoken-text fallback otherwise. Wraps in a
    <span class="math-block"> for CSS targeting; the alt_text becomes
    the aria-label for accessibility. When alt_text isn't supplied,
    we auto-compute the spoken form so screen readers + TTS still
    get a readable representation."""
    spoken = alt_text or latex_to_spoken(latex)
    aria = spoken.replace('"', "&quot;")
    if is_katex_available():
        try:
            svg = render_to_svg(latex, inline=inline)
            cls = "math-inline" if inline else "math-block"
            return (
                f'<span class="{cls}" aria-label="{aria}" role="math">{svg}</span>'
            )
        except RuntimeError:
            pass
    # Fallback: show both the spoken form (readable) + the raw LaTeX
    # (for users who can parse math notation; bracketed so it doesn't
    # confuse TTS).
    delim = "$" if inline else "$$"
    return (
        f'<span class="math-fallback" aria-label="{aria}" role="math">'
        f'{_escape_html(spoken)} <span class="math-raw">[{delim}'
        f'{_escape_html(latex)}{delim}]</span></span>'
    )


def _escape_html(s: str) -> str:
    import html
    return html.escape(s)


# ---------- spoken-form synthesizer (for TTS narration) ----------

# Map common LaTeX commands to their natural-language form. Far from
# exhaustive — covers the high-frequency K-12 patterns. Anything not
# matched falls through to the original LaTeX (which TTS will mangle,
# but that's an acceptable failure mode for an edge case).
_REPLACEMENTS = [
    # Apply innermost-first so nested patterns resolve correctly.
    (r"\\sqrt\{([^{}]+)\}", r"square root of \1"),
    (r"\\sum_\{([^{}]+)\}\^\{([^{}]+)\}", r"sum from \1 to \2 of"),
    (r"\\int_\{([^{}]+)\}\^\{([^{}]+)\}", r"integral from \1 to \2 of"),
    (r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"\1 over \2"),
    (r"\\cdot", " times "),
    (r"\\times", " times "),
    (r"\\div", " divided by "),
    (r"\\pm", " plus or minus "),
    (r"\\leq", " less than or equal to "),
    (r"\\geq", " greater than or equal to "),
    (r"\\neq", " not equal to "),
    (r"\\approx", " approximately equal to "),
    (r"\\pi", "pi"),
    (r"\\alpha", "alpha"),
    (r"\\beta", "beta"),
    (r"\\theta", "theta"),
    (r"\\Delta", "delta"),
    (r"\\infty", "infinity"),
    (r"\^2", " squared"),
    (r"\^3", " cubed"),
    (r"\^\{([^}]+)\}", r" to the power of \1"),
    (r"_\{([^}]+)\}", r" sub \1"),
    (r"\\\\", " "),
    (r"[{}]", ""),
]


def latex_to_spoken(latex: str) -> str:
    """Best-effort LaTeX → spoken English for TTS narration. Used as
    the default alt_text when Claude doesn't supply one explicitly.

    Example: '\\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}' →
    '-b plus or minus square root of b squared - 4ac over 2a'
    """
    s = latex
    # Apply iteratively until stable — nested patterns like
    # \frac{... \sqrt{x} ...}{...} need multiple passes since each
    # regex requires the inner braces to be already-stripped before
    # the outer regex can match.
    for _ in range(4):
        before = s
        for pat, repl in _REPLACEMENTS:
            s = re.sub(pat, repl, s)
        if s == before:
            break
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_block(block: MathBlock | dict) -> MathBlock:
    """Accept either a MathBlock or a plain dict (from Claude JSON
    output) and return a canonical MathBlock with alt_text filled in
    if missing."""
    if isinstance(block, dict):
        latex = block.get("latex") or ""
        alt = block.get("alt_text") or latex_to_spoken(latex)
        return MathBlock(
            latex=latex,
            alt_text=alt,
            inline=bool(block.get("inline", False)),
            position=block.get("position", "auto"),
        )
    if not block.alt_text:
        return MathBlock(
            latex=block.latex,
            alt_text=latex_to_spoken(block.latex),
            inline=block.inline,
            position=block.position,
        )
    return block
