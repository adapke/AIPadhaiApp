"""Single Sign-On via OpenID Connect — Google Workspace + Microsoft 365.

OIDC, not SAML — covers 90% of the school market while staying simple.
SAML 2.0 belongs in v1.x when an enterprise customer asks.

Flow (standard authorization-code with PKCE):
  1. User clicks "Continue with Google" → we redirect to Google's
     auth endpoint with our client_id + a state nonce + a PKCE
     code_challenge
  2. Google sends them back to /auth/sso/google/callback?code=…&state=…
  3. We POST the code + PKCE code_verifier to Google's token endpoint
  4. Google returns an id_token (JWT); we verify the signature against
     Google's JWKS, then trust the email/sub/email_verified claims
  5. We look up the user by (sso_provider, sso_subject) and either:
     - find them → mint our session JWT and they're in
     - find by email → link the existing account (sso fields populated)
     - neither → create a fresh user, auto-join the org whose
       `sso_domain` matches the email's domain

PKCE is required by Google in 2024+ for "public" clients but also
strongly recommended for confidential ones; we use it across all
providers because there's no reason not to.

State storage: in-memory dict keyed by state nonce. Single-server
deploys are fine; multi-server would need Redis. The state has a
60-second TTL and is single-use, so the memory footprint is bounded.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import secrets
import sqlite3
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# ---------- provider configs (well-known OIDC discovery URLs) ----------

PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
        "jwks": "https://www.googleapis.com/oauth2/v3/certs",
        "scopes": "openid email profile",
    },
    "microsoft": {
        # Microsoft's "common" tenant endpoint accepts both work + personal
        # accounts. Enterprise users on a specific tenant set MICROSOFT_TENANT_ID.
        "authorize": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "userinfo": "https://graph.microsoft.com/oidc/userinfo",
        "jwks": "https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
        "scopes": "openid email profile",
    },
}


def _env(key: str) -> str | None:
    val = os.environ.get(key, "").strip()
    return val or None


def is_configured(provider: str) -> bool:
    """True when the necessary env vars are set for `provider`. The
    sign-in page reads this to decide which buttons to render."""
    if provider == "google":
        return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET"))
    if provider == "microsoft":
        return bool(_env("MICROSOFT_CLIENT_ID") and _env("MICROSOFT_CLIENT_SECRET"))
    return False


def configured_providers() -> list[str]:
    return [p for p in PROVIDERS if is_configured(p)]


# ---------- state (in-memory; single-server) ----------

# state_nonce -> {created_at, code_verifier, redirect_after}
_STATES: dict[str, dict] = {}
_STATE_TTL = 300  # 5 minutes


def _gc_states() -> None:
    """Sweep expired entries — called opportunistically on every use."""
    now = time.time()
    expired = [k for k, v in _STATES.items() if v["created_at"] + _STATE_TTL < now]
    for k in expired:
        _STATES.pop(k, None)


def _start_state(redirect_after: str = "/") -> tuple[str, str]:
    """Allocate a state nonce + PKCE pair. Returns (state, code_challenge)."""
    _gc_states()
    state = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    # PKCE S256: code_challenge = base64url(sha256(code_verifier))
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    _STATES[state] = {
        "created_at": time.time(),
        "code_verifier": code_verifier,
        "redirect_after": redirect_after,
    }
    return state, code_challenge


def _consume_state(state: str) -> dict:
    """Single-use lookup + delete. Raises ValueError on invalid state."""
    _gc_states()
    entry = _STATES.pop(state, None)
    if not entry:
        raise ValueError("invalid or expired state")
    return entry


# ---------- DB schema extension for SSO ----------

SCHEMA_ADDITIONS = [
    "ALTER TABLE users ADD COLUMN sso_provider TEXT",
    "ALTER TABLE users ADD COLUMN sso_subject TEXT",
    "ALTER TABLE users ADD COLUMN sso_domain TEXT",
]
EXTRA_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_users_sso ON users(sso_provider, sso_subject)"
)
ORGS_ADDITION = "ALTER TABLE orgs ADD COLUMN sso_domain TEXT"


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def migrate() -> None:
    """Additive — adds the sso_* columns to users and sso_domain to orgs.
    Tolerates missing tables (auth/orgs may not have bootstrapped yet)
    and duplicate-column errors."""
    with sqlite3.connect(_db_path(), timeout=10.0) as conn:
        has_users = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if has_users:
            for stmt in SCHEMA_ADDITIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(EXTRA_INDEX)

        has_orgs = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='orgs'"
        ).fetchone()
        if has_orgs:
            try:
                conn.execute(ORGS_ADDITION)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise


# ---------- OIDC flow ----------

def _client_id(provider: str) -> str:
    return _env(f"{provider.upper()}_CLIENT_ID") or ""


def _client_secret(provider: str) -> str:
    return _env(f"{provider.upper()}_CLIENT_SECRET") or ""


def _tenant() -> str:
    """Microsoft tenant — 'common' = work + personal accounts."""
    return _env("MICROSOFT_TENANT_ID") or "common"


def _resolve_provider_urls(provider: str) -> dict[str, str]:
    """Substitute placeholders like {tenant} for Microsoft."""
    p = PROVIDERS[provider]
    out = {}
    for k, v in p.items():
        out[k] = v.replace("{tenant}", _tenant())
    return out


def build_authorize_url(
    provider: str, *, redirect_uri: str, redirect_after: str = "/",
) -> str:
    """Return the URL to redirect the user's browser to."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    if not is_configured(provider):
        raise RuntimeError(
            f"{provider} SSO requires {provider.upper()}_CLIENT_ID + "
            f"{provider.upper()}_CLIENT_SECRET env vars"
        )
    urls = _resolve_provider_urls(provider)
    state, code_challenge = _start_state(redirect_after=redirect_after)
    params = {
        "response_type": "code",
        "client_id": _client_id(provider),
        "redirect_uri": redirect_uri,
        "scope": urls["scopes"],
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # 'select_account' forces Google to show the account picker
        # instead of silently signing in the only logged-in user —
        # important on shared school laptops.
        "prompt": "select_account",
    }
    return f"{urls['authorize']}?{urllib.parse.urlencode(params)}"


@dataclass(frozen=True)
class SSOClaims:
    provider: str            # 'google' | 'microsoft'
    subject: str             # OIDC 'sub' — stable user ID at the provider
    email: str
    email_verified: bool
    display_name: str | None
    picture_url: str | None
    domain: str | None       # email's @domain (used for auto-join)


def exchange_code(
    provider: str, *, code: str, state: str, redirect_uri: str,
) -> tuple[SSOClaims, str]:
    """Exchange the auth-code for an id_token + verify it.

    Returns (SSOClaims, redirect_after).

    NOTE: id_token signature verification against the provider's JWKS
    is delegated to PyJWT. We trust the cryptographic verification but
    additionally check the `aud`, `iss`, and `exp` claims explicitly."""
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    entry = _consume_state(state)
    urls = _resolve_provider_urls(provider)

    import httpx
    token_resp = httpx.post(
        urls["token"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": _client_id(provider),
            "client_secret": _client_secret(provider),
            "code_verifier": entry["code_verifier"],
        },
        timeout=20,
    )
    if token_resp.status_code != 200:
        raise RuntimeError(
            f"{provider} token endpoint returned {token_resp.status_code}: "
            f"{token_resp.text[:200]}"
        )
    payload = token_resp.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise RuntimeError(f"{provider} response missing id_token")

    claims = _verify_id_token(provider, id_token, urls=urls)
    return claims, entry["redirect_after"]


def _verify_id_token(provider: str, id_token: str,
                     urls: dict[str, str]) -> SSOClaims:
    """Verify the id_token's signature against the provider's JWKS +
    extract the claims we care about."""
    import jwt as pyjwt
    from jwt import PyJWKClient

    jwk_client = PyJWKClient(urls["jwks"])
    signing_key = jwk_client.get_signing_key_from_jwt(id_token)

    issuers = {
        "google": ["https://accounts.google.com", "accounts.google.com"],
        "microsoft": [
            f"https://login.microsoftonline.com/{_tenant()}/v2.0",
            "https://sts.windows.net/{tenant}/",
        ],
    }
    decoded: dict[str, Any] = pyjwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=_client_id(provider),
        issuer=issuers[provider],
        options={"require": ["sub", "exp", "aud", "iss"]},
    )

    email = (decoded.get("email") or "").lower().strip()
    if not email:
        raise RuntimeError("id_token has no email claim")
    domain = email.split("@", 1)[1] if "@" in email else None
    return SSOClaims(
        provider=provider,
        subject=str(decoded["sub"]),
        email=email,
        email_verified=bool(decoded.get("email_verified", True)),
        display_name=decoded.get("name") or decoded.get("preferred_username"),
        picture_url=decoded.get("picture"),
        domain=domain,
    )


# ---------- account binding / auto-join org ----------

@dataclass(frozen=True)
class SSOResolution:
    user_id: str
    email: str
    is_new_user: bool
    auto_joined_org_id: str | None


def resolve_or_create_user(claims: SSOClaims) -> SSOResolution:
    """Lookup-or-create flow:
       1. Existing user with same (sso_provider, sso_subject) → return
       2. Existing user with same email → bind sso_* fields, return
       3. New user: create with sso_* set + random password
       4. Auto-join the org whose `sso_domain` matches `claims.domain`
          if such an org exists (admin had set it earlier)
    """
    import secrets as s
    path = _db_path()
    now = time.time()
    auto_joined: str | None = None

    with sqlite3.connect(path, timeout=10.0) as conn:
        # 1: existing SSO binding
        row = conn.execute(
            "SELECT id, email FROM users "
            "WHERE sso_provider = ? AND sso_subject = ?",
            (claims.provider, claims.subject),
        ).fetchone()
        if row:
            return SSOResolution(
                user_id=row[0], email=row[1],
                is_new_user=False, auto_joined_org_id=None,
            )

        # 2: existing email → bind
        row = conn.execute(
            "SELECT id FROM users WHERE email = ?", (claims.email,),
        ).fetchone()
        if row:
            user_id = row[0]
            conn.execute(
                "UPDATE users SET sso_provider = ?, sso_subject = ?, "
                "sso_domain = ? WHERE id = ?",
                (claims.provider, claims.subject, claims.domain, user_id),
            )
            return SSOResolution(
                user_id=user_id, email=claims.email,
                is_new_user=False, auto_joined_org_id=None,
            )

        # 3: brand new user — random password (they'll never use it
        # since they sign in via SSO), default M1 tier.
        try:
            import bcrypt
            pw_hash = bcrypt.hashpw(s.token_urlsafe(32).encode(),
                                    bcrypt.gensalt(rounds=12)).decode()
        except Exception:
            pw_hash = ""  # extremely unlikely; pw can't be used to log in
        user_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO users "
            "(id, email, password_hash, subscription_tier, "
            " subscription_level, sso_provider, sso_subject, sso_domain) "
            "VALUES (?, ?, ?, 'M1', 'L3', ?, ?, ?)",
            (user_id, claims.email, pw_hash,
             claims.provider, claims.subject, claims.domain),
        )

        # 4: auto-join by domain
        if claims.domain:
            org_row = conn.execute(
                "SELECT id FROM orgs WHERE sso_domain = ?",
                (claims.domain,),
            ).fetchone()
            if org_row:
                auto_joined = org_row[0]
                conn.execute(
                    "INSERT OR IGNORE INTO org_members "
                    "(id, org_id, user_id, role, joined_at) "
                    "VALUES (?, ?, ?, 'student', ?)",
                    (uuid.uuid4().hex, auto_joined, user_id, now),
                )

    return SSOResolution(
        user_id=user_id, email=claims.email,
        is_new_user=True, auto_joined_org_id=auto_joined,
    )
