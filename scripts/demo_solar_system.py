"""Generate a sample 'Solar System' video using a hand-authored Lesson.

Bypasses Claude (no API key needed) and pre-warms the audio cache with
silent MP3s of the right duration. This lets us demo the rendering
pipeline — slides, reveal animation, quiz scenes — without depending on
TTS network access. On a real machine with ANTHROPIC_API_KEY and gTTS or
Bhashini reachable, the same Lesson would be auto-generated from a
textbook scan and the silent audio would be replaced with real narration."""

import subprocess
import tempfile
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene
from padhai.render import _quiz_narration, render_lesson
from padhai.themes import DARK_ACADEMIC


LESSON = Lesson(
    title="Our Solar System",
    language_code="en",
    language_name="English",
    level="middle",
    scenes=[
        Scene(
            title="What is the Solar System?",
            narration=(
                "Our solar system is a vast collection of objects bound by "
                "gravity to a single star — the Sun. Eight planets orbit "
                "around it, along with dwarf planets, moons, and countless "
                "asteroids and comets. It formed about 4.6 billion years ago "
                "from a giant cloud of gas and dust."
            ),
            bullets=[
                "One star — the Sun — at the centre",
                "Eight planets, all orbiting the Sun",
                "Plus moons, asteroids, dwarf planets, comets",
                "Formed 4.6 billion years ago",
            ],
            diagram="solar_system",
        ),
        Scene(
            title="The Sun: Our Star",
            narration=(
                "At the centre sits the Sun, a giant ball of glowing plasma. "
                "It contains ninety-nine point eight percent of all the mass "
                "in our solar system. Deep in its core, hydrogen atoms fuse "
                "into helium, releasing enormous amounts of light and heat. "
                "This is the energy that warms Earth and powers nearly all "
                "life on our planet."
            ),
            bullets=[
                "A massive ball of hot plasma",
                "99.8% of the mass in the system",
                "Hydrogen fuses into helium in its core",
                "Source of all light and heat on Earth",
            ],
        ),
        Scene(
            title="The Rocky Planets",
            narration=(
                "The four planets closest to the Sun are small, rocky worlds "
                "with solid surfaces. Mercury is the smallest and closest. "
                "Venus is hidden under thick clouds and is the hottest. Earth, "
                "the third planet, is the only known home of life. Mars, the "
                "red planet, has the largest volcano in the solar system."
            ),
            bullets=[
                "Mercury — smallest, closest to Sun",
                "Venus — hottest, thick poisonous clouds",
                "Earth — only known home of life",
                "Mars — the red planet",
            ],
        ),
        Scene(
            title="The Gas and Ice Giants",
            narration=(
                "The four outer planets are huge and made mostly of gas and "
                "ice. Jupiter is the largest, big enough to fit over a "
                "thousand Earths inside. Saturn is famous for its rings of "
                "ice and rock. Uranus spins tilted on its side. Neptune has "
                "the fastest winds in the solar system, over two thousand "
                "kilometres per hour."
            ),
            bullets=[
                "Jupiter — largest, has the Great Red Spot",
                "Saturn — famous for its bright rings",
                "Uranus — tilted on its side",
                "Neptune — coldest, fastest winds",
            ],
        ),
        Scene(
            title="Moons, Asteroids, and Comets",
            narration=(
                "Beyond planets, our solar system has many smaller objects. "
                "Most planets have moons; Earth has one, but Jupiter and "
                "Saturn each have over eighty. A belt of rocky asteroids "
                "lies between Mars and Jupiter. Comets are icy travellers "
                "that grow glowing tails when they pass near the Sun."
            ),
            bullets=[
                "Moons orbit planets — Earth has one",
                "Asteroid belt sits between Mars and Jupiter",
                "Comets are icy with long bright tails",
                "Dwarf planets like Pluto",
            ],
        ),
        Scene(
            title="Held Together by Gravity",
            narration=(
                "What holds all of this together is gravity — an invisible "
                "force of attraction between any two objects with mass. The "
                "Sun's enormous gravity keeps every planet in orbit. Earth's "
                "gravity in turn holds the Moon close. Without gravity, "
                "every planet, moon, and asteroid would drift off into deep "
                "space."
            ),
            bullets=[
                "Gravity is the pull between masses",
                "Sun's gravity holds the planets in orbit",
                "Earth's gravity holds the Moon",
                "Without gravity, nothing would orbit",
            ],
        ),
    ],
    quiz=[
        {
            "question": "Which is the largest planet in our solar system?",
            "options": {
                "A": "Earth",
                "B": "Mars",
                "C": "Jupiter",
                "D": "Saturn",
            },
            "answer": "C",
        },
        {
            "question": "How many planets are there in our solar system?",
            "options": {"A": "7", "B": "8", "C": "9", "D": "10"},
            "answer": "B",
        },
        {
            "question": "What force keeps planets in orbit around the Sun?",
            "options": {
                "A": "Friction",
                "B": "Gravity",
                "C": "Magnetism",
                "D": "Wind",
            },
            "answer": "B",
        },
    ],
)


def espeak_mp3(text: str, out_path: Path) -> None:
    """Synthesise text with espeak-ng (fully offline) and convert to MP3.

    Used so the demo has real speech audio with realistic amplitude
    variation, which the lip-flap envelope can pick up. On a normal
    machine with ANTHROPIC_API_KEY + gTTS/Bhashini access, the pipeline
    uses those instead of espeak-ng — this is purely a sandbox helper."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = Path(f.name)
    subprocess.run(
        ["espeak-ng", "-w", str(wav_path), "-s", "170", text],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path),
         "-c:a", "libmp3lame", "-q:a", "5", str(out_path)],
        check=True, capture_output=True,
    )
    wav_path.unlink()


def main() -> None:
    output = Path("solar_system_demo.mp4").resolve()
    cache_dir = Path(tempfile.mkdtemp(prefix="padhai_demo_"))
    cache = Cache(root=cache_dir)

    print("synthesising narration with espeak-ng (offline)...")
    for i, scene in enumerate(LESSON.scenes, start=1):
        mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        espeak_mp3(scene.narration, mp3)
        cache.put_audio(scene.narration, LESSON.language_code, "gtts", mp3)
        mp3.unlink()
        print(f"  scene {i}: '{scene.title[:50]}' ✓")

    for qi, q in enumerate(LESSON.quiz, start=1):
        q_script, a_script = _quiz_narration(q, qi)
        q_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        a_mp3 = Path(tempfile.mkstemp(suffix=".mp3")[1])
        espeak_mp3(q_script, q_mp3)
        espeak_mp3(a_script, a_mp3)
        cache.put_audio(q_script, LESSON.language_code, "gtts", q_mp3)
        cache.put_audio(a_script, LESSON.language_code, "gtts", a_mp3)
        q_mp3.unlink()
        a_mp3.unlink()
        print(f"  quiz {qi} ✓")

    print("rendering at 12fps with lip-flap + typewriter draw...")
    out = render_lesson(
        LESSON, output,
        cache=cache,
        theme=DARK_ACADEMIC,
        render_mode="animated",
        think_time_seconds=3.0,
        show_teacher=True,
    )
    print(f"OK -> {out} ({out.stat().st_size:,} bytes)")
    print(cache.stats())


if __name__ == "__main__":
    main()
