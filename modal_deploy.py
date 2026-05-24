"""Modal deployment config for the photoreal Wav2Lip GPU worker.

This is the v1.0.1 follow-up to A1_PHOTOREAL_DEPLOY.md — the config file
that turns `python -m padhai.gpu_worker` into a managed, auto-scaling
Modal app with a warm Mumbai A10G replica.

Usage:
  modal token new                      # one-time, signs you into Modal
  modal volume create padhai-models    # one-time, holds wav2lip_gan.pth +
                                       # the teacher face photo
  # upload weights + photo into the volume (one-time):
  modal volume put padhai-models /path/to/wav2lip_gan.pth /models/wav2lip_gan.pth
  modal volume put padhai-models /path/to/teacher_face.jpg /models/teacher_face.jpg

  modal secret create padhai-secrets \\
      DATABASE_URL=postgres://...      \\
      ANTHROPIC_API_KEY=...            \\
      R2_ACCOUNT_ID=...                \\
      R2_ACCESS_KEY_ID=...             \\
      R2_SECRET_ACCESS_KEY=...

  modal deploy modal_deploy.py         # ship it

After deploy: the worker stays warm between jobs. First cold start = 1-2s
(image load), subsequent jobs = pure GPU time (~3s/lesson at 480p).

Rollback: `modal app stop padhai-wav2lip` — the avatar router's circuit
breaker then routes M3 users to the hosted-provider fallback chain
(HeyGen → Synthesia → Tavus) automatically. No web-tier change needed.
"""

from __future__ import annotations

import modal

# Same Wav2Lip-friendly base image the GPU worker needs. ffmpeg for the
# post-render compositing step the worker shells out to; git so we can
# clone the Wav2Lip repo into /opt at build time.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libsm6", "libxext6", "libgl1")
    .pip_install(
        "torch>=2.0",
        "torchvision",
        "numpy",
        "opencv-python",
        "librosa",
        "tqdm",
        # padhai runtime deps (same as the web Dockerfile, minus the
        # render-only packages we don't need on the GPU host)
        "anthropic>=0.30",
        "fastapi",
        "pydantic>=2",
        "pillow",
        "requests",
        "boto3",            # R2 (S3-compatible) for finished video upload
    )
    # Clone the Wav2Lip repo into the image. The model weights are NOT
    # baked in (license restriction) — they ship via the persistent
    # volume mounted at /models below.
    .run_commands(
        "git clone https://github.com/Rudrabha/Wav2Lip.git /opt/Wav2Lip",
    )
    # Mount the padhai source. Modal sees the local repo, ships the
    # right files, and `python -m padhai.gpu_worker` just works.
    .add_local_dir("padhai", "/root/padhai")
)

app = modal.App("padhai-wav2lip")


@app.function(
    image=image,
    gpu="A10G",                    # cheapest GPU that fits Wav2Lip + spare
    timeout=600,                   # any single lesson > 10 min is a bug
    secrets=[modal.Secret.from_name("padhai-secrets")],
    volumes={"/models": modal.Volume.from_name("padhai-models")},
    # Keep at least one replica warm so M3 users don't pay cold-start
    # latency on every render. Modal scales out automatically when the
    # queue depth grows.
    min_containers=1,
    max_containers=3,
    # Idle-shutdown after 5 min — saves cost during off-peak hours but
    # still gives us a warm worker during the school-day burst.
    scaledown_window=300,
)
def run_worker():
    """Long-running worker. Claims wav2lip jobs from the shared DB
    queue and renders them to MP4. Exits on SIGTERM or empty queue
    after the scaledown window."""
    import os
    import subprocess

    # Standard env vars the GPU worker expects. These point at the
    # files we uploaded into the Modal volume above.
    os.environ.setdefault("WAV2LIP_REPO_PATH", "/opt/Wav2Lip")
    os.environ.setdefault("WAV2LIP_CHECKPOINT", "/models/wav2lip_gan.pth")
    os.environ.setdefault("WAV2LIP_SOURCE_PHOTO", "/models/teacher_face.jpg")
    os.environ.setdefault("PADHAI_TALKING_HEAD_PROVIDER", "wav2lip")
    os.environ.setdefault("WAV2LIP_FPS", "25")
    os.environ.setdefault("WAV2LIP_RESIZE", "192")

    subprocess.run(
        ["python", "-m", "padhai.gpu_worker"],
        check=True,
        cwd="/root",
    )


@app.local_entrypoint()
def main():
    """`modal run modal_deploy.py` invokes this — smoke-test the worker
    by claiming one job and exiting. CI uses this to verify the image
    builds + the volume mounts before promoting to production."""
    run_worker.remote()
