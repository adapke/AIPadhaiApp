"""prod-147 — Daily Memory Boost push cron.

Runs at 07:00 IST each day. For each active student (i.e. one who is
enrolled in an exam pack with `status='active'` and `daily_minutes>0`),
builds today's 3-question pack via `memory_boost.get_or_create_pack`
and sends a streak nudge push notification via `push.send_one`.

Idempotent on the user-side because:
  * `memory_boost.get_or_create_pack` returns existing same-day picks
    if called twice on the same `pack_date`
  * `push.send_one` is gated by user opt-in (category='streak'); a
    user who opted out receives nothing

Cron line (Linux/macOS):
    30 1 * * * cd /opt/aipathshala && /opt/venv/bin/python \
        scripts/memory_boost_daily_push.py >> /var/log/memboost.log 2>&1

(07:00 IST = 01:30 UTC, so 30 1 * * * fires at the right wall-clock.)

Usage:
    python scripts/memory_boost_daily_push.py [--dry-run] [--limit N]

`--dry-run` prints what would happen without sending pushes.
`--limit N` only processes the first N candidate users — useful for
smoke-testing on a fresh box without burning push quota.

Exit codes:
    0 — success (even if some users had no tokens / opted out)
    1 — fatal error (DB unreachable, push module missing)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Default board+grade fallback when the enrollment doesn't carry one.
# Most enrollments link to an exam_pack; we look up the pack's board+grade.
# Board key is lowercase to match how `question_bank.upsert` stores it
# — exact-match search in `memory_boost.get_or_create_pack` would miss
# uppercase keys otherwise.
DEFAULT_BOARD = "cbse"
DEFAULT_GRADE = 10


def _active_users(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Return list of {user_id, pack_code, daily_minutes} for users
    currently enrolled in an active exam pack. We don't ALSO require
    recent activity — the cron's job is to NUDGE, including users who
    haven't shown up in a while (streak=0 → streak=1).
    """
    q = (
        "SELECT user_id, pack_code, daily_minutes "
        "FROM exam_pack_enrollments "
        "WHERE status = 'active' AND daily_minutes > 0 "
        "AND user_id IS NOT NULL AND user_id != '' "
    )
    try:
        if limit:
            q += "LIMIT ?"
            rows = conn.execute(q, (limit,)).fetchall()
        else:
            rows = conn.execute(q).fetchall()
    except sqlite3.OperationalError:
        # Fresh DB hasn't migrated the orgs/exam tables yet — that's
        # equivalent to "no enrollments" from our POV.
        return []
    return [
        {"user_id": r[0], "pack_code": r[1], "daily_minutes": r[2]}
        for r in rows
    ]


def _board_grade_for_user(
    conn: sqlite3.Connection,
    user_id: str,  # noqa: ARG001 — reserved for future per-user override lookup
    pack_code: str | None,
) -> tuple[str, int]:
    """Resolve the user's board+grade. Prefer the exam_pack's metadata
    when present, else default to CBSE/10. We keep this best-effort —
    a wrong default still gives the user *something* to practice."""
    # Try exam_packs.board / grade if the columns exist
    try:
        row = conn.execute(
            "SELECT board, grade FROM exam_packs WHERE code = ?",
            (pack_code or "",),
        ).fetchone()
        if row and row[0] and row[1]:
            return (row[0], int(row[1]))
    except sqlite3.OperationalError:
        # exam_packs may not have board/grade columns — that's fine
        pass
    return (DEFAULT_BOARD, DEFAULT_GRADE)


def _build_push_body(pack_size: int, streak: int) -> tuple[str, str]:
    """Title + body for the streak nudge. Keep India-friendly + crisp."""
    if streak == 0:
        title = "Your daily 3-question drill is ready 📚"
        body = (
            f"Today's pack has {pack_size} question{'s' if pack_size != 1 else ''}. "
            "Start a streak — it takes 2 minutes."
        )
    elif streak < 7:
        title = f"🔥 Day {streak + 1} starts now"
        body = (
            f"Keep your streak alive — today's {pack_size}-question drill is ready."
        )
    else:
        title = f"🔥 {streak}-day streak — don't break it!"
        body = (
            f"You're on a roll. {pack_size} fresh questions waiting."
        )
    return title, body


def run(
    *,
    dry_run: bool = False,
    limit: int | None = None,
    push_sender=None,
) -> dict:
    """Main loop. Returns a summary dict — used by tests + the cron log.

    `push_sender` is dependency-injected for tests; defaults to
    `padhai.push.send_one`.
    """
    from padhai import db, memory_boost, push
    if push_sender is None:
        push_sender = push.send_one

    memory_boost.migrate()

    path = db.sqlite_path()
    if not path.exists():
        print(f"[memboost-push] DB not found at {path} — skipping")
        return {
            "processed": 0,
            "sent": 0,
            "skipped_no_pack": 0,
            "skipped_opt_out": 0,
            "errors": [],
            "dry_run": dry_run,
        }

    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        users = _active_users(conn, limit=limit)
        if not users:
            print("[memboost-push] No active enrollments — nothing to do.")
            return {
                "processed": 0,
                "sent": 0,
                "skipped_no_pack": 0,
                "skipped_opt_out": 0,
                "errors": [],
                "dry_run": dry_run,
            }

        sent = 0
        skipped_no_pack = 0
        skipped_opt_out = 0
        errors: list[str] = []

        for entry in users:
            user_id = entry["user_id"]
            pack_code = entry["pack_code"]
            board, grade = _board_grade_for_user(conn, user_id, pack_code)
            try:
                picks = memory_boost.get_or_create_pack(
                    user_id=user_id, board=board, grade=grade,
                )
            except Exception as e:
                errors.append(f"{user_id}: pack creation failed: {e}")
                continue

            if not picks:
                skipped_no_pack += 1
                continue

            streak = memory_boost.get_streak(user_id)
            title, body = _build_push_body(
                pack_size=len(picks),
                streak=streak.get("current_streak", 0),
            )

            if dry_run:
                print(
                    f"[dry-run] {user_id}: {len(picks)} picks, "
                    f"streak={streak.get('current_streak', 0)} → "
                    f"would send: {title!r}"
                )
                sent += 1
                continue

            try:
                result = push_sender(
                    user_id=user_id,
                    category="streak",
                    title=title,
                    body=body,
                    payload={"link": "/memory-boost",
                             "pack_date": picks[0].pack_date},
                )
            except Exception as e:
                errors.append(f"{user_id}: push failed: {e}")
                continue

            if getattr(result, "skipped_opt_out", False):
                skipped_opt_out += 1
            elif getattr(result, "delivered", 0) > 0:
                sent += 1
            # else: user has no tokens — silently no-op

        summary = {
            "processed": len(users),
            "sent": sent,
            "skipped_no_pack": skipped_no_pack,
            "skipped_opt_out": skipped_opt_out,
            "errors": errors,
            "dry_run": dry_run,
        }
        print(
            f"[memboost-push] processed={summary['processed']} "
            f"sent={summary['sent']} "
            f"skipped_no_pack={summary['skipped_no_pack']} "
            f"skipped_opt_out={summary['skipped_opt_out']} "
            f"errors={len(summary['errors'])} "
            f"dry_run={dry_run}"
        )
        return summary
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without sending pushes.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N candidate users (smoke testing).",
    )
    args = parser.parse_args()

    started = time.time()
    try:
        result = run(dry_run=args.dry_run, limit=args.limit)
    except Exception as e:
        print(f"[memboost-push] FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    elapsed = time.time() - started
    print(f"[memboost-push] done in {elapsed:.1f}s")
    if result.get("errors"):
        # Non-zero exit on partial failure so monitoring picks it up,
        # but only when more than 10% errored — single bad rows are
        # expected with messy data.
        err_rate = len(result["errors"]) / max(1, result["processed"])
        if err_rate > 0.10:
            sys.exit(1)


if __name__ == "__main__":
    main()
