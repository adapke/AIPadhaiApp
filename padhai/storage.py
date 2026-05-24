"""Cloud object storage for rendered videos and cached audio.

At single-instance scale we keep files on local disk. At 1M-user scale
that breaks — files don't survive container restarts, multiple workers
can't share state, egress is bounded by the instance's network card,
and serving 50TB/mo of video from a Render instance is impossible.

Cloudflare R2 is the right primitive for this product specifically
because it has zero egress fees. A 7-min MP4 is ~10MB; at 1M users × 5
videos/month × 10MB = 50TB/month of egress. S3 charges ~$0.09/GB egress
($4,500/mo at that volume); R2 charges $0 egress.

For development and prototypes, LocalDiskStorage keeps the local-only
flow working — same interface, same code path, just a different
backend selected from the environment."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Protocol


class ObjectStorage(Protocol):
    """Minimum surface every storage backend implements.

    Keys are application-meaningful identifiers (typically sha256 hashes
    of the content's inputs). Returned URLs may be either time-limited
    signed URLs or public CDN URLs depending on the backend's config."""

    name: str

    def put(self, key: str, source: Path, content_type: str = "video/mp4") -> str:
        """Upload source file under key. Returns a URL the client can GET."""
        ...

    def get(self, key: str, dest: Path) -> bool:
        """If key exists, copy it to dest and return True; else False."""
        ...

    def exists(self, key: str) -> bool: ...

    def url(self, key: str, expires_seconds: int = 3600) -> str:
        """Return a URL for direct client access (skip the web tier entirely)."""
        ...


class LocalDiskStorage:
    """Default — used in dev and when no cloud creds are configured.
    URLs returned are local file paths; the web tier serves them via
    FileResponse."""

    name = "local"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys typically already have a folder prefix (videos/<hash>.mp4)
        # so we let the key dictate the layout. Extra sharding can be
        # added later if any single directory grows beyond ~100k entries.
        return self.root / key

    def put(self, key: str, source: Path, content_type: str = "video/mp4") -> str:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return self.url(key)

    def get(self, key: str, dest: Path) -> bool:
        src = self._path(key)
        if not src.exists():
            return False
        shutil.copyfile(src, dest)
        return True

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url(self, key: str, expires_seconds: int = 3600) -> str:
        return f"file://{self._path(key)}"


class S3Storage:
    """AWS S3 and S3-compatible providers (Cloudflare R2, Backblaze B2,
    Wasabi). The same boto3 client class works for all of them — only
    the endpoint URL differs.

    For Cloudflare R2:
        export S3_BUCKET=my-padhai-videos
        export S3_REGION=auto
        export S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
        export S3_ACCESS_KEY_ID=...
        export S3_SECRET_ACCESS_KEY=...
        # optional: a Cloudflare-fronted public URL for unsigned serves
        export S3_PUBLIC_URL_BASE=https://videos.padhai.ai

    For AWS S3:
        export S3_BUCKET=my-padhai-videos
        export S3_REGION=ap-south-1
        # AWS creds via IAM role / instance profile / standard chain

    For Backblaze B2:
        export S3_ENDPOINT_URL=https://s3.us-west-002.backblazeb2.com
        ... etc."""

    name = "s3"

    def __init__(
        self,
        bucket: str,
        region: str = "auto",
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        public_url_base: str | None = None,
    ):
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError(
                "boto3 required for S3Storage (pip install boto3)"
            ) from e

        self.bucket = bucket
        self.public_url_base = public_url_base
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

    def put(self, key: str, source: Path, content_type: str = "video/mp4") -> str:
        self._client.upload_file(
            str(source), self.bucket, key,
            ExtraArgs={"ContentType": content_type},
        )
        return self.url(key)

    def get(self, key: str, dest: Path) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.download_file(self.bucket, key, str(dest))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    def url(self, key: str, expires_seconds: int = 3600) -> str:
        """Prefer a public CDN URL when configured (no signing overhead,
        edge-cacheable). Otherwise return a signed URL valid for
        expires_seconds (default 1h — long enough for a student to watch,
        short enough to revoke if leaked)."""
        if self.public_url_base:
            return f"{self.public_url_base.rstrip('/')}/{key}"
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def get_storage() -> ObjectStorage:
    """Pick a backend from the environment. Falls back to LocalDiskStorage
    rooted at $PADHAI_OUTPUT_DIR (or ~/.padhai/outputs)."""
    if os.environ.get("S3_BUCKET"):
        return S3Storage(
            bucket=os.environ["S3_BUCKET"],
            region=os.environ.get("S3_REGION", "auto"),
            endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
            access_key_id=os.environ.get("S3_ACCESS_KEY_ID") or None,
            secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY") or None,
            public_url_base=os.environ.get("S3_PUBLIC_URL_BASE") or None,
        )
    root = Path(os.environ.get(
        "PADHAI_OUTPUT_DIR", str(Path.home() / ".padhai" / "outputs")
    ))
    return LocalDiskStorage(root)


def content_key(prefix: str, *parts: bytes | str) -> str:
    """Build a deterministic, sharded storage key from hash inputs.
    Used by the cache layer so multiple workers compute identical keys
    for identical inputs."""
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, str):
            p = p.encode("utf-8")
        h.update(p)
        h.update(b"\x00")
    digest = h.hexdigest()
    return f"{prefix}/{digest}.mp4" if prefix.endswith("videos") else f"{prefix}/{digest}"
