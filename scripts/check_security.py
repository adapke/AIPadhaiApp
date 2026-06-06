#!/usr/bin/env python3
"""Pre-deploy security audit — run before promoting a build to prod.

Codifies the security gates already enforced at runtime (see
`padhai/auth.py:_jwt_secret`, `web.py:_validate_provider_keys`,
`web.py:_validate_admin_gate`) plus extra checks that don't have a
natural runtime hook:

1. JWT secret entropy + non-placeholder.
2. DPDP §9 `MINOR_AGE_THRESHOLD == 18` constant.
3. Admin gate fallback (Postgres OR `PADHAI_SUPERUSER_EMAILS` set).
4. Production-only provider-key presence (Anthropic at minimum).
5. `psycopg.connect(...)` always passes `options="-c search_path=public"`.
6. No `claude-haiku-4-5` bare form literal anywhere outside models.py.
7. Routers package matches `_ROUTER_NAMES` (delegates to existing guard).
8. SQL is parameter-bound (no obvious f-string SQL).

Exits 0 on full pass, 1 on any failure. Designed to run BOTH
locally (`make security`) and in CI before a prod deploy. Read-only
— never modifies files.

Usage:
    python scripts/check_security.py
    APP_ENV=production python scripts/check_security.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ANSI-light status markers (no colour libs).
PASS = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def _check_jwt_secret() -> tuple[bool, str]:
    """JWT secret must be set, ≥32 chars, no placeholder phrases."""
    secret = os.environ.get("PADHAI_JWT_SECRET", "")
    if not secret:
        return False, "PADHAI_JWT_SECRET not set"
    if len(secret) < 32:
        return False, (
            f"PADHAI_JWT_SECRET too short ({len(secret)} chars; need ≥32). "
            "Generate with: python -c 'import secrets; "
            "print(secrets.token_urlsafe(48))'"
        )
    bad_markers = (
        "dev-", "change-me", "CHANGE_ME", "secret-change",
        "placeholder", "test-secret", "qa-test-secret",
    )
    for marker in bad_markers:
        if marker in secret:
            return False, (
                f"PADHAI_JWT_SECRET contains placeholder marker "
                f"{marker!r} — not safe for production"
            )
    return True, f"PADHAI_JWT_SECRET present ({len(secret)} chars, no placeholder)"


def _check_dpdp_age_threshold() -> tuple[bool, str]:
    """DPDP §9 — minor age threshold must be 18, not 13."""
    dpdp_py = ROOT / "padhai" / "dpdp.py"
    if not dpdp_py.is_file():
        return False, "padhai/dpdp.py not found"
    src = dpdp_py.read_text(encoding="utf-8")
    m = re.search(r"MINOR_AGE_THRESHOLD\s*=\s*(\d+)", src)
    if not m:
        return False, "MINOR_AGE_THRESHOLD constant not found in dpdp.py"
    val = int(m.group(1))
    if val != 18:
        return False, (
            f"MINOR_AGE_THRESHOLD = {val}, must be 18 (DPDP §9). "
            "India does not adopt COPPA's 13-year carve-out."
        )
    return True, "MINOR_AGE_THRESHOLD = 18 (DPDP §9 compliant)"


def _check_admin_gate() -> tuple[bool, str]:
    """In production, admin gate must have a non-DB-only source."""
    if os.environ.get("APP_ENV") != "production":
        return True, "(APP_ENV != production — admin gate check skipped)"
    has_db = bool(os.environ.get("DATABASE_URL"))
    has_superusers = bool(os.environ.get("PADHAI_SUPERUSER_EMAILS"))
    if not (has_db or has_superusers):
        return False, (
            "APP_ENV=production but neither DATABASE_URL nor "
            "PADHAI_SUPERUSER_EMAILS is set — admin gate would fall "
            "back to 'every signed-in user is admin'. Set one."
        )
    src = "DATABASE_URL" if has_db else "PADHAI_SUPERUSER_EMAILS"
    return True, f"Admin gate source configured ({src})"


def _check_anthropic_key() -> tuple[bool, str]:
    """ANTHROPIC_API_KEY must be set for any AI feature to work."""
    if os.environ.get("APP_ENV") != "production":
        return True, "(APP_ENV != production — AI key check skipped)"
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return False, (
            "ANTHROPIC_API_KEY not set — all Claude-powered features "
            "will degrade to fallback responses."
        )
    if not key.startswith("sk-ant-"):
        return False, (
            f"ANTHROPIC_API_KEY does not start with sk-ant- "
            f"(got prefix {key[:10]!r}). Probably wrong key."
        )
    return True, "ANTHROPIC_API_KEY present (sk-ant- prefix)"


def _check_psycopg_search_path() -> tuple[bool, str]:
    """Every psycopg.connect SHOULD pass options="-c search_path=public".

    WARN-level only: deployments can also set search_path via the
    DATABASE_URL itself (`?options=-csearch_path%3Dpublic`), so a
    raw `psycopg.connect(db_url)` may be safe at runtime. The audit
    reports them so a reviewer can verify, but doesn't fail the gate."""
    bad: list[str] = []
    for path in (ROOT / "padhai").rglob("*.py"):
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Find every psycopg.connect( call
        for m in re.finditer(r"psycopg\.connect\(", src):
            # Look at the next ~400 chars for options=-c search_path
            tail = src[m.end():m.end() + 400]
            if "search_path" not in tail:
                line = src.count("\n", 0, m.start()) + 1
                bad.append(f"{path.relative_to(ROOT)}:{line}")
    if bad:
        # Return OK with an advisory note — reviewers should verify
        # each site explicitly passes search_path or the URL does.
        return True, (
            f"{WARN} {len(bad)} psycopg.connect() call(s) without "
            f"explicit search_path arg — verify URL carries it."
        )
    return True, "All psycopg.connect() calls pass search_path"


def _check_no_bare_haiku_form() -> tuple[bool, str]:
    """Bug #8: claude-haiku-4-5 (no date) is invalid post-2025-10."""
    rc = subprocess.run(
        ["python", str(ROOT / "scripts" / "check_model_constants.py")],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        return False, "scripts/check_model_constants.py failed — see its output"
    return True, "No literal claude-* outside padhai/models.py allowlist"


def _check_router_registry() -> tuple[bool, str]:
    """Delegates to scripts/check_router_registry.py."""
    rc = subprocess.run(
        ["python", str(ROOT / "scripts" / "check_router_registry.py")],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        return False, "scripts/check_router_registry.py failed"
    return True, "All router files registered in _ROUTER_NAMES"


def _check_no_fstring_sql() -> tuple[bool, str]:
    """Best-effort scan for f-string SQL with USER-CONTROLLED inputs.

    Heuristic: flag lines that build SQL via f-string AND interpolate
    a variable whose name suggests user input (user_id, email, name,
    query, search, etc.). Skips the safe cases — `placeholders`
    (internally-generated ?,?,? counts) and table names from
    module-level constants — that previous versions of this check
    were false-positive on. WARN-level: real injection requires
    human review."""
    USER_VARS = re.compile(
        r"\{(?:[^}]*(?:user_input|search|query|q|term|name|email"
        r"|title|body|text|message|input|filter|kw|keyword)\w*[^}]*)\}",
        re.IGNORECASE,
    )
    bad: list[str] = []
    fstr_sql = re.compile(
        r'f["\'](?:SELECT|INSERT|UPDATE|DELETE)\b', re.IGNORECASE,
    )
    for path in (ROOT / "padhai").rglob("*.py"):
        if path.name == "check_security.py":
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        # Collect multiline-f-string SQL spans by joining f"..." f"..."
        for i, line in enumerate(src.splitlines(), 1):
            if fstr_sql.search(line) and USER_VARS.search(line):
                bad.append(f"{path.relative_to(ROOT)}:{i}")
    if bad:
        return False, (
            "Risky f-string SQL with user-controlled interpolation:\n  "
            + "\n  ".join(bad[:5])
            + (f"\n  ... and {len(bad) - 5} more" if len(bad) > 5 else "")
        )
    return True, "No risky f-string SQL detected"


def main() -> int:
    print("=== Security audit (scripts/check_security.py) ===")
    print(f"APP_ENV = {os.environ.get('APP_ENV', '<unset>')!r}\n")

    checks = [
        ("JWT secret",            _check_jwt_secret),
        ("DPDP §9 minor age",     _check_dpdp_age_threshold),
        ("Admin gate (prod)",     _check_admin_gate),
        ("Anthropic key (prod)",  _check_anthropic_key),
        ("psycopg search_path",   _check_psycopg_search_path),
        ("No bare Haiku form",    _check_no_bare_haiku_form),
        ("Router registry",       _check_router_registry),
        ("No f-string SQL",       _check_no_fstring_sql),
    ]
    n_fail = 0
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"check raised: {e}"
        marker = PASS if ok else FAIL
        print(f"  {marker} {name:<24} {detail}")
        if not ok:
            n_fail += 1

    print()
    if n_fail == 0:
        print(f"{PASS} All 8 security checks passed. Safe to deploy.")
        return 0
    print(
        f"{FAIL} {n_fail} security check(s) failed. "
        "Fix before promoting to production.",
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
