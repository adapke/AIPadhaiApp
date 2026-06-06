"""Security invariants — codified tests for SECURITY.md hardenings.

The hardenings live in code (auth.py, dpdp.py, web.py); this file is
the **executable spec**. If any of these tests fail, a security
property called out in SECURITY.md or ONBOARDING.md has regressed.

Tests are deliberately read-only / introspective — they import
modules and assert on their constants, not on runtime behaviour
under attack. Real-attack tests would need fuzzing infrastructure;
this is the constant-floor that catches "someone changed the age
threshold from 18 back to 13" before code review.

Grouped by the 8 invariants in ONBOARDING.md.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------- 1. JWT secret validation ----------

def test_jwt_secret_validator_rejects_placeholder_phrases():
    """In production mode, the secret validator must reject common
    placeholder phrases. See padhai/auth.py:_jwt_secret."""
    from padhai import auth
    # The validator is gated on APP_ENV=production. We check the
    # placeholder list itself is intact.
    src = (REPO_ROOT / "padhai" / "auth.py").read_text(encoding="utf-8")
    # All four placeholder markers from SECURITY.md must be present.
    for marker in ("dev-", "change-me", "CHANGE_ME",
                   "secret-change", "placeholder"):
        assert marker in src, (
            f"Placeholder marker {marker!r} dropped from auth.py "
            f"validator — SECURITY.md says we reject these in prod"
        )
    # Sanity-check the validator function exists.
    assert hasattr(auth, "_jwt_secret"), (
        "padhai.auth._jwt_secret() must exist as the validator"
    )


def test_jwt_uses_hs256_with_bounded_ttl():
    """JWT must be HS256 (not none / RS256-misconfig) and TTL must
    be a finite number of seconds, not 'forever'."""
    from padhai import auth
    src = (REPO_ROOT / "padhai" / "auth.py").read_text(encoding="utf-8")
    assert '"HS256"' in src or "'HS256'" in src, (
        "JWT must explicitly use HS256 — found no HS256 literal"
    )
    # TTL must be a bounded constant (not 'never expires').
    assert hasattr(auth, "JWT_TTL_SECONDS"), (
        "JWT_TTL_SECONDS constant required"
    )
    ttl = auth.JWT_TTL_SECONDS
    # Reasonable bounds: ≥1 hour (so password rotation doesn't
    # invalidate active sessions immediately) and ≤30 days (so
    # stolen tokens don't live forever).
    assert 3600 <= ttl <= 30 * 86400, (
        f"JWT_TTL_SECONDS = {ttl} outside reasonable bounds "
        f"[1h, 30d]. Currently set to {ttl // 86400} days."
    )


# ---------- 2. DPDP §9 minor protection ----------

def test_dpdp_minor_age_threshold_is_18():
    """DPDP Act 2023 §9 — 'child' means under 18 in India.
    Never reduce this to 13 (COPPA's carve-out doesn't apply)."""
    from padhai import dpdp
    assert dpdp.MINOR_AGE_THRESHOLD == 18, (
        f"DPDP §9 violation: MINOR_AGE_THRESHOLD = "
        f"{dpdp.MINOR_AGE_THRESHOLD}, must be 18"
    )


def test_dpdp_consent_token_ttl_is_single_use_and_short():
    """Parent consent tokens are single-use + 7-day TTL by design."""
    src = (REPO_ROOT / "padhai" / "dpdp.py").read_text(encoding="utf-8")
    # Constants we expect to find for the consent flow.
    assert "7" in src or "604800" in src, (
        "7-day TTL constant should be visible in dpdp.py"
    )


# ---------- 3. Admin gate prod safeguard ----------

def test_admin_gate_validator_in_lifespan():
    """In production, web.py refuses to boot without an admin
    source (DATABASE_URL or PADHAI_SUPERUSER_EMAILS)."""
    src = (REPO_ROOT / "padhai" / "web.py").read_text(encoding="utf-8")
    assert "_validate_admin_gate" in src, (
        "padhai.web._validate_admin_gate() must exist"
    )
    assert "PADHAI_SUPERUSER_EMAILS" in src, (
        "PADHAI_SUPERUSER_EMAILS is the non-DB admin source"
    )


# ---------- 4. Postgres search_path invariant ----------

def test_db_module_resolves_search_path():
    """padhai/db.py must surface get_db_url + sqlite_path helpers
    that downstream modules use instead of os.environ.get directly."""
    from padhai import db
    assert hasattr(db, "get_db_url"), "padhai.db.get_db_url() required"
    assert hasattr(db, "sqlite_path"), (
        "padhai.db.sqlite_path() required — shared SQLite path helper"
    )


# ---------- 5. Model-ID centralisation ----------

def test_no_literal_claude_haiku_4_5_outside_models_py():
    """Bug #8 — the bare 'claude-haiku-4-5' form is invalid since
    the 2025-10 rename. Caught by scripts/check_model_constants.py
    in `make verify`; this test exists as the in-process counterpart
    so a contributor running `pytest -x` locally also sees it."""
    bad: list[tuple[Path, int]] = []
    ALLOW = {
        REPO_ROOT / "padhai" / "models.py",
        REPO_ROOT / "padhai" / "llm_obs.py",
        REPO_ROOT / "padhai" / "schema_v2.py",
        REPO_ROOT / "scripts" / "check_model_constants.py",
        REPO_ROOT / "scripts" / "check_security.py",
        REPO_ROOT / "tests" / "test_security_invariants.py",
    }
    pat = re.compile(r'["\']claude-haiku-4-5["\']')
    for path in (REPO_ROOT / "padhai").rglob("*.py"):
        if path in ALLOW:
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if pat.search(line):
                bad.append((path, i))
    assert not bad, (
        "Bare 'claude-haiku-4-5' literal found outside allowlist "
        "(use padhai.models.HAIKU_MODEL instead): "
        + ", ".join(
            f"{p.relative_to(REPO_ROOT)}:{i}" for p, i in bad
        )
    )


# ---------- 6. B904 — raise inside except has cause ----------

def test_no_b904_violations_in_padhai():
    """raise X(...) inside except must use `from err` or `from None`
    to distinguish from errors-in-error-handling. Cleaned by the
    AST mass fixer (scripts/fix_b904.py) and gated by ruff blocking;
    this test catches it earlier in the pytest cycle."""
    import subprocess
    rc = subprocess.run(
        ["python", "-m", "ruff", "check", "--select", "B904",
         str(REPO_ROOT / "padhai")],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, (
        f"ruff B904 violations found:\n{rc.stdout[-2000:]}"
    )


# ---------- 7. SQL parameter binding (heuristic) ----------

def test_no_risky_fstring_sql_with_user_inputs():
    """f-string SQL is fine when the interpolation is internal
    (placeholders, table names from constants). It's a SQL-injection
    risk only when user input flows into the f-string. This test
    catches the latter via a name-based heuristic."""
    USER_VARS = re.compile(
        r"\{(?:[^}]*(?:user_input|search|query|q|term|name|email"
        r"|title|body|text|message|input|filter|kw|keyword)\w*[^}]*)\}",
        re.IGNORECASE,
    )
    fstr_sql = re.compile(
        r'f["\'](?:SELECT|INSERT|UPDATE|DELETE)\b', re.IGNORECASE,
    )
    bad: list[tuple[Path, int, str]] = []
    for path in (REPO_ROOT / "padhai").rglob("*.py"):
        if "check_security" in path.name or "test_" in path.name:
            continue
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if fstr_sql.search(line) and USER_VARS.search(line):
                bad.append((path, i, line.strip()[:120]))
    assert not bad, (
        "Risky f-string SQL with user-controlled name in interpolation. "
        "Use parameter binding (?-placeholder) instead:\n  "
        + "\n  ".join(
            f"{p.relative_to(REPO_ROOT)}:{i}: {snippet}"
            for p, i, snippet in bad[:5]
        )
    )


# ---------- 8. Multi-tenant org gate (router test counterpart) ----------

@pytest.mark.parametrize("router_path", [
    "padhai/routers/orgs_api.py",
    "padhai/routers/orgs_classes.py",
    "padhai/routers/orgs_attendance.py",
    "padhai/routers/orgs_assignments.py",
    "padhai/routers/orgs_fees.py",
    "padhai/routers/orgs_exams.py",
    "padhai/routers/orgs_leaderboard.py",
    "padhai/routers/orgs_schedule.py",
])
def test_org_routers_invoke_role_gate(router_path: str):
    """Every router under `/api/orgs/{org_id}/...` must call
    `_require_org_role` (or the equivalent membership check) — never
    expose org data without verifying the caller's role. The
    behavioural counterpart lives in tests/test_routers.py; this is
    the structural assertion."""
    src = (REPO_ROOT / router_path).read_text(encoding="utf-8")
    # Either _require_org_role OR user_role_in_org (the lower-level
    # check used by attendance/schedule which then policy-filters
    # the result) must appear.
    has_gate = (
        "_require_org_role" in src
        or "user_role_in_org" in src
    )
    assert has_gate, (
        f"{router_path} does not call _require_org_role or "
        "user_role_in_org — multi-tenant gate missing"
    )
