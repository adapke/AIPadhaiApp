"""prod-176 — First-admin bootstrap helper.

Wraps the one-time `POST /admin/signup` call that creates the first
admin account on a fresh deploy. Without this script, you'd need to
remember the exact curl + form-field names + bootstrap-token wiring.

Usage::

    # 1. On the server, set ADMIN_BOOTSTRAP_TOKEN to a strong random value:
    export ADMIN_BOOTSTRAP_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

    # 2. Restart the app so the env is picked up.

    # 3. Run this script with the admin's email + password:
    python scripts/bootstrap_admin.py \\
        --email ops@yourdomain.com \\
        --password 'YourStrongPw1!' \\
        --display-name 'Ops Admin' \\
        --base https://api.yourdomain.com

    # 4. After signup succeeds, UNSET ADMIN_BOOTSTRAP_TOKEN and restart
    #    so additional admins must be invited from inside the admin
    #    console.

Required env vars (the script will print clear errors if missing):
    ADMIN_BOOTSTRAP_TOKEN

Exit codes:
    0 — admin created successfully
    1 — anything else
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True,
                   help="Email for the admin account.")
    p.add_argument("--password", required=True,
                   help="Password (min 8 chars, mixed). Quote it.")
    p.add_argument("--display-name", default="Admin",
                   help="Display name for the admin (default 'Admin').")
    p.add_argument("--base", default=os.environ.get("PADHAI_BASE", "http://localhost:8000"),
                   help="Base URL of the running server.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    tok = (os.environ.get("ADMIN_BOOTSTRAP_TOKEN") or "").strip()
    if not tok:
        print("[bootstrap-admin] FAIL: ADMIN_BOOTSTRAP_TOKEN is unset.")
        print()
        print("Set it on the SERVER (not your laptop), restart the app, then re-run this:")
        print()
        print('  export ADMIN_BOOTSTRAP_TOKEN="$(python -c '
              "'import secrets; print(secrets.token_urlsafe(32))')\"")
        print()
        print("Remember to UNSET it after this script succeeds — leaving it set")
        print("means anyone who steals the token can mint new admins.")
        return 1

    if len(args.password) < 8:
        print("[bootstrap-admin] FAIL: password must be at least 8 characters.")
        return 1

    base = args.base.rstrip("/")
    url = base + "/admin/signup"
    body = urllib.parse.urlencode({
        "email": args.email,
        "password": args.password,
        "display_name": args.display_name,
        "bootstrap_token": tok,
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "AI-Pathshala-Admin-Bootstrap/prod-176",
        },
    )

    print(f"[bootstrap-admin] POST {url}")
    print(f"  email = {args.email}")
    print(f"  display_name = {args.display_name}")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            response_body = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        response_body = (e.read() or b"").decode("utf-8", "replace")
        status = e.code
    except urllib.error.URLError as e:
        print(f"[bootstrap-admin] FAIL: could not reach {url} -- {e}")
        return 1

    if args.verbose:
        print(f"  status={status} body={response_body[:400]}")

    if status in (200, 201):
        # Try to extract token from response for a nice "you're in" hint
        try:
            data = json.loads(response_body)
            email = data.get("email") or args.email
            print(f"\n[bootstrap-admin] SUCCESS — admin {email!r} created.")
            print("Next steps:")
            print("  1. UNSET ADMIN_BOOTSTRAP_TOKEN on the server "
                  "(close the bootstrap window).")
            print("  2. Restart the app so the unset env is picked up.")
            print(f"  3. Sign in at {base}/admin/ with the credentials above.")
            print("  4. Invite additional admins from inside the admin console.")
            return 0
        except json.JSONDecodeError:
            print(f"\n[bootstrap-admin] SUCCESS — but response body wasn't JSON: "
                  f"{response_body[:200]}")
            return 0

    # Non-2xx
    print(f"\n[bootstrap-admin] FAIL: HTTP {status}")
    print(f"  response: {response_body[:400]}")
    if status == 401:
        print("\nHint: ADMIN_BOOTSTRAP_TOKEN on this client doesn't match the server's.")
        print("Check that you copied the same token to both sides, and that the")
        print("server was restarted AFTER you set the env var.")
    elif status == 403:
        print("\nHint: bootstrap is closed (likely an admin already exists).")
        print("If you need to bootstrap again, restart the server with")
        print("ADMIN_BOOTSTRAP_TOKEN re-set; the bootstrap is one-shot per restart.")
    elif status == 409:
        print("\nHint: an account with this email already exists.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
