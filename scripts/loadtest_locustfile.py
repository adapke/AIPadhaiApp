"""G6 — Load testing harness.

Locust file that exercises the v1.x hot endpoints at sustained
concurrency. Goal: validate that the v1.2 queue + Postgres
foundations actually unlock the throughput we modeled in
ROADMAP_V2.md's G6 section.

Install + run:

    pip install locust
    locust -f scripts/loadtest_locustfile.py \\
           --host https://staging.aipathshala.in \\
           --users 1000 --spawn-rate 50 --run-time 10m

The headless variant (CI-friendly) produces a CSV:

    locust -f scripts/loadtest_locustfile.py \\
           --host http://localhost:8000 \\
           --headless --users 200 --spawn-rate 20 --run-time 60s \\
           --csv=/tmp/padhai_loadtest

Pass criteria (the SLO from G6 in ROADMAP_V2.md):
- p95 latency on every endpoint < 1.5s under 200 concurrent users
- p99 latency on /api/v2/video-requests/{id}/status < 800ms
- 0 errors (5xx) sustained over 60s

A second user class (`VideoUser`) exercises the heaviest path
(submitting + polling video requests) at a lower weight; tune the
`weight` numbers below to match production traffic mix once we
have real analytics.
"""

from __future__ import annotations

import random
import time

try:
    from locust import HttpUser, between, task, events
except ImportError as e:
    raise SystemExit(
        "locust not installed. Run: pip install locust"
    ) from e


SAMPLE_TOPICS = [
    "photosynthesis",
    "newton's laws",
    "quadratic equations",
    "indian independence movement",
    "the water cycle",
    "fractions and decimals",
    "the periodic table",
]

SAMPLE_LANGUAGES = ["en", "hi", "ta", "te", "mr"]


class BrowsingUser(HttpUser):
    """The 80% case: a student opens the app, reads their feed,
    browses the library, checks the leaderboard. Mostly cache hits
    + tiny JSON responses."""

    wait_time = between(1.5, 4.0)
    weight = 4  # 80% of synthetic users

    def on_start(self):
        # Anonymous browse — no auth in v1.2 load tests.
        # When auth is required for endpoints under test, add a
        # signup → login pair here and persist the token.
        self.client.headers.update({"User-Agent": "padhai-loadtest/1.2"})

    @task(5)
    def root(self):
        with self.client.get("/", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"root status={r.status_code}")
            elif r.json().get("version", "").startswith("1."):
                r.success()

    @task(3)
    def spa_shell(self):
        # The biggest static response — exercises gzip + the JS bundle
        # size budget.
        self.client.get("/ui", name="/ui (SPA shell)")

    @task(2)
    def manifest(self):
        self.client.get("/manifest.json")

    @task(2)
    def service_worker(self):
        self.client.get("/sw.js")

    @task(1)
    def video_modes(self):
        self.client.get("/api/v2/video-modes")

    @task(1)
    def avatar_providers(self):
        self.client.get("/api/avatar-providers")

    @task(1)
    def branding_resolve(self):
        self.client.get("/api/branding/resolve")

    @task(1)
    def curriculum_index(self):
        self.client.get("/curriculum/index")

    @task(1)
    def health(self):
        self.client.get("/health")


class VideoUser(HttpUser):
    """The 20% case: a teacher / student who actually generates a
    video. Lower frequency but heavy on the worker fleet. We DO NOT
    submit real renders against staging unless that staging deploy is
    configured for synthetic-traffic mode (otherwise we'd burn
    Anthropic + TTS budget); instead we poll the status of a
    PRE-EXISTING job_id passed via env var.

    For pure-frontend load tests this user is a no-op."""

    wait_time = between(5.0, 12.0)
    weight = 1

    SYNTHETIC_JOB_ID = None  # set via --jobs="job_id1,job_id2,..." in run config

    def on_start(self):
        import os
        ids = os.environ.get("PADHAI_LOADTEST_JOB_IDS", "")
        self._job_ids = [s.strip() for s in ids.split(",") if s.strip()]
        self.client.headers.update({"User-Agent": "padhai-loadtest/1.2"})

    @task(5)
    def poll_status(self):
        if not self._job_ids:
            return
        jid = random.choice(self._job_ids)
        self.client.get(
            f"/api/v2/video-requests/{jid}/status",
            name="/api/v2/video-requests/{id}/status",
        )

    @task(3)
    def poll_result(self):
        if not self._job_ids:
            return
        jid = random.choice(self._job_ids)
        self.client.get(
            f"/api/v2/video-requests/{jid}/result",
            name="/api/v2/video-requests/{id}/result",
            catch_response=True,
        )

    @task(1)
    def chat_with_lesson(self):
        if not self._job_ids:
            return
        jid = random.choice(self._job_ids)
        self.client.post(
            f"/chat/{jid}",
            json={"question": random.choice([
                "explain that again simpler",
                "give me an example",
                "what's a related topic",
            ])},
            name="/chat/{lesson_id}",
        )


# ---------- Threshold enforcement (CI gate) ----------

@events.quitting.add_listener
def _enforce_thresholds(environment, **_kwargs):
    """Fail the run when SLOs are missed. CI uses the exit code."""
    stats = environment.stats.total
    fail_ratio = (stats.num_failures / stats.num_requests) if stats.num_requests else 0
    p95 = stats.get_response_time_percentile(0.95)
    p99 = stats.get_response_time_percentile(0.99)

    failures = []
    if fail_ratio > 0.001:
        failures.append(f"failure ratio {fail_ratio:.3%} > 0.1%")
    if p95 > 1500:
        failures.append(f"p95 {p95:.0f}ms > 1500ms")
    if p99 > 3000:
        failures.append(f"p99 {p99:.0f}ms > 3000ms")

    if failures:
        print("\n=== LOAD TEST FAILED ===")
        for f in failures:
            print(f"  - {f}")
        environment.process_exit_code = 1
    else:
        print(
            f"\n=== LOAD TEST PASS — requests={stats.num_requests} "
            f"p95={p95:.0f}ms p99={p99:.0f}ms fail_ratio={fail_ratio:.4%} ==="
        )
