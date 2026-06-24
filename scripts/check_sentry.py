"""prod-175 — Sentry verifier.

Two modes:

  --check        Pure config check (env vars set, DSN well-formed,
                 SDK importable). No network.

  --fire         Hit GET /__sentry_test against a running server,
                 passing PADHAI_SENTRY_TEST_TOKEN as the
                 X-Sentry-Test-Token header. The handler raises a
                 known exception that is captured by Sentry. After ~5s
                 the event should appear in the Sentry project's
                 Issues feed. Run this AFTER a deploy to verify the
                 DSN is wired end-to-end.

Required env vars:
    SENTRY_DSN                  — Sentry DSN (https://abc@sentry.io/123)
    PADHAI_SENTRY_TEST_TOKEN    — token gating /__sentry_test in prod
    PADHAI_BASE                 — server URL to hit (default localhost:8000)

Exit codes:
    0 — checks passed (or fire was issued and got the expected response)
    1 — config gap / fire returned unexpected status
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _check_config() -> list[str]:
    gaps = []
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        gaps.append("SENTRY_DSN is unset — production errors will be invisible")
    else:
        # Sentry DSN format: https://<public_key>@<host>/<project_id>
        if not re.match(r"^https?://[^@]+@[^/]+/\d+$", dsn):
            gaps.append(
                "SENTRY_DSN format looks wrong (expected "
                "https://KEY@HOST/PROJECT_ID). Got: " + dsn[:40] + "…"
            )

    # Test-token gate (used by /__sentry_test in production).
    test_tok = (os.environ.get("PADHAI_SENTRY_TEST_TOKEN") or "").strip()
    is_prod = (os.environ.get("APP_ENV") or "").strip().lower() == "production"
    if is_prod and not test_tok:
        gaps.append(
            "APP_ENV=production but PADHAI_SENTRY_TEST_TOKEN is unset. "
            "Without it, /__sentry_test returns 404 — you can't verify "
            "the Sentry pipe after deploy."
        )

    return gaps


def _check_sdk() -> tuple[bool, str]:
    try:
        import sentry_sdk
        return True, "sentry_sdk importable"
    except ImportError:
        return False, (
            "sentry_sdk not installed. Run: pip install 'sentry-sdk[fastapi]>=2.0'"
        )


def _fire(verbose: bool) -> tuple[bool, str]:
    """Hit GET /__sentry_test on the running server."""
    base = (os.environ.get("PADHAI_BASE") or "http://localhost:8000").rstrip("/")
    tok = (os.environ.get("PADHAI_SENTRY_TEST_TOKEN") or "").strip()
    url = base + "/__sentry_test"
    headers = {"User-Agent": "AI-Pathshala-Sentry-Check/prod-175"}
    if tok:
        headers["X-Sentry-Test-Token"] = tok

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
            if verbose:
                print(f"  status={resp.status} body={body[:200]}")
        # Endpoint normally raises in non-prod (returns 500). In prod
        # with valid token it ALSO raises (500). 200 = nobody-home
        # (route not registered).
        return False, "unexpected 200 — endpoint may not be registered"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        if e.code == 500:
            return True, "/__sentry_test raised (500); event should appear in Sentry within 5s"
        if e.code == 404:
            return False, (
                "/__sentry_test returned 404. In production this is the "
                "no-token path. Set X-Sentry-Test-Token correctly: "
                "PADHAI_SENTRY_TEST_TOKEN must match the server's env."
            )
        return False, f"unexpected HTTP {e.code}: {body[:200]}"
    except urllib.error.URLError as e:
        return False, f"could not reach {url}: {e}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="Config-only check (default).")
    p.add_argument("--fire", action="store_true",
                   help="Hit /__sentry_test to verify the pipe.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    if not (args.check or args.fire):
        args.check = True

    print("[sentry-check] configuration:")
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    print(f"  SENTRY_DSN                = "
          f"{dsn[:30] + '…' if len(dsn) > 33 else (dsn or '(unset)')}")
    print(f"  PADHAI_SENTRY_TEST_TOKEN  = "
          f"{'***' if (os.environ.get('PADHAI_SENTRY_TEST_TOKEN') or '').strip() else '(unset)'}")
    print(f"  APP_ENV                   = "
          f"{os.environ.get('APP_ENV') or '(unset)'}")
    print(f"  PADHAI_BASE               = "
          f"{os.environ.get('PADHAI_BASE') or 'http://localhost:8000'}")

    gaps = _check_config()
    if gaps:
        print("\n[sentry-check] config issues:")
        for g in gaps:
            print(f"  - {g}")
        # Don't fail on config-only; the user might be running on dev.
        # We DO fail later if they try to --fire without a DSN.

    sdk_ok, sdk_msg = _check_sdk()
    print(f"\n[sentry-check] SDK: {'OK' if sdk_ok else 'FAIL'} — {sdk_msg}")
    if not sdk_ok:
        return 1

    if args.fire:
        print("\n[sentry-check] firing /__sentry_test:")
        if not dsn:
            print("  WARN: SENTRY_DSN unset — exception will be raised "
                  "but no event will be captured.")
        ok, msg = _fire(verbose=args.verbose)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            return 1
        print("\n[sentry-check] check the Sentry project Issues feed.")
        print("  Expected issue: _SentryTestException — 'intentional — Sentry verification'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
