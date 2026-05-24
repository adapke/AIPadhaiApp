"""Verify the Cache stores and retrieves Lesson + audio correctly."""

import tempfile
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(root=Path(tmp))

        # Lesson round-trip
        lesson = Lesson(
            title="Photosynthesis",
            language_code="en",
            language_name="English",
            level="middle",
            scenes=[
                Scene(title="Intro", narration="text", bullets=["a", "b"]),
                Scene(title="Recap", narration="text2", bullets=["c", "d"]),
            ],
            quiz=[{"question": "Q?", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "answer": "A"}],
        )
        img_bytes = b"\x89PNG\r\n\x1a\nfake image bytes"
        model = "claude-opus-4-7"

        assert c.get_lesson(img_bytes, "en", "middle", model) is None, "expected miss"
        c.put_lesson(img_bytes, "en", "middle", model, lesson)
        hit = c.get_lesson(img_bytes, "en", "middle", model)
        assert hit is not None and hit.title == "Photosynthesis"
        assert hit.scenes[0].bullets == ["a", "b"]
        assert hit.quiz[0]["answer"] == "A"

        # Different language → different key → miss
        assert c.get_lesson(img_bytes, "hi", "middle", model) is None

        # Audio round-trip
        fake_mp3 = Path(tmp) / "src.mp3"
        fake_mp3.write_bytes(b"ID3 fake mp3")
        dest = Path(tmp) / "dest.mp3"
        assert c.get_audio("hello world", "en", "gtts", dest) is False
        c.put_audio("hello world", "en", "gtts", fake_mp3)
        assert c.get_audio("hello world", "en", "gtts", dest) is True
        assert dest.read_bytes() == b"ID3 fake mp3"

        # Different text → different key → miss
        miss_dest = Path(tmp) / "miss.mp3"
        assert c.get_audio("different text", "en", "gtts", miss_dest) is False

        print("OK — cache round-trips work")
        print(c.stats())


if __name__ == "__main__":
    main()
