"""Request a Spot GPU instance for the PadhAI Wav2Lip worker.

Wraps the boto3 RunInstances spot-market call with sane defaults: a
g4dn.xlarge (16 GB VRAM, lowest-tier NVIDIA) is enough for Wav2Lip and
typically prices at ~30% of on-demand. Pass --instance-type g5.xlarge
when latency matters more than cost.

This is the minimum-viable launcher; production deployments should use
an Auto Scaling Group with mixed Spot pools and scale-from-zero based
on SQS / SQLite queue depth. See ops/README.md for the production
runbook.

Prereqs:
    pip install boto3
    aws configure   # or IAM role on your shell host
    Subnet, security group, key pair already created in the target VPC
    EFS file system with mount targets in the same subnet
    The PadhAI bootstrap script (ops/spot-bootstrap.sh) committed to a
        publicly readable URL OR fetched from S3 via instance role

Usage:
    python ops/spot-launch.py \\
        --subnet subnet-abc123 \\
        --security-group sg-abc123 \\
        --key-pair my-keypair \\
        --efs-dns fs-xxxx.efs.us-east-1.amazonaws.com \\
        --checkpoint-url https://signed-s3-url/wav2lip_gan.pth \\
        --teacher-photo-url https://signed-s3-url/teacher.jpg

The instance terminates itself if the Spot pool reclaims it. The
worker survives a reclaim because the SQLite job is set back to
'queued' on the next worker's startup (see padhai/gpu_worker.py)."""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="padhai-spot-launch")
    parser.add_argument("--subnet", required=True, help="VPC subnet id")
    parser.add_argument("--security-group", required=True, help="security group id")
    parser.add_argument("--key-pair", required=True, help="EC2 key pair name")
    parser.add_argument(
        "--instance-type", default="g4dn.xlarge",
        help="EC2 instance type (default: g4dn.xlarge ~ $0.50/h on-demand, ~$0.15/h spot)",
    )
    parser.add_argument(
        "--ami", default="ami-0fcf52bcf5db7b003",  # AWS DLAMI Ubuntu 22.04 (us-east-1)
        help="AMI id — needs CUDA + Docker preinstalled (default: DLAMI us-east-1)",
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--efs-dns", required=True,
        help="EFS DNS name where the shared SQLite/cache/outputs live",
    )
    parser.add_argument("--checkpoint-url", required=True,
                        help="signed URL for wav2lip_gan.pth")
    parser.add_argument("--teacher-photo-url", required=True,
                        help="signed URL for teacher portrait .jpg")
    parser.add_argument(
        "--bootstrap-script",
        type=Path, default=Path(__file__).resolve().parent / "spot-bootstrap.sh",
        help="path to the user-data bootstrap script",
    )
    parser.add_argument(
        "--max-spot-price", default="0.30",
        help="USD/hour cap (default: $0.30, comfortable above g4dn.xlarge avg)",
    )
    args = parser.parse_args(argv)

    try:
        import boto3
    except ImportError:
        print("error: boto3 not installed (pip install boto3)", file=sys.stderr)
        return 2

    bootstrap = args.bootstrap_script.read_text()
    # Inject the runtime env vars into the script's top via a heredoc.
    user_data = (
        "#!/bin/bash\n"
        f"export EFS_DNS={args.efs_dns!r}\n"
        f"export AWS_REGION={args.region!r}\n"
        f"export WAV2LIP_CHECKPOINT_URL={args.checkpoint_url!r}\n"
        f"export TEACHER_PHOTO_URL={args.teacher_photo_url!r}\n"
        "\n# === bootstrap ===\n"
        + bootstrap
    )

    ec2 = boto3.client("ec2", region_name=args.region)
    resp = ec2.run_instances(
        ImageId=args.ami,
        InstanceType=args.instance_type,
        KeyName=args.key_pair,
        SubnetId=args.subnet,
        SecurityGroupIds=[args.security_group],
        MinCount=1,
        MaxCount=1,
        UserData=base64.b64encode(user_data.encode()).decode(),
        InstanceMarketOptions={
            "MarketType": "spot",
            "SpotOptions": {
                "MaxPrice": args.max_spot_price,
                "SpotInstanceType": "one-time",
                "InstanceInterruptionBehavior": "terminate",
            },
        },
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "padhai-role", "Value": "gpu-worker"},
                {"Key": "padhai-tier", "Value": "M3-wav2lip"},
            ],
        }],
    )
    inst = resp["Instances"][0]
    print(f"launched {inst['InstanceId']} as {args.instance_type} spot in {args.region}")
    print(f"  watch logs:  ssh ubuntu@<public-ip> 'journalctl -fu padhai-gpu'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
