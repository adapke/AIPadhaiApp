"""End-to-end render of a Lesson in reveal mode across all 3 themes.
Uses a fake silent audio stub so we can verify the bullet-by-bullet
build-up and the theme system without needing TTS network access."""

import subprocess
import tempfile
from pathlib import Path

from padhai.pedagogy import Lesson, Scene
from padhai.render import _make_reveal_clip, _find_font
from padhai.themes import DARK_ACADEMIC, KINDERGARTEN, WHITEBOARD

scene = Scene(
    title="What is photosynthesis?",
    narration="placeholder",
    bullets=[
        "Plants make their own food",
        "Uses sunlight as the energy source",
        "Happens mainly in green leaves",
        "Releases oxygen we breathe",
    ],
)

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    silent_mp3 = tmp_path / "silent.mp3"
    # 8 seconds of silence so reveal stages have time to play
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", "8", "-c:a", "libmp3lame", str(silent_mp3),
        ],
        check=True, capture_output=True,
    )

    for theme, name in [
        (DARK_ACADEMIC, "dark_academic"),
        (WHITEBOARD, "whiteboard"),
        (KINDERGARTEN, "kindergarten"),
    ]:
        out = Path(f"reveal_{name}.mp4").resolve()
        title_font, body_font = _find_font("en", theme=theme)
        _make_reveal_clip(
            scene, 1, 1, title_font, body_font,
            "Photosynthesis", theme, silent_mp3, out, tmp_path,
            tail_pad_seconds=0.5,
        )
        # extract a frame at second 6 (latest reveal stage)
        png = Path(f"reveal_{name}.png").resolve()
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(out), "-ss", "6", "-vframes", "1", str(png)],
            check=True, capture_output=True,
        )
        print(f"OK {name}: video={out.stat().st_size:,}B, sample frame={png}")
