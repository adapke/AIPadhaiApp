"""Evaluate every configured talking-head provider on the same narration.

For each provider whose env vars are set, render a short single-scene
lesson with the test narration. Compare side-by-side in a grid and
print a cost+timing summary so you can pick which M4 provider to
standardise on before committing to a paid plan.

Usage:
    python -m scripts.eval_providers
    python -m scripts.eval_providers --grid eval_clips/comparison.mp4
    python -m scripts.eval_providers --narration "your custom narration text"

Detects providers by env-var presence. Skips any that aren't configured
(prints why). The cartoon baseline always runs — it's free and shows
what the M1 tier looks like next to the paid tiers.

Cost estimates are per-minute list prices (May 2026); your actual
billing depends on the plan you're on."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from padhai.cache import Cache
from padhai.pedagogy import Lesson, Scene
from padhai.render import render_lesson
from padhai.talking_head import (
    PROVIDER_NAMES,
    CartoonAvatarProvider,
    DeepBrainProvider,
    DIDProvider,
    HeyGenProvider,
    SynthesiaProvider,
    TavusProvider,
    Wav2LipProvider,
)
from padhai.themes import WHITEBOARD
from padhai.tts import get_provider as get_tts_provider

COST_PER_MIN_USD = {
    "cartoon":   0.00,
    "wav2lip":   0.02,   # GPU compute only
    "d-id":      0.15,   # midpoint of $0.10-0.20
    "heygen":    0.30,
    "tavus":     0.50,   # midpoint of $0.40-0.60
    "synthesia": 0.50,
    "deepbrain": 0.55,   # midpoint of $0.30-0.80
}

USD_TO_INR = 84.0  # rough; tune to whatever you treasury at


@dataclass
class ProviderResult:
    name: str
    status: str            # "ok" | "skipped" | "error"
    clip_path: Path | None
    elapsed_s: float
    message: str           # error or skip reason
    duration_s: float | None = None

    @property
    def cost_inr(self) -> float | None:
        if self.duration_s is None:
            return None
        return COST_PER_MIN_USD.get(self.name, 0) * (self.duration_s / 60.0) * USD_TO_INR


def detect_configured() -> list[str]:
    """Return the names of providers whose env vars are fully set."""
    out: list[str] = []
    if (
        os.environ.get("WAV2LIP_REPO_PATH")
        and os.environ.get("WAV2LIP_CHECKPOINT")
        and os.environ.get("WAV2LIP_SOURCE_PHOTO")
    ):
        out.append("wav2lip")
    if os.environ.get("SYNTHESIA_API_KEY"):
        out.append("synthesia")
    if os.environ.get("HEYGEN_API_KEY"):
        out.append("heygen")
    if os.environ.get("TAVUS_API_KEY") and os.environ.get("TAVUS_REPLICA_ID"):
        out.append("tavus")
    if os.environ.get("DEEPBRAIN_API_KEY") and os.environ.get("DEEPBRAIN_MODEL_ID"):
        out.append("deepbrain")
    if os.environ.get("DID_API_KEY") and os.environ.get("DID_SOURCE_PHOTO_URL"):
        out.append("d-id")
    return out


def build_named(name: str):
    """Construct a provider instance by name, deferring to env vars for keys."""
    if name == "wav2lip":
        return Wav2LipProvider(
            repo_path=Path(os.environ["WAV2LIP_REPO_PATH"]),
            checkpoint=Path(os.environ["WAV2LIP_CHECKPOINT"]),
            source_photo=Path(os.environ["WAV2LIP_SOURCE_PHOTO"]),
            python_bin=os.environ.get("WAV2LIP_PYTHON", "python"),
        )
    if name == "synthesia":
        return SynthesiaProvider(api_key=os.environ["SYNTHESIA_API_KEY"])
    if name == "heygen":
        return HeyGenProvider(api_key=os.environ["HEYGEN_API_KEY"])
    if name == "tavus":
        return TavusProvider(
            api_key=os.environ["TAVUS_API_KEY"],
            replica_id=os.environ["TAVUS_REPLICA_ID"],
        )
    if name == "deepbrain":
        return DeepBrainProvider(
            api_key=os.environ["DEEPBRAIN_API_KEY"],
            model_id=os.environ["DEEPBRAIN_MODEL_ID"],
            language=os.environ.get("DEEPBRAIN_LANGUAGE", "en"),
        )
    if name == "d-id":
        return DIDProvider(api_key=os.environ["DID_API_KEY"])
    if name == "cartoon":
        return CartoonAvatarProvider()
    raise ValueError(f"unknown provider {name!r}")


def soft_voice_mp3(text: str, out_path: Path) -> None:
    """Reuse the same Piper helper the demos use, so all providers share
    an identical narration baseline."""
    import os as _os
    model = Path(
        _os.environ.get(
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


def render_for_provider(
    name: str,
    narration: str,
    cache: Cache,
    output_dir: Path,
) -> ProviderResult:
    """Render a single-scene lesson with the named provider. Returns a
    ProviderResult capturing status + timing."""
    output = output_dir / f"{name}.mp4"
    try:
        provider = build_named(name)
    except (KeyError, ValueError) as e:
        return ProviderResult(name, "skipped", None, 0.0, str(e))

    lesson = Lesson(
        title="Provider Evaluation Clip",
        language_code="en",
        language_name="English",
        level="middle",
        scenes=[
            Scene(
                title="Photosynthesis",
                narration=narration,
                bullets=[
                    "Plants make food using sunlight",
                    "They take in CO₂ and release O₂",
                    "It powers nearly all life on Earth",
                ],
                diagram="photosynthesis",
            ),
        ],
        quiz=[],
    )

    t0 = time.monotonic()
    try:
        render_lesson(
            lesson, output,
            cache=cache,
            theme=WHITEBOARD,
            render_mode="animated",
            show_teacher=True,
            include_quiz=False,
            talking_head_provider=provider,
        )
        elapsed = time.monotonic() - t0
        # probe duration for cost calc
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
            capture_output=True, text=True, check=True,
        )
        duration = float(probe.stdout.strip())
        return ProviderResult(name, "ok", output, elapsed, "", duration_s=duration)
    except Exception as e:
        return ProviderResult(name, "error", None, time.monotonic() - t0, str(e))


def composite_grid(
    results: list[ProviderResult],
    output: Path,
    tile_w: int = 640,
    tile_h: int = 360,
) -> None:
    """Stack the successful clips into a 2x3 grid via ffmpeg filter_complex.
    Each tile is labelled with the provider name and cost-per-min."""
    ok = [r for r in results if r.status == "ok" and r.clip_path is not None]
    if not ok:
        raise RuntimeError("no successful clips to composite")
    n = len(ok)
    cols = 3 if n > 2 else n
    rows = (n + cols - 1) // cols

    inputs: list[str] = []
    for r in ok:
        inputs += ["-i", str(r.clip_path)]

    filter_parts = []
    for i, r in enumerate(ok):
        label = f"{r.name}  (${COST_PER_MIN_USD.get(r.name, 0):.2f}/min)"
        filter_parts.append(
            f"[{i}:v]scale={tile_w}:{tile_h},"
            f"drawtext=text='{label}':x=20:y=20:fontsize=24:fontcolor=white:"
            f"box=1:boxcolor=black@0.5:boxborderw=8[v{i}]"
        )

    # Pad up to cols*rows with black tiles so hstack widths match
    pad_n = cols * rows - n
    for i in range(pad_n):
        filter_parts.append(
            f"color=c=black:s={tile_w}x{tile_h}:d=1[pad{i}]"
        )

    # Build hstack rows
    row_outs = []
    idx = 0
    for r in range(rows):
        row_inputs = []
        for c in range(cols):
            if idx < n:
                row_inputs.append(f"[v{idx}]")
            else:
                row_inputs.append(f"[pad{idx - n}]")
            idx += 1
        row_label = f"row{r}"
        if cols == 1:
            filter_parts.append(f"{row_inputs[0]}null[{row_label}]")
        else:
            filter_parts.append(
                "".join(row_inputs) + f"hstack=inputs={cols}[{row_label}]"
            )
        row_outs.append(f"[{row_label}]")

    if rows == 1:
        filter_parts.append(f"{row_outs[0]}null[v]")
    else:
        filter_parts.append("".join(row_outs) + f"vstack=inputs={rows}[v]")

    # use the longest clip's audio so the grid keeps a soundtrack
    longest = max(ok, key=lambda r: r.duration_s or 0)
    a_idx = ok.index(longest)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]", "-map", f"{a_idx}:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(output),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eval_providers",
        description="A/B every configured talking-head provider on the same narration.",
    )
    parser.add_argument(
        "--narration",
        default=(
            "Photosynthesis is the process plants use to make food from "
            "sunlight, water, and air. It is why we can breathe."
        ),
        help="narration script to test on (default: a 12-second photosynthesis sentence)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path, default=Path("eval_clips"),
        help="where to write the per-provider clips (default: eval_clips/)",
    )
    parser.add_argument(
        "--grid",
        type=Path,
        help="if set, composite all successful clips into one MP4 at this path",
    )
    parser.add_argument(
        "--skip-cartoon",
        action="store_true",
        help="skip the cartoon baseline (M1 free tier)",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)

    configured = detect_configured()
    print(f"\nConfigured providers detected from env vars: {configured or ['(none)']}")
    if not args.skip_cartoon:
        configured = ["cartoon", *configured]
    if not configured:
        print("Nothing to evaluate. Set at least one provider's env vars and retry.")
        return 0

    cache_dir = Path(tempfile.mkdtemp(prefix="padhai_eval_"))
    cache = Cache(root=cache_dir)
    print("\nSynthesising shared narration with Piper TTS...")
    audio = Path(tempfile.mkstemp(suffix=".mp3")[1])
    soft_voice_mp3(args.narration, audio)
    cache.put_audio(args.narration, "en", "gtts", audio)
    audio.unlink()

    print("\nRendering each provider's clip (this hits live APIs for hosted ones):\n")
    print(f"  {'PROVIDER':10s}  {'STATUS':8s}  {'TIME':>7s}  {'DURATION':>9s}  {'COST':>10s}")
    print(f"  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*9}  {'-'*10}")

    results: list[ProviderResult] = []
    for name in configured:
        sys.stdout.write(f"  {name:10s}  ")
        sys.stdout.flush()
        r = render_for_provider(name, args.narration, cache, args.output_dir)
        results.append(r)
        dur_str = f"{r.duration_s:.1f}s" if r.duration_s else "—"
        cost_str = f"₹{r.cost_inr:.2f}" if r.cost_inr is not None else "—"
        sys.stdout.write(
            f"{r.status:8s}  {r.elapsed_s:6.1f}s  {dur_str:>9s}  {cost_str:>10s}"
        )
        if r.status != "ok":
            sys.stdout.write(f"  ({r.message[:60]})")
        sys.stdout.write("\n")

    if args.grid:
        ok = [r for r in results if r.status == "ok"]
        if len(ok) >= 1:
            print(f"\nCompositing grid → {args.grid}")
            composite_grid(results, args.grid)
            print(f"  done — {args.grid.stat().st_size:,} bytes")

    n_ok = sum(1 for r in results if r.status == "ok")
    n_err = sum(1 for r in results if r.status == "error")
    n_skip = sum(1 for r in results if r.status == "skipped")
    print(f"\nSummary: {n_ok} ok, {n_err} errored, {n_skip} skipped\n")
    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
