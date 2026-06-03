"""Accuracy benchmark runner — used by the accuracy-bench CI job.

Two modes:

  --mode=structural  (default; runs without ANTHROPIC_API_KEY)
    Loads the golden dataset, creates a draft + publishes it, then runs
    a stub-runner (returns the expected answer verbatim) to verify the
    storage + judge code paths work end-to-end. Fails on schema errors,
    storage errors, or judge crashes — but cannot detect real accuracy
    regressions. Suitable for every PR.

  --mode=live  (requires ANTHROPIC_API_KEY)
    Same as structural, but the runner calls Claude on each prompt.
    Produces a real pass-rate. Should be wired to a nightly cron once
    we have an API key in the CI secret store.

Exit codes:
  0  benchmark completed (structural mode always; live mode if
     pass_rate >= --min-pass-rate threshold, default 0.70)
  1  any error (dataset malformed, runner crash, threshold miss)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from padhai import accuracy_bench as bench  # noqa: E402


def _load_fixture(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _seed_dataset(fixture: dict) -> str:
    """Idempotent — re-uses the dataset if a published row with the same
    code already exists, otherwise creates + publishes a fresh version.

    Returns the dataset_id."""
    code = fixture["code"]
    existing = [d for d in bench.list_datasets() if d.code == code]
    if existing:
        # Bump version so we don't clash with the prior immutable row.
        next_version = max(d.version for d in existing) + 1
    else:
        next_version = 1
    ds = bench.create_dataset(
        code=code,
        title=fixture["title"],
        domain=fixture["domain"],
        task_kind=fixture["task_kind"],
        description=fixture.get("description"),
        version=next_version,
        reviewed_by=fixture.get("reviewed_by"),
    )
    for item in fixture["items"]:
        bench.add_item(
            dataset_id=ds.id,
            prompt=item["prompt"],
            expected=item["expected"],
            rubric=item.get("rubric"),
            difficulty=item.get("difficulty"),
            tags=item.get("tags"),
            weight=item.get("weight", 1.0),
        )
    bench.publish_dataset(ds.id)
    return ds.id


def _stub_runner(prompt: str) -> dict:
    """Structural-mode runner — looks up the expected answer in the
    in-process fixture so the judge has something to score. Verifies
    the storage + judge pipeline, NOT model accuracy."""
    return _STUB_LOOKUP.get(prompt, {"answer": ""})


def _claude_runner_factory():
    """Live-mode runner — calls Claude haiku and parses a one-line
    answer. Lazy-import so structural mode doesn't pull anthropic."""
    from anthropic import Anthropic
    client = Anthropic()
    model = os.environ.get("PADHAI_BENCH_MODEL", "claude-haiku-4-5-20251001")

    def _runner(prompt: str) -> dict:
        resp = client.messages.create(
            model=model,
            max_tokens=80,
            system=(
                "Answer the user's question in one short word or phrase. "
                "No explanations, no punctuation beyond what's required. "
                "Lower-case unless the answer is a proper noun."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return {"answer": text}
    return _runner


_STUB_LOOKUP: dict[str, dict] = {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=str(ROOT / "tests" / "fixtures" / "golden_answers.json"),
    )
    parser.add_argument("--mode", choices=["structural", "live"], default="structural")
    parser.add_argument("--judge", default="rouge_l")
    parser.add_argument("--min-pass-rate", type=float, default=0.70)
    parser.add_argument("--db", default=os.environ.get("PADHAI_DB_PATH", "/tmp/padhai_bench.db"))
    args = parser.parse_args()

    os.environ["PADHAI_DB_PATH"] = args.db

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"[bench] fixture missing: {fixture_path}", file=sys.stderr)
        return 1

    fixture = _load_fixture(fixture_path)
    print(f"[bench] fixture: {fixture['code']} ({len(fixture['items'])} items)")

    global _STUB_LOOKUP
    _STUB_LOOKUP = {it["prompt"]: it["expected"] for it in fixture["items"]}

    try:
        dataset_id = _seed_dataset(fixture)
    except Exception as e:  # noqa: BLE001
        print(f"[bench] seed failed: {e}", file=sys.stderr)
        return 1

    if args.mode == "live":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("[bench] live mode requires ANTHROPIC_API_KEY", file=sys.stderr)
            return 1
        runner = _claude_runner_factory()
        target = os.environ.get("PADHAI_BENCH_MODEL", "claude-haiku-4-5-20251001")
    else:
        runner = _stub_runner
        target = "stub_runner"

    started = time.time()
    run = bench.run_benchmark(
        dataset_id=dataset_id,
        target=target,
        judge=args.judge,
        runner=runner,
        notes=f"CI {args.mode} mode",
    )
    duration = time.time() - started

    total = run.pass_count + run.fail_count
    pass_rate = run.pass_count / total if total else 0.0
    print(
        f"[bench] target={run.target} judge={run.judge} "
        f"items={run.item_count} pass={run.pass_count} fail={run.fail_count} "
        f"skipped={run.skipped_count} mean={run.mean_score} "
        f"p50={run.p50_score} p90={run.p90_score} "
        f"pass_rate={pass_rate:.3f} took={duration:.1f}s"
    )

    if args.mode == "live" and pass_rate < args.min_pass_rate:
        print(
            f"[bench] FAIL: pass_rate {pass_rate:.3f} < threshold "
            f"{args.min_pass_rate}",
            file=sys.stderr,
        )
        return 1

    if args.mode == "structural":
        # In structural mode the stub always returns the expected answer,
        # so a non-perfect pass rate indicates a judge bug or fixture
        # malformation, not a model regression. Fail loudly.
        if pass_rate < 1.0:
            print(
                f"[bench] FAIL: structural pass_rate {pass_rate:.3f} != 1.0 — "
                "judge or fixture is broken",
                file=sys.stderr,
            )
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
