#!/usr/bin/env python3
"""prod-13 — Single-concept explainer video generator.

Given a concept (e.g. "Newton's first law of motion") and a language,
produces a ~60-90 second MP4 with:
  * Claude-generated script (intro + 3-4 key points + outro)
  * gTTS narration per section
  * PIL-rendered slides with title + key text
  * moviepy assembly of slides + audio → MP4

Built for prod-13's "one example first, then decide" plan. Cost per
run: ~₹0.50-1 (one Haiku call) + free TTS + free local render.

Usage:
  python scripts/generate_concept_video.py \\
      --concept "Newton's first law of motion" \\
      --lang en \\
      --out ~/.padhai/cache/concept_videos/newton1_en.mp4

Env: needs ANTHROPIC_API_KEY in shell or .env.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

# Clear EMPTY Anthropic-prefix env vars — the anthropic SDK 0.96 reads
# ANTHROPIC_AUTH_TOKEN first and falls back to ANTHROPIC_API_KEY only if
# the former is unset. An *empty* AUTH_TOKEN makes it send
# `Authorization: Bearer ` which httpx rejects as LocalProtocolError.
for _k in list(os.environ):
    if (
        _k.startswith("ANTHROPIC_")
        and _k != "ANTHROPIC_API_KEY"
        and not os.environ[_k].strip()
    ):
        del os.environ[_k]

# Language code → gTTS code (a couple need remapping)
GTTS_LANG = {
    "en": "en", "hi": "hi", "ta": "ta", "te": "te",
    "kn": "kn", "ml": "ml", "mr": "mr",
    "bn": "bn", "gu": "gu", "pa": "pa-IN",
}

# Language code → human label for the Claude prompt
LANG_NAME = {
    "en": "English", "hi": "Hindi", "ta": "Tamil", "te": "Telugu",
    "kn": "Kannada", "ml": "Malayalam", "mr": "Marathi",
    "bn": "Bengali", "gu": "Gujarati", "pa": "Punjabi",
}


def generate_script(concept: str, lang: str, grade: int) -> dict:
    """Ask Claude Haiku for a structured explainer script.

    Returns: {
        "title": str,
        "intro": str,
        "key_points": [{"heading": str, "narration": str}, ...],
        "outro": str,
    }
    """
    from anthropic import Anthropic

    from padhai import models as _models

    client = Anthropic()
    lang_label = LANG_NAME.get(lang, lang)
    system = (
        f"You are an expert educator writing a 60-90 second explainer "
        f"video script for Indian students (grade {grade}) in {lang_label}. "
        f"Output strict JSON ONLY, no preamble, no markdown fences:\n"
        f"{{\n"
        f'  "title": "<concept name in {lang_label}>",\n'
        f'  "intro": "<2-sentence hook in {lang_label}>",\n'
        f'  "key_points": [\n'
        f'    {{"heading": "<short label>", "narration": "<2-3 sentences>"}},\n'
        f"    ...3-4 entries total\n"
        f"  ],\n"
        f'  "outro": "<1-sentence summary in {lang_label}>"\n'
        f"}}\n\n"
        f"All text must be in {lang_label}. Use simple words a student "
        f"would understand. Each narration block should be 15-20 seconds "
        f"when spoken at normal pace (about 40-60 words)."
    )
    resp = client.messages.create(
        model=_models.HAIKU_MODEL,
        max_tokens=1200,
        system=system,
        messages=[{
            "role": "user",
            "content": f"Concept: {concept}",
        }],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    # Strip leading ```json or ``` if model added them
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Claude returned non-JSON: {e}\n--- raw response ---\n{text}",
        ) from e
    # Cost report
    in_tok = getattr(resp.usage, "input_tokens", 0)
    out_tok = getattr(resp.usage, "output_tokens", 0)
    # Haiku 4.5 pricing: $1/M input, $5/M output (approx)
    cost_usd = (in_tok * 1.0 + out_tok * 5.0) / 1_000_000
    print(
        f"  Claude: {in_tok} in + {out_tok} out tokens "
        f"(~${cost_usd:.4f} = ₹{cost_usd*85:.2f})",
    )
    return data


def synthesize_audio(text: str, lang: str, out_path: Path) -> float:
    """gTTS narration → MP3. Returns the audio duration in seconds."""
    from gtts import gTTS
    gtts_lang = GTTS_LANG.get(lang, "en")
    tts = gTTS(text=text, lang=gtts_lang, slow=False)
    tts.save(str(out_path))
    # Probe duration
    from moviepy import AudioFileClip
    with AudioFileClip(str(out_path)) as a:
        return float(a.duration)


def render_slide(
    title: str, heading: str, body: str,
    width: int = 1280, height: int = 720,
    out_path: Path | None = None,
) -> Path:
    """PIL renders a slide PNG with title at top, heading + body
    centered. No avatar yet — just clean typography on a soft gradient.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), (18, 24, 38))
    draw = ImageDraw.Draw(img)

    # Subtle gradient bar across top
    for y in range(80):
        c = int(60 + (y / 80) * 30)
        draw.line([(0, y), (width, y)], fill=(c, c, c + 20))

    # Font setup — fall back to default if no TrueType available
    def font(size: int):
        for f in [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            if Path(f).is_file():
                return ImageFont.truetype(f, size)
        return ImageFont.load_default()

    # Brand strip
    draw.text((40, 24), "AI Pathshala", fill=(170, 200, 255), font=font(28))

    # Title (concept name)
    draw.text((40, 100), title, fill=(255, 255, 255), font=font(40))

    # Heading
    draw.text((40, 200), heading, fill=(255, 220, 130), font=font(54))

    # Body — word-wrap to 60 chars per line
    f_body = font(34)
    lines = _wrap(body, 50)
    y = 320
    for line in lines[:8]:  # cap at 8 lines so it fits
        draw.text((40, y), line, fill=(220, 230, 245), font=f_body)
        y += 50

    if out_path is None:
        out_path = Path(tempfile.mktemp(suffix=".png"))
    img.save(str(out_path))
    return out_path


def _wrap(text: str, width: int) -> list[str]:
    """Simple word-wrap — splits on whitespace, packs to `width` chars
    per line. Doesn't break long words."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def assemble_video(
    sections: list[dict], out_path: Path, fps: int = 24,
) -> None:
    """Each `section` = {"slide": Path, "audio": Path, "duration": float}.
    Concatenates slides over audio into one MP4."""
    from moviepy import (
        AudioFileClip,
        ImageClip,
        concatenate_videoclips,
    )

    clips = []
    audios = []
    for s in sections:
        a = AudioFileClip(str(s["audio"]))
        c = (
            ImageClip(str(s["slide"]))
            .with_duration(a.duration)
            .with_audio(a)
        )
        clips.append(c)
        audios.append(a)
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(
        str(out_path),
        fps=fps,
        codec="libx264",
        audio_codec="aac",
        logger=None,
    )
    final.close()
    for a in audios:
        a.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--concept", required=True,
                    help='topic, e.g. "Newton\'s first law of motion"')
    ap.add_argument("--lang", default="en",
                    choices=list(LANG_NAME.keys()))
    ap.add_argument("--grade", type=int, default=10)
    ap.add_argument("--out", required=True,
                    help="output MP4 path")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY not set. Put it in .env or shell env.",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()

    print(
        f"[generate] concept={args.concept!r} lang={args.lang} "
        f"grade={args.grade}",
    )
    print(f"  out → {out_path}")

    # 1. Script
    print("[1/3] generating script via Claude Haiku…")
    script = generate_script(args.concept, args.lang, args.grade)
    print(f"  title: {script['title']!r}")
    print(f"  sections: 1 intro + {len(script['key_points'])} key + 1 outro")

    # 2. Audio per section
    print("[2/3] synthesising narration (gTTS — free)…")
    sections: list[dict] = []
    tmpdir = Path(tempfile.mkdtemp(prefix="concept_vid_"))

    def add_section(idx: int, heading: str, narration: str) -> None:
        audio_p = tmpdir / f"sec_{idx:02d}.mp3"
        slide_p = tmpdir / f"sec_{idx:02d}.png"
        duration = synthesize_audio(narration, args.lang, audio_p)
        render_slide(script["title"], heading, narration, out_path=slide_p)
        sections.append({
            "slide": slide_p, "audio": audio_p, "duration": duration,
        })
        print(f"  sec_{idx:02d}: {heading!r} ({duration:.1f}s)")

    add_section(0, script["title"], script["intro"])
    for i, kp in enumerate(script["key_points"], start=1):
        add_section(i, kp["heading"], kp["narration"])
    add_section(len(sections), "Summary", script["outro"])

    # 3. Assemble
    print("[3/3] assembling MP4 (moviepy + bundled ffmpeg)…")
    assemble_video(sections, out_path)

    duration = time.time() - started
    total_sec = sum(s["duration"] for s in sections)
    size_kb = out_path.stat().st_size // 1024
    print(
        f"\n✓ wrote {out_path} ({size_kb} KB, {total_sec:.1f}s playback, "
        f"{duration:.1f}s wall time)",
    )

    # Save the script JSON alongside for easy inspection
    script_path = out_path.with_suffix(".script.json")
    script_path.write_text(
        json.dumps(script, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  script JSON → {script_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
