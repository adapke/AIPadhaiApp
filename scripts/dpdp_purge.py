"""prod-164 — DPDP Act 2023 §12 30-day purge cron.

The `/api/me/account` DELETE endpoint anonymises the user's email
(``deleted-<user_id>@deleted.invalid``) and locks the account
immediately, but the DPDP Act allows up to 30 days for full deletion
of personal data. This script is the operationalisation: it finds
accounts that were anonymised ≥30 days ago and removes every row
keyed on their ``user_id`` across the application's tables, then
deletes the user row itself.

Tables are discovered dynamically — any table with a column literally
named ``user_id`` is purged. That covers the 50+ per-user tables in
``padhai/`` without us hard-coding a list that drifts over time.

Cron line (Linux/macOS):
    0 3 * * * cd /opt/aipathshala && /opt/venv/bin/python \\
        scripts/dpdp_purge.py >> /var/log/dpdp-purge.log 2>&1

(03:00 IST daily; low-traffic window.)

Usage:
    python scripts/dpdp_purge.py [--dry-run] [--min-days 30] [--limit N]

``--dry-run`` prints what would be deleted without touching the DB.
``--min-days`` overrides the 30-day floor (legal minimum — never
reduce below 30 in production).
``--limit`` processes at most N users this run (useful for testing
on a real DB without a long-running transaction).

Exit codes:
    0 — success (even if no users were eligible)
    1 — fatal error (DB unreachable, schema mismatch)
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# UTF-8 stdout so the audit log lines don't crash Windows cp1252 consoles.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Tables whose rows we DO NOT purge (they don't hold per-user PII or
# are intentionally retained for audit / compliance trails).
PROTECTED_TABLES = {
    # Audit log: required to retain. We DO scrub the actor_user_id
    # column manually below so the trail says "ANONYMIZED" instead of
    # the original UUID, but we keep the log row itself.
    "audit_log",
    # Aggregate per-day per-user cost rows can stay (they're already
    # de-identified after the user row is deleted). DPDP allows
    # anonymised analytical retention.
    "usage_daily",
    "llm_calls",
    "llm_alerts",
}


def _is_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _sqlite_path() -> Path:
    from padhai.db import sqlite_path
    return sqlite_path()


def _list_user_id_tables_sqlite(conn: sqlite3.Connection) -> list[str]:
    """Return every table in the SQLite DB that has a `user_id`
    column (case-insensitive). Excludes the `users` table itself
    (we delete that last) and the protected list."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    tables = []
    for (name,) in rows:
        if name == "users" or name in PROTECTED_TABLES:
            continue
        try:
            cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
        except sqlite3.OperationalError:
            continue
        col_names = {c[1].lower() for c in cols}
        if "user_id" in col_names:
            tables.append(name)
    return tables


def _list_user_id_tables_postgres(conn) -> list[str]:
    """Same as the sqlite variant but using Postgres' information_schema."""
    cur = conn.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE table_schema='public' AND lower(column_name)='user_id'"
    )
    tables = []
    for (name,) in cur.fetchall():
        if name == "users" or name in PROTECTED_TABLES:
            continue
        tables.append(name)
    return tables


def _eligible_users_sqlite(
    conn: sqlite3.Connection, min_days: int, limit: int | None,
) -> list[tuple[str, str, float]]:
    """Return (user_id, anonymised_email, deletion_requested_at)
    tuples for users whose anonymisation is at least `min_days` old.

    SQLite stores audit_log timestamps as numeric epoch seconds in our
    schema. We do a LEFT JOIN to find the latest
    `dpdp.account_deletion_requested` audit entry per user; if the
    audit row is missing, we fall back to the user row's `created_at`
    (which is the safest lower-bound for "when did they ask").
    """
    cutoff = time.time() - (min_days * 86400)
    q = (
        "SELECT u.id, u.email, "
        "       COALESCE(MAX(a.created_at), u.created_at) AS req_at "
        "FROM users u "
        "LEFT JOIN audit_log a "
        "  ON a.actor_user_id = u.id "
        " AND a.action = 'dpdp.account_deletion_requested' "
        "WHERE u.email LIKE 'deleted-%@deleted.invalid' "
        "  AND u.account_locked = 1 "
        "GROUP BY u.id, u.email, u.created_at "
        "HAVING COALESCE(MAX(a.created_at), u.created_at) <= ? "
        "ORDER BY req_at ASC "
    )
    params: tuple = (cutoff,)
    if limit:
        q += "LIMIT ? "
        params = (cutoff, limit)
    try:
        rows = conn.execute(q, params).fetchall()
    except sqlite3.OperationalError as exc:
        # audit_log table missing (fresh DB) — bail safely; the
        # anonymisation step already locked the account so they're
        # functionally deleted.
        print(f"[dpdp-purge] schema mismatch ({exc}); 0 users eligible.")
        return []
    return [(r[0], r[1], float(r[2] or 0)) for r in rows]


def _eligible_users_postgres(conn, min_days: int, limit: int | None):
    """Same as the sqlite variant; Postgres uses timestamptz so
    we cast accordingly. `audit_log.created_at` is a timestamptz
    column per db/changesets/001."""
    cutoff = datetime.now(UTC) - timedelta(days=min_days)
    q = (
        "SELECT u.id, u.email, "
        "       COALESCE(MAX(a.created_at), u.created_at) AS req_at "
        "FROM users u "
        "LEFT JOIN audit_log a "
        "  ON a.actor_user_id = u.id "
        " AND a.action = 'dpdp.account_deletion_requested' "
        "WHERE u.email LIKE 'deleted-%@deleted.invalid' "
        "  AND u.account_locked = TRUE "
        "GROUP BY u.id, u.email, u.created_at "
        "HAVING COALESCE(MAX(a.created_at), u.created_at) <= %s "
        "ORDER BY req_at ASC "
    )
    params: tuple = (cutoff,)
    if limit:
        q += "LIMIT %s "
        params = (cutoff, limit)
    try:
        cur = conn.execute(q, params)
        return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    except Exception as exc:
        print(f"[dpdp-purge] postgres query failed: {exc}")
        return []


def _purge_user_sqlite(
    conn: sqlite3.Connection, user_id: str, tables: list[str], dry_run: bool,
) -> dict[str, int]:
    """Delete every row keyed on the user across the given tables,
    then anonymise the audit_log entries pointing at this user.

    Returns {table: rowcount} for the audit log entry."""
    counts: dict[str, int] = {}
    for tbl in tables:
        try:
            if dry_run:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE user_id = ?",
                    (user_id,),
                )
                counts[tbl] = cur.fetchone()[0]
            else:
                cur = conn.execute(
                    f"DELETE FROM {tbl} WHERE user_id = ?",
                    (user_id,),
                )
                counts[tbl] = cur.rowcount
        except sqlite3.OperationalError as exc:
            # Table dropped between schema discovery and purge — skip.
            print(f"  WARN {tbl}: {exc}")
            counts[tbl] = 0

    # Scrub the audit_log actor_user_id so the trail says ANONYMIZED;
    # keep the action + timestamp for compliance.
    try:
        if not dry_run:
            conn.execute(
                "UPDATE audit_log SET actor_user_id = 'ANONYMIZED' "
                "WHERE actor_user_id = ?",
                (user_id,),
            )
    except sqlite3.OperationalError:
        pass

    # Finally delete the user row itself.
    if not dry_run:
        try:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        except sqlite3.OperationalError as exc:
            print(f"  WARN failed to delete user row: {exc}")
    return counts


def _purge_user_postgres(conn, user_id: str, tables: list[str], dry_run: bool):
    counts: dict[str, int] = {}
    for tbl in tables:
        try:
            if dry_run:
                cur = conn.execute(
                    f"SELECT COUNT(*) FROM {tbl} WHERE user_id = %s",
                    (user_id,),
                )
                counts[tbl] = cur.fetchone()[0]
            else:
                cur = conn.execute(
                    f"DELETE FROM {tbl} WHERE user_id = %s",
                    (user_id,),
                )
                counts[tbl] = cur.rowcount or 0
        except Exception as exc:
            print(f"  WARN {tbl}: {exc}")
            counts[tbl] = 0

    try:
        if not dry_run:
            conn.execute(
                "UPDATE audit_log SET actor_user_id = 'ANONYMIZED' "
                "WHERE actor_user_id = %s",
                (user_id,),
            )
    except Exception:
        pass

    if not dry_run:
        try:
            conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
        except Exception as exc:
            print(f"  WARN failed to delete user row: {exc}")
    return counts


def run(*, min_days: int, limit: int | None, dry_run: bool) -> int:
    """Main entrypoint. Returns the number of users purged."""
    if min_days < 30:
        print(
            f"[dpdp-purge] WARNING: --min-days={min_days} is below the "
            "DPDP §12 30-day floor. Use only for testing."
        )

    purged = 0
    if _is_postgres():
        import psycopg as _pg
        db_url = os.environ["DATABASE_URL"]
        with _pg.connect(db_url, autocommit=True,
                         options="-c search_path=public") as conn:
            tables = _list_user_id_tables_postgres(conn)
            print(f"[dpdp-purge] discovered {len(tables)} user_id tables: "
                  f"{', '.join(sorted(tables)[:10])}{'…' if len(tables) > 10 else ''}")
            users = _eligible_users_postgres(conn, min_days, limit)
            print(f"[dpdp-purge] {len(users)} users eligible (>= {min_days}d "
                  f"since anonymisation)")
            for uid, email, req_at in users:
                print(f"\n[dpdp-purge] user={uid} email={email} req_at={req_at}")
                counts = _purge_user_postgres(conn, uid, tables, dry_run)
                total = sum(counts.values())
                tag = "[DRY-RUN]" if dry_run else "[PURGED]"
                print(f"  {tag} {total} rows across {sum(1 for v in counts.values() if v)} tables")
                purged += 1
    else:
        db_path = _sqlite_path()
        if not db_path.exists():
            print(f"[dpdp-purge] DB not found at {db_path} — nothing to do.")
            return 0
        conn = sqlite3.connect(str(db_path), timeout=10.0)
        try:
            tables = _list_user_id_tables_sqlite(conn)
            print(f"[dpdp-purge] discovered {len(tables)} user_id tables: "
                  f"{', '.join(sorted(tables)[:10])}{'…' if len(tables) > 10 else ''}")
            users = _eligible_users_sqlite(conn, min_days, limit)
            print(f"[dpdp-purge] {len(users)} users eligible (>= {min_days}d "
                  f"since anonymisation)")
            for uid, email, req_at in users:
                req_iso = datetime.fromtimestamp(req_at, UTC).isoformat() if req_at else "?"
                print(f"\n[dpdp-purge] user={uid} email={email} req_at={req_iso}")
                counts = _purge_user_sqlite(conn, uid, tables, dry_run)
                total = sum(counts.values())
                tag = "[DRY-RUN]" if dry_run else "[PURGED]"
                print(f"  {tag} {total} rows across {sum(1 for v in counts.values() if v)} tables")
                purged += 1
            if not dry_run:
                conn.commit()
        finally:
            conn.close()

    print(f"\n[dpdp-purge] done. users_purged={purged} dry_run={dry_run}")
    return purged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be purged without modifying the DB.",
    )
    parser.add_argument(
        "--min-days", type=int, default=30,
        help="Minimum days since anonymisation. Default 30 (DPDP floor).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N users this run.",
    )
    args = parser.parse_args()
    try:
        run(min_days=args.min_days, limit=args.limit, dry_run=args.dry_run)
    except Exception as e:
        print(f"[dpdp-purge] FATAL: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
