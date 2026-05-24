"""GPU worker — long-running process on a CUDA machine that picks up
Wav2Lip-tier jobs from the shared SQLite queue and renders them.

Why this lives outside the web service:
    Wav2Lip needs a GPU (RTX 3090 / A10G / L4 — anything with >= 8 GB VRAM
    and CUDA). The web service runs on Render or Cloud Run with no GPU.
    We separate the two so the web tier can autoscale on cheap CPU
    instances while only the GPU pool pays for accelerator time. AWS
    Spot or Azure Spot drops GPU cost ~70%.

Run on the GPU host:
    pip install -r requirements.txt
    git clone https://github.com/Rudrabha/Wav2Lip /opt/Wav2Lip
    # download checkpoint to /opt/Wav2Lip/checkpoints/wav2lip_gan.pth
    export ANTHROPIC_API_KEY=...
    export PADHAI_DB_PATH=/shared/padhai/jobs.db
    export PADHAI_CACHE_DIR=/shared/padhai/cache
    export PADHAI_OUTPUT_DIR=/shared/padhai/outputs
    export WAV2LIP_REPO_PATH=/opt/Wav2Lip
    export WAV2LIP_CHECKPOINT=/opt/Wav2Lip/checkpoints/wav2lip_gan.pth
    export WAV2LIP_SOURCE_PHOTO=/opt/Wav2Lip/teacher_portrait.jpg

    python -m padhai.gpu_worker

The DB path / cache dir / output dir must be on shared storage
accessible from BOTH the web service and this GPU instance — AWS EFS,
GCP Filestore, or a Litestream-replicated SQLite over S3. Otherwise
the web service can never serve the rendered MP4."""

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from .jobs import JobStore, PollingRunner
from .web import _render_worker as render_job


def _wav2lip_filter(payload: dict) -> bool:
    """Pick only jobs the web service marked for Wav2Lip rendering."""
    return payload.get("talking_head_provider") == "wav2lip"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="padhai-gpu-worker",
        description="Render Wav2Lip-tier jobs from the shared queue.",
    )
    parser.add_argument(
        "--db-path", type=Path,
        default=Path(os.environ.get("PADHAI_DB_PATH",
                                     str(Path.home() / ".padhai" / "jobs.db"))),
        help="path to the shared SQLite job database",
    )
    parser.add_argument(
        "--poll-seconds", type=float, default=2.0,
        help="how often to poll for new queued jobs (default: 2.0)",
    )
    args = parser.parse_args(argv)

    # Fail fast at startup if the GPU + Wav2Lip env vars aren't set —
    # otherwise we silently sit waiting for jobs we can't render.
    required = ("WAV2LIP_REPO_PATH", "WAV2LIP_CHECKPOINT", "WAV2LIP_SOURCE_PHOTO")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"error: required env vars not set: {missing}", file=sys.stderr)
        print("       see padhai/talking_head.Wav2LipProvider docstring for setup",
              file=sys.stderr)
        return 2

    store = JobStore(args.db_path)
    poller = PollingRunner(
        store,
        worker_fn=render_job,
        job_filter=_wav2lip_filter,
        poll_seconds=args.poll_seconds,
    )

    # Reset 'running' jobs on startup — they probably belonged to a
    # previous GPU worker that got preempted. The cache layer makes
    # re-running them safe (idempotent).
    for jid in store.pending_ids():
        store.update(jid, status="queued")

    def _graceful(_sig, _frame):
        print("\n[gpu_worker] stopping after current job…", file=sys.stderr)
        poller.stop()

    signal.signal(signal.SIGINT, _graceful)
    signal.signal(signal.SIGTERM, _graceful)

    print(f"[gpu_worker] polling {args.db_path} for wav2lip jobs every "
          f"{args.poll_seconds:.1f}s — ctrl-c to stop")
    poller.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
