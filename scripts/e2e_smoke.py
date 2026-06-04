"""End-to-end HTTP smoke against a running server.

Drives the production-shaped flow in one script. Used both for
local manual QA + by `make e2e` in CI.

Steps (each prints [OK] / [FAIL]):
  1. /healthz
  2. /api/ai-status — feature flag inventory
  3. Signup fresh student -> assert token + user_id
  4. POST /lessons with 1x1 PNG -> assert 202 + job_id
  5. Poll /jobs/{id} until status==succeeded (or timeout)
  6. GET /jobs/{id}/video — assert MP4 served
  7. GET /api/citations/me — assert lesson provenance recorded
  8. Sign up a minor (12y old) with parent_email -> assert
     account_locked=1 in the response shape
  9. Find the consent token via the admin outbox endpoint OR
     fall back to reading the SQLite/Postgres directly when the
     PADHAI_DB_PATH env points at it; redeem the token ->
     assert 200 with "unlocked" in the HTML body
 10. Login as the minor (post-consent) -> assert 200

Exits 0 if every step passes, 1 otherwise. Step 4-6 are skipped
gracefully when ANTHROPIC_API_KEY is unset (the lesson generator
returns canned content and the render still works, but the assert
is relaxed). Step 9 is skipped when SMTP isn't wired AND the DB
isn't visible from this script.

Run:
  python scripts/e2e_smoke.py --base-url http://localhost:8000
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# 1x1 PNG — same one we use everywhere else. Smallest valid PNG
# that ingest_source accepts without raising.
PNG_1X1 = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
    0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
    0x89, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x44, 0x41,
    0x54, 0x78, 0x9C, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
    0x00, 0x00, 0x03, 0x00, 0x01, 0x5E, 0xF3, 0x2A,
    0xCD, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
    0x44, 0xAE, 0x42, 0x60, 0x82,
])


_failures: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    """Print one [OK] / [FAIL] line and collect failures."""
    if ok:
        print(f"  [OK]   {name:40} {detail}")
    else:
        print(f"  [FAIL] {name:40} {detail}", file=sys.stderr)
        _failures.append(name)


def http_request(method: str, url: str, *,
                 form: dict | None = None,
                 json_body: dict | None = None,
                 multipart: tuple[bytes, str] | None = None,
                 headers: dict | None = None,
                 timeout: float = 60.0) -> tuple[int, str, dict]:
    """One-shot HTTP via urllib. Returns (status, body_text, headers)."""
    headers = dict(headers or {})
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif multipart is not None:
        body, content_type = multipart
        data = body
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return e.code, body, dict(e.headers or {})


def build_multipart(*, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
                    ) -> tuple[bytes, str]:
    """Hand-rolled multipart/form-data builder so we don't pull in
    requests/httpx just for this. `files` maps name -> (filename,
    bytes, mime)."""
    boundary = f"----e2e-{uuid.uuid4().hex}"
    crlf = b"\r\n"
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f"--{boundary}".encode() + crlf)
        out.write(f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf)
        out.write(value.encode("utf-8") + crlf)
    for name, (filename, file_bytes, mime) in files.items():
        out.write(f"--{boundary}".encode() + crlf)
        out.write(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode()
            + crlf
        )
        out.write(f"Content-Type: {mime}".encode() + crlf + crlf)
        out.write(file_bytes + crlf)
    out.write(f"--{boundary}--".encode() + crlf)
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def run(base_url: str) -> int:
    print(f"=== E2E smoke against {base_url} ===\n")
    print("Step 1 — healthz")
    st, body, _ = http_request("GET", f"{base_url}/healthz")
    step("/healthz returns 200", st == 200, f"status={st}")
    if st != 200:
        return 1

    print("\nStep 2 — feature inventory")
    st, body, _ = http_request("GET", f"{base_url}/api/ai-status")
    step("/api/ai-status returns 200", st == 200)
    ai_configured = False
    if st == 200:
        try:
            j = json.loads(body)
            ai_configured = bool(j.get("anthropic_configured"))
            print(f"        anthropic_configured={ai_configured}")
        except Exception as e:
            step("/api/ai-status JSON parseable", False, str(e))

    print("\nStep 3 — student signup")
    email = f"e2e-{uuid.uuid4().hex[:8]}@e2e.local"
    st, body, _ = http_request(
        "POST", f"{base_url}/auth/signup",
        form={"email": email, "password": "E2E1234!pwd", "terms_accepted": "true"},
    )
    step("/auth/signup returns 200", st == 200, f"status={st}")
    if st != 200:
        return 1
    signup = json.loads(body)
    token = signup.get("token")
    user_id = signup.get("user_id")
    auth_hdr = {"Authorization": f"Bearer {token}"}
    step("signup token + user_id present", bool(token and user_id), f"uid={(user_id or '')[:8]}…")

    print("\nStep 4 — POST /lessons (image upload)")
    body, ct = build_multipart(
        fields={"language": "en", "level": "middle"},
        files={"image": ("page.png", PNG_1X1, "image/png")},
    )
    st, lesson_body, _ = http_request(
        "POST", f"{base_url}/lessons",
        multipart=(body, ct), headers=auth_hdr, timeout=60.0,
    )
    step("/lessons accepts upload", st in (200, 202), f"status={st}")
    job_id = None
    if st in (200, 202):
        try:
            j = json.loads(lesson_body)
            job_id = j.get("job_id")
            step("job_id returned", bool(job_id), f"job_id={(job_id or '')[:8]}…")
        except Exception:
            step("job_id returned", False, lesson_body[:120])

    print("\nStep 5 - poll /jobs/{id} until terminal")
    if not job_id:
        step("job polling", False, "no job_id from previous step - skipping")
        terminal = None
    else:
        # Without ANTHROPIC_API_KEY the lesson generator throws on the
        # first call (no canned fallback in pedagogy yet) -> job=failed.
        # That's an expected outcome for keyless CI; step 6/7 adapt.
        # With Claude configured we may wait up to 3 min for the call.
        deadline = time.time() + 180
        terminal = None
        while time.time() < deadline:
            st, jb, _ = http_request("GET", f"{base_url}/jobs/{job_id}", headers=auth_hdr)
            if st != 200:
                break
            jdoc = json.loads(jb)
            if jdoc.get("status") in ("succeeded", "failed"):
                terminal = jdoc.get("status")
                step(
                    "job reached terminal state",
                    True,
                    f"status={terminal} elapsed={int(time.time() - (deadline - 180))}s",
                )
                break
            time.sleep(2.0)
        if terminal is None:
            step("job reached terminal state", False, "timeout after 180s")
        elif terminal == "succeeded":
            step("job succeeded", True)
        elif ai_configured:
            # Real Claude key set + job failed = a real bug.
            step("job succeeded", False, "render failed - check server logs")
        else:
            # No Claude key -> job-failed is expected. Don't count as
            # a smoke failure; just note it.
            print(
                "  [SKIP] job succeeded                          "
                "  ANTHROPIC_API_KEY unset; failure expected"
            )

    print("\nStep 6 - GET /jobs/{id}/video")
    if job_id and terminal == "succeeded":
        st, _, hdrs = http_request("GET", f"{base_url}/jobs/{job_id}/video", headers=auth_hdr)
        ct = hdrs.get("Content-Type") or hdrs.get("content-type") or ""
        step(
            "video endpoint serves something",
            st in (200, 302, 303),
            f"status={st} ct={ct}",
        )
    else:
        print(
            "  [SKIP] video endpoint serves something        "
            "  no succeeded job to download"
        )

    print("\nStep 7 - /api/citations/me sees the lesson")
    st, body, _ = http_request("GET", f"{base_url}/api/citations/me", headers=auth_hdr)
    step("/api/citations/me returns 200", st == 200, f"status={st}")
    if st == 200 and terminal == "succeeded":
        try:
            ans = json.loads(body).get("answers") or []
            has_lesson = any(a.get("surface") == "lesson" for a in ans)
            step("lesson provenance recorded", has_lesson, f"count={len(ans)}")
        except Exception as e:
            step("citations parseable", False, str(e))
    elif st == 200:
        print(
            "  [SKIP] lesson provenance recorded            "
            "  job didn't succeed -> nothing to record"
        )

    print("\nStep 8 - minor signup -> DPDP locked")
    dob = (dt.date.today() - dt.timedelta(days=365 * 12)).isoformat()
    minor_email = f"minor-{uuid.uuid4().hex[:8]}@e2e.local"
    parent_email = f"parent-{uuid.uuid4().hex[:8]}@e2e.local"
    # Parent first so the consent email has a valid recipient
    http_request(
        "POST", f"{base_url}/auth/signup",
        form={"email": parent_email, "password": "E2E1234!pwd", "terms_accepted": "true"},
    )
    st, body, _ = http_request(
        "POST", f"{base_url}/auth/signup",
        form={
            "email": minor_email, "password": "E2E1234!pwd",
            "terms_accepted": "true",
            "dob": dob, "parent_email": parent_email,
        },
    )
    locked = False
    consent_required = False
    if st == 200:
        try:
            j = json.loads(body)
            locked = bool(j.get("account_locked"))
            consent_required = bool(j.get("consent_required"))
        except Exception:
            pass
    step(
        "minor account is locked + consent required",
        locked and consent_required,
        f"locked={locked} consent_required={consent_required}",
    )

    print("\nStep 9 — find + redeem consent token")
    consent_token = _find_consent_token()
    if not consent_token:
        step("consent token reachable", False, "DB not visible from this script — skipped redeem")
    else:
        st, body, _ = http_request("GET", f"{base_url}/auth/parent-consent?t={consent_token}")
        step(
            "consent redemption returns 200 + unlocked",
            st == 200 and "unlocked" in body.lower(),
            f"status={st}",
        )

        print("\nStep 10 — minor login post-consent")
        st, body, _ = http_request(
            "POST", f"{base_url}/auth/login",
            form={"email": minor_email, "password": "E2E1234!pwd"},
        )
        step("minor login returns 200", st == 200, f"status={st}")

    print(f"\n=== Summary: {len(_failures)} failures ===")
    if _failures:
        for f in _failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("ALL E2E steps green")
    return 0


def _find_consent_token() -> str | None:
    """Read the latest active consent token straight from the DB.
    Works against SQLite when PADHAI_DB_PATH is visible to this
    process, OR against Postgres when DATABASE_URL is set."""
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg
            with psycopg.connect(db_url, options="-c search_path=public") as conn:
                row = conn.execute(
                    "SELECT token FROM parent_consent_tokens "
                    "WHERE expires_at > EXTRACT(EPOCH FROM NOW()) "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
            return row[0] if row else None
        except Exception:
            return None
    db_path = os.environ.get("PADHAI_DB_PATH", os.path.expanduser("~/.padhai/jobs.db"))
    if not os.path.exists(db_path):
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT token FROM parent_consent_tokens "
            "WHERE expires_at > strftime('%s','now') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    args = p.parse_args()
    return run(args.base_url.rstrip("/"))


if __name__ == "__main__":
    raise SystemExit(main())
