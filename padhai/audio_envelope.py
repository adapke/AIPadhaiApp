"""Compute an amplitude envelope from a narration audio file and turn it
into a per-frame lip-flap timeline.

Lip flap is the classic, cheap, surprisingly-effective trick that
Sun/Disney/South Park used long before phoneme-accurate lip sync existed:
when the speaker's voice is above a silence threshold, the avatar's
mouth is open; when it's below, the mouth is closed. The mouth toggles
many times per second along with speech energy, which the brain reads as
"talking".

We don't try to do real viseme detection (would need Rhubarb, Whisper-
align, or a phoneme model). For an explainer-video tutor at 12 fps,
amplitude thresholding is the right granularity."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


def _to_wav(audio_path: Path, work_dir: Path) -> Path:
    """Convert any audio file to a mono 22050 Hz PCM WAV via ffmpeg, so we
    can read raw samples with the stdlib `wave` module."""
    out = work_dir / "envelope.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ac", "1", "-ar", "22050", "-sample_fmt", "s16",
            str(out),
        ],
        check=True, capture_output=True,
    )
    return out


def _rms(samples: bytes) -> float:
    """Crude RMS over int16-LE samples without pulling in numpy."""
    if not samples:
        return 0.0
    total = 0
    n = len(samples) // 2
    for i in range(0, len(samples), 2):
        lo = samples[i]
        hi = samples[i + 1]
        v = lo | (hi << 8)
        if v & 0x8000:
            v -= 0x10000
        total += v * v
    return (total / n) ** 0.5


def mouth_timeline(
    audio_path: Path,
    fps: int = 12,
    silence_floor: float = 600.0,
) -> list[bool]:
    """Return a list[bool] of length `frame_count`, where True means the
    avatar's mouth should be open at that frame.

    The silence_floor RMS threshold is tuned for espeak-ng / gTTS output;
    bump it down for quieter voices."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg required for envelope analysis")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        wav = _to_wav(audio_path, work)
        with wave.open(str(wav), "rb") as w:
            sr = w.getframerate()
            total = w.getnframes()
            samples_per_frame = sr // fps
            timeline: list[bool] = []
            while w.tell() < total:
                chunk = w.readframes(samples_per_frame)
                if not chunk:
                    break
                rms = _rms(chunk)
                timeline.append(rms > silence_floor)
        return timeline
