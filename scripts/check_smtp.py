"""prod-173 — SMTP verifier.

Three modes:

  --check        Pure config check. Verifies env vars are set + parse;
                 no network call. Always safe to run (no email sent).

  --connect      Opens a TCP+STARTTLS handshake to SMTP_HOST:SMTP_PORT,
                 authenticates with SMTP_USER:SMTP_PASS, then DISCONNECTS
                 without sending an email. Catches credential / TLS bugs
                 without spamming anyone.

  --send=TO      Sends an actual test email to the address TO. Use after
                 --connect passes, to verify deliverability end-to-end.

Without any flag, the default is --check.

Required env vars:
    SMTP_HOST
    SMTP_PORT       (default 587 for STARTTLS, 465 for implicit TLS)
    SMTP_USER
    SMTP_PASS
    SMTP_FROM       (the From: address used by parent-consent emails)

Exit codes:
    0 — check passed
    1 — config gap / connection error / auth failure
"""
from __future__ import annotations

import argparse
import contextlib
import os
import smtplib
import socket
import ssl
import sys
from email.message import EmailMessage

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


REQUIRED = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM")


def _check_config() -> list[str]:
    """Return list of config-gap error strings; empty list means OK."""
    gaps = []
    for k in REQUIRED:
        if not (os.environ.get(k) or "").strip():
            gaps.append(f"{k} is unset or empty")
    port = (os.environ.get("SMTP_PORT") or "587").strip()
    if not port.isdigit():
        gaps.append(f"SMTP_PORT={port!r} is not numeric")
    return gaps


def _connect_and_auth(verbose: bool = False) -> tuple[bool, str]:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    timeout = 10.0
    try:
        if port == 465:
            # Implicit TLS
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout) as smtp:
                if verbose:
                    print(f"  connected (SMTPS:{host}:{port})")
                smtp.login(user, pwd)
                if verbose:
                    print(f"  auth OK for user={user!r}")
                return True, "OK"
        # STARTTLS (port 587 default)
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                ctx = ssl.create_default_context()
                smtp.starttls(context=ctx)
                smtp.ehlo()
                if verbose:
                    print(f"  STARTTLS upgraded (SMTP:{host}:{port})")
            else:
                if verbose:
                    print("  WARN: server does not advertise STARTTLS")
            smtp.login(user, pwd)
            if verbose:
                print(f"  auth OK for user={user!r}")
            return True, "OK"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"SMTPAuthenticationError: {e.smtp_code} {e.smtp_error!r}"
    except smtplib.SMTPException as e:
        return False, f"SMTPException: {type(e).__name__}: {e}"
    except (TimeoutError, OSError) as e:
        return False, f"network: {type(e).__name__}: {e}"


def _send_test(to_addr: str, verbose: bool = False) -> tuple[bool, str]:
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or "587")
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    sender = os.environ["SMTP_FROM"]
    timeout = 15.0

    msg = EmailMessage()
    msg["Subject"] = "[AI Pathshala] SMTP smoke test (prod-173)"
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(
        "This is a smoke-test email from scripts/check_smtp.py.\n\n"
        "If you received it, your SMTP path is configured correctly\n"
        "and parent-consent emails for under-18 students will be\n"
        "delivered as expected (DPDP Act 2023 §9).\n\n"
        "No action required. Delete this email.\n",
    )

    try:
        if port == 465:
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=timeout) as smtp:
                smtp.login(user, pwd)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=timeout) as smtp:
                smtp.ehlo()
                if smtp.has_extn("starttls"):
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(user, pwd)
                smtp.send_message(msg)
        if verbose:
            print(f"  sent: From={sender!r} To={to_addr!r}")
        return True, f"sent to {to_addr}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true",
                   help="Config-only check (default).")
    p.add_argument("--connect", action="store_true",
                   help="TCP+TLS+auth handshake; no email sent.")
    p.add_argument("--send", metavar="TO",
                   help="Send a test email to TO.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    # Force at least one mode
    if not (args.check or args.connect or args.send):
        args.check = True

    print("[smtp-check] configuration:")
    gaps = _check_config()
    for k in REQUIRED:
        val = (os.environ.get(k) or "").strip()
        # Redact pass; show prefixes for the rest.
        if k == "SMTP_PASS":
            shown = "***" if val else "(unset)"
        elif k == "SMTP_HOST":
            shown = val or "(unset)"
        else:
            shown = (val[:30] + "…") if len(val) > 33 else (val or "(unset)")
        print(f"  {k:12s} = {shown}")
    print(f"  {'SMTP_PORT':12s} = {os.environ.get('SMTP_PORT') or '587 (default)'}")

    if gaps:
        print("\n[smtp-check] FAIL — config gaps:")
        for g in gaps:
            print(f"  - {g}")
        return 1
    print("\n[smtp-check] config OK.")

    if args.connect or args.send:
        print("\n[smtp-check] connect + auth handshake:")
        ok, msg = _connect_and_auth(verbose=args.verbose)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            return 1

    if args.send:
        print(f"\n[smtp-check] sending test email to {args.send!r}:")
        ok, msg = _send_test(args.send, verbose=args.verbose)
        print(f"  {'OK' if ok else 'FAIL'}: {msg}")
        if not ok:
            return 1
        print("\n[smtp-check] DELIVERED. Check the inbox.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
