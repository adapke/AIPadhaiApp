"""prod-16 — Systematic logged-in audit.

Sign in, hit every documented public-route, catalog failures.
Splits findings by severity:
  HARD   — HTTP 5xx, JS parse error, exception in handler
  SOFT   — HTTP 4xx (auth/permission/validation)
  EMPTY  — 200 OK but body is empty / no useful data for a fresh user

Output: a concise per-area summary, then the full detail.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import urllib.request

BASE = "http://127.0.0.1:8000"
EMAIL = "adapke@gmail.com"
PASSWORD = "Pass@12345"


def http(method: str, path: str, token: str | None = None,
         data: str | None = None, content_type: str = "application/x-www-form-urlencoded") -> tuple[int, str]:
    url = BASE + path
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body: bytes | None = None
    if data is not None:
        body = data.encode("utf-8")
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"<connection error: {type(e).__name__}: {e}>"


def login() -> str:
    code, body = http("POST", "/auth/login",
                      data=urllib.parse.urlencode({"email": EMAIL, "password": PASSWORD}))
    if code != 200:
        raise SystemExit(f"login failed: {code} {body[:200]}")
    return json.loads(body)["token"]


def js_parse_check(html: str) -> list[str]:
    """Extract <script> blocks and look for the apostrophe-in-single-quoted-string
    pattern that's tripped us up twice already. Returns a list of issues."""
    issues = []
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for i, s in enumerate(scripts):
        # Look for `\'` immediately followed by a letter inside what looks
        # like a JS single-quoted string. False positives possible but the
        # signal is good enough for a smoke audit.
        for m in re.finditer(r"'[^'\n]{0,200}\\'[a-z]", s):
            snippet = m.group(0)
            if len(snippet) > 80:
                snippet = "..." + snippet[-80:]
            issues.append(f"script[{i}] suspicious: {snippet}")
    return issues


# All routes worth auditing. (path, expected_status, type)
# type: 'html', 'json', 'json-authed'
PROBES = [
    # Public
    ("/healthz",                                 200, "json"),
    ("/api/ai-status",                           200, "json"),
    ("/landing",                                 200, "html"),
    ("/home",                                    200, "html"),
    ("/home/hi",                                 200, "html"),
    ("/home/ta",                                 200, "html"),
    ("/ui",                                      200, "html"),
    ("/ui-legacy",                               200, "html"),
    ("/api/concept-videos",                      200, "json"),
    ("/api/concept-videos/stats",                200, "json"),
    # Authed HTML pages
    ("/onboarding",                              200, "html"),
    ("/chat",                                    200, "html"),
    ("/profile",                                 200, "html"),
    ("/teacher",                                 200, "html"),
    ("/parent",                                  200, "html"),
    ("/dashboard",                               200, "html"),
    ("/lessons/new",                             200, "html"),
    ("/quiz",                                    200, "html"),
    # Authed JSON APIs
    ("/auth/me",                                 200, "json-authed"),
    ("/api/me/dashboard",                        200, "json-authed"),
    ("/api/home/me/dashboard",                   200, "json-authed"),
    ("/api/navigation/manifest",                 200, "json-authed"),
    ("/api/flashcards/due",                      200, "json-authed"),
    ("/api/flashcards/due?limit=10",             200, "json-authed"),
    ("/api/onboarding/status",                   200, "json-authed"),
    ("/api/me/stats",                            200, "json-authed"),
    ("/api/notifications/me",                    200, "json-authed"),
    ("/api/exam-mode/active",                    200, "json-authed"),
    ("/api/fees/config",                         200, "json"),
    ("/api/avatar-providers",                    200, "json"),
    ("/api/branding/resolve",                    200, "json"),
    ("/api/essay/rubrics",                       200, "json"),
    ("/api/uploads",                             200, "json-authed"),  # list
    ("/api/parents/me",                          200, "json-authed"),
    ("/api/push/stats",                          200, "json"),
    ("/api/avatar-stats",                        200, "json-authed"),
    ("/jobs?limit=20",                           200, "json-authed"),
    ("/curriculum/index",                        200, "json"),
    ("/api/exam-packs",                          200, "json-authed"),
    ("/api/me/data/export",                      200, "json-authed"),
    # Misc HTML
    ("/terms",                                   200, "html"),
    ("/privacy",                                 200, "html"),
]


def main() -> int:
    token = login()
    print(f"[audit] logged in as {EMAIL}")
    print(f"[audit] running {len(PROBES)} probes…\n")

    hard: list[str] = []
    soft: list[str] = []
    empty: list[str] = []
    js_issues: list[str] = []
    ok: int = 0

    for path, expected, kind in PROBES:
        tok = token if "authed" in kind else None
        code, body = http("GET", path, token=tok)
        label = f"{path:<40} -> HTTP {code}"
        if code == 0:
            hard.append(f"{label}  {body[:100]}")
        elif code >= 500:
            hard.append(f"{label}  body={body[:120]}")
        elif code != expected:
            soft.append(f"{label}  body={body[:120]}")
        else:
            ok += 1
            if kind == "html":
                # JS sanity check
                problems = js_parse_check(body)
                if problems:
                    for p in problems:
                        js_issues.append(f"{path}  {p}")
            else:
                # JSON shape — flag emptyish payloads
                try:
                    j = json.loads(body)
                    if (
                        j == {} or j == [] or j == {"rows": []}
                        or (isinstance(j, dict) and j.get("count") == 0)
                        or (isinstance(j, dict) and j.get("total") == 0
                            and not any(j.get(k) for k in ("by_board","by_quality_tier","by_subject")))
                    ):
                        empty.append(f"{path}  payload looks empty: {body[:100]}")
                except json.JSONDecodeError:
                    pass

    print("=" * 60)
    print(f"PASSED: {ok}/{len(PROBES)}")
    print(f"HARD failures (5xx / connection): {len(hard)}")
    print(f"SOFT failures (4xx / wrong status): {len(soft)}")
    print(f"EMPTY responses (200 OK but no data): {len(empty)}")
    print(f"JS syntax suspicions on HTML pages: {len(js_issues)}")
    print()
    if hard:
        print("--- HARD ---")
        for h in hard: print(" ", h)
        print()
    if soft:
        print("--- SOFT ---")
        for s in soft: print(" ", s)
        print()
    if js_issues:
        print("--- JS issues ---")
        for j in js_issues: print(" ", j)
        print()
    if empty:
        print("--- EMPTY (informational; new user, no data yet) ---")
        for e in empty: print(" ", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
