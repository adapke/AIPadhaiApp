"""SMS + WhatsApp provider — India-first.

Supports two providers, picked by which env var is set:
  MSG91 (default)    MSG91_AUTH_KEY        — covers SMS + WhatsApp + RCS
  Gupshup            GUPSHUP_API_KEY        — SMS + WhatsApp Business API
  Twilio             TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN  — fallback

When no provider is configured (dev), every send returns a "stub"
result that includes the rendered text — so the UI flow can be
exercised without burning real credits.

Two channels:
  • SMS                   — plain text, 140-char DLT template
  • WhatsApp (Business)   — richer formatting + media; needs approved
                            template ids

Parent-alert use cases that drive this module:
  • Daily / weekly progress digest
  • Mock test result published
  • Assignment due tomorrow
  • Account locked (DPDP under-18 lockdown)
  • Subscription expiring in 7 days

The send_*() functions are idempotent on `(recipient, template_key,
content_hash)` per day — a retry doesn't re-send. The dedupe lives
inside `sms_outbox` so admin can audit what went out.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SCHEMA = """
CREATE TABLE IF NOT EXISTS sms_outbox (
    id              TEXT PRIMARY KEY,
    user_id         TEXT,
    recipient       TEXT NOT NULL,        -- E.164 phone (+91XXXXXXXXXX)
    channel         TEXT NOT NULL,        -- 'sms' | 'whatsapp'
    template_key    TEXT NOT NULL,        -- 'parent_weekly_digest' / 'mock_published' / ...
    content_hash    TEXT NOT NULL,        -- sha256 of body for dedupe
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'queued',
    -- queued | sent | failed | stub
    provider        TEXT,                  -- 'msg91' | 'gupshup' | 'twilio' | 'stub'
    provider_id     TEXT,                  -- vendor's message id
    error           TEXT,
    queued_at       REAL NOT NULL,
    sent_at         REAL,
    delivered_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_sms_user ON sms_outbox(user_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_sms_status ON sms_outbox(status, queued_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_dedupe
    ON sms_outbox(recipient, template_key, content_hash, date(queued_at, 'unixepoch'));
"""


# Templates — keep keys stable; bodies can change without DLT re-approval
# as long as the variable shape matches.
TEMPLATES: dict[str, str] = {
    "parent_weekly_digest": (
        "AI Pathshala: {child_name}'s weekly report — {streak} day streak, "
        "{mocks} mock tests, top weak topic: {weak_topic}. "
        "View: {dashboard_url}"
    ),
    "parent_daily_digest": (
        "AI Pathshala: {child_name} studied {minutes} mins today. "
        "Streak: {streak} days. {pending} pending tasks. "
        "View: {dashboard_url}"
    ),
    "mock_published": (
        "AI Pathshala: New mock test for {exam} published. "
        "Take it before {deadline}. Start: {test_url}"
    ),
    "assignment_due": (
        "AI Pathshala: {child_name}'s {assignment} is due tomorrow. "
        "Pending. {assignment_url}"
    ),
    "account_locked_minor": (
        "AI Pathshala: Your child's account is locked pending parental "
        "consent (DPDP Act). Approve here: {consent_url}. Valid for 7 days."
    ),
    "subscription_expiring": (
        "AI Pathshala: Your {plan} plan expires on {expiry_date}. "
        "Renew to keep access: {renew_url}"
    ),
    "subscription_renewed": (
        "AI Pathshala: Thanks! {plan} plan renewed till {expiry_date}. "
        "Receipt: {receipt_url}"
    ),
    "live_class_reminder": (
        "AI Pathshala: Live class '{title}' starts in {minutes} min. "
        "Join: {join_url}"
    ),
    "exam_countdown": (
        "AI Pathshala: {days} days to {exam}! Today's plan: {plan_url}. "
        "Stay consistent — you're {progress}% ready."
    ),
    "otp_login": "AI Pathshala OTP: {otp}. Do not share. Valid 10 min.",
}


VALID_CHANNELS = ("sms", "whatsapp")


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


# ============================================================================
# Provider selection
# ============================================================================

def active_provider() -> str:
    """Returns 'msg91' | 'gupshup' | 'twilio' | 'stub'. MSG91 wins
    when multiple are configured (cheapest in INR, India-resident,
    DLT-compliant)."""
    if os.environ.get("MSG91_AUTH_KEY"):
        return "msg91"
    if os.environ.get("GUPSHUP_API_KEY"):
        return "gupshup"
    if os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN"):
        return "twilio"
    return "stub"


def is_configured() -> bool:
    return active_provider() != "stub"


# ============================================================================
# Send
# ============================================================================

@dataclass(frozen=True)
class SmsResult:
    outbox_id: str
    status: str           # 'sent' | 'failed' | 'stub' | 'duplicate_skipped'
    provider: str
    provider_id: str | None
    body: str
    error: str | None = None


def send(
    *,
    recipient: str,
    template_key: str,
    variables: dict,
    channel: Literal["sms", "whatsapp"] = "sms",
    user_id: str | None = None,
) -> SmsResult:
    """Render the template + send via the active provider. Dedupes
    on (recipient, template_key, content_hash, day) so a retry storm
    can't spam the same person."""
    if channel not in VALID_CHANNELS:
        raise ValueError(f"channel must be in {VALID_CHANNELS}")
    if template_key not in TEMPLATES:
        raise ValueError(f"unknown template_key {template_key!r}")
    recipient = (recipient or "").strip()
    if not _is_valid_phone(recipient):
        raise ValueError(
            "recipient must be E.164 (+91XXXXXXXXXX); got "
            + recipient[:30],
        )

    body = _render(TEMPLATES[template_key], variables)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
    now = time.time()
    outbox_id = uuid.uuid4().hex

    # Check dedupe (same recipient + template + hash same day)
    with _conn() as conn:
        dup = conn.execute(
            "SELECT id, status, provider FROM sms_outbox "
            "WHERE recipient = ? AND template_key = ? AND content_hash = ? "
            "  AND date(queued_at, 'unixepoch') = date(?, 'unixepoch') "
            "LIMIT 1",
            (recipient, template_key, content_hash, now),
        ).fetchone()
        if dup:
            return SmsResult(
                outbox_id=dup[0],
                status="duplicate_skipped",
                provider=dup[2] or "stub",
                provider_id=None,
                body=body,
            )

        provider = active_provider()
        conn.execute(
            "INSERT INTO sms_outbox "
            "(id, user_id, recipient, channel, template_key, content_hash, "
            " body, status, provider, queued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (outbox_id, user_id, recipient, channel, template_key,
             content_hash, body, provider, now),
        )

    # Actually deliver
    provider_id: str | None = None
    error: str | None = None
    status = "sent"
    try:
        if provider == "msg91":
            provider_id = _send_msg91(recipient, body, channel)
        elif provider == "gupshup":
            provider_id = _send_gupshup(recipient, body, channel)
        elif provider == "twilio":
            provider_id = _send_twilio(recipient, body, channel)
        else:
            status = "stub"
    except Exception as e:
        status = "failed"
        error = str(e)[:500]

    with _conn() as conn:
        conn.execute(
            "UPDATE sms_outbox SET status = ?, provider_id = ?, "
            " error = ?, sent_at = ? WHERE id = ?",
            (status, provider_id, error, time.time(), outbox_id),
        )

    return SmsResult(
        outbox_id=outbox_id,
        status=status,
        provider=provider,
        provider_id=provider_id,
        body=body,
        error=error,
    )


def _render(template: str, variables: dict) -> str:
    """str.format_map but tolerant — missing keys render as
    '(unknown)' so a bad caller doesn't crash the send."""
    class _DefaultMap(dict):
        def __missing__(self, key):
            return "(unknown)"
    return template.format_map(_DefaultMap(variables or {}))


_PHONE_REGEX = None


def _is_valid_phone(s: str) -> bool:
    import re
    global _PHONE_REGEX
    if _PHONE_REGEX is None:
        _PHONE_REGEX = re.compile(r"^\+\d{10,15}$")
    return bool(_PHONE_REGEX.match(s))


# ============================================================================
# Provider adapters
# ============================================================================

def _send_msg91(recipient: str, body: str, channel: str) -> str:
    """MSG91 Flow API. SMS uses /api/v5/flow; WhatsApp uses
    /api/v5/whatsapp/whatsapp-outbound-message. We use the simpler
    `sms` endpoint here; production should switch to flow-template
    ids for DLT compliance."""
    import requests
    auth = os.environ["MSG91_AUTH_KEY"]
    sender = os.environ.get("MSG91_SENDER_ID", "PADHAI")
    if channel == "whatsapp":
        url = "https://api.msg91.com/api/v5/whatsapp/whatsapp-outbound-message/"
        payload = {
            "integrated_number": os.environ.get("MSG91_WHATSAPP_NUMBER", ""),
            "content_type": "template",
            "payload": {
                "messaging_product": "whatsapp",
                "type": "text",
                "to": recipient.lstrip("+"),
                "text": {"body": body},
            },
        }
        headers = {"authkey": auth, "Content-Type": "application/json"}
    else:
        url = "https://api.msg91.com/api/v5/flow/"
        payload = {
            "sender": sender,
            "short_url": "0",
            "mobiles": recipient.lstrip("+"),
            "message": body,
        }
        headers = {"authkey": auth, "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    return str(data.get("request_id") or data.get("message_id") or "")


def _send_gupshup(recipient: str, body: str, channel: str) -> str:
    """Gupshup HTTP API."""
    import requests
    api_key = os.environ["GUPSHUP_API_KEY"]
    if channel == "whatsapp":
        url = "https://api.gupshup.io/sm/api/v1/msg"
        data = {
            "channel": "whatsapp",
            "source": os.environ.get("GUPSHUP_WHATSAPP_NUMBER", ""),
            "destination": recipient.lstrip("+"),
            "message": json.dumps({"type": "text", "text": body}),
            "src.name": os.environ.get("GUPSHUP_APP_NAME", "AIPadhai"),
        }
    else:
        url = "https://enterprise.smsgupshup.com/GatewayAPI/rest"
        data = {
            "userid": os.environ.get("GUPSHUP_USER_ID", ""),
            "password": api_key,
            "method": "SendMessage",
            "send_to": recipient.lstrip("+"),
            "msg": body,
            "msg_type": "TEXT",
            "auth_scheme": "plain",
        }
    headers = {"apikey": api_key} if channel == "whatsapp" else {}
    r = requests.post(url, data=data, headers=headers, timeout=10)
    r.raise_for_status()
    # Gupshup returns "success | msgid" plain text for SMS, JSON for WA
    if channel == "whatsapp":
        return str(r.json().get("messageId", ""))
    return r.text.split("|", 1)[-1].strip()


def _send_twilio(recipient: str, body: str, channel: str) -> str:
    import requests
    from requests.auth import HTTPBasicAuth
    sid = os.environ["TWILIO_ACCOUNT_SID"]
    tok = os.environ["TWILIO_AUTH_TOKEN"]
    if channel == "whatsapp":
        sender = "whatsapp:" + os.environ.get("TWILIO_WHATSAPP_NUMBER", "")
        to = "whatsapp:" + recipient
    else:
        sender = os.environ.get("TWILIO_FROM_NUMBER", "")
        to = recipient
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=HTTPBasicAuth(sid, tok),
        data={"From": sender, "To": to, "Body": body},
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json().get("sid", ""))


# ============================================================================
# Admin / audit
# ============================================================================

def list_outbox(
    *,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    sql = (
        "SELECT id, user_id, recipient, channel, template_key, "
        "       status, provider, provider_id, error, queued_at, sent_at "
        "FROM sms_outbox"
    )
    params: list = []
    where: list[str] = []
    if user_id:
        where.append("user_id = ?"); params.append(user_id)
    if status:
        where.append("status = ?"); params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY queued_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r[0], "user_id": r[1], "recipient": r[2],
            "channel": r[3], "template_key": r[4],
            "status": r[5], "provider": r[6], "provider_id": r[7],
            "error": r[8], "queued_at": r[9], "sent_at": r[10],
        }
        for r in rows
    ]


def describe() -> dict:
    return {
        "provider": active_provider(),
        "configured": is_configured(),
        "msg91": bool(os.environ.get("MSG91_AUTH_KEY")),
        "gupshup": bool(os.environ.get("GUPSHUP_API_KEY")),
        "twilio": bool(
            os.environ.get("TWILIO_ACCOUNT_SID")
            and os.environ.get("TWILIO_AUTH_TOKEN")
        ),
        "templates": list(TEMPLATES.keys()),
    }
