"""Interactive voice Q&A quiz for a generated PadhAI lesson.

Usage:
    python -m padhai.quiz_cli lesson_script.json
    python -m padhai.quiz_cli lesson_script.json --voice   # mic in + Whisper out

Flow per question:
    1. Synthesise + play the question audio.
    2. Capture the student's answer — either by mic + Whisper STT, or by
       typed input as a fallback.
    3. Synthesise + play feedback ("Correct!" or "The right answer was X").
    4. Track score, narrate the final tally.

Voice mode requires:
    - ffmpeg on PATH (used to record from the default mic)
    - the `openai-whisper` Python package installed locally (`pip install
      openai-whisper`) — kept out of requirements.txt because it pulls a
      ~1GB model on first use.

Kindergarten mode (--kid) makes the feedback gentler — the kid is always
encouraged even when they get it wrong, and the matcher is more lenient
(accepts the option text, not just the letter)."""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .pedagogy import Lesson, Scene
from .tts import TTSProvider, get_provider


@dataclass
class QuizConfig:
    voice: bool
    kid_mode: bool
    record_seconds: float
    language: str
    provider: TTSProvider


def _ffmpeg_record_input_args() -> list[str] | None:
    """Build ffmpeg args to capture from the platform's default mic.
    Returns None when we don't know how to record on this platform."""
    sysname = platform.system()
    if sysname == "Linux":
        # Try pulseaudio first, fall back to alsa
        if shutil.which("pactl") is not None:
            return ["-f", "pulse", "-i", "default"]
        return ["-f", "alsa", "-i", "default"]
    if sysname == "Darwin":
        return ["-f", "avfoundation", "-i", ":0"]
    if sysname == "Windows":
        return ["-f", "dshow", "-i", "audio=default"]
    return None


def play_audio(path: Path) -> None:
    """Play an MP3/WAV via ffplay. Falls back to a silent no-op if ffplay is
    missing — the user still sees the text prompts."""
    if shutil.which("ffplay") is None:
        return
    subprocess.run(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
        check=False,
    )


def record_audio(out_path: Path, seconds: float) -> bool:
    """Record `seconds` of audio from the default mic to out_path.
    Returns False if recording isn't supported on this platform / mic
    isn't available."""
    record_args = _ffmpeg_record_input_args()
    if record_args is None or shutil.which("ffmpeg") is None:
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", *record_args,
             "-t", f"{seconds:.1f}",
             "-ac", "1", "-ar", "16000",
             str(out_path)],
            check=True, capture_output=True,
        )
        return out_path.exists() and out_path.stat().st_size > 0
    except subprocess.CalledProcessError:
        return False


def transcribe(audio_path: Path, language: str) -> str | None:
    """Transcribe via local Whisper if available. Returns None on miss."""
    try:
        import whisper  # type: ignore
    except ImportError:
        return None
    try:
        model = whisper.load_model("base")
        result = model.transcribe(str(audio_path), language=language, fp16=False)
        return str(result.get("text", "")).strip()
    except Exception:
        return None


def match_answer(transcript: str, options: dict[str, str]) -> str | None:
    """Map a free-form spoken transcript to one of A/B/C/D.

    Strategy:
      1. Token match: 'b', 'option b', 'bee' → B
      2. Substring match against the option text (covers cases where the
         kid says the actual answer rather than the letter)
    """
    if not transcript:
        return None
    t = transcript.lower().strip()
    tokens = t.replace(".", " ").replace(",", " ").split()

    # Phonetic letter hints — Whisper sometimes hallucinates spellings
    letter_aliases = {
        "A": {"a", "ay", "eh"},
        "B": {"b", "bee", "be"},
        "C": {"c", "see", "sea", "cee"},
        "D": {"d", "dee", "the"},
    }
    for letter, aliases in letter_aliases.items():
        if any(tok in aliases for tok in tokens):
            return letter

    # Substring match against option text
    best_letter = None
    best_len = 0
    for letter, opt in options.items():
        words = [w for w in opt.lower().split() if len(w) > 2]
        for w in words:
            if w in t and len(w) > best_len:
                best_letter = letter
                best_len = len(w)
    return best_letter


def _say(provider: TTSProvider, text: str, language: str, work_dir: Path) -> Path:
    """Synthesise text → MP3 inside work_dir, return the path."""
    with tempfile.NamedTemporaryFile(
        suffix=".mp3", dir=work_dir, delete=False,
    ) as f:
        out = Path(f.name)
    provider.synthesise(text, language, out)
    return out


def _format_question_script(q: dict, q_num: int, kid_mode: bool) -> str:
    opts = q["options"]
    if kid_mode:
        return (
            f"Here's question number {q_num}. "
            f"{q['question']} "
            f"Listen carefully. Option A: {opts['A']}. "
            f"Option B: {opts['B']}. Option C: {opts['C']}. "
            f"Option D: {opts['D']}. What do you think?"
        )
    return (
        f"Question {q_num}. {q['question']} "
        f"A: {opts['A']}. B: {opts['B']}. C: {opts['C']}. D: {opts['D']}."
    )


def _feedback_script(
    correct_letter: str,
    chosen_letter: str | None,
    options: dict[str, str],
    kid_mode: bool,
) -> str:
    is_correct = chosen_letter == correct_letter
    correct_text = options[correct_letter]
    if is_correct:
        return "Yay! That's right!" if kid_mode else f"Correct! The answer is {correct_letter}."
    if kid_mode:
        return f"Good try! The answer was {correct_letter}: {correct_text}. Let's keep going!"
    return f"Not quite. The correct answer is {correct_letter}: {correct_text}."


def run_quiz(lesson_path: Path, cfg: QuizConfig) -> int:
    data = json.loads(lesson_path.read_text(encoding="utf-8"))
    questions = data["quiz"]
    score = 0

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # opening line
        intro = "Let's start the quiz!" if cfg.kid_mode else "Quiz time."
        play_audio(_say(cfg.provider, intro, cfg.language, work))
        print(f"\n=== Quiz: {data['title']} ===\n")

        for i, q in enumerate(questions, start=1):
            print(f"\nQ{i}.  {q['question']}")
            for letter in ("A", "B", "C", "D"):
                print(f"      {letter}.  {q['options'][letter]}")

            script = _format_question_script(q, i, cfg.kid_mode)
            play_audio(_say(cfg.provider, script, cfg.language, work))

            chosen: str | None = None
            if cfg.voice:
                rec = work / f"answer_{i}.wav"
                print(f"[listening {cfg.record_seconds:.0f}s — say A, B, C, or D...]")
                if record_audio(rec, cfg.record_seconds):
                    transcript = transcribe(rec, cfg.language)
                    if transcript:
                        print(f"  heard: {transcript!r}")
                        chosen = match_answer(transcript, q["options"])
                    else:
                        print("  (no transcript — Whisper missing or recording empty)")

            if chosen is None:
                while True:
                    raw = input("Your answer (A/B/C/D): ").strip().upper()
                    if raw in ("A", "B", "C", "D"):
                        chosen = raw
                        break
                    print("  please type A, B, C, or D")

            correct = q["answer"]
            if chosen == correct:
                score += 1

            feedback = _feedback_script(correct, chosen, q["options"], cfg.kid_mode)
            print(f"  → {feedback}")
            play_audio(_say(cfg.provider, feedback, cfg.language, work))

        total = len(questions)
        if cfg.kid_mode:
            summary = f"You did so well! You got {score} out of {total} right!"
        else:
            summary = f"You scored {score} out of {total}."
        print(f"\n{summary}")
        play_audio(_say(cfg.provider, summary, cfg.language, work))

    return score


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="padhai-quiz",
        description="Interactive voice quiz for a PadhAI lesson script.",
    )
    parser.add_argument(
        "lesson",
        type=Path,
        help="path to a lesson script JSON (produced by `padhai --script-out`)",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="capture answers via the mic (requires ffmpeg + openai-whisper)",
    )
    parser.add_argument(
        "--kid",
        action="store_true",
        help="kindergarten mode: gentler feedback, more encouragement",
    )
    parser.add_argument(
        "--record-seconds",
        type=float, default=6.0,
        help="how long to listen per answer in --voice mode (default: 6.0)",
    )
    args = parser.parse_args(argv)

    if not args.lesson.exists():
        print(f"error: lesson script not found: {args.lesson}", file=sys.stderr)
        return 2

    data = json.loads(args.lesson.read_text(encoding="utf-8"))
    cfg = QuizConfig(
        voice=args.voice,
        kid_mode=args.kid,
        record_seconds=args.record_seconds,
        language=data["language_code"],
        provider=get_provider(),
    )

    if cfg.voice:
        try:
            import whisper
        except ImportError:
            print(
                "warning: --voice was requested but the `openai-whisper` package "
                "is not installed. Mic input will record but transcription will "
                "fail, so we'll fall back to typed input. "
                "Install with: pip install openai-whisper",
                file=sys.stderr,
            )

    return 0 if run_quiz(args.lesson, cfg) >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
