"""Generate a sample lesson teaching the concept of addition.

Aimed at kindergarten / early primary: short sentences, dot-counting
intuition, kindergarten theme, animated render with lip-flap and
typewriter draw-on-board. Audio is espeak-ng so the demo runs fully
offline in this sandbox."""

import subprocess
import tempfile
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene
from padhai.render import _quiz_narration, render_lesson
from padhai.themes import KINDERGARTEN

LESSON = Lesson(
    title="Learning to Add",
    language_code="en",
    language_name="English",
    level="kg",
    scenes=[
        Scene(
            title="What is Adding?",
            narration=(
                "Hello! Today we will learn about adding. Adding means putting "
                "things together. If you have two apples, and a friend gives "
                "you one more apple, you now have three apples. That is adding!"
            ),
            bullets=[
                "Adding means putting things together",
                "It makes a bigger group",
                "We use the plus sign +",
            ],
            diagram=None,
        ),
        Scene(
            title="Three plus Two equals Five",
            narration=(
                "Look at the dots on the board. On the left, count three blue "
                "dots. Now look at the right, count two red dots. When we put "
                "them all together, we count one, two, three, four, five. "
                "Three plus two equals five!"
            ),
            bullets=[
                "3 blue dots, plus 2 red dots",
                "Put them together",
                "We get 5 dots in total!",
            ],
            diagram="addition_dots",
        ),
        Scene(
            title="The Plus and Equals Signs",
            narration=(
                "The plus sign looks like a small cross. It means 'put together'. "
                "The equals sign is two short lines. It means 'is the same as'. "
                "When we write three plus two equals five, we are saying: three "
                "and two together is the same as five."
            ),
            bullets=[
                "+ means 'put together'",
                "= means 'is the same as'",
                "Read it left to right",
            ],
            diagram=None,
        ),
        Scene(
            title="Adding Zero",
            narration=(
                "Zero is special. Zero means none, nothing at all. If you have "
                "four toys and you add zero more toys, you still have four toys. "
                "Adding zero never changes the number. Four plus zero equals "
                "four!"
            ),
            bullets=[
                "Zero means nothing",
                "Number + 0 stays the same",
                "Try: 4 + 0 = 4",
            ],
            diagram=None,
        ),
    ],
    quiz=[
        {
            "question": "What is two plus three?",
            "options": {"A": "Four", "B": "Five", "C": "Six", "D": "Seven"},
            "answer": "B",
        },
        {
            "question": "What is seven plus zero?",
            "options": {"A": "Zero", "B": "One", "C": "Seven", "D": "Eight"},
            "answer": "C",
        },
    ],
)


def soft_voice_mp3(text: str, out_path: Path) -> None:
    """Offline narration via Piper TTS (free, neural, runs on CPU).

    Piper sounds like a real soft-spoken teacher, not the buzzy formant
    synth espeak-ng produces. We slow the rate slightly (length_scale=1.18)
    for a calm, deliberate pace — the kind of voice you can listen to
    repeatedly without irritation.

    Voice model: en-us-amy-low.onnx (downloaded from the Piper GitHub
    release). For Indic-script lessons, point PIPER_MODEL_HI etc. at the
    matching language-specific Piper model.

    For production: swap to BhashiniProvider for native Indic voices."""
    import os
    model = Path(
        os.environ.get("PIPER_MODEL", str(Path.home() / ".piper_models" / "en-us-amy-low.onnx"))
    )
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = Path(f.name)
    subprocess.run(
        [
            "piper",
            "--model", str(model),
            "--length_scale", "1.18",      # slightly slower = softer pace
            "--noise_scale", "0.567",      # less variance = calmer
            "--noise_w", "0.7",
            "--output_file", str(wav),
        ],
        input=text.encode("utf-8"),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame",
         "-q:a", "4", str(out_path)],
        check=True, capture_output=True,
    )
    wav.unlink()


# Back-compat alias for the previous espeak-based helper.
espeak_mp3 = soft_voice_mp3


def main() -> None:
    output = Path("addition_demo.mp4").resolve()
    cache_dir = Path(tempfile.mkdtemp(prefix="padhai_demo_"))
    cache = Cache(root=cache_dir)

    print("synthesising narration with Piper neural TTS (offline)...")
    for i, scene in enumerate(LESSON.scenes, start=1):
        mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        soft_voice_mp3(scene.narration, mp3)
        cache.put_audio(scene.narration, LESSON.language_code, "gtts", mp3)
        mp3.unlink()
        print(f"  scene {i}: '{scene.title}' ✓")

    for qi, q in enumerate(LESSON.quiz, start=1):
        q_script, a_script = _quiz_narration(q, qi)
        q_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        a_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        soft_voice_mp3(q_script, q_mp3)
        soft_voice_mp3(a_script, a_mp3)
        cache.put_audio(q_script, LESSON.language_code, "gtts", q_mp3)
        cache.put_audio(a_script, LESSON.language_code, "gtts", a_mp3)
        q_mp3.unlink()
        a_mp3.unlink()
        print(f"  quiz {qi} ✓")

    print("rendering at 12fps with KG theme + lip-flap + typewriter draw...")
    out = render_lesson(
        LESSON, output,
        cache=cache,
        theme=KINDERGARTEN,
        render_mode="animated",
        think_time_seconds=3.0,
        show_teacher=True,
    )
    print(f"OK -> {out} ({out.stat().st_size:,} bytes)")
    print(cache.stats())


if __name__ == "__main__":
    main()
