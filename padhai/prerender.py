"""Pre-render manifest — feed a directory of textbook pages through
Claude's Batch API at 50% off list price.

The Batch API is ideal for content that doesn't need real-time
generation: NCERT Class 10 syllabi, state-board chapters, the
top-1000-most-requested pages identified from logs, etc. Submit
overnight, get back lesson JSON the next morning, write it to the
lesson cache. Once a lesson is cached, sync generate_lesson() calls
for the same (image, language, level, model) return in <10ms without
hitting Claude at all.

Pair this with the video cache (padhai/cache.py::get_video) and the
most-trafficked content is pre-rendered AND pre-cached as MP4, so
both Claude AND TTS AND render compute are amortised to ~₹0 per
serve. LEARN.md §6 explains the cost math.

Usage:
    from padhai.prerender import submit_batch, fetch_results

    batch_id = submit_batch(
        image_paths=["scans/ch3_p1.png", "scans/ch3_p2.png", ...],
        languages=["en", "hi", "ta"],
        levels=["middle", "secondary"],
    )
    # ... wait, can be hours, can pause and re-poll later ...
    n_lessons = fetch_results(batch_id, cache=Cache())
    print(f"cached {n_lessons} lessons")

CLI is in scripts/prerender.py. The batch may take up to 24 hours;
typically completes within an hour. Results are valid for 29 days."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from .cache import Cache
from .pedagogy import (
    MODEL,
    SYSTEM_PROMPT,
    build_schema,
    build_user_text,
    image_to_block,
    parse_lesson_json,
)


def submit_batch(
    image_paths: Iterable[Path | str],
    languages: Iterable[str],
    levels: Iterable[str],
    client: anthropic.Anthropic | None = None,
) -> str:
    """Submit a batch of (image × language × level) lesson-generation
    requests to the Anthropic Batch API. Returns the batch ID.

    The Cartesian product gets large fast — 100 images × 3 languages ×
    2 levels = 600 requests. The Batch API supports up to 100k requests
    or 256 MB per batch; size accordingly.

    `custom_id` for each request encodes (image_stem, language, level)
    so `fetch_results` can map results back to cache keys."""
    client = client or anthropic.Anthropic()
    image_paths = [Path(p) for p in image_paths]
    languages = list(languages)
    levels = list(levels)

    requests: list[Request] = []
    for image_path in image_paths:
        for lang in languages:
            for lvl in levels:
                custom_id = f"{image_path.stem}|{lang}|{lvl}"
                requests.append(
                    Request(
                        custom_id=custom_id,
                        params=MessageCreateParamsNonStreaming(
                            model=MODEL,
                            max_tokens=8000,
                            system=SYSTEM_PROMPT,
                            thinking={"type": "adaptive"},
                            output_config={
                                "effort": "high",
                                "format": {
                                    "type": "json_schema",
                                    "schema": build_schema(lvl),
                                },
                            },
                            messages=[
                                {
                                    "role": "user",
                                    "content": [
                                        image_to_block(image_path),
                                        {"type": "text", "text": build_user_text(lang, lvl)},
                                    ],
                                }
                            ],
                        ),
                    )
                )

    batch = client.messages.batches.create(requests=requests)
    return batch.id


def wait_for_batch(
    batch_id: str,
    poll_seconds: float = 60.0,
    timeout_seconds: float = 24 * 3600.0,
    client: anthropic.Anthropic | None = None,
    on_status=None,
) -> dict:
    """Poll until the batch finishes. Returns the batch metadata.
    `on_status(batch)` is called once per poll if provided — use it to
    print progress."""
    client = client or anthropic.Anthropic()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        batch = client.messages.batches.retrieve(batch_id)
        if on_status is not None:
            on_status(batch)
        if batch.processing_status == "ended":
            return batch.model_dump() if hasattr(batch, "model_dump") else dict(batch)
        time.sleep(poll_seconds)
    raise TimeoutError(f"batch {batch_id} did not finish within {timeout_seconds}s")


def fetch_results(
    batch_id: str,
    image_paths: Iterable[Path | str],
    cache: Cache,
    client: anthropic.Anthropic | None = None,
) -> int:
    """Walk the completed batch's results, parse each Lesson, and write
    it to the lesson cache so subsequent generate_lesson() calls hit
    cache without round-tripping to Claude.

    `image_paths` must include the same images used in submit_batch so
    we can resolve the image_bytes for each custom_id's cache key.
    Returns the count of lessons cached."""
    client = client or anthropic.Anthropic()
    # build a stem → image_bytes map so we can resolve cache keys
    stem_to_bytes: dict[str, bytes] = {}
    for p in image_paths:
        p = Path(p)
        stem_to_bytes[p.stem] = p.read_bytes()

    cached = 0
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            continue
        msg = result.result.message
        text = next((b.text for b in msg.content if b.type == "text"), None)
        if text is None:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        parts = result.custom_id.split("|")
        if len(parts) != 3:
            continue
        stem, lang, lvl = parts
        if stem not in stem_to_bytes:
            continue
        try:
            lesson = parse_lesson_json(data, lang, lvl)
        except Exception:
            continue
        cache.put_lesson(stem_to_bytes[stem], lang, lvl, MODEL, lesson)
        cached += 1
    return cached
