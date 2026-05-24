# A1 — Photoreal Wav2Lip Production Deployment

The Wav2Lip provider code already exists at `padhai/talking_head.py:Wav2LipProvider`
and the GPU worker scaffolding at `padhai/gpu_worker.py`. This document
covers what's needed to take it from "code shipped" to "rendering production
photoreal lessons."

## Prerequisites

| Item | Where to get it | Notes |
|---|---|---|
| **GPU host** | RunPod, Modal, Lambda Labs, fly.io GPU | One A10G is plenty for our load; A100 only if you scale past ~10k photoreal lessons/day. ~₹15-30/hour on-demand. |
| **Model checkpoint** | Pre-trained Wav2Lip weights from the original repo | ~416 MB; download once + cache on the GPU host |
| **Source photo** | Stable head-and-shoulders photo of the teacher avatar | 512×512 or larger; well-lit, facing camera. We render lip-sync onto this photo for every clip. |
| **ffmpeg** | System package | Used by the worker after Wav2Lip writes its raw output. |

## Architecture (already shipped)

```
                   ┌─────────────┐
                   │  padhai     │  ── enqueues "wav2lip" jobs into
                   │  web        │     the shared SQLite/Postgres
                   └──────┬──────┘     queue when user is on M3 tier
                          │
                  shared jobs DB
                          │
              ┌───────────┴───────────┐
              │                       │
       ┌──────▼──────┐         ┌──────▼──────┐
       │  CPU worker │         │  GPU worker │   ← runs padhai/gpu_worker.py
       │  (in-app)   │         │ on the GPU  │     against the same DB
       │             │         │   host      │
       │ handles     │         │             │
       │ cartoon +   │         │ claims only │
       │ hosted-API  │         │ talking_head │
       │ jobs        │         │ ==wav2lip    │
       └─────────────┘         └─────────────┘
```

The GPU worker is just `python -m padhai.gpu_worker` with the env vars
below. The web tier doesn't change — it routes M3-tier users to the
wav2lip provider via the existing `resolve_provider_for_tier()` logic.

## Render → GPU host wiring

### Option A: Modal (recommended for India-friendly latency)

Modal has Mumbai GPUs, sub-second cold-start, and bills by the second.

```python
# modal_deploy.py
import modal

image = modal.Image.debian_slim().apt_install("ffmpeg", "git").pip_install(
    "torch>=2.0", "numpy", "opencv-python", "librosa", "tqdm",
    # Plus padhai's render dependencies
    "anthropic", "fastapi", "pillow", "moviepy",
)

app = modal.App("padhai-wav2lip")

@app.function(
    image=image,
    gpu="A10G",
    timeout=600,
    secrets=[modal.Secret.from_name("padhai-secrets")],
    # Mount the model checkpoint + source photo from a persistent volume
    volumes={"/models": modal.Volume.from_name("padhai-models")},
)
def run_worker():
    import subprocess
    subprocess.run(["python", "-m", "padhai.gpu_worker"], check=True)
```

Deploy: `modal deploy modal_deploy.py`. The worker stays alive between
jobs (Modal keeps a warm replica) — first job has 1-2s cold start; the
rest hit the warm worker.

### Option B: RunPod Serverless

```bash
# Build the image (same Dockerfile as the web tier + an extra
# COPY for the GPU worker entrypoint)
docker build -f Dockerfile.gpu -t padhai-wav2lip .
docker push <registry>/padhai-wav2lip:latest

# In the RunPod dashboard:
#   - "Serverless" → "New Endpoint"
#   - Image: <registry>/padhai-wav2lip:latest
#   - Min workers: 0, Max workers: 3 (auto-scale)
#   - GPU: A10G (or A4500 for cheaper)
#   - Idle timeout: 60s (saves cost on bursty load)
#   - Disk: 20GB (model + transient files)
```

The handler downloads the model weights once at cold-start, caches in
`/runpod-volume/wav2lip-model.pth`, and then handles jobs.

### Option C: Single dedicated GPU box (fly.io or Hetzner GPU)

Simplest mental model. One server, always running, always claims jobs
from the queue. Best when you have predictable load >2k photoreal
lessons/day; cheaper than serverless at scale.

## Required env vars on the GPU host

```bash
# Wav2Lip-specific
WAV2LIP_REPO_PATH=/opt/Wav2Lip                      # cloned repo
WAV2LIP_CHECKPOINT=/models/wav2lip_gan.pth          # downloaded weights
WAV2LIP_SOURCE_PHOTO=/models/teacher_face.jpg       # the avatar photo

# Optional tuning
WAV2LIP_FPS=25
WAV2LIP_RESIZE=192             # output resolution; 192 default is fast
                               # + readable, 384 for premium

# Same DB the web tier writes to
DATABASE_URL=postgres://...    # Render Postgres connection string

# So the GPU worker only claims wav2lip jobs (not cartoon)
PADHAI_TALKING_HEAD_PROVIDER=wav2lip
```

## Model weights

The Wav2Lip checkpoint is **not** redistributable from the original
repo's license terms; each deploy must download it from the official
source:

```bash
# On the GPU host, one-time:
git clone https://github.com/Rudrabha/Wav2Lip.git /opt/Wav2Lip
mkdir -p /models
# Download wav2lip_gan.pth per the repo's README. Cache in a persistent
# volume so cold-starts don't re-download.
```

## Cost model (today's numbers, May 2026)

| Tier | Provider | Per-lesson cost | Notes |
|---|---|---|---|
| M1 Free | Cartoon | ₹0 (in-process CPU) | Cartoon Amy voice, 5-min cap |
| M2 Student Basic | Cartoon + better TTS | ₹0.50 (Piper or Bhashini neural) | |
| **M3 Student Pro** | **Wav2Lip on A10G** | **₹3-4** (~3s GPU/lesson @ ₹15/hr GPU) | Self-hosted; cheapest photoreal |
| M4 Enterprise | Hosted (HeyGen/Synthesia/Tavus) | ₹15-30/lesson | API rate; passed through |

A10G at Modal Mumbai is ~₹15/hour on-demand. A 5-min lesson takes ~3s
of GPU time (Wav2Lip is fast); 4 lessons fit in 1 GPU-minute. So a single
warm A10G can serve ~240 lessons/hour = ~₹0.06/lesson at full
utilisation. At 30% utilisation the per-lesson cost rises to ~₹0.20.
Round up to ₹3-4 to cover the GPU host's other overhead (memory, disk,
network egress).

## Operational checklist before flipping M3 on

- [ ] GPU host live + `python -m padhai.gpu_worker` running
- [ ] Model weights downloaded to the persistent volume
- [ ] Source photo uploaded (512×512 minimum, well-lit)
- [ ] Test render queued via `/api/v2/video-requests?render_tier=m3` and
      completes within 90s
- [ ] First successful video viewed on a phone — lip-sync looks natural
      at 480p portrait
- [ ] Alerting: Render dashboard wired to ping you when the GPU host is
      unreachable for >5 min (use the `/healthz` endpoint we expose)
- [ ] Avatar router (`padhai/avatar_router.py`) has wav2lip in the
      fallback chain so degraded GPU host routes to HeyGen/Tavus
      automatically
- [ ] M3 tier pricing visible at `/tiers` matches the cost model above
- [ ] Manual rollback plan: flip `PADHAI_TALKING_HEAD_PROVIDER` env
      override to `cartoon` to immediately route ALL M3 users away
      from the GPU host (e.g. for emergency maintenance)

## Why not bake the GPU into the main Dockerfile

We deliberately keep the web tier on CPU-only Render instances:

1. Web tier handles ~95% of traffic (cartoon + hosted-API videos).
   Paying for GPU on every web replica wastes money.
2. The GPU host needs different secrets, different scaling policy,
   different uptime guarantee — cleaner as its own deploy.
3. Splitting lets us scale them independently. A K-12 school adoption
   spike hits the CPU worker; an M3 paid-user surge hits the GPU.
4. If the GPU host is down, the avatar router automatically falls
   through to hosted providers (HeyGen / Synthesia). The web tier
   keeps serving everyone.

## What v1.0 didn't ship

This document is **deployment guide only**. The actual GPU host setup
is an ops/billing task that needs:
- A Modal / RunPod / Lambda account creation
- A budget call (₹X/month for the GPU host)
- A model weights download from the original Wav2Lip repo

When you're ready, follow the checklist above and ping me — I'll add
the `modal_deploy.py` config, the smoke tests, and the dashboard
alerts in a v1.0.1 follow-up.
