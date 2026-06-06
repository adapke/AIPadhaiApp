"""SSO router — twentieth web.py slice.

Three endpoints implementing the OAuth/OIDC sign-in flow:

  GET /auth/sso/providers           (list configured providers)
  GET /auth/sso/{provider}/start    (kick off OAuth redirect)
  GET /auth/sso/{provider}/callback (handle code exchange)

The error-page generator + the redirect-uri builder are
endpoint-local helpers — they move with the router. `_set_auth_cookie`
and `_escape_html` stay in web.py because they're shared with the
core auth endpoints; we late-import them.

The callback issues a session JWT via `issue_token` (from auth.py)
and bounces through a tiny HTML page that stores the token in
localStorage + redirects, keeping the token out of access logs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import issue_token

router = APIRouter()


def _sso_redirect_uri(request: Request, provider: str) -> str:
    """Build the canonical OAuth callback URL the provider should
    redirect back to. We respect x-forwarded-* headers so the URL is
    correct behind a load balancer or Cloudflare proxy."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{scheme}://{host}/auth/sso/{provider}/callback"


def _sso_error_page(title: str, detail: str) -> str:
    from .. import web as _web
    return f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Sign-in error — AI Pathshala</title>
<style>body{{font:16px/1.5 system-ui; padding:40px; max-width:560px; margin:auto;}}
.card{{background:#fff; border:1px solid #fee2e2; border-radius:12px; padding:24px;}}
h1{{color:#991b1b; font-size:20px; margin:0 0 8px;}}
p{{color:#4b5563; margin:6px 0;}} a{{color:#5E60CE;}}</style>
</head><body><div class='card'>
<h1>⚠ {_web._escape_html(title)}</h1>
<p>{_web._escape_html(detail)}</p>
<p><a href='/'>← Back to AI Pathshala</a></p>
</div></body></html>"""


@router.get("/auth/sso/providers")
def sso_providers_route() -> JSONResponse:
    """Which SSO providers are configured on this deploy. Sign-in UI
    uses this to decide which buttons to render — no point showing a
    'Continue with Google' button if GOOGLE_CLIENT_ID isn't set."""
    from .. import web as _web
    return JSONResponse({"providers": _web._sso.configured_providers()})


@router.get("/auth/sso/{provider}/start")
def sso_start_route(provider: str, request: Request, next: str = "/"):
    """Kick off the OIDC flow — redirects the browser to the
    provider."""
    from .. import web as _web
    if provider not in _web._sso.PROVIDERS:
        raise HTTPException(404, f"unknown provider {provider!r}")
    if not _web._sso.is_configured(provider):
        raise HTTPException(
            503,
            f"{provider} SSO not configured (set "
            f"{provider.upper()}_CLIENT_ID + "
            f"{provider.upper()}_CLIENT_SECRET on this deploy)",
        )
    try:
        url = _web._sso.build_authorize_url(
            provider,
            redirect_uri=_sso_redirect_uri(request, provider),
            redirect_after=next or "/",
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse(url, status_code=302)


@router.get(
    "/auth/sso/{provider}/callback", response_class=HTMLResponse,
)
def sso_callback_route(
    provider: str, request: Request,
    code: str | None = None, state: str | None = None,
    error: str | None = None, error_description: str | None = None,
):
    """OAuth callback. The provider redirects the user here with
    either a `code` (success) or an `error` (user cancelled / IdP
    rejected).

    On success: exchange code → claims → resolve-or-create user →
    mint our session JWT → bounce through a tiny HTML page that
    stores the token in localStorage + redirects into the app
    (keeps the token out of access logs)."""
    from .. import web as _web
    if error:
        return HTMLResponse(
            _sso_error_page(
                f"{provider} sign-in cancelled or rejected",
                error_description or error,
            ),
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(400, "missing code or state")
    if provider not in _web._sso.PROVIDERS:
        raise HTTPException(404, f"unknown provider {provider!r}")

    try:
        claims, redirect_after = _web._sso.exchange_code(
            provider, code=code, state=state,
            redirect_uri=_sso_redirect_uri(request, provider),
        )
    except (ValueError, RuntimeError) as e:
        return HTMLResponse(
            _sso_error_page("Sign-in failed", str(e)),
            status_code=400,
        )

    resolution = _web._sso.resolve_or_create_user(claims)
    token = issue_token(resolution.user_id)

    body = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Signing in…</title></head><body>"
        f"<script>"
        f"localStorage.setItem('pathshala_token', {token!r});"
        f"localStorage.setItem('pathshala_email', {resolution.email!r});"
        f"window.location.replace({redirect_after!r});"
        f"</script>"
        f"<p>Signing you in… "
        f"<a href={redirect_after!r}>continue</a></p>"
        f"</body></html>"
    )
    return _web._set_auth_cookie(HTMLResponse(body), token, request)
