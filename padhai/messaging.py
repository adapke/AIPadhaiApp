"""v3.13 — WhatsApp / SMS notification rails.

Per review §17 — Indian students rely heavily on WhatsApp + SMS;
push notifications miss many users (especially on entry-level
Android). Existing `push.py` handles FCM web push. This module
adds:

  • WhatsApp Business Cloud API channel (template-based)
  • SMS channel (Twilio / MSG91 shim)
  • Scheduled reminders ('Take 10 Polity PYQs before 9 PM')
  • Per-user opt-in registry + per-template throttle

Three tables:
  user_phone_channels    per (user, phone) channel + opt_in_status
  message_templates      approved templates (WhatsApp requires this)
  scheduled_messages     queued + sent messages (with retry log)

Provider abstraction:
  PADHAI_WHATSAPP_PROVIDER  'meta' / 'twilio' / 'msg91' / 'sandbox'
  PADHAI_SMS_PROVIDER       'twilio' / 'msg91' / 'sandbox'

Default 'sandbox' just records what would have been sent — used
in dev + tests. Production swaps in env-gated providers.

Templates: WhatsApp requires pre-approved templates from Meta.
We store the template_code + body_template (with {{var}}
placeholders) + an approval status. A scheduled message picks a
template + supplies variables.

Opt-in: students explicitly opt-in to WhatsApp / SMS reminders.
DPDP §6 (consent) — record consent_text + consented_at.
Withdrawal flips opt_in_status to 'opted_out' (DPDP §13).

Throttle: per-template + per-user — caps how often the same
template can fire for the same user in a day. 'Take your daily
PYQs' template defaults to once/day.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_phone_channels (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    phone_number    TEXT NOT NULL,                  -- E.164 format
    channel         TEXT NOT NULL,                  -- 'whatsapp' | 'sms'
    opt_in_status   TEXT NOT NULL DEFAULT 'opted_in',
    -- 'opted_in' | 'opted_out' | 'bounced' | 'pending_verification'
    consent_text    TEXT,                            -- exact text shown at opt-in
    consented_at    REAL NOT NULL,
    revoked_at      REAL,
    UNIQUE (user_id, phone_number, channel)
);
CREATE INDEX IF NOT EXISTS idx_upc_user
    ON user_phone_channels(user_id, opt_in_status);
CREATE INDEX IF NOT EXISTS idx_upc_phone
    ON user_phone_channels(phone_number);

CREATE TABLE IF NOT EXISTS message_templates (
    id              TEXT PRIMARY KEY,
    template_code   TEXT NOT NULL,                  -- 'daily_pyq_reminder'
    channel         TEXT NOT NULL,                  -- 'whatsapp' | 'sms'
    language        TEXT NOT NULL DEFAULT 'en',
    title           TEXT NOT NULL,
    body_template   TEXT NOT NULL,                  -- '{{name}}, take {{n}} PYQs before 9 PM'
    variables_json  TEXT NOT NULL,                  -- required var list
    approval_status TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'approved' | 'rejected'
    provider_template_id TEXT,                       -- ID on Meta / Twilio side
    daily_max_per_user INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    approved_at     REAL,
    UNIQUE (template_code, channel, language)
);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    template_code   TEXT NOT NULL,
    channel         TEXT NOT NULL,
    phone_number    TEXT NOT NULL,
    rendered_body   TEXT NOT NULL,
    variables_json  TEXT,
    scheduled_at    REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'scheduled',
    -- 'scheduled' | 'sent' | 'failed' | 'throttled' | 'cancelled'
    sent_at         REAL,
    error_reason    TEXT,
    retries         INTEGER NOT NULL DEFAULT 0,
    provider_msg_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sm_due
    ON scheduled_messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sm_user
    ON scheduled_messages(user_id, scheduled_at DESC);
"""


VALID_CHANNELS = frozenset({"whatsapp", "sms"})
VALID_OPT_IN_STATUSES = frozenset({
    "opted_in", "opted_out", "bounced", "pending_verification",
})
VALID_APPROVAL_STATUSES = frozenset({
    "pending", "approved", "rejected",
})
VALID_MESSAGE_STATUSES = frozenset({
    "scheduled", "sent", "failed", "throttled", "cancelled",
})

# E.164 — '+91XXXXXXXXXX'. We're lenient on the digit count
# (international phones vary 10-15 digits).
_E164_RE = re.compile(r"^\+\d{10,15}$")

# Template placeholder — {{var_name}}
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")

# Default max retries on send failure
MAX_RETRIES = 3

DEFAULT_WHATSAPP_PROVIDER = os.environ.get(
    "PADHAI_WHATSAPP_PROVIDER", "sandbox",
)
DEFAULT_SMS_PROVIDER = os.environ.get(
    "PADHAI_SMS_PROVIDER", "sandbox",
)


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


# ---------- dataclasses ----------

@dataclass(frozen=True)
class PhoneChannel:
    id: str
    user_id: str
    phone_number: str
    channel: str
    opt_in_status: str
    consent_text: str | None
    consented_at: float
    revoked_at: float | None


@dataclass(frozen=True)
class Template:
    id: str
    template_code: str
    channel: str
    language: str
    title: str
    body_template: str
    variables: list[str]
    approval_status: str
    provider_template_id: str | None
    daily_max_per_user: int
    created_at: float
    approved_at: float | None


@dataclass(frozen=True)
class ScheduledMessage:
    id: str
    user_id: str
    template_code: str
    channel: str
    phone_number: str
    rendered_body: str
    variables: dict
    scheduled_at: float
    status: str
    sent_at: float | None
    error_reason: str | None
    retries: int
    provider_msg_id: str | None


# ---------- channel opt-in ----------

def opt_in_channel(
    *,
    user_id: str,
    phone_number: str,
    channel: str,
    consent_text: str,
) -> PhoneChannel:
    phone_number = (phone_number or "").strip()
    if not _E164_RE.match(phone_number):
        raise ValueError(
            "phone_number must be E.164 (+CCXXXXXXXXXX)",
        )
    if channel not in VALID_CHANNELS:
        raise ValueError(
            f"channel must be in {sorted(VALID_CHANNELS)}",
        )
    consent_text = (consent_text or "").strip()
    if len(consent_text) < 20 or len(consent_text) > 2000:
        raise ValueError(
            "consent_text must be [20, 2000] chars",
        )
    now = time.time()
    cid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO user_phone_channels "
                "(id, user_id, phone_number, channel, "
                " consent_text, consented_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cid, user_id, phone_number, channel,
                 consent_text, now),
            )
    except sqlite3.IntegrityError:
        # Re-opt-in on a previously-opted-out row
        with _conn() as conn:
            conn.execute(
                "UPDATE user_phone_channels SET "
                " opt_in_status = 'opted_in', "
                " consent_text = ?, consented_at = ?, "
                " revoked_at = NULL "
                "WHERE user_id = ? AND phone_number = ? "
                "AND channel = ?",
                (consent_text, now, user_id,
                 phone_number, channel),
            )
            r = conn.execute(
                "SELECT id FROM user_phone_channels "
                "WHERE user_id = ? AND phone_number = ? "
                "AND channel = ?",
                (user_id, phone_number, channel),
            ).fetchone()
            cid = r[0] if r else cid
    return _get_channel(cid)  # type: ignore[return-value]


def _get_channel(channel_id: str) -> PhoneChannel | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, user_id, phone_number, channel, "
            "       opt_in_status, consent_text, consented_at, "
            "       revoked_at "
            "FROM user_phone_channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
    if not r:
        return None
    return PhoneChannel(
        id=r[0], user_id=r[1], phone_number=r[2],
        channel=r[3], opt_in_status=r[4],
        consent_text=r[5], consented_at=r[6],
        revoked_at=r[7],
    )


def list_user_channels(
    user_id: str,
    *,
    channel: str | None = None,
    opt_in_status: str | None = None,
) -> list[PhoneChannel]:
    sql = (
        "SELECT id, user_id, phone_number, channel, "
        "       opt_in_status, consent_text, consented_at, "
        "       revoked_at "
        "FROM user_phone_channels WHERE user_id = ?"
    )
    params: list = [user_id]
    if channel:
        if channel not in VALID_CHANNELS:
            raise ValueError(
                f"channel must be in {sorted(VALID_CHANNELS)}",
            )
        sql += " AND channel = ?"; params.append(channel)
    if opt_in_status:
        if opt_in_status not in VALID_OPT_IN_STATUSES:
            raise ValueError(
                f"opt_in_status must be in "
                f"{sorted(VALID_OPT_IN_STATUSES)}",
            )
        sql += " AND opt_in_status = ?"
        params.append(opt_in_status)
    sql += " ORDER BY consented_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        PhoneChannel(
            id=r[0], user_id=r[1], phone_number=r[2],
            channel=r[3], opt_in_status=r[4],
            consent_text=r[5], consented_at=r[6],
            revoked_at=r[7],
        )
        for r in rows
    ]


def opt_out_channel(
    *,
    user_id: str,
    phone_number: str,
    channel: str,
) -> bool:
    """DPDP §13 — user withdraws consent. We keep the row for
    audit + flip opt_in_status."""
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE user_phone_channels SET "
            " opt_in_status = 'opted_out', revoked_at = ? "
            "WHERE user_id = ? AND phone_number = ? "
            "AND channel = ? AND opt_in_status = 'opted_in'",
            (now, user_id, phone_number, channel),
        )
        return cur.rowcount > 0


def mark_bounced(
    *,
    phone_number: str,
    channel: str,
) -> int:
    """Provider webhook → bounced. Stops further sends to this
    phone on this channel."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE user_phone_channels SET "
            " opt_in_status = 'bounced' "
            "WHERE phone_number = ? AND channel = ?",
            (phone_number, channel),
        )
    return cur.rowcount


def get_active_channel(
    *, user_id: str, channel: str,
) -> PhoneChannel | None:
    """Return the user's active (opted_in) channel of the given
    kind. None if they don't have one."""
    channels = list_user_channels(
        user_id, channel=channel, opt_in_status="opted_in",
    )
    return channels[0] if channels else None


# ---------- templates ----------

def create_template(
    *,
    template_code: str,
    channel: str,
    language: str,
    title: str,
    body_template: str,
    daily_max_per_user: int = 1,
    provider_template_id: str | None = None,
) -> Template:
    template_code = (template_code or "").strip()
    if not re.match(r"^[a-z0-9_]{3,80}$", template_code):
        raise ValueError(
            "template_code must be 3-80 lowercase letters/digits/_ chars",
        )
    if channel not in VALID_CHANNELS:
        raise ValueError(
            f"channel must be in {sorted(VALID_CHANNELS)}",
        )
    title = (title or "").strip()
    if len(title) < 3 or len(title) > 200:
        raise ValueError("title must be [3, 200] chars")
    body_template = (body_template or "").strip()
    if len(body_template) < 5 or len(body_template) > 2000:
        raise ValueError("body_template must be [5, 2000] chars")
    if daily_max_per_user < 1 or daily_max_per_user > 100:
        raise ValueError("daily_max_per_user must be in [1, 100]")
    # Extract variables from template body
    variables = sorted(set(_PLACEHOLDER_RE.findall(body_template)))
    tid = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO message_templates "
                "(id, template_code, channel, language, title, "
                " body_template, variables_json, "
                " provider_template_id, daily_max_per_user, "
                " created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, template_code, channel, language, title,
                 body_template, json.dumps(variables),
                 provider_template_id, daily_max_per_user,
                 time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"template {template_code!r}/{channel}/{language} "
            "already exists",
        ) from e
    return get_template_by_code(
        template_code=template_code,
        channel=channel, language=language,
    )  # type: ignore[return-value]


def get_template_by_code(
    *,
    template_code: str,
    channel: str,
    language: str,
) -> Template | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, template_code, channel, language, title, "
            "       body_template, variables_json, approval_status, "
            "       provider_template_id, daily_max_per_user, "
            "       created_at, approved_at "
            "FROM message_templates "
            "WHERE template_code = ? AND channel = ? "
            "AND language = ?",
            (template_code, channel, language),
        ).fetchone()
    if not r:
        return None
    return Template(
        id=r[0], template_code=r[1], channel=r[2],
        language=r[3], title=r[4],
        body_template=r[5],
        variables=json.loads(r[6]) if r[6] else [],
        approval_status=r[7],
        provider_template_id=r[8],
        daily_max_per_user=r[9],
        created_at=r[10], approved_at=r[11],
    )


def approve_template(
    *,
    template_code: str,
    channel: str,
    language: str,
    provider_template_id: str | None = None,
) -> bool:
    now = time.time()
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE message_templates SET "
            " approval_status = 'approved', "
            " provider_template_id = COALESCE(?, provider_template_id), "
            " approved_at = ? "
            "WHERE template_code = ? AND channel = ? "
            "AND language = ? AND approval_status = 'pending'",
            (provider_template_id, now, template_code,
             channel, language),
        )
        return cur.rowcount > 0


def list_templates(
    *,
    channel: str | None = None,
    approval_status: str | None = None,
) -> list[Template]:
    sql = (
        "SELECT id, template_code, channel, language, title, "
        "       body_template, variables_json, approval_status, "
        "       provider_template_id, daily_max_per_user, "
        "       created_at, approved_at "
        "FROM message_templates WHERE 1=1"
    )
    params: list = []
    if channel:
        sql += " AND channel = ?"; params.append(channel)
    if approval_status:
        if approval_status not in VALID_APPROVAL_STATUSES:
            raise ValueError(
                f"approval_status must be in "
                f"{sorted(VALID_APPROVAL_STATUSES)}",
            )
        sql += " AND approval_status = ?"
        params.append(approval_status)
    sql += " ORDER BY template_code, channel, language"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Template(
            id=r[0], template_code=r[1], channel=r[2],
            language=r[3], title=r[4],
            body_template=r[5],
            variables=json.loads(r[6]) if r[6] else [],
            approval_status=r[7],
            provider_template_id=r[8],
            daily_max_per_user=r[9],
            created_at=r[10], approved_at=r[11],
        )
        for r in rows
    ]


# ---------- scheduling + send ----------

def _render(body_template: str, variables: dict) -> str:
    """Substitute {{var}} placeholders. Raises ValueError if any
    required var is missing."""
    missing: list[str] = []

    def _sub(m):
        key = m.group(1)
        if key not in variables:
            missing.append(key)
            return f"{{{{{key}}}}}"
        return str(variables[key])

    rendered = _PLACEHOLDER_RE.sub(_sub, body_template)
    if missing:
        raise ValueError(
            f"missing variables: {sorted(set(missing))}",
        )
    return rendered


def _within_daily_throttle(
    *,
    user_id: str,
    template_code: str,
    channel: str,
    daily_max: int,
) -> bool:
    """Return True if the user is BELOW their daily cap (i.e.
    can still receive)."""
    now = time.time()
    day_start = now - (now % 86400)
    with _conn() as conn:
        r = conn.execute(
            "SELECT COUNT(*) FROM scheduled_messages "
            "WHERE user_id = ? AND template_code = ? "
            "AND channel = ? AND status = 'sent' "
            "AND sent_at >= ?",
            (user_id, template_code, channel, day_start),
        ).fetchone()
    return int(r[0] or 0) < daily_max


def schedule_message(
    *,
    user_id: str,
    template_code: str,
    variables: dict,
    scheduled_at: float | None = None,
    language: str = "en",
    channel: str | None = None,
) -> ScheduledMessage:
    """Queue a message. If channel is None we pick whichever active
    channel the user has (prefer WhatsApp; fall back to SMS).
    Throttle + opt-in checks happen at send_due() time, not here —
    so callers can pre-schedule a week of reminders cheaply."""
    if channel and channel not in VALID_CHANNELS:
        raise ValueError(
            f"channel must be in {sorted(VALID_CHANNELS)}",
        )
    # Resolve channel
    if not channel:
        if get_active_channel(
            user_id=user_id, channel="whatsapp",
        ):
            channel = "whatsapp"
        elif get_active_channel(
            user_id=user_id, channel="sms",
        ):
            channel = "sms"
        else:
            raise ValueError(
                "user has no opted-in channel",
            )
    template = get_template_by_code(
        template_code=template_code,
        channel=channel, language=language,
    )
    if not template:
        raise ValueError(
            f"template {template_code!r}/{channel}/{language} "
            "not found",
        )
    if template.approval_status != "approved":
        raise ValueError(
            f"template approval_status is "
            f"{template.approval_status!r}, not approved",
        )
    rendered = _render(template.body_template, variables)
    user_ch = get_active_channel(
        user_id=user_id, channel=channel,
    )
    if not user_ch:
        raise ValueError(
            f"user not opted in to {channel}",
        )
    sched = scheduled_at if scheduled_at is not None else time.time()
    sid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO scheduled_messages "
            "(id, user_id, template_code, channel, "
            " phone_number, rendered_body, variables_json, "
            " scheduled_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled')",
            (sid, user_id, template_code, channel,
             user_ch.phone_number, rendered,
             json.dumps(variables), sched),
        )
    return _get_message(sid)  # type: ignore[return-value]


def _get_message(message_id: str) -> ScheduledMessage | None:
    with _conn() as conn:
        r = conn.execute(
            _M_SEL + " WHERE id = ?", (message_id,),
        ).fetchone()
    return _row_to_message(r) if r else None


_M_SEL = (
    "SELECT id, user_id, template_code, channel, phone_number, "
    "       rendered_body, variables_json, scheduled_at, "
    "       status, sent_at, error_reason, retries, "
    "       provider_msg_id "
    "FROM scheduled_messages"
)


def _row_to_message(r) -> ScheduledMessage:
    return ScheduledMessage(
        id=r[0], user_id=r[1], template_code=r[2],
        channel=r[3], phone_number=r[4],
        rendered_body=r[5],
        variables=json.loads(r[6]) if r[6] else {},
        scheduled_at=r[7], status=r[8],
        sent_at=r[9], error_reason=r[10],
        retries=r[11], provider_msg_id=r[12],
    )


def get_message(message_id: str) -> ScheduledMessage | None:
    return _get_message(message_id)


def cancel_message(
    *, message_id: str, user_id: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE scheduled_messages SET status = 'cancelled' "
            "WHERE id = ? AND user_id = ? "
            "AND status = 'scheduled'",
            (message_id, user_id),
        )
        return cur.rowcount > 0


def list_user_messages(
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 100,
) -> list[ScheduledMessage]:
    limit = max(1, min(limit, 500))
    sql = _M_SEL + " WHERE user_id = ?"
    params: list = [user_id]
    if status:
        if status not in VALID_MESSAGE_STATUSES:
            raise ValueError(
                f"status must be in "
                f"{sorted(VALID_MESSAGE_STATUSES)}",
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY scheduled_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_message(r) for r in rows]


def send_due(*, batch_size: int = 50) -> dict:
    """Worker entry — pull due 'scheduled' messages + send them.
    Applies opt-in check + throttle at send time (not schedule
    time) so we catch users who opted out between scheduling
    and send.

    Provider call is a stub when PADHAI_WHATSAPP_PROVIDER /
    PADHAI_SMS_PROVIDER is 'sandbox' — we just record sent_at.
    Returns counts per outcome."""
    batch_size = max(1, min(batch_size, 500))
    now = time.time()
    out = {
        "sent": 0, "throttled": 0, "skipped_opt_out": 0,
        "failed": 0,
    }
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, template_code, channel, "
            "       phone_number, rendered_body, retries "
            "FROM scheduled_messages "
            "WHERE status = 'scheduled' AND scheduled_at <= ? "
            "ORDER BY scheduled_at LIMIT ?",
            (now, batch_size),
        ).fetchall()
    for mid, uid, code, ch, phone, body, retries in rows:
        # Opt-in check
        active = get_active_channel(user_id=uid, channel=ch)
        if not active or active.phone_number != phone:
            with _conn() as conn:
                conn.execute(
                    "UPDATE scheduled_messages SET "
                    " status = 'cancelled', "
                    " error_reason = 'user not opted in at send' "
                    "WHERE id = ?", (mid,),
                )
            out["skipped_opt_out"] += 1
            continue
        # Throttle check
        template = get_template_by_code(
            template_code=code, channel=ch, language="en",
        )
        cap = template.daily_max_per_user if template else 1
        if not _within_daily_throttle(
            user_id=uid, template_code=code,
            channel=ch, daily_max=cap,
        ):
            with _conn() as conn:
                conn.execute(
                    "UPDATE scheduled_messages SET "
                    " status = 'throttled', "
                    " error_reason = 'daily cap reached' "
                    "WHERE id = ?", (mid,),
                )
            out["throttled"] += 1
            continue
        # Provider call (sandbox = no-op + fake msg id)
        provider_msg_id, error = _provider_send(
            channel=ch, phone=phone, body=body, template=template,
        )
        if error:
            new_retries = retries + 1
            if new_retries >= MAX_RETRIES:
                with _conn() as conn:
                    conn.execute(
                        "UPDATE scheduled_messages SET "
                        " status = 'failed', "
                        " error_reason = ?, retries = ? "
                        "WHERE id = ?",
                        (error, new_retries, mid),
                    )
                out["failed"] += 1
            else:
                # Back off — bump retries + leave scheduled
                with _conn() as conn:
                    conn.execute(
                        "UPDATE scheduled_messages SET "
                        " retries = ?, error_reason = ? "
                        "WHERE id = ?",
                        (new_retries, error, mid),
                    )
                out["failed"] += 1
        else:
            with _conn() as conn:
                conn.execute(
                    "UPDATE scheduled_messages SET "
                    " status = 'sent', sent_at = ?, "
                    " provider_msg_id = ? "
                    "WHERE id = ?",
                    (now, provider_msg_id, mid),
                )
            out["sent"] += 1
    return out


VALID_SMS_PROVIDERS = frozenset({"sandbox", "msg91", "twilio", "kaleyra"})
VALID_WHATSAPP_PROVIDERS = frozenset({"sandbox", "whatsapp_cloud", "twilio"})


def _provider_send(
    *,
    channel: str,
    phone: str,
    body: str,
    template,
) -> tuple[str | None, str | None]:
    """Provider dispatch. Returns (provider_msg_id, error).

    Selection: `PADHAI_SMS_PROVIDER` / `PADHAI_WHATSAPP_PROVIDER` env
    var pins the active provider for each channel. Each adapter reads
    its own credentials at call time (lazy) so a missing key only
    fails the message it's used for — not the whole module.

    Sandbox returns an immediate fake message id — used by tests and
    by dev environments without a real provider account.

    Adapters use plain urllib so we don't pull a provider SDK into
    requirements.txt; the actual HTTP calls are simple POSTs.
    """
    provider = (
        DEFAULT_WHATSAPP_PROVIDER if channel == "whatsapp"
        else DEFAULT_SMS_PROVIDER
    )
    if provider == "sandbox":
        return f"sandbox_{uuid.uuid4().hex[:12]}", None
    valid = (
        VALID_WHATSAPP_PROVIDERS if channel == "whatsapp"
        else VALID_SMS_PROVIDERS
    )
    if provider not in valid:
        return None, f"provider {provider!r} not in {sorted(valid)}"
    try:
        if provider == "msg91":
            return _send_via_msg91(phone=phone, body=body, template=template)
        if provider == "twilio":
            return _send_via_twilio(
                channel=channel, phone=phone, body=body,
            )
        if provider == "kaleyra":
            return _send_via_kaleyra(phone=phone, body=body)
        if provider == "whatsapp_cloud":
            return _send_via_whatsapp_cloud(
                phone=phone, body=body, template=template,
            )
    except Exception as e:
        return None, f"provider {provider} error: {e}"
    return None, f"provider {provider!r} dispatch fell through"


# ---------- per-provider adapters ----------
#
# Each adapter is a thin urllib wrapper around the provider's REST
# API. We deliberately avoid bringing in provider SDKs (msg91-python,
# twilio, etc.) so requirements.txt stays small + cold-start fast.


def _http_post_json(url: str, *, headers: dict, payload: dict, timeout: float = 10.0) -> tuple[int, str]:
    """Minimal urllib POST. Returns (status_code, response_body_text)."""
    import json as _json
    import urllib.error
    import urllib.request
    data = _json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return e.code, body


def _http_post_form(url: str, *, headers: dict, form: dict, timeout: float = 10.0) -> tuple[int, str]:
    """Form-encoded POST for providers that don't speak JSON."""
    import urllib.error
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        return e.code, body


def _send_via_msg91(
    *, phone: str, body: str, template,
) -> tuple[str | None, str | None]:
    """MSG91 — India's largest transactional SMS provider.
    Requires DLT-registered template ids (MSG91_TEMPLATE_ID per
    template). Reads `MSG91_AUTH_KEY` + `MSG91_SENDER_ID` from env."""
    import json as _json
    auth_key = os.environ.get("MSG91_AUTH_KEY", "").strip()
    sender = os.environ.get("MSG91_SENDER_ID", "").strip()
    if not auth_key:
        return None, "MSG91_AUTH_KEY not set"
    template_id = getattr(template, "msg91_template_id", None) if template else None
    if not template_id:
        # MSG91 requires a template id under DLT — refuse if missing
        return None, "msg91 requires template.msg91_template_id"
    e164 = phone.lstrip("+")
    status, resp_text = _http_post_json(
        "https://control.msg91.com/api/v5/flow/",
        headers={"authkey": auth_key},
        payload={
            "template_id": template_id,
            "sender": sender or "PADHAI",
            "short_url": "0",
            "mobiles": e164,
            "VAR1": body,
        },
    )
    if status not in (200, 201, 202):
        return None, f"msg91 http {status}: {resp_text[:200]}"
    try:
        j = _json.loads(resp_text)
        msg_id = j.get("request_id") or j.get("data") or j.get("type")
        return str(msg_id), None
    except Exception:
        return None, f"msg91 unparseable response: {resp_text[:200]}"


def _send_via_twilio(
    *, channel: str, phone: str, body: str,
) -> tuple[str | None, str | None]:
    """Twilio REST API for SMS + WhatsApp. Reads `TWILIO_ACCOUNT_SID`,
    `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_SMS` / `TWILIO_FROM_WHATSAPP`."""
    import base64
    import json as _json
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    if not sid or not token:
        return None, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set"
    from_env = (
        "TWILIO_FROM_WHATSAPP" if channel == "whatsapp"
        else "TWILIO_FROM_SMS"
    )
    from_ = os.environ.get(from_env, "").strip()
    if not from_:
        return None, f"{from_env} not set"
    to_ = (
        f"whatsapp:{phone}" if channel == "whatsapp" else phone
    )
    from_value = (
        f"whatsapp:{from_}" if channel == "whatsapp" else from_
    )
    basic = base64.b64encode(f"{sid}:{token}".encode()).decode()
    status, resp_text = _http_post_form(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        headers={"Authorization": f"Basic {basic}"},
        form={"To": to_, "From": from_value, "Body": body},
    )
    if status not in (200, 201):
        return None, f"twilio http {status}: {resp_text[:200]}"
    try:
        j = _json.loads(resp_text)
        return str(j.get("sid", "")), None
    except Exception:
        return None, f"twilio unparseable response: {resp_text[:200]}"


def _send_via_kaleyra(
    *, phone: str, body: str,
) -> tuple[str | None, str | None]:
    """Kaleyra (India SMS/voice). Reads `KALEYRA_API_KEY`,
    `KALEYRA_SID`, `KALEYRA_SENDER`."""
    import json as _json
    api_key = os.environ.get("KALEYRA_API_KEY", "").strip()
    sid = os.environ.get("KALEYRA_SID", "").strip()
    sender = os.environ.get("KALEYRA_SENDER", "").strip()
    if not api_key or not sid:
        return None, "KALEYRA_API_KEY / KALEYRA_SID not set"
    status, resp_text = _http_post_form(
        f"https://api.kaleyra.io/v1/{sid}/messages",
        headers={"api-key": api_key},
        form={
            "to": phone,
            "sender": sender or "PADHAI",
            "type": "TXN",
            "body": body,
        },
    )
    if status not in (200, 201, 202):
        return None, f"kaleyra http {status}: {resp_text[:200]}"
    try:
        j = _json.loads(resp_text)
        return str(j.get("id", "")), None
    except Exception:
        return None, f"kaleyra unparseable response: {resp_text[:200]}"


def _send_via_whatsapp_cloud(
    *, phone: str, body: str, template,
) -> tuple[str | None, str | None]:
    """Meta WhatsApp Business Cloud API. Reads
    `WHATSAPP_PHONE_NUMBER_ID` + `WHATSAPP_ACCESS_TOKEN`. Supports
    text messages (no template) or template-language messages when
    the template carries `whatsapp_template_name`."""
    import json as _json
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "").strip()
    if not phone_id or not token:
        return None, "WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN not set"
    to_ = phone.lstrip("+")
    template_name = getattr(template, "whatsapp_template_name", None) if template else None
    if template_name:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en_US"},
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to_,
            "type": "text",
            "text": {"body": body},
        }
    status, resp_text = _http_post_json(
        f"https://graph.facebook.com/v18.0/{phone_id}/messages",
        headers={"Authorization": f"Bearer {token}"},
        payload=payload,
    )
    if status not in (200, 201):
        return None, f"whatsapp_cloud http {status}: {resp_text[:200]}"
    try:
        j = _json.loads(resp_text)
        msgs = j.get("messages") or []
        if msgs:
            return str(msgs[0].get("id", "")), None
        return None, f"whatsapp_cloud response missing messages[]: {resp_text[:200]}"
    except Exception:
        return None, f"whatsapp_cloud unparseable response: {resp_text[:200]}"


def user_message_stats(user_id: str) -> dict:
    """Per-user counters — drives the 'how many reminders did I
    get' UI."""
    with _conn() as conn:
        by_status = conn.execute(
            "SELECT status, COUNT(*) FROM scheduled_messages "
            "WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    return {r[0]: r[1] for r in by_status}
