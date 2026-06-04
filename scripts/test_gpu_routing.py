"""Verify the cross-worker job routing.

Simulates the production setup with two workers sharing one SQLite DB:
the web-tier JobRunner that runs everything except wav2lip, and a
PollingRunner that runs only wav2lip. Asserts:
  - jobs go to the correct worker by `talking_head_provider`
  - claim() prevents double-execution under concurrent workers
"""

from __future__ import annotations

import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

from padhai.jobs import Job, JobRunner, JobStore, PollingRunner


def main() -> None:
    db = Path(tempfile.mkstemp(suffix=".db")[1])
    store = JobStore(db)

    executed_by: dict[str, list[str]] = defaultdict(list)
    executed_lock = threading.Lock()

    def web_worker(job: Job) -> dict:
        with executed_lock:
            executed_by["web"].append(job.id)
        time.sleep(0.05)
        return {"provider": job.payload.get("talking_head_provider"), "by": "web"}

    def gpu_worker(job: Job) -> dict:
        with executed_lock:
            executed_by["gpu"].append(job.id)
        time.sleep(0.05)
        return {"provider": job.payload.get("talking_head_provider"), "by": "gpu"}

    # web tier: handles everything except wav2lip
    web_runner = JobRunner(
        store,
        worker_fn=web_worker,
        max_workers=2,
        job_filter=lambda p: p.get("talking_head_provider") != "wav2lip",
    )

    # gpu tier: only wav2lip
    gpu_runner = PollingRunner(
        store,
        worker_fn=gpu_worker,
        job_filter=lambda p: p.get("talking_head_provider") == "wav2lip",
        poll_seconds=0.1,
    )
    gpu_thread = threading.Thread(target=gpu_runner.run_forever, daemon=True)
    gpu_thread.start()

    # Enqueue: 3 wav2lip jobs + 3 cartoon jobs + 2 heygen jobs
    submitted: list[str] = []
    for provider in ("wav2lip", "cartoon", "heygen", "wav2lip", "cartoon",
                     "wav2lip", "heygen", "cartoon"):
        j = web_runner.enqueue({"talking_head_provider": provider})
        submitted.append(j.id)

    # wait for everything to drain
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        statuses = [store.get(jid).status for jid in submitted]
        if all(s in ("succeeded", "failed") for s in statuses):
            break
        time.sleep(0.1)

    gpu_runner.stop()
    web_runner.shutdown(wait=True)

    # Verify
    all_executed = executed_by["web"] + executed_by["gpu"]
    duplicates = [jid for jid in all_executed if all_executed.count(jid) > 1]
    assert not duplicates, f"jobs executed twice: {duplicates}"

    print(f"\nweb worker executed {len(executed_by['web'])} jobs")
    print(f"gpu worker executed {len(executed_by['gpu'])} jobs")
    print("  expected:  web=5 (3 cartoon + 2 heygen), gpu=3 (wav2lip)")

    for jid in submitted:
        job = store.get(jid)
        provider = job.payload["talking_head_provider"]
        actual_worker = job.result["by"]
        expected_worker = "gpu" if provider == "wav2lip" else "web"
        assert actual_worker == expected_worker, (
            f"job {jid[:6]} provider={provider} expected {expected_worker} "
            f"but ran on {actual_worker}"
        )

    assert len(executed_by["web"]) == 5
    assert len(executed_by["gpu"]) == 3
    print("\nOK — routing is correct and no double-execution")


if __name__ == "__main__":
    main()
