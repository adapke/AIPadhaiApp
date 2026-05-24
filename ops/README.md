# PadhAI Ops Runbook — GPU Spot Workers

This directory holds the scripts that turn the **M3 Premium tier** (photoreal Wav2Lip teacher) from "code that runs on someone's laptop GPU" into "production fleet on AWS Spot at ~₹0.30 / minute of generated video".

## Architecture

```
                ┌──────────────────────┐
                │  Render / Cloud Run  │  ← web service (CPU only)
                │  uvicorn padhai.web  │     - serves /lessons + /jobs/*
                └──────────┬───────────┘     - runs cartoon + M2/M4 jobs inline
                           │                 - leaves Wav2Lip jobs queued
                           │
                           ▼
            ┌──────────────────────────────┐
            │   shared SQLite job queue    │  ← AWS EFS (or Litestream)
            │   /shared/padhai/jobs.db     │
            └──────────────┬───────────────┘
                           │ polled via padhai.jobs.PollingRunner
                           ▼
        ┌───────────────────────────────────────┐
        │   GPU Spot fleet                      │
        │   one or more g4dn.xlarge / g5.xlarge │
        │   each runs `python -m padhai.gpu_worker` │
        │   each renders ~3-5 videos / hour     │
        └───────────────────────────────────────┘
```

The cache layer makes the whole thing safe under Spot preemption:
losing an instance mid-render just bumps the job back to `queued`,
and the next worker picks it up.

## One-time setup

1. **Create a VPC subnet** in your target region (us-east-1 is cheapest for Spot GPUs as of 2026).
2. **Provision an EFS file system** with a mount target in that subnet. This is the shared storage for `jobs.db`, the video cache, and the output MP4s. ~₹2.50/GB-month.
3. **Create a security group** that allows port 2049 (NFS) within the VPC.
4. **Create an EC2 key pair** so you can SSH in to debug.
5. **Upload the Wav2Lip checkpoint** (`wav2lip_gan.pth`, ~400 MB) to a private S3 bucket; generate a signed URL valid for 24h.
6. **Upload a teacher portrait** (`teacher.jpg`, head-on, mouth closed, plain background); same private S3 bucket + signed URL.
7. **Put your secrets in SSM Parameter Store** — `ANTHROPIC_API_KEY` at minimum. The bootstrap script reads them into `/etc/padhai/env` via a sidecar; never bake them into the user-data.

## Launching a worker

```bash
python ops/spot-launch.py \
    --subnet subnet-abc123 \
    --security-group sg-abc123 \
    --key-pair my-keypair \
    --region us-east-1 \
    --efs-dns fs-xxxx.efs.us-east-1.amazonaws.com \
    --checkpoint-url 'https://my-bucket.s3.amazonaws.com/wav2lip_gan.pth?X-Amz-Signature=...' \
    --teacher-photo-url 'https://my-bucket.s3.amazonaws.com/teacher.jpg?X-Amz-Signature=...'
```

The script requests one g4dn.xlarge Spot instance, runs the bootstrap script as user-data (clones the repo, installs CUDA deps, downloads Wav2Lip + checkpoint, sets up the systemd service), and prints the instance id. SSH in and `journalctl -fu padhai-gpu.service` to watch live logs.

## Scaling

| Demand | Instances | Throughput | Spot cost / hour |
|---|---|---|---|
| Light (dev / pilot) | 1 × g4dn.xlarge | ~4 videos/hour | ~₹15 |
| Medium (~1,000 active subs) | 3 × g4dn.xlarge | ~12 videos/hour | ~₹45 |
| Heavy (10k+ active subs) | Auto Scaling Group with mixed Spot pools, 5-20 instances based on queue depth | up to ~80 videos/hour at 20 instances | ~₹300 |

For the ASG-driven scaling, swap the one-shot `RunInstances` call in `spot-launch.py` for an `aws autoscaling create-auto-scaling-group` flow with a target-tracking policy on a CloudWatch metric that reports SQLite `queued_ids` length. Out of scope here; the prototype's single-instance launch is the minimum to validate the unit economics.

## Why this is cheaper than the hosted M4 options

| Provider | Per-min cost | Per 7-min video |
|---|---|---|
| Synthesia | ~$0.50 | ~₹290 |
| Tavus | ~$0.50 | ~₹290 |
| HeyGen | ~$0.30 | ~₹175 |
| D-ID | ~$0.15 | ~₹90 |
| **Wav2Lip on g4dn.xlarge Spot (this stack)** | **~$0.04** | **~₹23** |

Once volume crosses ~50,000 minutes / month (~7,000 videos), the M3 tier amortises the GPU fleet cost. For institutional B2G contracts, M3 is the right answer; M4 stays for differentiated marketing where the teacher's face is brand-defining.

## Limits & known issues

- **SQLite over NFS is fine for low concurrency.** Above ~10 concurrent writers it'll start contending; switch to Litestream over S3 or migrate to Postgres on RDS.
- **Wav2Lip output quality** is good but not Synthesia-level — slight smearing around the lips at high speech rates. Pre-process Piper output through `--length_scale 1.2` for cleaner mouth motion (already the demo default).
- **Spot preemption rate** in us-east-1 for g4dn.xlarge is typically <5% per day. The cache + idempotent re-execution handles this transparently.
- **Cold starts are slow.** First worker job after a fresh instance takes ~3 min extra for Wav2Lip's CUDA warmup. Subsequent jobs on the same instance are ~2-3× audio duration.
