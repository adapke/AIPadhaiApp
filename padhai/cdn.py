"""G3 — CDN signed URLs for video/audio/subtitle delivery.

Today: rendered MP4s are served directly out of `/jobs/{id}/video`
either as a local FileResponse or a RedirectResponse to R2's public
URL. That leaks revenue (public R2 URLs are torrent-shareable) and
adds latency (R2 → Delhi/Bangalore is slower than Cloudflare's
30+ India PoPs).

G3 introduces an HMAC-signed URL layer. When `PADHAI_CDN_BASE_URL`
+ `PADHAI_CDN_SIGNING_KEY` are set, video/audio/subtitle endpoints
issue a 302 to a signed CDN URL that expires after a configurable
TTL (default 24h). The CDN edge worker validates the signature
+ expiry before serving the upstream R2 object.

When the env vars are NOT set (dev / fresh boot / fallback), the
existing direct-serve path keeps working — this module is purely
additive.

Sign format (URL query string):
    ?expires=<unix_ts>&sig=<hex>

Signature input:
    HMAC_SHA256(key=signing_key, msg="<path>?expires=<unix_ts>")

We sign the path-with-expiry, not the full URL, so signatures stay
valid across CDN edges + custom domains pointing at the same origin.

Rotation:
- The signing key is a single env var. Rotation = deploy with a new
  key + grace period during which the CDN worker accepts BOTH old
  and new keys (worker config; not this module's concern).
- Per-org signing keys (so a school's leaked URL doesn't expose
  others) is a v1.2 follow-up.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import quote

# When the CDN edge worker fetches from our origin, it injects this
# header carrying PADHAI_CDN_ORIGIN_SECRET. If the header is present
# + matches, we serve directly (no redirect loop). If unset / mismatch,
# we 302 to the signed CDN URL.
_ORIGIN_HEADER = "x-cdn-origin-fetch"


_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def is_configured() -> bool:
    """Returns True when both env vars are set + non-empty. Callers
    fall back to direct-serve when this is False."""
    return bool(_base_url() and _signing_key())


def _base_url() -> str:
    # Trailing slash stripped so we get clean joins.
    return (os.environ.get("PADHAI_CDN_BASE_URL") or "").rstrip("/")


def _signing_key() -> str:
    return os.environ.get("PADHAI_CDN_SIGNING_KEY") or ""


def _ttl_seconds() -> int:
    try:
        v = int(os.environ.get("PADHAI_CDN_TTL_SECONDS") or _DEFAULT_TTL_SECONDS)
    except ValueError:
        v = _DEFAULT_TTL_SECONDS
    # Clamp to [60s, 7 days]. Below 60s breaks <video> seek behaviour
    # on flaky networks (the player re-requests + the URL has expired);
    # above 7 days is too long for a leak window.
    return max(60, min(v, 7 * 24 * 60 * 60))


def sign_path(path: str, *, ttl_seconds: int | None = None) -> str:
    """Return the path with `?expires=...&sig=...` appended. The path
    should start with `/` (e.g. `/jobs/abc/video`). Idempotent — if
    the path already has a query, we append with `&`.

    This is intentionally NOT a full URL: callers concatenate with
    `_base_url()` when they need to emit a 302."""
    expires = int(time.time()) + (ttl_seconds or _ttl_seconds())
    # Sign the canonical "<path>?expires=<n>" string. URL-encoding the
    # path is up to the caller; we sign exactly what the CDN edge will
    # see (after CF normalises percent-encoding for the typical chars
    # in our job IDs).
    msg = f"{path}?expires={expires}"
    sig = hmac.new(
        _signing_key().encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    sep = "&" if "?" in path else "?"
    return f"{path}{sep}expires={expires}&sig={sig}"


def signed_url(path: str, *, ttl_seconds: int | None = None) -> str:
    """Full signed URL ready for a 302 Location header. Requires
    is_configured() to be true; else raises RuntimeError."""
    if not is_configured():
        raise RuntimeError(
            "CDN not configured — set PADHAI_CDN_BASE_URL + "
            "PADHAI_CDN_SIGNING_KEY before calling signed_url()"
        )
    if not path.startswith("/"):
        path = "/" + path
    return _base_url() + sign_path(path, ttl_seconds=ttl_seconds)


def verify_signed_path(path: str, *, expires: int, sig: str, now: float | None = None) -> bool:
    """Verify a previously-signed URL. Used by the (optional) origin
    fallback handler — when the CDN is bypassed and a request hits
    our origin directly with a signed URL, the origin can re-verify.

    Constant-time comparison; rejects expired URLs.
    """
    if not _signing_key():
        return False
    current = (now if now is not None else time.time())
    if expires < current:
        return False
    msg = f"{path}?expires={expires}"
    expected = hmac.new(
        _signing_key().encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


# ---------- helpers wired into the web layer ----------

def is_origin_fetch(request) -> bool:
    """True when the inbound request is the CDN edge fetching from us
    (so we must NOT redirect — would loop). Matches the header against
    PADHAI_CDN_ORIGIN_SECRET; constant-time compare."""
    secret = os.environ.get("PADHAI_CDN_ORIGIN_SECRET") or ""
    if not secret:
        # Origin protection not configured — assume any non-CDN request
        # path is a real client and a redirect is safe. (In production
        # you should always set the secret; this fallback prevents
        # dev-time errors.)
        return False
    provided = ""
    try:
        provided = request.headers.get(_ORIGIN_HEADER) or ""
    except Exception:  # noqa: BLE001
        return False
    return bool(provided) and hmac.compare_digest(provided, secret)


def maybe_redirect(
    path: str,
    *,
    request=None,
    ttl_seconds: int | None = None,
) -> str | None:
    """Returns a signed CDN URL string when (a) CDN is configured AND
    (b) the request is NOT a CDN-origin fetch. Else None — the caller
    falls back to direct FileResponse / R2-direct.

    Usage:

        cdn_url = cdn.maybe_redirect(f"/jobs/{job.id}/video", request=request)
        if cdn_url:
            return RedirectResponse(cdn_url, status_code=302)
        # fall through to local FileResponse / R2 direct
    """
    if not is_configured():
        return None
    if request is not None and is_origin_fetch(request):
        return None
    return signed_url(path, ttl_seconds=ttl_seconds)
