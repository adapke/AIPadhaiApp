"""AI Pathshala Admin Console.

Standalone FastAPI app. No `from padhai import …` — admin and student
app share the underlying database file as their only contract, not
Python code. When this module moves to its own repository, nothing
needs to be patched here.

Deployment for v0.8.1: mounted into the main app at `/admin/*` so a
single Render service serves both. The mount happens in
`padhai/web.py`. When admin needs its own scale envelope, lift this
out into a sibling Render service by adding a service block to
`render.yaml` — no code change in this module.

Routes (all under /admin/* once mounted):
  GET  /admin/              dashboard (or login form if not signed in)
  POST /admin/signup        bootstrap signup (first signup only)
  POST /admin/login         email + password → sets `admin_token` cookie
  GET  /admin/logout        clears cookie, redirects to /admin/
  GET  /admin/jobs          jobs queue
  GET  /admin/topics        topics + languages
  GET  /admin/api/...       JSON endpoints (PRD §16)
  POST /admin/api/jobs/{id}/retry|cancel
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth, data
from .templates import (
    render_dashboard,
    render_jobs_queue,
    render_login,
    render_topics,
)


app = FastAPI(
    title="AI Pathshala — Admin Console",
    description="Operations dashboard. Admin-only.",
    version="0.8.1",
    # The OpenAPI docs are exposed too — useful for ops scripting.
    openapi_url="/openapi.json",
    docs_url="/docs",
)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "padhai-admin", "version": app.version}


# ---------- auth (login / signup / logout) ----------

def _set_token_cookie(response, token: str) -> None:
    """SameSite=Lax + HttpOnly so JavaScript on the page can't read it
    (defence-in-depth against XSS). Secure=True in production (any env
    where HTTPS is used); False only in local dev without TLS."""
    import os as _os
    is_secure = _os.environ.get("APP_BASE_URL", "http://").startswith("https://")
    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.TOKEN_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/admin",
    )


@app.post("/signup", response_class=HTMLResponse)
def signup(
    email: str = Form(...),
    password: str = Form(..., min_length=8),
    display_name: str = Form(""),
    bootstrap_token: str = Form(""),
):
    """Bootstrap signup — only allowed when zero admin users exist AND
    the caller supplies the correct ADMIN_BOOTSTRAP_TOKEN env var.
    Set this to a strong random secret before first deployment and
    delete it after the first admin account is created."""
    import os as _os
    required_token = _os.environ.get("ADMIN_BOOTSTRAP_TOKEN", "")
    if not required_token:
        raise HTTPException(
            status_code=403,
            detail="ADMIN_BOOTSTRAP_TOKEN is not set — bootstrap signup disabled. "
                   "Set it in your environment to create the first admin account.",
        )
    if bootstrap_token != required_token:
        raise HTTPException(status_code=403, detail="invalid bootstrap token")
    if auth.count_users() > 0:
        # Surface as the login page with a clear error.
        html = render_login(
            allow_signup=False,
            error="Signups are closed. The first admin already exists.",
        )
        return HTMLResponse(html, status_code=403)
    user = auth.create_user(
        email=email, password=password,
        display_name=display_name.strip() or None,
    )
    token = auth.issue_token(user.id)
    auth.mark_login(user.id)
    resp = RedirectResponse(url="/admin/", status_code=303)
    _set_token_cookie(resp, token)
    return resp


@app.post("/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
):
    """Email + password → JWT cookie. Re-renders the login page with an
    error message on bad credentials (no JSON — keeps the no-JS path
    workable for ops who curl this)."""
    record = auth.find_by_email(email)
    if record is None or not auth.verify_password(password, record[1]):
        # Constant-time-ish; both branches do roughly the same work.
        html = render_login(
            allow_signup=(auth.count_users() == 0),
            error="Invalid email or password.",
        )
        return HTMLResponse(html, status_code=401)
    user, _ = record
    token = auth.issue_token(user.id)
    auth.mark_login(user.id)
    resp = RedirectResponse(url="/admin/", status_code=303)
    _set_token_cookie(resp, token)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse(url="/admin/", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/admin")
    return resp


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
def home(user: Annotated[auth.AdminUser | None, Depends(auth.optional_admin)] = None):
    if user is None:
        return HTMLResponse(render_login(allow_signup=(auth.count_users() == 0)))
    summary = data.dashboard_summary()
    return HTMLResponse(render_dashboard(
        user=user,
        summary=summary,
        languages=data.language_usage(),
        topics=data.popular_topics(),
        daily=data.daily_volume(),
    ))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    status: str | None = None,
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    jobs = data.list_jobs(status=status, limit=100)
    return HTMLResponse(render_jobs_queue(user=user, jobs=jobs, filter_status=status))


@app.get("/topics", response_class=HTMLResponse)
def topics_page(
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    return HTMLResponse(render_topics(
        user=user,
        topics=data.popular_topics(limit=50),
        languages=data.language_usage(limit=20),
    ))


# ---------- JSON API ----------

@app.get("/api/dashboard")
def api_dashboard(
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    s = data.dashboard_summary()
    return {
        "total_jobs": s.total_jobs,
        "queued": s.queued,
        "running": s.running,
        "succeeded": s.succeeded,
        "failed": s.failed,
        "succeeded_today": s.succeeded_today,
        "failed_today": s.failed_today,
        "cache_artifacts": s.cache_artifacts_total,
        "avg_render_seconds": s.avg_render_seconds,
        "p95_render_seconds": s.p95_render_seconds,
        "languages": data.language_usage(),
        "topics": data.popular_topics(),
        "daily": data.daily_volume(),
    }


@app.get("/api/jobs")
def api_jobs(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    return {
        "jobs": data.list_jobs(status=status, limit=limit, offset=offset),
        "filter_status": status,
        "limit": limit, "offset": offset,
    }


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(
    job_id: str,
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    new_status = data.retry_job(job_id)
    return {
        "job_id": job_id,
        "status": new_status,
        "admin": user.email,
        "retried_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(
    job_id: str,
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    data.cancel_job(job_id, admin_email=user.email)
    return {"job_id": job_id, "status": "failed", "admin": user.email}


@app.get("/api/cache-stats")
def api_cache_stats(
    user: Annotated[auth.AdminUser, Depends(auth.require_admin)] = None,
):
    return data.cache_stats()
