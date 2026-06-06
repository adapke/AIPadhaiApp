"""DPDP rights router — twenty-fifth web.py slice.

Two endpoints implementing DPDP Act 2023 §11 (right to access /
data portability) + §12 (right to erasure):

  GET    /api/me/data/export   (§11 — full personal data dump)
  DELETE /api/me/account       (§12 — anonymise + lock + schedule purge)

These are **legally required** for any India-facing user-data
service. The Privacy Policy at `/privacy` already references both
URLs verbatim; they were implemented in web.py from v3.x and lifted
here in prod-2 so the legal contract has a dedicated, auditable
home.

The export carries: profile + preferences + recent jobs + flashcard
decks + last 100 audit events. Schema is versioned (`schema_version:
1`) — increment when fields are added or renamed so downstream
parsers can branch.

The deletion is a **soft delete**: email anonymised + account locked
immediately; full purge of generated content (job artifacts, cache
entries) scheduled within 30 days by ops. The action is irreversible
once started — the session token used to call it stops working as
soon as the next request comes in (account_locked → 403 from
`current_user`).

Both endpoints append an audit-log entry (`dpdp.data_export` /
`dpdp.account_deletion_requested`) so the compliance officer can
prove the request was honoured.

Late-imports web.py for `_rl` (rate limit), `cache`, `store`,
`_audit`, `_log`, `_ensure_profile_cols`. The actual DB writes go
through psycopg directly — same shape as the original implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..auth import AuthUser
from ..db import get_db_url
from ..web import current_user

router = APIRouter()


@router.get("/api/me/data/export")
def export_my_data_route(
    request: Request,
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """DPDP Act 2023 §11 — right to access. Returns all personal
    data we hold for the authenticated user as a single JSON
    document. Rate-limited (5 cost units against the
    file_upload bucket) to prevent DB exhaustion via tight loops."""
    from .. import web as _web

    if user is None:
        raise HTTPException(401, "authentication required")

    rate_key = _web._rl.client_ip_from_request(request)
    if not _web._rl.file_upload.try_consume(rate_key, cost=5):
        raise HTTPException(
            429,
            "rate limit exceeded — please wait before exporting again",
        )

    export: dict = {
        "generated_at": datetime.now(UTC).isoformat(),
        "schema_version": 1,
        "profile": {
            "id": user.id,
            "email": user.email,
            "subscription_tier": user.subscription_tier,
            "subscription_level": user.subscription_level,
        },
        "preferences": None,
        "jobs": [],
        "flashcard_decks": [],
        "audit_events": [],
    }

    # Preferences + audit events from Postgres (non-fatal if absent)
    db_url = get_db_url()
    if db_url:
        try:
            import psycopg as _pg
            with _pg.connect(db_url, autocommit=True) as conn:
                row = conn.execute(
                    "SELECT display_name, preferred_language, "
                    "       preferred_level, preferred_mode, created_at "
                    "FROM users WHERE id = %s",
                    (user.id,),
                ).fetchone()
                if row:
                    export["profile"]["display_name"] = row[0]
                    export["profile"]["created_at"] = (
                        row[4].isoformat()
                        if hasattr(row[4], "isoformat")
                        else str(row[4])
                    ) if row[4] else None
                    export["preferences"] = {
                        "language": row[1],
                        "level": row[2],
                        "mode": row[3],
                    }
                # Audit events: last 100 actions for this user
                audit_rows = conn.execute(
                    "SELECT action, note, created_at FROM audit_log "
                    "WHERE actor_user_id = %s "
                    "ORDER BY created_at DESC LIMIT 100",
                    (user.id,),
                ).fetchall()
                export["audit_events"] = [
                    {
                        "action": r[0],
                        "note": r[1],
                        "timestamp": (
                            r[2].isoformat()
                            if hasattr(r[2], "isoformat") else str(r[2])
                        ),
                    }
                    for r in audit_rows
                ]
        except Exception as exc:
            _web._log.warning(
                "[data_export] profile/audit query failed "
                "(non-fatal): %s", exc,
            )

    # Job history — use the module-level store
    try:
        jobs = _web.store.recent_jobs(limit=200, filter_user_id=user.id)
        export["jobs"] = [
            {
                "id": j.get("id"),
                "topic": j.get("topic"),
                "language": j.get("language"),
                "status": j.get("status"),
                "created_at": j.get("created_at"),
            }
            for j in (jobs or [])
        ]
    except Exception as exc:
        _web._log.warning(
            "[data_export] jobs query failed (non-fatal): %s", exc,
        )

    # Flashcard decks
    try:
        from .. import spaced_repetition as _sr
        decks = _sr.list_my_decks(user_id=user.id)
        export["flashcard_decks"] = [
            {"id": d.id, "name": d.name, "card_count": d.card_count}
            for d in (decks or [])
        ]
    except Exception as exc:
        _web._log.warning(
            "[data_export] flashcard query failed (non-fatal): %s", exc,
        )

    _web._audit.record(action="dpdp.data_export", actor_user_id=user.id)
    return JSONResponse(export)


@router.delete("/api/me/account")
def delete_my_account_route(
    user: AuthUser | None = Depends(current_user),
) -> JSONResponse:
    """DPDP Act 2023 §12 — right to erasure. Soft-deletes the
    account: the email is anonymised immediately and the account is
    locked. Full purge of all personal data happens within 30 days
    (scheduled by ops).

    This action is **irreversible**. The token used to call this
    endpoint becomes invalid as soon as the next request hits
    `current_user` (account_locked = TRUE → 403)."""
    from .. import web as _web

    if user is None:
        raise HTTPException(401, "authentication required")

    db_url = get_db_url()
    if not db_url:
        raise HTTPException(
            503, "database not configured — cannot delete account",
        )

    anon_email = f"deleted-{user.id}@deleted.invalid"
    deletion_requested_at = datetime.now(UTC).isoformat()

    # Ensure profile columns exist before we try to NULL them out.
    _web._ensure_profile_cols()

    try:
        import psycopg as _pg
        with _pg.connect(db_url, autocommit=True) as conn:
            # Anonymise email + lock account in one statement.
            conn.execute(
                "UPDATE users SET email = %s, account_locked = TRUE, "
                "display_name = NULL, preferred_language = NULL, "
                "preferred_level = NULL, preferred_mode = NULL "
                "WHERE id = %s",
                (anon_email, user.id),
            )
    except Exception as exc:
        _web._log.error(
            "[delete_account] anonymisation failed for user %s: %s",
            user.id, exc,
        )
        raise HTTPException(
            500,
            "account deletion failed — please contact support",
        ) from exc

    _web._audit.record(
        action="dpdp.account_deletion_requested",
        actor_user_id=user.id,
        note=f"deletion_requested_at={deletion_requested_at}",
    )
    _web._log.info(
        "[delete_account] user %s requested erasure; email "
        "anonymised, full purge scheduled within 30 days",
        user.id,
    )
    return JSONResponse({
        "ok": True,
        "message": (
            "Account anonymised. All remaining personal data will be "
            "purged within 30 days in accordance with the DPDP Act 2023."
        ),
        "deletion_requested_at": deletion_requested_at,
    })
