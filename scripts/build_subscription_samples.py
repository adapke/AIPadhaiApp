"""Build the demo videos the subscription page advertises.

Each plan in LEARN.md §7 carries a 'Watch a sample' link; this script
populates `samples/m1_*.mp4` so those links resolve. M2/M3/M4 samples
need a live API key (Bhashini / Wav2Lip / HeyGen / etc.) and must be
generated on a machine that has them — this script renders only the
free-tier (M1) samples in three flavours so the marketing page has
something to show today.

Each sample is short (one teaching scene + one quiz question, ~30 s)
so the subscription page can autoplay it without holding the viewer
for long. The script reuses the existing render pipeline; nothing
new in the codebase."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene
from padhai.render import _quiz_narration, render_lesson
from padhai.themes import DARK_ACADEMIC, KINDERGARTEN, WHITEBOARD


def soft_voice_mp3(text: str, out_path: Path) -> None:
    import os
    model = Path(
        os.environ.get(
            "PIPER_MODEL",
            str(Path.home() / ".piper_models" / "en-us-amy-low.onnx"),
        )
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    subprocess.run(
        ["piper", "--model", str(model),
         "--length_scale", "1.18", "--noise_scale", "0.567",
         "--output_file", str(wav)],
        input=text.encode("utf-8"), check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame",
         "-q:a", "4", str(out_path)],
        check=True, capture_output=True,
    )
    wav.unlink()


SAMPLES: dict[str, tuple[Lesson, object]] = {
    "m1_kg": (
        Lesson(
            title="Let's Learn to Count",
            language_code="en", language_name="English", level="kg",
            scenes=[Scene(
                title="One, Two, Three",
                narration=(
                    "Hi friend! Let's count together. One, two, three. "
                    "See the stars? There are three stars. Counting is fun!"
                ),
                bullets=["1, 2, 3 stars on the board", "Count with me!", "Counting helps us add"],
                diagram=None,
            )],
            quiz=[{
                "question": "How many stars do we have here?",
                "options": {"A": "Two", "B": "Three", "C": "Four", "D": "Five"},
                "answer": "B",
            }],
        ),
        KINDERGARTEN,
    ),
    "m1_primary": (
        Lesson(
            title="What Plants Need",
            language_code="en", language_name="English", level="primary",
            scenes=[Scene(
                title="Plants Make Their Own Food",
                narration=(
                    "Plants are special. They make their own food using "
                    "sunlight, water, and air. This process is called "
                    "photosynthesis. Look at the diagram on the board."
                ),
                bullets=[
                    "Plants make their own food",
                    "They need sunlight, water, and air",
                    "The process is called photosynthesis",
                ],
                diagram="photosynthesis",
            )],
            quiz=[{
                "question": "What gas do plants release?",
                "options": {"A": "Carbon dioxide", "B": "Oxygen", "C": "Nitrogen", "D": "Helium"},
                "answer": "B",
            }],
        ),
        WHITEBOARD,
    ),
    "m1_middle": (
        Lesson(
            title="Our Solar System",
            language_code="en", language_name="English", level="middle",
            scenes=[Scene(
                title="Eight Planets Around One Star",
                narration=(
                    "Our solar system has one star, the Sun, with eight "
                    "planets orbiting it. The four closest are small rocky "
                    "worlds; the four farthest are huge gas and ice giants. "
                    "Gravity holds everything in orbit."
                ),
                bullets=[
                    "One star — the Sun — at the centre",
                    "Eight planets orbit around it",
                    "Four rocky, four gas/ice giants",
                ],
                diagram="solar_system",
            )],
            quiz=[{
                "question": "Which is the largest planet?",
                "options": {"A": "Earth", "B": "Mars", "C": "Jupiter", "D": "Saturn"},
                "answer": "C",
            }],
        ),
        DARK_ACADEMIC,
    ),
}


def main() -> None:
    out_dir = Path("samples")
    out_dir.mkdir(exist_ok=True)
    cache_dir = Path(tempfile.mkdtemp(prefix="padhai_samples_"))
    cache = Cache(root=cache_dir)

    for key, (lesson, theme) in SAMPLES.items():
        print(f"\n=== Building {key} sample ({theme.name}) ===")
        for _i, scene in enumerate(lesson.scenes, start=1):
            mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
            soft_voice_mp3(scene.narration, mp3)
            cache.put_audio(scene.narration, lesson.language_code, "gtts", mp3)
            mp3.unlink()
        for qi, q in enumerate(lesson.quiz, start=1):
            q_script, a_script = _quiz_narration(q, qi)
            q_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
            a_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
            soft_voice_mp3(q_script, q_mp3)
            soft_voice_mp3(a_script, a_mp3)
            cache.put_audio(q_script, lesson.language_code, "gtts", q_mp3)
            cache.put_audio(a_script, lesson.language_code, "gtts", a_mp3)
            q_mp3.unlink(); a_mp3.unlink()

        out = out_dir / f"{key}.mp4"
        render_lesson(
            lesson, out, cache=cache,
            theme=theme, render_mode="animated",
            show_teacher=True,
            think_time_seconds=2.0,
        )
        print(f"  -> {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
