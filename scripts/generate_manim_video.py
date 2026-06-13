#!/usr/bin/env python3
"""prod-13b — Manim-based animated concept explainer.

Replaces the slide-based first iteration (`generate_concept_video.py`)
with real 2D animation: ball/arrow physics, motion paths, vectors,
labels that appear in sync with narration. 3Blue1Brown style.

Pipeline per concept:
  1. Claude Sonnet generates a Manim CE Scene class (one file, one
     scene). System prompt constrains it to: no LaTeX (uses Text not
     Tex), no external assets, total runtime ~90s, scene class named
     `ExplainerScene`.
  2. Save the scene to a temp .py, render with `manim -ql` (low
     quality, ~5-10 min per minute of animation).
  3. (Optional) Splice in gTTS narration audio matching the scene
     timings.

Why Sonnet not Haiku: scene generation needs precise Python +
Manim API knowledge. Haiku produces broken code more often.
Sonnet ~₹3/scene vs Haiku ~₹0.20; worth the spend on quality
when the alternative is rendering broken code.

Usage:
  python scripts/generate_manim_video.py \\
      --concept "Newton's first law of motion" \\
      --lang en --grade 9 \\
      --out data/concept_videos/newton1_en_manim.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

# Clear empty Anthropic env vars (see generate_concept_video.py for why)
for _k in list(os.environ):
    if (
        _k.startswith("ANTHROPIC_")
        and _k != "ANTHROPIC_API_KEY"
        and not os.environ[_k].strip()
    ):
        del os.environ[_k]


MANIM_SYSTEM = """\
You are a Manim Community Edition (v0.20+) scene author writing
animated explainers for Indian school students. Output ONLY valid
Python code for a single Manim scene file — no markdown fences,
no preamble, no commentary.

Hard rules (the renderer will reject violations):
- Top of file: `from manim import *`
- Class name MUST be exactly `ExplainerScene` and inherit from `Scene`.
- Use `Text(...)` NOT `Tex(...)` / `MathTex(...)` (no LaTeX available).
- Use only built-in Manim mobjects: Dot, Circle, Square, Rectangle,
  Line, Arrow, DoubleArrow, Text, Polygon, Triangle, VGroup, Mobject.
- No external image/svg/audio assets — everything is procedural.
- No `self.embed()`, no `self.interactive_embed()`, no IPython.
- Total runtime target ~60-90 seconds (use `self.wait(N)` for pauses
  and the durations of `self.play(..., run_time=X)`).
- Use a clean colour palette: WHITE for primary text, BLUE_C for
  objects, RED_C for forces opposing motion, GREEN_C for applied
  forces, YELLOW for highlights. Background stays BLACK (default).
- Build the scene as a sequence of 4-6 narrative beats. Each beat:
  - title at top (use `Text(..., font_size=36)` and shift up)
  - visual elements in the middle that animate in
  - 2-4 second pause for student to absorb
  - clear out (FadeOut) before next beat
- For text use `font_size` in {24, 32, 36, 48} only.
- For physics concepts, use arrows to denote forces/velocity with
  labels above them ("Force", "Velocity", etc.).
- Add `from manim import config` at top and set
  `config.frame_rate = 24` so render time stays reasonable.
"""

MANIM_USER_TEMPLATE = """\
Concept: {concept}
Student grade: {grade}
Language: {lang_label} (text labels in {lang_label}; if {lang_label}
isn't English use the {lang_label} script. Keep technical terms in
their conventional Indian-English form when standard, e.g. "Force",
"Velocity" stay English. Hindi: use Devanagari for explanations).

Write a Manim scene that VISUALLY explains this concept. Don't just
animate the text — animate the underlying phenomenon. Examples:
- Newton's first law → ball at rest, then arrow pushes it, then
  ball keeps rolling at constant velocity until friction stops it.
- Photosynthesis → sun rays hitting a leaf, CO2 + H2O entering,
  glucose + O2 emerging.
- Pythagorean theorem → right triangle, squares on each side,
  visual rearrangement showing a² + b² = c².

Be educational, not decorative.
"""


def _strip_code_fences(text: str) -> str:
    """LLMs sometimes wrap code in ```python fences despite the
    system prompt. Strip them."""
    text = re.sub(r"^```(?:python)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return text.strip()


def generate_manim_code(concept: str, lang: str, grade: int) -> tuple[str, dict]:
    """Returns (code, cost_info)."""
    from anthropic import Anthropic

    from padhai import models as _models

    LANG_NAME = {
        "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
        "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi",
        "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi",
    }
    client = Anthropic()
    user_msg = MANIM_USER_TEMPLATE.format(
        concept=concept, grade=grade,
        lang_label=LANG_NAME.get(lang, "English"),
    )
    resp = client.messages.create(
        model=_models.SONNET_MODEL,
        max_tokens=4000,
        system=MANIM_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    code = _strip_code_fences(text)

    in_tok = getattr(resp.usage, "input_tokens", 0)
    out_tok = getattr(resp.usage, "output_tokens", 0)
    # Sonnet 4.6 pricing: ~$3/M input, $15/M output
    cost_usd = (in_tok * 3.0 + out_tok * 15.0) / 1_000_000
    cost = {
        "input_tokens": in_tok, "output_tokens": out_tok,
        "usd": cost_usd, "inr": cost_usd * 85,
    }
    return code, cost


def render_manim(code: str, out_dir: Path, quality: str = "l") -> Path:
    """Write `code` to a .py file and invoke `manim -ql` to render.
    Returns the path to the resulting MP4."""
    script = out_dir / "explainer_scene.py"
    script.write_text(code, encoding="utf-8")
    media_dir = out_dir / "_media"
    media_dir.mkdir(exist_ok=True)
    # `-ql` = low quality (480p, 15fps) — fastest render
    # `-qm` = medium (720p, 30fps); `-qh` = high (1080p, 60fps)
    cmd = [
        sys.executable, "-m", "manim",
        f"-q{quality}",
        "--media_dir", str(media_dir),
        str(script),
        "ExplainerScene",
    ]
    print(f"  manim render: {' '.join(cmd[3:])}")
    result = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print("--- manim stderr ---")
        print(result.stderr[-3000:])
        raise RuntimeError(
            f"manim render failed (exit {result.returncode}). "
            "See output above.",
        )
    # Find the MP4 — manim emits to _media/videos/<scriptname>/<quality>/<class>.mp4
    candidates = list(media_dir.rglob("ExplainerScene.mp4"))
    if not candidates:
        raise RuntimeError(
            f"no MP4 produced; check {media_dir}",
        )
    return candidates[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concept", required=True)
    ap.add_argument("--lang", default="en")
    ap.add_argument("--grade", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--quality", default="l", choices=["l", "m", "h"],
        help="l=480p fast, m=720p, h=1080p slow",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing — put it in .env", file=sys.stderr)
        return 1

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="manim_vid_"))
    started = time.time()

    print(
        f"[manim] concept={args.concept!r} lang={args.lang} "
        f"grade={args.grade} quality={args.quality}",
    )
    print(f"  workdir → {tmpdir}")
    print(f"  out → {out_path}")

    # 1. Generate scene code
    print("[1/2] generating Manim scene via Claude Sonnet…")
    code, cost = generate_manim_code(args.concept, args.lang, args.grade)
    print(
        f"  Claude: {cost['input_tokens']} in + {cost['output_tokens']} "
        f"out tokens (~${cost['usd']:.4f} = ₹{cost['inr']:.2f})",
    )
    print(f"  scene size: {len(code)} chars, {len(code.splitlines())} lines")
    # Save the code for inspection
    code_archive = out_path.with_suffix(".scene.py")
    code_archive.write_text(code, encoding="utf-8")
    print(f"  scene code → {code_archive}")

    # 2. Render
    print(f"[2/2] rendering with manim (-q{args.quality})…")
    rendered_mp4 = render_manim(code, tmpdir, quality=args.quality)
    # `Path.replace` fails across drives on Windows (tmp=C:, out=D:);
    # use shutil.copy2 + unlink instead for cross-drive safety.
    import shutil
    shutil.copy2(rendered_mp4, out_path)
    size_kb = out_path.stat().st_size // 1024
    elapsed = time.time() - started
    print(
        f"\n✓ wrote {out_path} ({size_kb} KB, {elapsed:.1f}s wall time)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
