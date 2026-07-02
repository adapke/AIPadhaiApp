"""Filesystem cache for expensive PadhAI operations.

Keys are SHA-256 hashes of inputs so the same scan + language + level
always resolves to the same cache entry across runs.

Cache directory defaults to ~/.padhai/cache and can be overridden via
PADHAI_CACHE_DIR or --cache-dir on the CLI.

Three tiers, layered for compounding savings:
- lessons/  — the structured Lesson JSON (one Claude vision call ≈ ₹5)
- audio/    — per-scene MP3 (one TTS call; varies by provider)
- videos/   — final rendered MP4 (full lesson; saves Claude + TTS +
              talking-head + render compute, the big one for popular pages)

Why the video tier matters most economically:

  If 100,000 students all scan the same NCERT Class 10 Chapter 3 page,
  without the video cache that's 100,000 Claude calls (~₹5L), 100,000
  TTS renders, 100,000 Wav2Lip GPU runs (~₹3-4L on the M3 tier),
  100,000 ffmpeg encodes.

  With the video cache, only the FIRST student pays the full
  generation cost. The other 99,999 hit the cache and get the MP4 in
  ~10ms. At unicorn scale this is the difference between a viable
  business and a cost-runaway.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import asdict
from pathlib import Path

from .pedagogy import Lesson, Scene


def _default_cache_dir() -> Path:
    custom = os.environ.get("PADHAI_CACHE_DIR")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".padhai" / "cache"


class Cache:
    def __init__(self, root: Path | None = None, enabled: bool = True):
        self.enabled = enabled
        self.root = (root or _default_cache_dir()).resolve()
        if enabled:
            for sub in ("lessons", "audio", "videos", "recaps", "notes", "explainers"):
                (self.root / sub).mkdir(parents=True, exist_ok=True)
        self.lesson_hits = 0
        self.lesson_misses = 0
        self.audio_hits = 0
        self.audio_misses = 0
        self.video_hits = 0
        self.video_misses = 0

    # ---------- key derivation ----------

    @staticmethod
    def _hash(*chunks: bytes) -> str:
        h = hashlib.sha256()
        for c in chunks:
            h.update(c)
            h.update(b"\x00")
        return h.hexdigest()[:32]

    def lesson_key(self, image_bytes: bytes, language: str, level: str, model: str) -> str:
        return self._hash(
            image_bytes, language.encode(), level.encode(), model.encode()
        )

    def audio_key(self, text: str, language: str, provider: str) -> str:
        return self._hash(text.encode("utf-8"), language.encode(), provider.encode())

    # ---------- lessons ----------

    def get_lesson(
        self, image_bytes: bytes, language: str, level: str, model: str
    ) -> Lesson | None:
        if not self.enabled:
            return None
        key = self.lesson_key(image_bytes, language, level, model)
        return self._read_lesson(key)

    def learning_path_key(
        self, student_class: int, subjects: list[str], weeks: int,
        daily_minutes: int, focus_topics: list[str], library_size: int,
    ) -> str:
        return self._hash(
            str(student_class).encode(),
            ",".join(sorted(subjects)).encode(),
            str(weeks).encode(), str(daily_minutes).encode(),
            ",".join(sorted(focus_topics)).encode(),
            str(library_size).encode(),
        )

    def get_learning_path(self, key: str) -> dict | None:
        if not self.enabled:
            return None
        path = self.root / "learning_paths" / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put_learning_path(self, key: str, plan: dict) -> None:
        if not self.enabled:
            return
        (self.root / "learning_paths").mkdir(parents=True, exist_ok=True)
        path = self.root / "learning_paths" / f"{key}.json"
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def get_curriculum_matches(self, lesson_id: str) -> list[dict] | None:
        if not self.enabled:
            return None
        path = self.root / "curriculum" / f"{lesson_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put_curriculum_matches(self, lesson_id: str, matches: list[dict]) -> None:
        if not self.enabled:
            return
        (self.root / "curriculum").mkdir(parents=True, exist_ok=True)
        path = self.root / "curriculum" / f"{lesson_id}.json"
        path.write_text(json.dumps(matches, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def get_flashcards(self, lesson_id: str) -> list[dict] | None:
        """Flashcards generated from a Lesson live under a separate tier
        so the same lesson can have its video cached, its audio cached,
        AND its derived flashcards cached without keying collisions."""
        if not self.enabled:
            return None
        path = self.root / "flashcards" / f"{lesson_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put_flashcards(self, lesson_id: str, cards: list[dict]) -> None:
        if not self.enabled:
            return
        (self.root / "flashcards").mkdir(parents=True, exist_ok=True)
        path = self.root / "flashcards" / f"{lesson_id}.json"
        path.write_text(json.dumps(cards, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def explainer_key(
        self, topic: str, language: str, level: str, scope_key: str = "",
    ) -> str:
        # prod-212 — `scope_key` (board/exam) makes a curriculum-grounded
        # explainer cache-distinct from the generic one, so a CBSE-10 student's
        # "Trigonometry" doesn't collide with an ungrounded request. Appended
        # only when non-empty, so pre-existing (ungrounded) cache keys are
        # preserved byte-for-byte.
        parts = [
            topic.strip().lower().encode("utf-8"),
            language.encode(),
            level.encode(),
        ]
        if scope_key:
            parts.append(scope_key.strip().lower().encode("utf-8"))
        return self._hash(*parts)

    def get_explainer(
        self, topic: str, language: str, level: str, scope_key: str = "",
    ) -> dict | None:
        if not self.enabled:
            return None
        key = self.explainer_key(topic, language, level, scope_key)
        path = self.root / "explainers" / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def put_explainer(
        self, topic: str, language: str, level: str, payload: dict,
        scope_key: str = "",
    ) -> None:
        if not self.enabled:
            return
        (self.root / "explainers").mkdir(parents=True, exist_ok=True)
        key = self.explainer_key(topic, language, level, scope_key)
        path = self.root / "explainers" / f"{key}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def get_recap_text(self, lesson_id: str) -> str | None:
        """Audio recap text. Cached per lesson so the 2nd listener is free.
        Different tier from `audio/` (which is keyed by narration text)
        because we want one MP3 per lesson, not one per scene."""
        if not self.enabled:
            return None
        path = self.root / "recaps" / f"{lesson_id}.txt"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def put_recap_text(self, lesson_id: str, text: str) -> None:
        if not self.enabled:
            return
        (self.root / "recaps").mkdir(parents=True, exist_ok=True)
        (self.root / "recaps" / f"{lesson_id}.txt").write_text(
            text, encoding="utf-8",
        )

    def recap_audio_path(self, lesson_id: str, provider: str) -> Path:
        """Path where the recap MP3 lives (whether or not it exists yet).
        Provider-suffixed so swapping TTS provider doesn't serve stale audio."""
        return self.root / "recaps" / f"{lesson_id}.{provider}.mp3"

    def get_notes(self, lesson_id: str, user_key: str = "anon") -> str | None:
        """User-authored notes attached to a lesson. Per-user when logged in,
        falls back to 'anon' for unauthenticated browsing (which lives in
        localStorage on the client too, so this is just a safety net)."""
        if not self.enabled:
            return None
        path = self.root / "notes" / user_key / f"{lesson_id}.txt"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def put_notes(self, lesson_id: str, text: str, user_key: str = "anon") -> None:
        if not self.enabled:
            return
        (self.root / "notes" / user_key).mkdir(parents=True, exist_ok=True)
        (self.root / "notes" / user_key / f"{lesson_id}.txt").write_text(
            text, encoding="utf-8",
        )

    def get_lesson_by_key(self, lesson_key: str) -> Lesson | None:
        """Direct-key lookup. `lesson_key` is the 32-hex-char digest the
        cache uses internally; the API returns it as `lesson_id` in
        every job result so clients can chat about a lesson without
        re-uploading the image."""
        if not self.enabled:
            return None
        return self._read_lesson(lesson_key)

    def _read_lesson(self, key: str) -> Lesson | None:
        path = self.root / "lessons" / f"{key}.json"
        if not path.exists():
            self.lesson_misses += 1
            return None
        self.lesson_hits += 1
        data = json.loads(path.read_text(encoding="utf-8"))
        return Lesson(
            title=data["title"],
            language_code=data["language_code"],
            language_name=data["language_name"],
            level=data["level"],
            scenes=[Scene(**s) for s in data["scenes"]],
            quiz=data["quiz"],
        )

    def put_lesson(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        model: str,
        lesson: Lesson,
    ) -> None:
        if not self.enabled:
            return
        key = self.lesson_key(image_bytes, language, level, model)
        path = self.root / "lessons" / f"{key}.json"
        path.write_text(
            json.dumps(asdict(lesson), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ---------- audio ----------

    def get_audio(
        self, text: str, language: str, provider: str, dest: Path
    ) -> bool:
        """If cached, copy the audio to `dest` and return True."""
        if not self.enabled:
            return False
        key = self.audio_key(text, language, provider)
        src = self.root / "audio" / f"{key}.mp3"
        if not src.exists():
            self.audio_misses += 1
            return False
        self.audio_hits += 1
        shutil.copyfile(src, dest)
        return True

    def put_audio(
        self, text: str, language: str, provider: str, source: Path
    ) -> None:
        if not self.enabled:
            return
        key = self.audio_key(text, language, provider)
        target = self.root / "audio" / f"{key}.mp3"
        shutil.copyfile(source, target)

    # ---------- videos ----------

    def video_key(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        theme_name: str,
        provider_name: str,
        render_mode: str,
    ) -> str:
        return self._hash(
            image_bytes,
            language.encode(),
            level.encode(),
            theme_name.encode(),
            provider_name.encode(),
            render_mode.encode(),
        )

    def get_video(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        theme_name: str,
        provider_name: str,
        render_mode: str,
        dest: Path,
    ) -> bool:
        """If a previous render with the same inputs is cached, copy it to
        `dest` and return True. The cache key includes the talking-head
        provider name — different tiers (cartoon vs Wav2Lip vs HeyGen)
        cache separately because they produce visually different MP4s."""
        if not self.enabled:
            return False
        key = self.video_key(
            image_bytes, language, level, theme_name, provider_name, render_mode,
        )
        src = self.root / "videos" / f"{key}.mp4"
        if not src.exists():
            self.video_misses += 1
            return False
        self.video_hits += 1
        shutil.copyfile(src, dest)
        return True

    def put_video(
        self,
        image_bytes: bytes,
        language: str,
        level: str,
        theme_name: str,
        provider_name: str,
        render_mode: str,
        source: Path,
    ) -> None:
        if not self.enabled:
            return
        key = self.video_key(
            image_bytes, language, level, theme_name, provider_name, render_mode,
        )
        target = self.root / "videos" / f"{key}.mp4"
        shutil.copyfile(source, target)

    # ---------- reporting ----------

    def stats(self) -> str:
        if not self.enabled:
            return "cache: disabled"
        lesson_total = self.lesson_hits + self.lesson_misses
        audio_total = self.audio_hits + self.audio_misses
        video_total = self.video_hits + self.video_misses
        parts = []
        if lesson_total:
            parts.append(f"lessons {self.lesson_hits}/{lesson_total} hit")
        if audio_total:
            parts.append(f"audio {self.audio_hits}/{audio_total} hit")
        if video_total:
            parts.append(f"videos {self.video_hits}/{video_total} hit")
        if not parts:
            return "cache: enabled, no lookups"
        # rough savings estimate: each lesson hit ≈ ₹5, each video hit
        # ≈ ₹8 (Claude + TTS + render avoided on the free tier; more on
        # paid tiers).
        saved = self.lesson_hits * 5 + self.video_hits * 8
        suffix = f"  (~₹{saved} saved)" if saved else ""
        return "cache: " + ", ".join(parts) + suffix
