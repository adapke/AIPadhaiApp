"""Production observability — structured logging, metrics, error reporting.

What this module gives ops on a production deploy:

1. **Structured request logging.** Every HTTP request emits a single
   JSON line to stdout with method, path, status, duration_ms, and
   (when auth'd) user_id. Render's log aggregator picks it up; a
   shipper like Grafana Loki or Datadog can parse + alert on it.

2. **In-memory request metrics.** Counters per (method, path, status)
   plus latency percentiles. Exposed at GET /metrics for human / Grafana
   scraping. Reset every process restart (intentional — we don't run
   our own metrics DB).

3. **Sentry / PostHog hooks.** Initialised when SENTRY_DSN /
   POSTHOG_API_KEY are set. Stubs no-op when absent so dev / sandbox
   stays clean. We don't import the SDKs at module load — only if the
   env var is configured.

The middleware is deliberately small. It does NOT replace a real APM
(Datadog APM, New Relic, OpenTelemetry-instrumented) — for a 2-engineer
team at our scale, "JSON to stdout + counters in memory" hits the 80/20
sweet spot. When you outgrow it, swap in OpenTelemetry middleware
without changing the rest of the codebase.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# ---------- structured logging ----------

def log_event(event: str, **fields: Any) -> None:
    """Emit a single JSON line. Standardised on stdout so Render's log
    pipeline catches it without configuration. Times go in as
    `ts_iso`; the level defaults to 'info'."""
    payload = {
        "ts": time.time(),
        "event": event,
        **fields,
    }
    # We use json.dumps directly (not logging.Logger.info) because
    # logging adds a prefix; we want the line to BE the JSON.
    sys.stdout.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---------- in-memory metrics ----------

@dataclass
class _RouteMetrics:
    count: int = 0
    error_count: int = 0
    latencies_ms: list[float] = field(default_factory=list)  # bounded ring

    def record(self, status: int, duration_ms: float) -> None:
        self.count += 1
        if status >= 500:
            self.error_count += 1
        # Bounded ring so memory stays flat. 1024 = enough for p50/p95
        # over a meaningful window without growing unbounded.
        self.latencies_ms.append(duration_ms)
        if len(self.latencies_ms) > 1024:
            self.latencies_ms.pop(0)


_lock = Lock()
_metrics: dict[tuple[str, str], _RouteMetrics] = defaultdict(_RouteMetrics)
_started_at = time.time()


def _record(method: str, path: str, status: int, duration_ms: float) -> None:
    # Collapse path-params (UUIDs) so /jobs/abc and /jobs/def land
    # in the same bucket. The route template comes from FastAPI when
    # available (see middleware) so this is just a safety net.
    bucket = (method, _collapse_path(path))
    with _lock:
        _metrics[bucket].record(status, duration_ms)


_UUID_RE = None  # lazy import


def _collapse_path(path: str) -> str:
    global _UUID_RE
    if _UUID_RE is None:
        import re
        _UUID_RE = re.compile(
            r"/(?:[0-9a-f]{32}|[0-9a-f-]{36}|\d+)(?=/|$)",
            re.IGNORECASE,
        )
    return _UUID_RE.sub("/{id}", path)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(len(s) * pct)))
    return round(s[idx], 1)


def snapshot() -> dict:
    """Return the current metrics in a JSON-friendly shape. Read-locked."""
    out_routes: list[dict] = []
    with _lock:
        total_requests = 0
        total_errors = 0
        for (method, path), m in _metrics.items():
            total_requests += m.count
            total_errors += m.error_count
            out_routes.append({
                "method": method, "path": path,
                "count": m.count,
                "errors": m.error_count,
                "p50_ms": _percentile(m.latencies_ms, 0.50),
                "p95_ms": _percentile(m.latencies_ms, 0.95),
                "p99_ms": _percentile(m.latencies_ms, 0.99),
            })
    out_routes.sort(key=lambda r: r["count"], reverse=True)
    return {
        "uptime_seconds": int(time.time() - _started_at),
        "total_requests": total_requests,
        "total_errors": total_errors,
        "error_rate_pct": (
            round(total_errors * 100 / total_requests, 2)
            if total_requests else 0.0
        ),
        "routes": out_routes,
    }


# ---------- ASGI middleware ----------

class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Wraps every request to:
      1. Time it (monotonic, ms resolution)
      2. Record into the in-memory metrics by route template
      3. Emit a single JSON log line with method/path/status/duration
      4. Forward exceptions to Sentry if installed

    Health and metrics endpoints are special-cased to keep the noise
    floor low — those get hit hundreds of times an hour by Render's
    healthchecker."""

    QUIET_PATHS = frozenset({"/health", "/healthz", "/metrics"})

    async def dispatch(self, request, call_next):
        start = time.perf_counter()
        method = request.method
        path = request.url.path
        status = 500   # default for the exception path below

        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as e:
            _maybe_capture_exception(e)
            raise
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            # Prefer the route template if FastAPI matched a route —
            # otherwise the raw path goes through _collapse_path.
            route = getattr(request.scope.get("route", None), "path", None)
            _record(method, route or path, status, duration_ms)
            if path not in self.QUIET_PATHS:
                log_event(
                    "http.request",
                    method=method, path=route or path,
                    status=status, duration_ms=round(duration_ms, 2),
                )


# ---------- Sentry + PostHog (no-op when not configured) ----------

_sentry_initialised = False
_posthog_initialised = False


def init_sentry() -> bool:
    """Lazily set up Sentry if SENTRY_DSN is set. Returns True if
    initialised. Idempotent."""
    global _sentry_initialised
    if _sentry_initialised:
        return True
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.environ.get("RENDER_SERVICE_NAME", "dev"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES", "0.05")),
            send_default_pii=False,  # never send IPs / emails by default
        )
        _sentry_initialised = True
        log_event("observability.sentry.init", status="ok")
        return True
    except Exception as e:
        log_event("observability.sentry.init", status="failed", error=str(e))
        return False


def init_posthog() -> bool:
    """Same shape as init_sentry but for PostHog product analytics."""
    global _posthog_initialised
    if _posthog_initialised:
        return True
    key = os.environ.get("POSTHOG_API_KEY", "").strip()
    if not key:
        return False
    try:
        import posthog
        posthog.api_key = key
        posthog.host = os.environ.get(
            "POSTHOG_HOST", "https://app.posthog.com",
        )
        _posthog_initialised = True
        log_event("observability.posthog.init", status="ok")
        return True
    except Exception as e:
        log_event("observability.posthog.init", status="failed", error=str(e))
        return False


def _maybe_capture_exception(exc: BaseException) -> None:
    if not _sentry_initialised:
        return
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass


def track(event: str, user_id: str | None = None, **props: Any) -> None:
    """PostHog event capture — no-op when not configured. Use sparingly:
    PostHog free tier has a 1M event/month limit."""
    if not _posthog_initialised:
        return
    try:
        import posthog
        posthog.capture(
            distinct_id=user_id or "anonymous",
            event=event,
            properties=props,
        )
    except Exception:
        pass


def install(app) -> None:
    """One-shot setup: init Sentry + PostHog if their keys exist, then
    add the middleware. Call once from app startup."""
    init_sentry()
    init_posthog()
    app.add_middleware(ObservabilityMiddleware)
    log_event("observability.installed",
              sentry=_sentry_initialised,
              posthog=_posthog_initialised)
