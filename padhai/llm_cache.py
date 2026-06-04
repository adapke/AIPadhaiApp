"""Q2 — LLM cost optimization (prompt caching + batch API).

Two levers for cutting our Anthropic bill:

1. **Prompt caching** — Anthropic charges 10% of normal input rate
   for cached tokens (90% discount). Add `cache_control` blocks to
   the large invariant parts of a prompt (system, few-shots,
   curriculum context); their re-use across N students within the
   5-min cache window costs ~once.

2. **Batch API** — 50% discount on non-interactive generation
   (lesson plans, daily current-affairs digests, mock test
   scoring). Submit a JSONL of requests; results land within 24h.

This module wraps both. Per-module opt-in:
  - `pedagogy` (high-volume, identical system prompt): caching ON
  - `tutor` (interactive, varied prompts): caching ON for system,
    OFF for user turns
  - `scorer` (batch-friendly, no latency requirement): batch ON
  - `essay_grader` (similar): batch ON

Falls back to plain calls when env vars unset. L6 observability
records `cached=True` on the call so the dashboard tracks savings.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

# ---------- prompt caching ----------

def is_caching_enabled() -> bool:
    """Env-gated. Default OFF for safety — flip on per-deploy when
    the L6 dashboard shows steady-state usage that justifies it."""
    return os.environ.get("PADHAI_LLM_CACHE_ENABLED") == "1"


@dataclass(frozen=True)
class CachedBlock:
    """One block of a prompt eligible for caching. Caller adds these
    to the system + tools + user-message structure before the
    Anthropic call. The SDK passes them through to the API which
    keys on the SHA of the block content."""
    text: str
    cache_type: str = "ephemeral"   # 'ephemeral' is the only Anthropic option as of 2026


def with_caching(
    *,
    system_text: str,
    user_text: str,
    cacheable_system: bool = True,
) -> dict:
    """Build an Anthropic `messages.create` kwargs dict with cache
    control. When caching is disabled, returns a plain shape; when
    enabled, marks the system block as ephemeral-cached.

    Caller passes the dict to `client.messages.create(**dict)`.

    The system block is the high-value cache target — it's typically
    1000+ tokens (style guide + few-shots + curriculum context) and
    identical across N requests. The user message stays uncached
    because each is unique."""
    if not cacheable_system or not is_caching_enabled():
        return {
            "system": system_text,
            "messages": [{"role": "user", "content": user_text}],
        }
    # Anthropic SDK shape for cached system: list of blocks instead
    # of a string. Each block can carry `cache_control`.
    return {
        "system": [
            {"type": "text", "text": system_text,
             "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_text}],
    }


# ---------- batch API ----------

def is_batch_enabled() -> bool:
    """Batch path: env-gated, OFF by default. Independent of caching
    — they compose."""
    return os.environ.get("PADHAI_LLM_BATCH_ENABLED") == "1"


@dataclass(frozen=True)
class BatchRequest:
    """One unit of batch work. `custom_id` lets the caller correlate
    the batch result back to its source (e.g. essay_submission.id)."""
    custom_id: str
    model: str
    system: str
    user_text: str
    max_tokens: int = 1024
    module: str = "unknown"   # for L6 observability


@dataclass(frozen=True)
class BatchSubmission:
    """Returned from `submit_batch()`. The caller stores `batch_id` +
    polls `poll_batch(batch_id)` periodically; on success it gets
    back `(custom_id, response_text)` pairs."""
    batch_id: str
    request_count: int
    submitted_at: float


def _default_outbox_dir() -> Path:
    """Return a portable default for local batch JSONL files."""
    return Path(tempfile.gettempdir()) / "padhai_llm_batch"


# Outbox dir — when batch isn't configured we write JSONL files to
# disk so the dev path still exercises the schema + the cron worker
# can pick them up later. Use tempfile.gettempdir() instead of a
# hard-coded /tmp path so Windows dev/test runs work.
_OUTBOX_DIR = Path(
    os.environ.get("PADHAI_LLM_BATCH_OUTBOX") or _default_outbox_dir()
)


def submit_batch(requests: list[BatchRequest]) -> BatchSubmission:
    """Submit a list of requests to Anthropic's batch API.

    When the API isn't configured (no key, no batch env flag), we
    serialize the requests to a JSONL file under
    `PADHAI_LLM_BATCH_OUTBOX` — the dev path still produces an
    artifact that an ops cron can submit later. In prod with
    `PADHAI_LLM_BATCH_ENABLED=1` we'd call
    `client.messages.batches.create(requests=...)`.

    Returns the batch_id so the caller can poll.
    """
    if not requests:
        raise ValueError("at least one request required")
    batch_id = "batch_" + uuid.uuid4().hex[:24]
    now = time.time()

    # Always write the outbox file — useful for debugging + fallback
    _OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _OUTBOX_DIR / f"{batch_id}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in requests:
            f.write(json.dumps({
                "custom_id": r.custom_id,
                "params": {
                    "model": r.model,
                    "max_tokens": r.max_tokens,
                    "system": r.system,
                    "messages": [{"role": "user", "content": r.user_text}],
                },
                "module": r.module,
            }) + "\n")

    if not is_batch_enabled():
        # Dev / unconfigured path — the JSONL on disk is the deliverable.
        return BatchSubmission(
            batch_id=batch_id,
            request_count=len(requests),
            submitted_at=now,
        )

    # Real submission would happen here. Lazy import the SDK so this
    # module loads without anthropic installed.
    try:
        from anthropic import Anthropic  # noqa: WPS433
        client = Anthropic()
        # SDK shape: client.messages.batches.create(requests=[...])
        # Returns a Batch object with .id, .processing_status, etc.
        sdk_requests = []
        for r in requests:
            sdk_requests.append({
                "custom_id": r.custom_id,
                "params": {
                    "model": r.model,
                    "max_tokens": r.max_tokens,
                    "system": r.system,
                    "messages": [
                        {"role": "user", "content": r.user_text},
                    ],
                },
            })
        result = client.messages.batches.create(requests=sdk_requests)
        return BatchSubmission(
            batch_id=getattr(result, "id", batch_id),
            request_count=len(requests),
            submitted_at=now,
        )
    except Exception as e:  # noqa: BLE001
        # Submission failed — outbox file persists so we can retry.
        print(f"[llm_cache] batch submit failed (outbox retained): {e}")
        return BatchSubmission(
            batch_id=batch_id,
            request_count=len(requests),
            submitted_at=now,
        )


def poll_batch(batch_id: str) -> dict:
    """Returns {status, results} where status ∈ {'pending',
    'completed', 'errored', 'not_found'} and results is a list of
    {custom_id, response_text, error} dicts (only when completed).

    Dev path: if the outbox JSONL still exists and Anthropic isn't
    wired, returns 'pending' so the caller can keep polling.
    Production: calls `client.messages.batches.retrieve(batch_id)`."""
    out_path = _OUTBOX_DIR / f"{batch_id}.jsonl"
    if not is_batch_enabled():
        return {
            "status": "pending" if out_path.exists() else "not_found",
            "batch_id": batch_id,
            "results": [],
        }
    try:
        from anthropic import Anthropic  # noqa: WPS433
        client = Anthropic()
        b = client.messages.batches.retrieve(batch_id)
        status = getattr(b, "processing_status", "pending")
        if status != "ended":
            return {
                "status": "pending", "batch_id": batch_id,
                "results": [],
            }
        # Iterate results
        results = []
        for item in client.messages.batches.results(batch_id):
            results.append({
                "custom_id": getattr(item, "custom_id", None),
                "response_text": _extract_text(item),
                "error": _extract_error(item),
            })
        return {
            "status": "completed", "batch_id": batch_id,
            "results": results,
        }
    except Exception as e:  # noqa: BLE001
        return {
            "status": "errored", "batch_id": batch_id,
            "error": str(e), "results": [],
        }


def _extract_text(item) -> str | None:
    res = getattr(item, "result", None)
    if not res:
        return None
    msg = getattr(res, "message", None) or res
    content = getattr(msg, "content", None)
    if not content:
        return None
    return "".join(
        getattr(b, "text", "") for b in content
        if getattr(b, "type", "") == "text"
    ) or None


def _extract_error(item) -> str | None:
    res = getattr(item, "result", None)
    if not res:
        return None
    err = getattr(res, "error", None)
    return str(err) if err else None


# ---------- module-level helpers for callers ----------

def describe() -> dict:
    """Diagnostic — drives the admin dashboard's 'cost optimization
    status' widget."""
    return {
        "caching_enabled": is_caching_enabled(),
        "batch_enabled": is_batch_enabled(),
        "outbox_dir": str(_OUTBOX_DIR),
        "outbox_pending_files": (
            len(list(_OUTBOX_DIR.glob("*.jsonl")))
            if _OUTBOX_DIR.exists() else 0
        ),
    }
