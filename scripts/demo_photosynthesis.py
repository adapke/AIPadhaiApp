"""Generate a sample lesson teaching photosynthesis.

Aimed at primary / middle-school level: uses the photosynthesis diagram
(leaf with CO2 + sunlight + water arrows in; O2 + glucose arrows out),
the animated render mode, soft Piper neural narration. Runs fully
offline in this sandbox."""

import subprocess
import tempfile
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene
from padhai.render import _quiz_narration, render_lesson
from padhai.themes import WHITEBOARD

LESSON = Lesson(
    title="How Plants Make Their Food",
    language_code="en",
    language_name="English",
    level="primary",
    scenes=[
        Scene(
            title="Plants Are Special",
            narration=(
                "Hello! Today we will learn how plants make their own food. "
                "Plants are very special. They are the only living things on "
                "Earth that can cook their own meals using just sunlight, "
                "water, and air. This process is called photosynthesis."
            ),
            bullets=[
                "Plants make their own food",
                "They use sunlight, water, and air",
                "The process is called photosynthesis",
            ],
            diagram=None,
        ),
        Scene(
            title="Inside a Leaf",
            narration=(
                "Look at the diagram. Sunlight shines down on the leaf. "
                "Carbon dioxide enters from the air. Water travels up through "
                "the stem from the roots. Inside the green parts of the leaf, "
                "all of these come together and make glucose, the plant's food. "
                "Oxygen, which we breathe, comes out as a gift."
            ),
            bullets=[
                "Sunlight comes from above",
                "CO₂ enters from the air",
                "Water rises from the roots",
                "Glucose and O₂ come out",
            ],
            diagram="photosynthesis",
        ),
        Scene(
            title="Why It Matters",
            narration=(
                "Photosynthesis is the reason we can breathe. Every breath of "
                "oxygen you take was made by a plant or an ocean alga. Plants "
                "are also the start of every food chain. Animals eat plants. "
                "Other animals eat those animals. So in a way, every meal you "
                "have started with a leaf."
            ),
            bullets=[
                "Plants give us oxygen to breathe",
                "They start every food chain",
                "Without plants, no life on Earth",
            ],
            diagram=None,
        ),
    ],
    quiz=[
        {
            "question": "What do plants give out during photosynthesis?",
            "options": {
                "A": "Carbon dioxide",
                "B": "Oxygen",
                "C": "Nitrogen",
                "D": "Hydrogen",
            },
            "answer": "B",
        },
        {
            "question": "Where does the water for photosynthesis come from?",
            "options": {
                "A": "The air",
                "B": "The leaves",
                "C": "The roots",
                "D": "The flowers",
            },
            "answer": "C",
        },
        {
            "question": "What is the name of this whole process?",
            "options": {
                "A": "Respiration",
                "B": "Digestion",
                "C": "Photosynthesis",
                "D": "Evaporation",
            },
            "answer": "C",
        },
    ],
)


def soft_voice_mp3(text: str, out_path: Path) -> None:
    """Offline narration via Piper neural TTS — soft, calm, listenable."""
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
        [
            "piper",
            "--model", str(model),
            "--length_scale", "1.18",
            "--noise_scale", "0.567",
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


def main() -> None:
    output = Path("photosynthesis_demo.mp4").resolve()
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

    print("rendering at 12fps with whiteboard theme + lip-flap + typewriter...")
    out = render_lesson(
        LESSON, output,
        cache=cache,
        theme=WHITEBOARD,
        render_mode="animated",
        think_time_seconds=3.0,
        show_teacher=True,
    )
    print(f"OK -> {out} ({out.stat().st_size:,} bytes)")
    print(cache.stats())


if __name__ == "__main__":
    main()
