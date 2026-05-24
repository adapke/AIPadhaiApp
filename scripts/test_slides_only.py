"""Prove PIL slide rendering + ffmpeg stitching work in this sandbox
(skips gTTS, which needs internet egress to translate.google.com)."""

import subprocess
import tempfile
from pathlib import Path

from padhai.pedagogy import Lesson, Scene
from padhai.render import _draw_slide, _find_font

lesson = Lesson(
    title="Photosynthesis",
    language_code="en",
    language_name="English",
    level="middle",
    scenes=[
        Scene(
            title="What is photosynthesis?",
            narration="(silent test)",
            bullets=[
                "Plants make their own food",
                "Uses sunlight as the energy source",
                "Happens mainly in green leaves",
            ],
        ),
        Scene(
            title="The simple equation",
            narration="(silent test)",
            bullets=[
                "Inputs: water + carbon dioxide",
                "Energy: sunlight + chlorophyll",
                "Outputs: glucose + oxygen",
            ],
        ),
    ],
    quiz=[],
)

title_font, body_font = _find_font(lesson.language_code)
out = Path("test_slides_only.mp4").resolve()

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    clips = []
    for i, scene in enumerate(lesson.scenes, start=1):
        slide = _draw_slide(scene, i, len(lesson.scenes), title_font, body_font, lesson.title)
        png = tmp_path / f"s{i}.png"
        slide.save(png)
        clip = tmp_path / f"s{i}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-loop", "1", "-i", str(png),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-shortest", "-t", "3", "-r", "24",
                str(clip),
            ],
            check=True, capture_output=True,
        )
        clips.append(clip)
    concat = tmp_path / "concat.txt"
    concat.write_text("\n".join(f"file '{c}'" for c in clips))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-c", "copy", str(out)],
        check=True, capture_output=True,
    )

print(f"OK -> {out} ({out.stat().st_size:,} bytes)")
