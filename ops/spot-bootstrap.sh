#!/usr/bin/env bash
# AWS EC2 user-data bootstrap for a PadhAI Wav2Lip GPU worker.
#
# Designed for the Deep Learning AMI (Ubuntu 22.04, CUDA 12.x). Run as
# the EC2 user-data on a g4dn.xlarge / g5.xlarge / similar Spot instance.
#
# Picks up four pieces of state at launch:
#   $PADHAI_REPO         git URL of this codebase
#   $PADHAI_BRANCH       branch to deploy (default: main)
#   $WAV2LIP_CHECKPOINT_URL  signed S3 URL or HTTPS link to wav2lip_gan.pth
#   $TEACHER_PHOTO_URL   signed S3 URL to the teacher portrait .jpg
#
# Plus all the standard PadhAI env vars (PADHAI_DB_PATH on shared EFS,
# ANTHROPIC_API_KEY etc.) which should be injected via systemd EnvironmentFile
# or AWS Systems Manager Parameter Store — never baked into the bootstrap.

set -euo pipefail

# --- 1. system deps ---
apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg fontconfig fonts-noto-core fonts-noto-cjk fonts-indic \
    python3-pip python3-venv git nfs-common

# --- 2. mount the shared EFS for the SQLite job DB + cache + outputs ---
# Replace fs-XXXX with your EFS file system ID; mount target must exist in
# this instance's subnet. EFS is the simplest "shared SQLite" path; for
# higher throughput consider Litestream-replicated SQLite on S3 instead.
mkdir -p /shared/padhai
mount -t nfs4 -o nfsvers=4.1 "${EFS_DNS:-fs-XXXX.efs.${AWS_REGION:-us-east-1}.amazonaws.com}":/ /shared/padhai || \
    echo "[bootstrap] WARNING: EFS mount failed; jobs will be local-only"

# --- 3. clone the PadhAI codebase ---
PADHAI_REPO="${PADHAI_REPO:-https://github.com/adapke/paymentoneclick.git}"
PADHAI_BRANCH="${PADHAI_BRANCH:-main}"
cd /opt
git clone --branch "$PADHAI_BRANCH" --depth 1 "$PADHAI_REPO" padhai
cd /opt/padhai
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# --- 4. clone Wav2Lip + checkpoint + source photo ---
git clone --depth 1 https://github.com/Rudrabha/Wav2Lip /opt/Wav2Lip
.venv/bin/pip install -r /opt/Wav2Lip/requirements.txt

mkdir -p /opt/Wav2Lip/checkpoints
curl -L --fail "${WAV2LIP_CHECKPOINT_URL:?WAV2LIP_CHECKPOINT_URL not set}" \
    -o /opt/Wav2Lip/checkpoints/wav2lip_gan.pth
curl -L --fail "${TEACHER_PHOTO_URL:?TEACHER_PHOTO_URL not set}" \
    -o /opt/Wav2Lip/teacher_portrait.jpg

# --- 5. systemd service so the worker survives reboots & Spot warnings ---
cat >/etc/systemd/system/padhai-gpu.service <<'EOF'
[Unit]
Description=PadhAI Wav2Lip GPU worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/padhai/env
WorkingDirectory=/opt/padhai
ExecStart=/opt/padhai/.venv/bin/python -m padhai.gpu_worker
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# /etc/padhai/env should be deployed out-of-band (Ansible / SSM
# Parameter Store / sealed secrets) — never write API keys to disk via
# user-data, since EC2 user-data is readable from inside the instance.
mkdir -p /etc/padhai
chmod 700 /etc/padhai
[ -f /etc/padhai/env ] || cat >/etc/padhai/env <<'EOF'
# placeholder — replace with real values via your secrets pipeline
PADHAI_DB_PATH=/shared/padhai/jobs.db
PADHAI_CACHE_DIR=/shared/padhai/cache
PADHAI_OUTPUT_DIR=/shared/padhai/outputs
ANTHROPIC_API_KEY=replace_me
WAV2LIP_REPO_PATH=/opt/Wav2Lip
WAV2LIP_CHECKPOINT=/opt/Wav2Lip/checkpoints/wav2lip_gan.pth
WAV2LIP_SOURCE_PHOTO=/opt/Wav2Lip/teacher_portrait.jpg
EOF
chmod 600 /etc/padhai/env

systemctl daemon-reload
systemctl enable --now padhai-gpu.service

# --- 6. AWS Spot-termination handler ---
# Spot can pull the rug with a 2-minute warning. Catch it via IMDSv2 and
# let the worker finish its current job before shutdown. systemd's
# RestartSec=10 + the cache layer makes losing the instance survivable
# anyway, but graceful shutdown saves a few cents.
cat >/usr/local/bin/spot-termination-watcher.sh <<'EOF'
#!/bin/bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
while true; do
    code=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "X-aws-ec2-metadata-token: $TOKEN" \
        http://169.254.169.254/latest/meta-data/spot/instance-action)
    if [ "$code" = "200" ]; then
        logger "[spot-watcher] termination notice; stopping padhai-gpu cleanly"
        systemctl stop padhai-gpu.service
        break
    fi
    sleep 5
done
EOF
chmod +x /usr/local/bin/spot-termination-watcher.sh
nohup /usr/local/bin/spot-termination-watcher.sh &>/var/log/spot-watcher.log &

logger "[bootstrap] padhai-gpu worker started"
