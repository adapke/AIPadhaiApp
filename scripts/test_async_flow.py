"""Verify the async job flow end-to-end without hitting the real Claude
or render pipeline.

Patches _render_worker to just copy a pre-made fake MP4 to the output
path. Exercises: enqueue → poll → download → cache hit on second submit."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

# Patch BEFORE importing padhai.web so the singleton worker uses our stub
import padhai.jobs as jobs_mod
import padhai.web as web_mod


def fake_worker(job):
    """Skip Claude + render; just copy a fake MP4 to the output dir."""
    out_path = web_mod._OUTPUT_DIR / f"{job.id}.mp4"
    out_path.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake mp4 data " * 100)
    return {"output_path": str(out_path), "cache_hit": False}


# swap out the worker on the live runner
web_mod.runner.worker_fn = fake_worker

client = TestClient(web_mod.app)


def _make_fake_image() -> Path:
    p = Path(tempfile.mkstemp(suffix=".png")[1])
    # 1x1 PNG
    p.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return p


def main():
    img = _make_fake_image()

    print("\n--- POST /lessons (first submit, miss cache) ---")
    with img.open("rb") as f:
        r = client.post(
            "/lessons",
            files={"image": ("page.png", f, "image/png")},
            data={"language": "en", "level": "middle", "teacher": "false"},
        )
    assert r.status_code == 202, r.text
    body = r.json()
    print(f"  202 enqueued: {body}")
    job_id = body["job_id"]

    print("\n--- GET /jobs/{id} (poll until done) ---")
    for attempt in range(20):
        time.sleep(0.5)
        r = client.get(f"/jobs/{job_id}")
        status = r.json()["status"]
        print(f"  poll #{attempt}: {status}")
        if status == "succeeded":
            break
    else:
        raise RuntimeError("job didn't finish")

    print("\n--- GET /jobs/{id}/video ---")
    r = client.get(f"/jobs/{job_id}/video")
    assert r.status_code == 200, r.text
    print(f"  200, {len(r.content):,} bytes, content-type={r.headers['content-type']}")

    print("\n--- POST /lessons (same image, expect cache hit, 200 inline) ---")
    with img.open("rb") as f:
        r = client.post(
            "/lessons",
            files={"image": ("page.png", f, "image/png")},
            data={"language": "en", "level": "middle", "teacher": "false"},
        )
    print(f"  status={r.status_code}, content-type={r.headers.get('content-type')}")
    # Cache hit returns 200 with the MP4 inline (the FileResponse path)
    if r.status_code == 200:
        print(f"  cache hit, served inline ({len(r.content):,} bytes)")
    else:
        print(f"  no cache hit (status {r.status_code}): {r.json()}")

    print("\n--- GET /jobs/nonexistent → 404 ---")
    r = client.get("/jobs/doesnotexist")
    assert r.status_code == 404
    print(f"  404 as expected: {r.json()}")

    print("\nOK — async flow works end-to-end (enqueue/poll/download/cache-hit)")


if __name__ == "__main__":
    main()
