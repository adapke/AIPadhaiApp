"""Seed a deterministic demo dataset against a running server.

Designed for two callers:
  1. `make seed` in the dev compose stack — runs at first boot to
     populate the SPA with something to look at.
  2. Manual QA — re-run before every release to reset the demo
     accounts.

Idempotent: re-running with the same emails returns "already
seeded" and exits 0 without raising. Use `--reset` to delete the
demo accounts first (requires DATABASE_URL pointing at the same DB
the server is using — only works for Postgres).

Standalone — no dependency on padhai.* internals. Drives HTTP
endpoints only, so it works against a remote staging server too:

    python scripts/seed_demo.py --base-url https://staging.aipathshala.in
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# Stable demo emails so re-running picks up the same accounts.
DEMO = {
    "admin":       {"email": "admin@demo.local",         "password": "Demo1234!", "display_name": "Demo Admin"},
    "parent":      {"email": "parent@demo.local",        "password": "Demo1234!", "display_name": "Asha Sharma (Parent)"},
    "teacher":     {"email": "teacher@demo.local",       "password": "Demo1234!", "display_name": "Mr. Rao (Teacher)"},
    "student_adult": {"email": "riya@demo.local",        "password": "Demo1234!", "display_name": "Riya (Class 10)"},
    "student_minor": {"email": "arjun@demo.local",       "password": "Demo1234!", "display_name": "Arjun (Class 6, minor)"},
}

ORG_SLUG = "demo-school"
ORG_NAME = "Demo School"
PACK_CODE = "cbse_class_10_2026"


def _post(url: str, *, form: dict | None = None, json_body: dict | None = None,
          headers: dict | None = None, timeout: float = 30.0) -> tuple[int, str]:
    """Minimal urllib POST. Returns (status, body_text)."""
    headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        data = b""
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        with contextlib.suppress(Exception):
            body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def _get(url: str, *, headers: dict | None = None, timeout: float = 30.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="GET", headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)


def _log(stage: str, msg: str) -> None:
    print(f"[seed] {stage:14} {msg}", flush=True)


def ensure_signup(base_url: str, role: str) -> dict | None:
    """Sign up the demo user; treat 409 (email already exists) as
    success — re-run safe. Returns {email, token, user_id} or None
    when the auth service is unavailable (503)."""
    cfg = DEMO[role]
    extra: dict = {}
    if role == "student_minor":
        # 12 years old → triggers DPDP minor flow
        dob = (dt.date.today() - dt.timedelta(days=365 * 12)).isoformat()
        extra["dob"] = dob
        extra["parent_email"] = DEMO["parent"]["email"]
    status, body = _post(
        f"{base_url}/auth/signup",
        form={
            "email": cfg["email"],
            "password": cfg["password"],
            "terms_accepted": "true",
            **extra,
        },
    )
    if status == 503:
        _log(role, "auth not configured (503) — skipping")
        return None
    if status in (200, 201):
        j = json.loads(body)
        _log(role, f"signed up {cfg['email']} (user_id={j.get('user_id', '?')[:8]}…)")
        return {"email": cfg["email"], "token": j.get("token"), "user_id": j.get("user_id")}
    if status == 409 or "already" in body.lower():
        # Existing — try a login to recover the token
        st, lb = _post(
            f"{base_url}/auth/login",
            form={"email": cfg["email"], "password": cfg["password"]},
        )
        if st in (200, 201):
            j = json.loads(lb)
            _log(role, f"already exists — logged in as {cfg['email']}")
            return {"email": cfg["email"], "token": j.get("token"), "user_id": j.get("user_id")}
        _log(role, f"already exists but login failed: {st}")
        return {"email": cfg["email"], "token": None, "user_id": None}
    _log(role, f"FAIL status={status} body={body[:200]}")
    return None


def ensure_admin(base_url: str) -> dict | None:
    """Use the bootstrap-token signup if no admin exists; else log in.
    Returns {email, token} or None when ADMIN_BOOTSTRAP_TOKEN is unset
    AND no admin exists (we can't create the first one)."""
    cfg = DEMO["admin"]
    bootstrap = os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
    # Try login first — covers the re-run case
    st, lb = _post(
        f"{base_url}/admin/login",
        form={"email": cfg["email"], "password": cfg["password"]},
    )
    if st in (200, 303):
        _log("admin", f"already exists — logged in as {cfg['email']}")
        return {"email": cfg["email"], "token": None}
    if not bootstrap:
        _log("admin", "ADMIN_BOOTSTRAP_TOKEN unset + no existing admin — skipping")
        return None
    st, sb = _post(
        f"{base_url}/admin/signup",
        form={
            "email": cfg["email"],
            "password": cfg["password"],
            "display_name": cfg["display_name"],
            "bootstrap_token": bootstrap,
        },
    )
    if st in (200, 201, 303):
        _log("admin", f"bootstrapped {cfg['email']}")
        return {"email": cfg["email"], "token": None}
    _log("admin", f"FAIL status={st} body={sb[:200]}")
    return None


def ensure_org(base_url: str, teacher_token: str) -> dict | None:
    """Create the demo org via the teacher account. Idempotent on
    409."""
    if not teacher_token:
        _log("org", "no teacher token — skipping")
        return None
    st, body = _post(
        f"{base_url}/api/orgs",
        form={"slug": ORG_SLUG, "name": ORG_NAME, "kind": "school"},
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    if st in (200, 201):
        j = json.loads(body)
        _log("org", f"created {ORG_NAME} (id={j.get('id', '?')[:8]}…)")
        return j
    if st == 409 or "exist" in body.lower():
        _log("org", f"already exists ({ORG_SLUG})")
        return {"slug": ORG_SLUG}
    _log("org", f"FAIL status={st} body={body[:200]}")
    return None


def ensure_enrollment(base_url: str, student_token: str) -> dict | None:
    """Enroll the adult student in the CBSE Class 10 pack."""
    if not student_token:
        _log("enroll", "no student token — skipping")
        return None
    st, body = _post(
        f"{base_url}/api/exam-packs/{PACK_CODE}/enroll",
        json_body={"daily_minutes": 60},
        headers={"Authorization": f"Bearer {student_token}"},
    )
    if st in (200, 201):
        j = json.loads(body)
        _log("enroll", f"enrolled riya@ in {PACK_CODE}")
        return j
    if "already enrolled" in body.lower():
        _log("enroll", f"already enrolled in {PACK_CODE}")
        return {"pack_code": PACK_CODE, "status": "active"}
    _log("enroll", f"FAIL status={st} body={body[:200]}")
    return None


def ensure_parent_link(base_url: str, parent_token: str) -> dict | None:
    """Parent invites the minor child. Pending until the child verifies."""
    if not parent_token:
        _log("link", "no parent token — skipping")
        return None
    st, body = _post(
        f"{base_url}/api/parents/link",
        form={
            "other_email": DEMO["student_minor"]["email"],
            "role": "parent",
            "relation": "mother",
        },
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    if st in (200, 201):
        j = json.loads(body)
        _log("link", f"parent->child link created (id={(j.get('link_id') or '?')[:8]}…)")
        return j
    if "already" in body.lower() or st == 409:
        _log("link", "parent-child link already exists")
        return {}
    _log("link", f"FAIL status={st} body={body[:200]}")
    return None


def consent_token_for_minor(base_url: str) -> str | None:
    """Show the parent the URL they'd click to consent for the minor.
    Reads it from the admin outbox (works only when DATABASE_URL is
    pointing at the same SQLite/Postgres the server uses, AND we have
    direct DB access — i.e. compose stack). When unavailable, prints
    instructions instead."""
    db_path = os.environ.get("PADHAI_DB_PATH", os.path.expanduser("~/.padhai/jobs.db"))
    if not os.path.exists(db_path):
        _log("consent", f"DB at {db_path} not visible from this script; print outbox via admin UI")
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
        if not row:
            _log("consent", "no active consent tokens — minor signup may have failed silently")
            return None
        token = row[0]
        _log("consent", f"redeem URL: {base_url}/auth/parent-consent?t={token}")
        return token
    except Exception as e:  # noqa: BLE001
        _log("consent", f"DB read failed: {e}")
        return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000",
                   help="server base URL (default: http://localhost:8000)")
    p.add_argument("--skip-admin", action="store_true",
                   help="skip the admin bootstrap step (use when ADMIN_BOOTSTRAP_TOKEN is unset)")
    args = p.parse_args()
    base = args.base_url.rstrip("/")

    # Connectivity check
    st, body = _get(f"{base}/healthz")
    if st != 200:
        print(f"[seed] FATAL: {base}/healthz returned {st}", file=sys.stderr)
        return 1
    _log("connect", f"{base} healthy")

    parent  = ensure_signup(base, "parent")
    teacher = ensure_signup(base, "teacher")
    riya    = ensure_signup(base, "student_adult")
    arjun   = ensure_signup(base, "student_minor")
    if not args.skip_admin:
        ensure_admin(base)

    if teacher and teacher.get("token"):
        ensure_org(base, teacher["token"])
    if riya and riya.get("token"):
        ensure_enrollment(base, riya["token"])
    if parent and parent.get("token"):
        ensure_parent_link(base, parent["token"])

    consent_token_for_minor(base)

    print(
        "\n[seed] DONE. Sign in URLs:\n"
        f"  Student (adult): {base}/login -> riya@demo.local / Demo1234!\n"
        f"  Student (minor, locked until consent): arjun@demo.local / Demo1234!\n"
        f"  Parent:          {base}/login -> parent@demo.local / Demo1234!\n"
        f"  Teacher:         {base}/login -> teacher@demo.local / Demo1234!\n"
        f"  Admin:           {base}/admin/login -> admin@demo.local / Demo1234!\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
