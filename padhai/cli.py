"""padhai: scan a textbook page and turn it into a video lesson."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .cache import Cache
from .pedagogy import LEVEL_GUIDANCE, SUPPORTED_LANGUAGES, generate_lesson
from .render import render_lesson
from .themes import REGISTRY as THEME_REGISTRY
from .themes import theme_for_level


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="padhai",
        description="Scan a textbook page and generate a video lesson.",
    )
    parser.add_argument("image", type=Path, help="path to a page image (JPG/PNG)")
    parser.add_argument(
        "-l", "--language",
        default="en",
        choices=sorted(SUPPORTED_LANGUAGES),
        help="language code for narration (default: en)",
    )
    parser.add_argument(
        "-d", "--level",
        default="middle",
        choices=sorted(LEVEL_GUIDANCE),
        help="difficulty level (default: middle)",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path, default=Path("lesson.mp4"),
        help="output MP4 path (default: lesson.mp4)",
    )
    parser.add_argument(
        "--script-only",
        action="store_true",
        help="generate the JSON lesson script but skip video rendering",
    )
    parser.add_argument(
        "--script-out",
        type=Path,
        help="also write the lesson script JSON to this path",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="disable the filesystem cache (always regenerate)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="cache directory (default: $PADHAI_CACHE_DIR or ~/.padhai/cache)",
    )
    parser.add_argument(
        "--no-quiz",
        action="store_true",
        help="skip the quiz scenes at the end of the video",
    )
    parser.add_argument(
        "--think-time",
        type=float, default=4.0,
        help="seconds of silence after each quiz question (default: 4.0)",
    )
    parser.add_argument(
        "--theme",
        choices=sorted(THEME_REGISTRY),
        help="override the visual theme (default picked by level)",
    )
    parser.add_argument(
        "--render-mode",
        choices=["reveal", "static"], default="reveal",
        help="reveal = progressive bullet build (default); static = one slide per scene",
    )
    parser.add_argument(
        "--teacher",
        action="store_true",
        help="show an animated cartoon teacher next to a whiteboard panel",
    )

    args = parser.parse_args(argv)
    cache = Cache(root=args.cache_dir, enabled=not args.no_cache)

    if not args.image.exists():
        print(f"error: image not found: {args.image}", file=sys.stderr)
        return 2

    # Video-cache short-circuit: if this exact (image + language + level +
    # theme + provider + render mode) has been rendered before, copy the
    # cached MP4 and skip Claude, TTS, and ffmpeg entirely. This is the
    # primary cost lever at scale — popular NCERT pages are requested
    # millions of times across users; only the first one pays full cost.
    from .talking_head import get_provider as get_th_provider
    image_bytes = args.image.read_bytes()
    theme_for_cache = theme_for_level(args.level, args.theme)
    th_provider = get_th_provider() if args.teacher else None
    provider_name = th_provider.name if th_provider else "none"
    if not args.script_only and cache.get_video(
        image_bytes, args.language, args.level,
        theme_for_cache.name, provider_name, args.render_mode,
        args.output,
    ):
        print(f"[cache hit] full video served from cache -> {args.output}")
        print(cache.stats())
        return 0

    print(f"[1/3] reading page and writing lesson script ({args.language}, {args.level})...")
    lesson = generate_lesson(args.image, args.language, args.level, cache=cache)
    print(f"      title: {lesson.title}")
    print(f"      scenes: {len(lesson.scenes)}")

    if args.script_out:
        args.script_out.write_text(
            json.dumps(asdict(lesson), ensure_ascii=False, indent=2)
        )
        print(f"      script written to {args.script_out}")

    if args.script_only:
        return 0

    print("[2/3] generating narration audio (gTTS) and slides...")
    print("[3/3] stitching video via ffmpeg...")
    theme = theme_for_level(args.level, args.theme)
    print(f"      theme: {theme.name}, render mode: {args.render_mode}")
    out = render_lesson(
        lesson, args.output,
        cache=cache,
        include_quiz=not args.no_quiz,
        think_time_seconds=args.think_time,
        theme=theme,
        render_mode=args.render_mode,
        show_teacher=args.teacher,
        talking_head_provider=th_provider,
    )
    # populate the video cache so repeat requests for the same page hit
    cache.put_video(
        image_bytes, args.language, args.level,
        theme.name, provider_name, args.render_mode,
        out,
    )
    print(f"done -> {out}")
    print(cache.stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
